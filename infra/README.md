# infra/: AWS production stack (Terraform)

Everything martom-ai runs on in AWS (`eu-central-1`), as Terraform:

```text
customer page (Cloudflare Pages)
        │ HTTPS
        ▼
CloudFront  https://<id>.cloudfront.net    no caching, SSE passed straight through
        │ HTTP + secret X-Origin-Verify header
        ▼
ALB (public subnets, accepts CloudFront addresses only)
        │ :8000
        ▼
ECS Fargate service: API task = `martom api` + Redis sidecar (localhost:6379)
        │ :5432                                  ▲
        ▼                                        │ same image, same network rules
RDS Postgres 16 + pgvector (private subnets)     │
        ▲                                        │
        └── job task: `martom sync` → `martom extract` → `martom embed`
              started by EventBridge Scheduler: nightly 01:00, weekly full Mon 05:00 (Europe/Sarajevo)
```

| File | What it holds |
| --- | --- |
| `bootstrap/` | One-time module with local state: the S3 bucket for the main state (versioned, encrypted, private) |
| `versions.tf` | Terraform ≥ 1.10, providers, S3 backend with native locking (`use_lockfile`, no DynamoDB) |
| `network.tf` | VPC, 2 public + 2 private subnets, no NAT gateway |
| `security_groups.tf` | CloudFront → ALB → API → RDS, job → RDS, outbound HTTPS |
| `alb.tf`, `cloudfront.tf` | Load balancer and the CloudFront distribution in front of it |
| `rds.tf` | Postgres 16, db.t4g.micro, single AZ, 7 days of backups |
| `secrets.tf` | Secrets Manager: `database-url` (written by Terraform), `app` (JSON, set by hand) |
| `ecr.tf`, `ecs.tf` | Image repository, cluster, API service, job task definition, env var mapping |
| `scheduler.tf` | The nightly and weekly sync schedules |

Agents only write and validate this code (`terraform fmt -check`, `terraform init -backend=false`, `terraform validate`). Tomislav runs `init`, `plan` and `apply` with his own SSO profile; `terraform apply` is blocked for agents in `.claude/settings.json`.

## First deploy, step by step

You need Terraform ≥ 1.10, AWS CLI v2, Docker, and an admin SSO profile for the account (`aws sso login --profile <profile>`, then `export AWS_PROFILE=<profile>`). All commands run from the repo root unless they `cd`.

### 0. Budget alerts (manual, AWS console, day one)

- [ ] **Billing and Cost Management → Budgets → Create budget**: a monthly cost budget of **$80**, with an email alert at **$65** (warning) and at **$80** (actual), plus a forecast alert at $80. Use your own email; it is set only in the console and never goes into the repo. Budgets are deliberately not in Terraform.

### 1. Bootstrap the state bucket (once)

```sh
cd infra/bootstrap
terraform init
terraform apply          # creates martom-ai-tfstate-<random suffix>
terraform output -raw state_bucket
```

The bootstrap's own state stays in `infra/bootstrap/terraform.tfstate` (git-ignored). Keep it somewhere safe; it is only needed to change the bucket later.

### 2. Init and plan the main stack

```sh
cd infra
cp backend.hcl.example backend.hcl        # git-ignored; put the bucket name from step 1 in it
terraform init -backend-config=backend.hcl
terraform plan -var api_desired_count=0 -out tfplan
terraform apply tfplan
```

`api_desired_count=0` for the first apply, because ECR is still empty: the API service starts no task until an image exists (step 4).

### 3. Set the app secrets

Write the JSON to a file **outside the repo**, upload it, delete the file:

```json
{
  "ANTHROPIC_API_KEY": "sk-ant-...",
  "COHERE_API_KEY": "...",
  "LANGSMITH_API_KEY": "lsv2_...",
  "ADMIN_TOKEN": "<openssl rand -hex 32>"
}
```

```sh
aws secretsmanager put-secret-value \
  --secret-id "$(terraform -chdir=infra output -raw app_secret_name)" \
  --secret-string file://$HOME/martom-app-secret.json
rm ~/martom-app-secret.json
```

Every key above must be present, or ECS refuses to start the task. `ADMIN_TOKEN` must be at least 32 characters, or the API refuses to start (`check_admin_token`). When a later milestone adds a secret setting (`ORDER_PHONE` in M5-6, `TURNSTILE_SECRET_KEY` in M5-7), add the key to this JSON first, then to `api_secret_keys` in `secrets.tf`.

### 4. Push the app image

