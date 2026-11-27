# infra/: AWS production stack (Terraform)

Everything martom-ai runs on in AWS (`eu-central-1`), as Terraform:

```text
customer page (Cloudflare Pages)
        │ HTTPS
        ▼
CloudFront  https://<id>.cloudfront.net    no caching, SSE passed straight through
        │ VPC origin: HTTP over the AWS network, never the public internet
        ▼
internal ALB (private subnets, no public address)
        │ :8000
        ▼
ECS Fargate service: API task = `martom api` + Redis sidecar (localhost:6379)
        │ :5432                                  ▲
        ▼                                        │ same image, same network rules
RDS Postgres 16 + pgvector (private subnets)     │
        ▲                                        │
        └── job task: `martom sync` → `martom extract` → `martom embed`
              started by EventBridge Scheduler at 01:00 Europe/Sarajevo, every night (the sync's rolling refresh replaced the weekly full run, #135)
```

| File | What it holds |
| --- | --- |
| `bootstrap/` | One-time module with local state: the S3 bucket for the main state (versioned, encrypted, private) |
| `versions.tf` | Terraform ≥ 1.10, providers, S3 backend with native locking (`use_lockfile`, no DynamoDB) |
| `network.tf` | VPC, 2 public subnets (tasks) + 2 private subnets (ALB, RDS), no NAT gateway |
| `security_groups.tf` | CloudFront → ALB → API → RDS, job → RDS, outbound HTTPS |
| `alb.tf`, `cloudfront.tf` | Internal load balancer, and the CloudFront distribution that reaches it through a VPC origin |
| `rds.tf` | Postgres 16, db.t4g.micro, single AZ, 7 days of backups |
| `secrets.tf` | Secrets Manager: the RDS-managed master password (Terraform knows only its ARN), `app` (JSON, set by hand) |
| `ecr.tf`, `ecs.tf` | Image repository, cluster, API service, job task definition, env var mapping |
| `scheduler.tf` | The nightly and Sunday-night schedules, their dead-letter queue |

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
terraform plan -var api_desired_count=0 -out "$HOME/martom.tfplan"
terraform apply "$HOME/martom.tfplan"
rm "$HOME/martom.tfplan"
```

`api_desired_count=0` for the first apply, because ECR is still empty: the API service starts no task until an image exists (step 4). The schedules are created **disabled** (`schedules_enabled` defaults to `false`), so no job runs against an empty database; step 6 turns them on.

**Treat plan files and state as private.** No password is in them: RDS generates the database password and keeps it in its own Secrets Manager secret, the app secrets are set by hand outside Terraform, and Terraform stores only the two secrets' ARNs. But a saved plan holds the full state of the account in plain text (names, ARNs, endpoints, the network layout). So write it outside the repo, as above, and delete it after the apply. `tfplan`, `*.tfplan`, `*.tfstate*`, `*.tfvars` and `.terraform/` are git-ignored, and CI fails if a plan or state file is ever tracked.

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

The repo has no `Dockerfile` yet; it arrives with CD (M6-2), which also pushes on every merge. The image must have `martom` on `PATH`, `sh`, `timeout`, and `migrations/` and `knowledge/` in its working directory (both commands read them relative to the current folder). Build for x86 (the task definitions use `X86_64`):

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

### 6. Start the API and turn on the schedules

```sh
cp infra/terraform.tfvars.example infra/terraform.tfvars   # git-ignored; sets schedules_enabled = true
terraform -chdir=infra apply              # api_desired_count back to its default, 1; schedules ENABLED
curl -s "$(terraform -chdir=infra output -raw api_url)/health"
```

`/health` answers `{"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}`. Put `api_url` into the frontend's API setting (Cloudflare Pages, M5/M6-2). Terraform reads `terraform.tfvars` on every later plan and apply, so the schedules stay on.

The morning after the first night, check that it ran: a `sync_run` row started around 01:00 local time, and no messages in the dead-letter queue (`aws sqs get-queue-attributes --queue-url "$(terraform -chdir=infra output -raw scheduler_dlq_url)" --attribute-names ApproximateNumberOfMessages`). A message there means EventBridge Scheduler couldn't start the task (for example permissions or Fargate capacity); the message says why.

## Settings: where each env var comes from

The app reads every setting from environment variables (`src/martom/settings.py`). ECS sets plain values in the task definition and injects secrets from Secrets Manager at start-up. The same table is in `ecs.tf`.

| Env var | API task | Job task | Kind |
| --- | --- | --- | --- |
| `APP_ENV` | `production` | `production` | plain |
| `LOG_LEVEL` | `INFO` | `INFO` | plain |
| `CORS_ALLOWED_ORIGINS` | `http://localhost:5173,https://martom-savjetnik.pages.dev` | not set | plain |
| `DATABASE_URL` | `postgresql://martom@<rds endpoint>/martom?sslmode=require`, no password | same | plain |
| `PGPASSWORD` | RDS-managed secret → `password` (libpq reads it for every connection) | same | secret (RDS) |
| `REDIS_URL` | `redis://localhost:6379/0` (sidecar) | not set | plain |
| `OLX_API_BASE_URL` | not set | `https://api.olx.ba` | plain |
| `OLX_SHOP_USERNAME` | not set | `martom` | plain |
| `OLX_REQUEST_INTERVAL` | not set | `2.8` (`var.sync_request_interval`, at least 1.3), for the nightly sync and one-off syncs | plain |
| `OLX_NIGHTLY_DETAIL_CAP` | not set | `1000` detail GETs a night (changed listings + rolling slice) | plain |
| `OLX_ROLLING_REFRESH_DAYS` | not set | `7` nights | plain |
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

Not set in AWS: `TEST_DATABASE_URL`, `TEST_REDIS_URL`, `POSTGRES_PORT`, `REDIS_PORT` (local and CI only), and `ORDER_PHONE`, `TURNSTILE_SECRET_KEY`, `RATE_LIMIT_PER_MINUTE`, `MAX_TOKENS_PER_CONVERSATION` until M5-6/M5-7 read them. If your LangSmith account is in the EU region, add `LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com` to `common_environment` in `ecs.tf`.

## Monthly cost estimate

On-demand eu-central-1 list prices, checked 27 Nov 2026 from memory of the AWS price pages: confirm them in the AWS Pricing Calculator before you apply. Launch traffic: a few hundred conversations a month, the job running at most ~1.5 h a day on average (on the high side: most nights take minutes, the Sunday-night task ~2.5 h).

| Item | Size | Per month |
| --- | --- | --- |
| Application Load Balancer | $0.027/h + ~0.2 LCU | ~$21 |
| Fargate, API task | 0.25 vCPU, 1 GB, 24/7 | ~$12 |
| Fargate, job task | 0.5 vCPU, 1 GB, ~50 h | ~$1.50 |
| RDS Postgres | db.t4g.micro single-AZ + 20 GB gp3 (backups free up to the DB size) | ~$16 |
| Public IPv4 addresses | $0.005/h each: 1 for the API task, 1 for the job task while it runs (the internal ALB has none) | ~$4 |
| Secrets Manager | 2 secrets | ~$0.80 |
| CloudWatch Logs | ~1 GB ingested | ~$1 |
| ECR | ≤ 10 images | ~$0.50 |
| CloudFront | always-free tier: 1 TB and 10 M requests | $0 |
| **Total** | | **~$56** |

What isn't here: NAT gateway (~$35, avoided with public IPs on the tasks), Multi-AZ RDS (double the RDS line), Container Insights, WAF. Claude, Cohere and LangSmith are billed separately (see `docs/PLAN.md`, Running costs).

## Cutting cost between test phases

Two options, with what each keeps and loses.

### Pause: stop RDS and scale the API to 0 (~$25/month left)

```sh
terraform -chdir=infra apply -var api_desired_count=0 -var schedules_enabled=false
aws rds stop-db-instance --db-instance-identifier martom-production
# resume (terraform.tfvars turns the schedules back on, api_desired_count goes back to 1):
aws rds start-db-instance --db-instance-identifier martom-production
terraform -chdir=infra apply
```

- **Kept:** everything. All database data, secrets, images, logs, the CloudFront address.
- **Still billed:** the ALB (~$21), RDS storage and backups (~$3), Secrets Manager.
- **Saved:** the RDS instance (~$13), the API task and its IP (~$16), the nightly job.
- **AWS starts a stopped RDS instance by itself after 7 days.** Stop it again each week, or destroy instead if the pause is longer. Run `terraform apply` while the database is stopped only to change `api_desired_count` or `schedules_enabled`.

### Tear down: `terraform destroy` (~$1–3/month left)

```sh
terraform -chdir=infra apply   -var db_deletion_protection=false
terraform -chdir=infra destroy -var db_deletion_protection=false
```

- **Kept:**
  - the final RDS snapshot `martom-production-final` (~$0.10/GB-month) and the automated backups, until their 7 days end;
  - the S3 state bucket (bootstrap, protected by `prevent_destroy`).
- **Lost:**
  - the running database. A new apply creates an **empty** database with a new password. The catalogue tables come back from OLX with migrate → sync → extract → embed (a few dollars of Haiku). Order requests, Toni's ignored review issues and the conversation checkpoints come back only if you restore the final snapshot by hand.
  - all logs (the `/ecs/...` log groups and the RDS log group are deleted), the dead-letter queue and the ECR images (rebuilt from git).
  - the CloudFront address: a new distribution gets a new `*.cloudfront.net` name, so update the frontend's API URL.
- **Secrets:** RDS deletes its password secret with the database. The `app` secret is scheduled for deletion with a 7-day recovery window. Within that window a new apply fails because the name is taken: run `aws secretsmanager restore-secret --secret-id martom/production/app`, then `terraform import aws_secretsmanager_secret.app <its ARN>`, or wait out the 7 days and set the app secret again (step 3).
- **Before the next destroy**, delete or rename the old `martom-production-final` snapshot, or the destroy fails because that snapshot name already exists.

## Checking the code

No AWS credentials and no backend are needed: these commands only format, lint and
validate the code. They run pinned tool versions in throwaway containers (`--rm`) that see
only `infra/`, with no `~/.aws` and no environment passed in. Run them from the repo root.

```sh
# a function, not a variable: zsh doesn't split "$TF" into words
tf() { docker run --rm -v "$PWD/infra:/w" -w /w hashicorp/terraform:1.10.5 "$@"; }
tf fmt -check -recursive
tf init -backend=false -input=false           # downloads the providers into infra/.terraform (git-ignored)
tf validate
docker run --rm -v "$PWD/infra/bootstrap:/w" -w /w hashicorp/terraform:1.10.5 init -backend=false -input=false
docker run --rm -v "$PWD/infra/bootstrap:/w" -w /w hashicorp/terraform:1.10.5 validate

# tflint with the AWS ruleset (.tflint.hcl); --init downloads the ruleset inside the container
for d in infra infra/bootstrap; do
  docker run --rm -v "$PWD/$d:/data" -w /data --entrypoint sh \
    ghcr.io/terraform-linters/tflint:v0.55.1 -c "tflint --init >/dev/null && tflint --format compact"
done

# checkov over both modules; accepted findings and their reasons are in .checkov.yaml
docker run --rm -v "$PWD/infra:/tf" -w /tf bridgecrew/checkov:3.2.334 \
  -d . --config-file .checkov.yaml --compact --quiet
```

The `.terraform.lock.hcl` files pin the provider versions and hold their checksums for
macOS (arm64 and x86) and Linux x86. After a provider upgrade, refresh them with
`tf providers lock -platform=darwin_arm64 -platform=darwin_amd64 -platform=linux_amd64`
(and the same in `infra/bootstrap`).

## Schedules

One scheduled task a night, at 01:00 Europe/Sarajevo (00:00 UTC in winter, 23:00 UTC in summer):

| Night | Schedule | Task runs |
| --- | --- | --- |
| every night (cron `* * ?`) | `nightly-sync` | `sync --scheduled` → `extract` → `embed` |

There is no weekly full run any more (#135). #105's full run made 2,719 OLX requests in 2 h 37 min, found no changed listing and still got 429s, so the nightly sync now does a **rolling refresh** instead: besides the listings whose list-page fields changed, it re-fetches the details of about 1/7 of the active listings (the ones fetched longest ago), so every listing is refreshed within 7 nights. The night's detail GETs are capped at 1,000 (`OLX_NIGHTLY_DETAIL_CAP`); changed listings always go through, and the rolling slice gets what is left (`sync_run.rolling_deferred` counts what waited). The slice runs last in the sync, after the allowlist guard and the deactivation re-check, so an OLX stop inside it can't skip those. An active listing that has left the listing pages is never in the slice; its deactivation re-check saves its detail when OLX says it is still active. `sync_run.oldest_detail_age_days` records the oldest detail among the active listings after each run, and `detail_refresh_overdue` is set when it is over 8 days, with a WARNING in the log that names the overdue listing ids (`overdue_listing_ids`): that is what the M6-3 alarm watches. `martom sync --full` still works as a one-off task (`run_once martom sync --full`, which runs at the task's 2.8 s too).

`martom sync` has no lock of its own. Two tasks can't overlap: each is killed after 16 h (`timeout`, `job_timeout_seconds` in `ecs.tf`) and the next one starts 24 h after it. Don't start a one-off `martom sync --full` while the nightly task is running.

Worst-case timeline for one night's task:

| Step | Normal | Worst case | What bounds it |
| --- | --- | --- | --- |
| `sync --scheduled` | ~20–25 min: ~372 slice GETs + the changed listings + ~50 list and discovery requests at 2.8 s; a night that fills the 1,000 cap ≈ 50 min | 3 h | the sync's own 3 h deadline |
| `extract` | minutes (changed listings only) | ~2 h | every listing changed: ~2,600 Haiku calls, 4 at a time, with retries |
| `embed` | minutes | ~3 h | ~28 Cohere batches × 6 attempts × 60 s |
| **Total** | **~30 min** | **~8 h**; 16 h is the hard stop | `timeout` kills the task |

The job task runs every sync at `sync_request_interval` (2.8 s by default, never below 1.3): OLX allows about 1,400 requests before a ~1,600 s 429 penalty (#105), and a nightly run that fills the detail cap makes about 1,050 plus the re-check GETs (a typical night about 420). A sync started by hand outside the task uses the Settings default of 2.0 s unless `OLX_REQUEST_INTERVAL` says otherwise.

So the latest a task can still be running is 17:00 UTC (a 01:00 UTC start after an hour of start retries, plus 16 h). The RDS backup window (18:00–18:30 UTC daily) and maintenance window (Wed 19:00–20:00 UTC) come after that, in summer and winter time alike.

## Notes

- **Image requirements:** `martom` on `PATH`, `sh`, `timeout` (coreutils or busybox), and `migrations/` and `knowledge/` in the working directory.
- **SSE through CloudFront:** CloudFront drops an origin connection after 60 s without bytes. The `/api/chat` stream must send something (a token or a `: keep-alive` comment) at least every 60 s.
- **pgvector:** RDS for PostgreSQL 16.5 and later ship pgvector 0.8.0; the engine version is `16`, so RDS picks its current default minor. Migration 007 checks the version itself.
- **No passwords in Terraform:** the database password lives only in the RDS-managed secret, the API keys only in the `app` secret you set by hand. Plan, state and tfvars hold just the two ARNs. To check after an apply: `terraform -chdir=infra state pull | grep -i -E 'password|secret_string'` shows only `manage_master_user_password` and ARNs. The state bucket is still private, encrypted and versioned, because state maps the whole account.

## Database password

RDS generates the master password and keeps it in a Secrets Manager secret it owns (`terraform -chdir=infra output -raw db_password_secret_arn`). ECS reads it into `PGPASSWORD` when a task starts; `DATABASE_URL` carries no password.

RDS-managed secrets always have rotation on (it can't be turned off), and the default is every 7 days. A rotation changes the password at once, but a running task keeps the `PGPASSWORD` it started with, and the API opens a new connection per request, so the API fails until its task restarts. Scheduled jobs are fine: each one starts fresh. So `secrets.tf` sets the schedule to every 365 days, and the recommended way is to rotate by hand at a quiet time and redeploy straight away:

```sh
aws rds modify-db-instance --db-instance-identifier martom-production \
  --rotate-master-user-password --apply-immediately
# wait until the secret has a new version (a minute or two), then restart the API tasks:
aws ecs update-service --cluster martom-production --service martom-production-api --force-new-deployment
```

Check when the next scheduled rotation is due with `aws secretsmanager describe-secret --secret-id "$(terraform -chdir=infra output -raw db_password_secret_arn)" --query NextRotationDate`, and rotate by hand before that date. Don't run this while a scheduled job is running (01:00 local until its end, see "Schedules").