The repo has no `Dockerfile` yet; it arrives with CD (M6-2), which also pushes on every merge. The image must have `martom` on `PATH`, `sh`, and `migrations/` and `knowledge/` in its working directory (both commands read them relative to the current folder). Build for x86 (the task definitions use `X86_64`):

```sh
REPO=$(terraform -chdir=infra output -raw ecr_repository_url)
aws ecr get-login-password | docker login --username AWS --password-stdin "${REPO%%/*}"
docker build --platform linux/amd64 -t "$REPO:latest" .
docker push "$REPO:latest"
```

### 5. Run the migrations, load the knowledge, fill the catalogue

One-off commands run as the job task with a command override:

```sh
run_once() {   # usage: run_once martom migrate
  aws ecs run-task \
    --cluster "$(terraform -chdir=infra output -raw ecs_cluster)" \
    --task-definition "$(terraform -chdir=infra output -raw job_task_definition)" \
    --launch-type FARGATE \
    --network-configuration "$(terraform -chdir=infra output -raw job_network_configuration)" \
    --overrides "$(jq -nc --args '{containerOverrides: [{name: "job", command: $ARGS.positional}]}' "$@")"
}

run_once martom migrate
run_once martom load-knowledge
```

Then start the pipeline once by hand instead of waiting for the night: `run_once sh -c 'martom sync --full && martom extract && martom embed'`. The first extract sends every listing to Haiku (a few dollars, once). Each task's output is in CloudWatch Logs, `/ecs/martom-production/job`. Runs started this way don't count toward the 10%-guard auto-apply streak, which is what you want.

### 6. Start the API

```sh
terraform -chdir=infra apply              # api_desired_count back to its default, 1
curl -s "$(terraform -chdir=infra output -raw api_url)/health"
```

`/health` answers `{"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}`. Put `api_url` into the frontend's API setting (Cloudflare Pages, M5/M6-2).

## Settings: where each env var comes from

The app reads every setting from environment variables (`src/martom/settings.py`). ECS sets plain values in the task definition and injects secrets from Secrets Manager at start-up. The same table is in `ecs.tf`.

| Env var | API task | Job task | Kind |
| --- | --- | --- | --- |
| `APP_ENV` | `production` | `production` | plain |
| `LOG_LEVEL` | `INFO` | `INFO` | plain |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173,https://martom-savjetnik.pages.dev` | not set | plain |
| `DATABASE_URL` | `martom/production/database-url` | same | secret (Terraform) |
| `REDIS_URL` | `redis://localhost:6379/0` (sidecar) | not set | plain |
| `OLX_API_BASE_URL` | not set | `https://api.olx.ba` | plain |
| `OLX_SHOP_USERNAME` | not set | `martom` | plain |
| `OLX_REQUEST_INTERVAL` | not set | `2.0` | plain |
| `ANTHROPIC_API_KEY` | `martom/production/app` → `ANTHROPIC_API_KEY` | same | secret (by hand) |
| `FAST_MODEL` | `claude-haiku-5-5` | `claude-haiku-5-5` | plain |
| `AGENT_MODEL` | `claude-sonnet-5-5` (read from M4) | not set | plain |
| `EXTRACTION_CONCURRENCY` | not set | `4` | plain |
| `EMBEDDING_PROVIDER` | `cohere` | `cohere` | plain |
| `EMBEDDING_MODEL` | `embed-multilingual-v3.0` | `embed-multilingual-v3.0` | plain |
| `COHERE_API_KEY` | `app` → `COHERE_API_KEY` | same | secret (by hand) |
| `ADMIN_TOKEN` | `app` → `ADMIN_TOKEN` | not set | secret (by hand) |
| `LANGSMITH_TRACING` | `true` | `true` | plain |
| `LANGSMITH_PROJECT` | `martom-ai-production` | same | plain |
| `LANGSMITH_API_KEY` | `app` → `LANGSMITH_API_KEY` | same | secret (by hand) |
| `SYNC_ARGS` | not set | `--scheduled` (nightly), `--full --scheduled` (weekly) | plain, job script only |

Not set in AWS: `TEST_DATABASE_URL`, `TEST_REDIS_URL`, `POSTGRES_PORT`, `REDIS_PORT` (local and CI only), and `ORDER_PHONE`, `TURNSTILE_SECRET_KEY`, `RATE_LIMIT_PER_MINUTE`, `MAX_TOKENS_PER_CONVERSATION` until M5-6/M5-7 read them. If your LangSmith account is in the EU region, add `LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com` to `common_environment` in `ecs.tf`.

## Monthly cost estimate

On-demand eu-central-1 list prices, checked 27 Nov 2026 from memory of the AWS price pages: confirm them in the AWS Pricing Calculator before you apply. Launch traffic: a few hundred conversations a month, the nightly job running about 1.5 h a day.

| Item | Size | Per month |
| --- | --- | --- |
| Application Load Balancer | $0.027/h + ~0.2 LCU | ~$21 |
| Fargate, API task | 0.25 vCPU, 1 GB, 24/7 | ~$12 |
| Fargate, job task | 0.5 vCPU, 1 GB, ~50 h | ~$1.50 |
| RDS Postgres | db.t4g.micro single-AZ + 20 GB gp3 (backups free up to the DB size) | ~$16 |
| Public IPv4 addresses | $0.005/h each: 2 for the ALB, 1 for the API task, the job task while it runs | ~$11 |
| Secrets Manager | 2 secrets | ~$0.80 |
| CloudWatch Logs | ~1 GB ingested | ~$1 |
| ECR | ≤ 10 images | ~$0.50 |
| CloudFront | always-free tier: 1 TB and 10 M requests | $0 |
| **Total** | | **~$63** |

What isn't here: NAT gateway (~$35, avoided with public IPs on the tasks), Multi-AZ RDS (double the RDS line), Container Insights, WAF. Claude, Cohere and LangSmith are billed separately (see `docs/PLAN.md`, Running costs).

## Cutting cost between test phases

Two options, with what each keeps and loses.

### Pause: stop RDS and scale the API to 0 (~$33/month left)

```sh
terraform -chdir=infra apply -var api_desired_count=0 -var schedules_enabled=false
aws rds stop-db-instance --db-instance-identifier martom-production
# resume:
aws rds start-db-instance --db-instance-identifier martom-production
terraform -chdir=infra apply
```

- **Kept:** everything. All database data, secrets, images, logs, the CloudFront address.
- **Still billed:** the ALB and its two IPv4 addresses (~$28), RDS storage and backups (~$3), Secrets Manager.
- **Saved:** the RDS instance (~$13), the API task and its IP (~$16), the nightly job.
- **AWS starts a stopped RDS instance by itself after 7 days.** Stop it again each week, or destroy instead if the pause is longer. Run `terraform apply` while the database is stopped only to change `api_desired_count` or `schedules_enabled`.

### Tear down: `terraform destroy` (~$1–3/month left)

```sh
terraform -chdir=infra apply   -var db_deletion_protection=false
terraform -chdir=infra destroy -var db_deletion_protection=false
```

- **Kept:**
  - the final RDS snapshot `martom-production-final` (~$0.10/GB-month) and the automated backups, until their 7 days end;
  - the S3 state bucket (bootstrap, protected by `prevent_destroy`);
  - the RDS log group `/aws/rds/instance/martom-production/postgresql`, which RDS created, not Terraform.
- **Lost:**
  - the running database. A new apply creates an **empty** database with a new password. The catalogue tables come back from OLX with migrate → sync → extract → embed (a few dollars of Haiku). Order requests, Toni's ignored review issues and the conversation checkpoints come back only if you restore the final snapshot by hand.
  - container logs (the `/ecs/...` log groups are deleted) and the ECR images (rebuilt from git).
  - the CloudFront address: a new distribution gets a new `*.cloudfront.net` name, so update the frontend's API URL.
- **Secrets:** both secrets are scheduled for deletion with a 7-day recovery window. Within that window a new apply fails because the names are taken: run `aws secretsmanager restore-secret --secret-id martom/production/app` (and the same for `.../database-url`), then `terraform import` them, or wait out the 7 days and set the app secret again (step 3).
- **Before the next destroy**, delete or rename the old `martom-production-final` snapshot, or the destroy fails because that snapshot name already exists.

## Notes

- **Non-overlapping syncs:** `martom sync` has no lock, so the schedules keep it apart. The weekly full run starts 4 h after that night's nightly run, which stops itself at its 3 h deadline. Keep that gap if you change a cron.
- **SSE through CloudFront:** CloudFront drops an origin connection after 60 s without bytes. The `/api/chat` stream must send something (a token or a `: keep-alive` comment) at least every 60 s.
- **pgvector:** RDS for PostgreSQL 16.5 and later ship pgvector 0.8.0; the engine version is `16`, so RDS picks its current default minor. Migration 007 checks the version itself.
- **Secrets in state:** the generated database password and the CloudFront origin header are in the Terraform state, which is why the state bucket is private, encrypted and versioned.
