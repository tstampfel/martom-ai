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
        └── job task: `martom purge-chats` (chats idle > 90 days), at 00:15 Europe/Sarajevo, every night (#129)
        └── chat export task (by hand only): `martom export-chats` → private S3 bucket, objects deleted within 90 days (#158)

visit statistics (#163): customer page ──HTTPS──▶ second CloudFront distribution (Umami's own *.cloudfront.net)
        ──▶ same VPC origin and ALB; a rule on a secret header ──▶ Umami service (FARGATE_SPOT) ──▶ database `umami` on the same RDS
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
| `scheduler.tf` | The nightly sync and chat clean-up schedules, their dead-letter queue |
| `umami.tf` | Umami visit statistics: its CloudFront distribution, ALB header rule, FARGATE_SPOT service, database set-up task, secret (see "Umami" below, and `docs/analytics.md`) |
| `chat_export.tf` | The private bucket for scrubbed chat exports (EU, no public access, SSE-S3, 89-day expiry so nothing outlives 90 days), the export task and its least-privilege role |

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

Before the first plan, copy `terraform.tfvars.example` to `terraform.tfvars` and set `chat_export_admin_arns` to your own IAM user (and SSO role) ARNs; it has no default (see "Exporting chats"). Leave `schedules_enabled` at `false` until step 6.

`api_desired_count=0` for the first apply, because ECR is still empty: the API service starts no task until an image exists (step 4). The schedules are created **disabled** (`schedules_enabled` defaults to `false`), so no job runs against an empty database; step 6 turns them on.

**Treat plan files and state as private.** No password is in them: RDS generates the database password and keeps it in its own Secrets Manager secret, the app secrets are set by hand outside Terraform, and Terraform stores only the two secrets' ARNs. But a saved plan holds the full state of the account in plain text (names, ARNs, endpoints, the network layout). So write it outside the repo, as above, and delete it after the apply. `tfplan`, `*.tfplan`, `*.tfstate*`, `*.tfvars` and `.terraform/` are git-ignored, and CI fails if a plan or state file is ever tracked.

### 3. Set the app secrets

Write the JSON to a file **outside the repo**, upload it, delete the file:

```json
{
  "ANTHROPIC_API_KEY": "sk-ant-...",
  "COHERE_API_KEY": "...",
  "LANGSMITH_API_KEY": "lsv2_... (a key from the EU organization, see \"Chat retention and LangSmith traces\")",
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
# In infra/terraform.tfvars (made in step 2), change schedules_enabled to true.
# Don't copy the example over it again: that would put back the fake chat_export_admin_arns.
terraform -chdir=infra apply              # api_desired_count back to its default, 1; schedules ENABLED
curl -s "$(terraform -chdir=infra output -raw api_url)/health"
```

`/health` answers `{"status": "ok", "checks": {"postgres": "ok", "redis": "ok"}}` (with Redis down: 200, `"status": "degraded"`, `"redis": "error"`; with Postgres down: 503). Put `api_url` into the frontend's API setting (Cloudflare Pages, M5/M6-2). Terraform reads `terraform.tfvars` on every later plan and apply, so the schedules stay on and the export bucket keeps your real admin ARNs.

Then do the LangSmith steps (EU organization and key, EU endpoint check, 14-day trace retention): see "Chat retention and LangSmith traces" below; they are launch steps (#33).

The morning after the first night, check that it ran: a `purge-chats: deleted N threads` line in the job log group around 00:15 local time, a `sync_run` row started around 01:00 local time, and no messages in the dead-letter queue (`aws sqs get-queue-attributes --queue-url "$(terraform -chdir=infra output -raw scheduler_dlq_url)" --attribute-names ApproximateNumberOfMessages`). A message there means EventBridge Scheduler couldn't start the task (for example permissions or Fargate capacity); the message says why.

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
| `SEARCH_CACHE_ENABLED` | `true` (M3-5 search cache in the Redis sidecar) | not set | plain |
| `SEARCH_CACHE_TTL_SECONDS` | `3600` (cached search results; a catalogue change also expires them) | not set | plain |
| `QUERY_CACHE_TTL_SECONDS` | `86400` (cached parsed filters and query vectors) | not set | plain |
| `OLX_API_BASE_URL` | not set | `https://api.olx.ba` | plain |
| `OLX_SHOP_USERNAME` | not set | `martom` | plain |
| `OLX_REQUEST_INTERVAL` | not set | `2.8` (`var.sync_request_interval`, at least 1.3), for the nightly sync and one-off syncs | plain |
| `OLX_NIGHTLY_REQUEST_BUDGET` | not set | `950` OLX requests of every kind a night (only the rolling slice gives way) | plain |
| `OLX_ROLLING_REFRESH_DAYS` | not set | `7` nights | plain |
| `ANTHROPIC_API_KEY` | `martom/production/app` → `ANTHROPIC_API_KEY` | same | secret (by hand) |
| `FAST_MODEL` | `claude-haiku-5-5` | `claude-haiku-5-5` | plain |
| `AGENT_MODEL` | `claude-sonnet-5-5` (read from M4) | not set | plain |
| `EXTRACTION_CONCURRENCY` | not set | `4` | plain |
| `CHAT_RETENTION_DAYS` | not set | `90` (`martom purge-chats`; the privacy notice promises 90 days) | plain |
| `CHAT_EXPORT_BUCKET` | not set | not set; only the chat export task: the export bucket's name | plain |
| `EMBEDDING_PROVIDER` | `cohere` | `cohere` | plain |
| `EMBEDDING_MODEL` | `embed-multilingual-v3.0` | `embed-multilingual-v3.0` | plain |
| `COHERE_API_KEY` | `app` → `COHERE_API_KEY` | same | secret (by hand) |
| `ADMIN_TOKEN` | `app` → `ADMIN_TOKEN` | not set | secret (by hand) |
| `LANGSMITH_TRACING` | `true` | `true` | plain |
| `LANGSMITH_PROJECT` | `martom-ai-production` | same | plain |
| `LANGSMITH_ENDPOINT` | `https://eu.api.smith.langchain.com` (EU region, #129) | same | plain |
| `LANGSMITH_API_KEY` | `app` → `LANGSMITH_API_KEY` (an EU organization key) | same | secret (by hand) |

Not set in AWS: `TEST_DATABASE_URL`, `TEST_REDIS_URL`, `POSTGRES_PORT`, `REDIS_PORT` (local and CI only), and `ORDER_PHONE`, `TURNSTILE_SECRET_KEY`, `RATE_LIMIT_PER_MINUTE`, `MAX_TOKENS_PER_CONVERSATION` until M5-6/M5-7 read them. Production traces go to LangSmith's EU region (`LANGSMITH_ENDPOINT` in `common_environment`, `ecs.tf`); see "Chat retention and LangSmith traces".

## Monthly cost estimate

On-demand eu-central-1 list prices, checked 27 Nov 2026 from memory of the AWS price pages: confirm them in the AWS Pricing Calculator before you apply. Launch traffic: a few hundred conversations a month, the job running at most ~1.5 h a day on average (on the high side: most nights take minutes, the Sunday-night task ~2.5 h).

| Item | Size | Per month |
| --- | --- | --- |
| Application Load Balancer | $0.027/h + ~0.2 LCU | ~$21 |
| Fargate, API task | 0.25 vCPU, 1 GB, 24/7 | ~$12 |
| Fargate, job task | 0.5 vCPU, 1 GB, ~50 h | ~$1.50 |
| RDS Postgres | db.t4g.micro single-AZ + 20 GB gp3 (backups free up to the DB size) | ~$16 |
| Public IPv4 addresses | $0.005/h each: 1 for the API task, 1 for the job task while it runs (the internal ALB has none) | ~$4 |
| Secrets Manager | 3 secrets (RDS password, `app`, `umami`) | ~$1.20 |
| Umami (visit statistics, #163) | Fargate **Spot** 0.25 vCPU, 1 GB, 24/7 (~$12 on-demand; Spot about 70% less) ~$3.70, plus its task's public IPv4 ~$3.65. Its CloudFront distribution is in the free tier, its database on the RDS instance above | ~$7.50 |
| CloudWatch Logs | ~1 GB ingested | ~$1 |
| ECR | ≤ 10 images | ~$0.50 |
| CloudFront | always-free tier: 1 TB and 10 M requests | $0 |
| S3, chat export bucket | a few MB of JSON, deleted within 90 days | ~$0 |
| **Total** | | **~$64** |

Umami's line is approximate: Spot prices move. The issue's "about $3–4 a month" is the Fargate Spot compute alone; the public IPv4 address that every task needs without a NAT gateway roughly doubles it. The budget alerts (step 0) stay as they are; Tomislav decides any change.

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

Two scheduled tasks a night, Europe/Sarajevo time (01:00 local is 00:00 UTC in winter, 23:00 UTC in summer):

| Night | Schedule | Task runs |
| --- | --- | --- |
| every night at 00:15 (`purge_chats_cron`) | `purge-chats` | `martom purge-chats` (the job task with its command overridden) |
| every night at 01:00 (`nightly_sync_cron`) | `nightly-sync` | `sync --scheduled` → `extract` → `embed` |

`purge-chats` deletes the customer chats (checkpointer threads) whose last message is older than `CHAT_RETENTION_DAYS` (90) and prints only the count. It takes seconds. Its start retries end after 30 min and it is killed after 15 min (`timeout 900`), so it is gone before the sync chain starts at 01:00, and the previous night's chain is killed by 17:00 UTC: the two never run at the same time. A failed purge exits 1 (the task shows as failed); the next night deletes what it missed, so nothing is kept much longer than 90 days.

There is no weekly full run any more (#135). #105's full run made 2,719 OLX requests in 2 h 37 min, found no changed listing and still got 429s, so the nightly sync now does a **rolling refresh** instead: besides the listings whose list-page fields changed, it re-fetches the details of about 1/7 of the active listings (the ones fetched longest ago), so every listing is refreshed within 7 nights. OLX allows 1,000 requests per fixed hourly window (#105), so a nightly run sends at most 950 OLX requests of every kind (`OLX_NIGHTLY_REQUEST_BUDGET`): discovery and list pages, the shop profile, allowlist and re-check GETs, new, changed and last run's failed details all go through first, even past the budget (a WARNING, `request_budget_exceeded`), and the rolling slice gets what is left (`sync_run.rolling_deferred` counts what waited). A manual `--full` run has no budget. The slice runs last in the sync, after the allowlist guard and the deactivation re-check, so an OLX stop inside it can't skip those. An active listing that has left the listing pages is never in the slice; its deactivation re-check saves its detail when OLX says it is still active. `sync_run.oldest_detail_age_days` records the oldest detail among the active listings after each run, and `detail_refresh_overdue` is set when it is over 8 days, with a WARNING in the log that names the overdue listing ids (`overdue_listing_ids`): that is what the M6-3 alarm watches. `martom sync --full` still works as a one-off task (`run_once martom sync --full`, which runs at the task's 2.8 s too).

`martom sync` has no lock of its own. Two tasks can't overlap: each is killed after 16 h (`timeout`, `job_timeout_seconds` in `ecs.tf`) and the next one starts 24 h after it. Don't start a one-off `martom sync --full` while the nightly task is running.

Worst-case timeline for one night's task:

| Step | Normal | Worst case | What bounds it |
| --- | --- | --- | --- |
| `sync --scheduled` | ~20–25 min: ~372 slice GETs + the changed listings + ~50 list and discovery requests at 2.8 s; a night that uses the whole 950-request budget ≈ 44 min | 3 h | the sync's own 3 h deadline |
| `extract` | minutes (changed listings only) | ~2 h | every listing changed: ~2,600 Haiku calls, 4 at a time, with retries |
| `embed` | minutes | ~3 h | ~28 Cohere batches × 6 attempts × 60 s |
| **Total** | **~30 min** | **~8 h**; 16 h is the hard stop | `timeout` kills the task |

The job task runs every sync at `sync_request_interval` (2.8 s by default, never below 1.3): OLX allows 1,000 requests per fixed hourly window (#105), and a nightly run sends at most 950 (a typical night about 420). A `--full` run (~2,700 requests) has no budget, so expect 429 waits in it. A sync started by hand outside the task uses the Settings default of 2.0 s unless `OLX_REQUEST_INTERVAL` says otherwise.

So the latest a task can still be running is 17:00 UTC (a 01:00 UTC start after an hour of start retries, plus 16 h). The RDS backup window (18:00–18:30 UTC daily) and maintenance window (Wed 19:00–20:00 UTC) come after that, in summer and winter time alike.

## Chat retention and LangSmith traces

Tomislav's decision (#129, 27 Nov 2026), and what the start screen's privacy notice tells customers: **chats are kept 90 days, then deleted.** Chat text lives in two places: Postgres keeps it 90 days, LangSmith's trace copy only 14 days:

1. **Postgres (the checkpointer tables):** `martom purge-chats` deletes them every night (see "Schedules"). Orders (`order_request`, kept 30 days and then deleted automatically under the plan's customer-data rule, built with #27) are separate and never touched by it.
2. **LangSmith:** every conversation is traced (Tomislav, 28 Nov 2026: tracing stays on in production), and LangSmith keeps its own copy. Tomislav's decision after a privacy review (28 Nov 2026): this copy is **in the EU and kept 14 days**, LangSmith's shortest trace retention (the Privatnost page, #145, says so; chat retention in Postgres stays 90 days). PII masking in traces is a separate issue, #150. Both are LangSmith settings, not Terraform, and agents have no LangSmith keys, so **Tomislav does these steps by hand before launch** (they are on the launch checklist, #33). The facts below are from LangSmith's docs (Cloud regions: <https://docs.langchain.com/langsmith/cloud#regions>; retention: <https://docs.langchain.com/langsmith/usage-and-billing#data-retention>), not checked in the account yet.

   **a. An EU organization.** LangSmith Cloud stores an organization's data in the region where it was signed up, and an organization can't be moved to another region. **First check the region of the organization you already have:** the site you sign in to tells you (`eu.smith.langchain.com` = EU; `smith.langchain.com`, `aws.`, `azure.` or `apac.` = not EU), and so does the endpoint its key works with (a key that works only against `https://eu.api.smith.langchain.com` is an EU key). If it is US (or any other non-EU region), don't use it for production: create a new organization at **<https://eu.smith.langchain.com>** (an organization can't change region, and its existing traces stay where they are). Then, in the EU organization, create the production workspace there, and create the production API key in it (**Settings → API Keys**). A key from a US organization (`smith.langchain.com`) doesn't work in the EU region. Put that key in the `app` secret as `LANGSMITH_API_KEY` (step 3).
   - Check: you reach the production workspace at `eu.smith.langchain.com` (on `smith.langchain.com` it isn't there), and the key you put in the secret was created on the EU site.

   **b. The EU endpoint in both tasks.** `ecs.tf` sets `LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com` (a plain value in `common_environment`, so the API task, which traces the chats, and the job task, which traces extraction, both send to the EU). Nothing to do but apply.
   - Check: `aws ecs describe-task-definition --task-definition martom-production-api --query "taskDefinition.containerDefinitions[?name=='api'].environment[?name=='LANGSMITH_ENDPOINT']"` (and `martom-production-job`) shows the EU URL. Then send one chat on the site: its trace appears in the `martom-ai-production` project on `eu.smith.langchain.com`, and the API log group has no LangSmith authentication errors (a 401/403 from LangSmith means the key belongs to another region).
   - Any script you run by hand against the production workspace (uploading an eval dataset, for example) needs the same `LANGSMITH_ENDPOINT` and the EU key in its environment.

   **c. Trace retention: 14 days.** On LangSmith Cloud the shortest trace retention is the **Base** tier, which the docs give as exactly **14 days** ("On Cloud, base retention is 14 days and extended retention is 180 days", <https://docs.langchain.com/langsmith/security-overview#data-retention-and-deletion>); there is no shorter setting, and only the Extended period (30–180 days, Enterprise) can be changed. If the account's retention control shows Base as anything other than 14 days, stop and tell Tomislav before launch: the Privatnost page promises 14 days, so never pick a longer option.
   - In LangSmith, open the production tracing project (`LANGSMITH_PROJECT`, `martom-ai-production`; in the newer UI: the agent → **Tracing** → its environment) → **Retention** in the top bar → **Base**. On LangSmith Cloud, Base keeps traces **14 days**; **Extended** keeps them 180 days, which would break the 14-day promise.
   - **Settings → Usage configuration** → the workspace's **Data retention policy** / default trace tier: **Base** too, so a project created later (a renamed `LANGSMITH_PROJECT`) also starts at 14 days.
   - Automation rules on the production project must keep every **Extend Data Retention** toggle off (note: the "Add to Annotation Queue" action has it **on** by default), and online evaluators their retention option off: LangSmith upgrades every matching trace (with a thread rule, every trace in the thread) to 180 days. Never add the "Extend Data Retention" action itself. Feedback (thumbs) and notes added in the UI don't change a trace's retention.
   - **Datasets are kept until deleted**, whatever the trace retention. Never "Add to dataset" from a production trace by hand: anything kept longer than the traces' 14 days (a dataset, a feedback example, a private export) goes through `martom.evals.scrub.scrub_personal_data` first. A scrubbed real chat is never committed to the public repo, not even to `evals/` (Tomislav, 28 Nov 2026): it goes only to a private location (an S3 bucket in our AWS account or a private repo), and `evals/` holds only hand-written or hand-reviewed cases approved by Tomislav or Toni, with no real customer wording, street or village.
   - Check: the project's **Retention** shows **Base**, Settings → Rules lists no rule on the project with an extend-retention toggle on, and the retention control says 14 days, and a trace from the first day is gone after 14 days (open its link on day 15: "not found"). Changing the setting applies only to traces ingested afterwards, so set it **before** the first real customer conversation.

## Umami (visit statistics)

Cookieless visit statistics, self-hosted (#156, #163). What it collects, how the admin UI is protected and how to exclude your own visits: `docs/analytics.md`. Umami runs the **official image** (`var.umami_image`, pinned) at the root of its **own CloudFront distribution** (`umami_url`), because a sub-path would need a custom image build (Tomislav, 28 Nov 2026). It stores the **country only** (no region or city): CloudFront passes Umami an allowlist of headers with the country in `CloudFront-Viewer-Country`, and Umami's bundled GeoLite2-City lookup is switched off (`docs/analytics.md`, "Country only"). No MaxMind account, download or extra secret is needed. It reuses the ALB (a listener rule that requires a secret `X-Martom-Origin` header) and the RDS instance (its own database and role, at most 5 connections; see "Connections" below).

The ALB rule's header value is `var.umami_origin_secret`: no default, never committed, and **every `plan` and `apply` needs it**. Keep it in the git-ignored `infra/terraform.tfvars` (`umami_origin_secret = "..."`, see `terraform.tfvars.example`), which Terraform reads on every run, with a copy in the Umami secret (`ORIGIN_HEADER_SECRET`) so it can be recovered.

### First deploy

1. **Generate the header secret and apply with Umami stopped.** `openssl rand -hex 32`, put it in `infra/terraform.tfvars` as `umami_origin_secret`, then `terraform -chdir=infra apply -var umami_desired_count=0` (with the other first-deploy flags if this is the stack's first apply).
2. **Set the Umami secret.** Generate a database password and an app secret (`openssl rand -hex 32` each; hex, so the URL needs no escaping) and a strong admin password (`openssl rand -base64 24`). Write the JSON to a file **outside the repo**, upload it, delete the file:

   ```json
   {
     "DATABASE_URL": "postgresql://umami:<db password>@<db_endpoint output>/umami?sslmode=require&uselibpqcompat=true",
     "UMAMI_DB_PASSWORD": "<db password>",
     "APP_SECRET": "<app secret>",
     "ADMIN_PASSWORD": "<admin password>",
     "ORIGIN_HEADER_SECRET": "<the header secret from step 1>"
   }
   ```

   ```sh
   aws secretsmanager put-secret-value --secret-id "$(terraform -chdir=infra output -raw umami_secret_name)" \
     --secret-string file://$HOME/martom-umami-secret.json
   rm ~/martom-umami-secret.json
   ```

   `sslmode=require&uselibpqcompat=true` encrypts the connection without checking the RDS certificate, like the API's libpq `sslmode=require`. Umami 3.5 reads the URL with two drivers: node-postgres for its queries, which on its own treats `require` as "verify against Node's CA list" (that list lacks the RDS CA, so it would fail) and with `uselibpqcompat=true` follows libpq's meaning; and Prisma's migrate engine (`prisma migrate deploy` at start), which understands `require` but not `no-verify`. To be confirmed at the first start: the log shows `✓ Database connection successful` and the migrations applied. No container reads `ADMIN_PASSWORD` (Umami has no setting for it); it is kept for step 4.
3. **Create the database and role** with the one-off set-up task, `martom umami-db-setup` from the app image, so the app image must be in ECR (first deploy, step 4). It re-runs safely; run it again after changing `UMAMI_DB_PASSWORD`, then restart Umami. It sends Postgres only a SCRAM hash of the password (the plain password is never in a statement, so it can't reach the RDS log export) and prints only what it did:

   ```sh
   aws ecs run-task --cluster "$(terraform -chdir=infra output -raw ecs_cluster)" \
     --task-definition "$(terraform -chdir=infra output -raw umami_db_setup_task_definition)" \
     --launch-type FARGATE \
     --network-configuration "$(terraform -chdir=infra output -raw job_network_configuration)"
   ```

   Its log (`/ecs/martom-production/umami`, stream prefix `db-setup`) ends with `umami-db-setup: role umami created, database umami created; ready` (`updated` / `already there` on a re-run). The role can't log in to the app database `martom`; it can still connect to the RDS maintenance database `postgres`, where it has no privileges beyond the defaults (the master doesn't own that database, so it can't revoke them).
4. **Start Umami and change the default login at once (launch checklist, #33).** `terraform -chdir=infra apply` (`umami_desired_count` back to 1). On its first start Umami creates the admin login **admin / umami**, and the admin UI is on the public `umami_url`. So straight away, before the tracking script goes live: open `umami_url`, log in as admin / umami, change the password in the profile settings to `ADMIN_PASSWORD` from the secret, log out, and check that admin / umami no longer works.
5. **One admin account only.** Umami has no public sign-up; users exist only when an admin creates them. Never create a second user or a team; check the Users settings list only `admin`.
6. **Add the website** in the admin UI: name `Martom savjetnik`, domain `martom-savjetnik.pages.dev`. Copy its website id.
7. **Turn the script on in Cloudflare Pages, production environment only:** `VITE_UMAMI_ENABLED=true`, `VITE_UMAMI_HOST=<umami_url>`, `VITE_UMAMI_WEBSITE_ID=<website id>`, then redeploy. Leave all three unset in the preview environment. Open the site: the visit appears in Umami's realtime view. Then exclude your own browsers (`docs/analytics.md`).

The window between Umami's first start and the password change is a few minutes on a random, unpublished hostname (the review's R1-5). Tomislav asked for this runbook step and the #33 checklist item (28 Nov 2026); closing it entirely (the listener rule off until the change, or the change through an ECS Exec tunnel) is possible later.

### Connections

Umami 3.5 opens its queries through Prisma's node-postgres adapter, which gets only the connection string, and node-postgres's pool defaults to **10** connections; no URL parameter or Umami setting changes that (checked in Umami 3.5's `src/lib/prisma.ts`). The **role's `CONNECTION LIMIT 5`** is the real cap (`UMAMI_CONNECTIONS` in `src/martom/db.py`, counted in the connection budget): Postgres refuses a sixth connection, so a burst of more than 5 queries at once loses those visits (an error in the Umami log) and never takes a connection from the API. At launch traffic Umami uses one or two. If the log shows `too many connections for role "umami"`, raise the limit together with `UMAMI_CONNECTIONS` (the budget test must still pass), then re-run step 3.

### Checks

- `curl -s "$(terraform -chdir=infra output -raw umami_url)/api/heartbeat"` answers `{"ok":true}`.
- Umami is not reachable through the API's address: `curl -s "$(terraform -chdir=infra output -raw api_url)/api/heartbeat"` answers the API's 404, not `{"ok":true}`.
- Country only, once after the first apply (checks what can't be tested locally; `docs/analytics.md`, "Country only"): in the Umami admin UI, add a throwaway website, then send one visit with fake location headers: `curl -s -X POST "$(terraform -chdir=infra output -raw umami_url)/api/send" -H 'Content-Type: application/json' -H 'User-Agent: Mozilla/5.0 (Macintosh) Chrome/131.0' -H 'CloudFront-Viewer-Country: XX' -H 'cf-ipcountry: YY' -H 'cf-ipcity: Fakeville' -H 'cloudfront-viewer-city: Fakeville' -d '{"type":"event","payload":{"website":"<throwaway website id>","hostname":"martom-savjetnik.pages.dev","url":"/"}}'`. Its session must show your real country (not XX or YY) and no region or city. Then delete the throwaway website.
- Spot interruptions: ECS replaces the task by itself (`aws ecs describe-services --cluster martom-production --services martom-production-umami --query 'services[0].events[:5]'`). Visits in those minutes aren't counted; nothing else is affected.

## Exporting chats

Real customer chats are never committed to the public repo, even scrubbed (Tomislav, 28 Nov 2026). To study chosen chats, export them, scrubbed, to the private bucket in `chat_export.tf` (#158):

- the bucket is in `eu-central-1` like the rest of the stack, so exports never leave the EU;
- all public access is blocked, objects are encrypted (SSE-S3), and the bucket policy refuses any request that isn't HTTPS;
- every object is deleted **within 90 days** of being written. The Privatnost page promises "Očišćeni primjeri razgovora: najduže 90 dana"; S3 rounds an expiry up to the next midnight UTC and deletes asynchronously, so the rule says **89** days. It is a literal in `chat_export.tf`, and `tests/test_infra_chat_export.py` fails if it is ever above 89. Versioning is off; if someone turns it on, old versions are deleted after 1 day;
- only the principals in `chat_export_admin_arns` (terraform.tfvars) and the export task role may use the bucket; the bucket policy denies everyone else, whatever their IAM policies say. List your IAM user ARN and, if you sign in with SSO, the permission set's role ARN (`aws iam list-roles --path-prefix /aws-reserved/sso.amazonaws.com/`), because `aws:PrincipalArn` is the role, not the session. **The principal that runs `terraform apply` must be in the list**, or Terraform itself is refused on the bucket. If you lock yourself out, sign in as the account root user and delete the bucket policy (S3 always allows the root user to do that), then fix the list and apply again;
- the export task role may only `PutObject` under `chats/` and read and delete export requests under `requests/`: it can't list, read or delete earlier exports.

The database is reachable only inside the VPC, so the export runs as a one-off task. **Never pass thread ids, names or addresses on the task's command line:** a command override is stored in CloudTrail's RunTask event (and shown by `DescribeTasks`), outside the scrub and outside the 90-day promise, and a thread id lets its holder continue that chat. Put them in a request file instead, upload it under `requests/`, and give the task only the object's key; the task reads it, deletes it at once (even if it is malformed), and a lifecycle rule removes any request it never read after a day:

```sh
# Write the request outside every git checkout. Find thread ids in the chat's LangSmith trace metadata.
cat > "$HOME/export-request.json" <<'JSON'
{"thread_ids": ["<thread id 1>", "<thread id 2>"], "names": ["<order form name>"], "addresses": ["<order form address>"]}
JSON
bucket="$(terraform -chdir=infra output -raw chat_export_bucket)"
key="requests/$(date -u +%Y%m%dT%H%M%S).json"
aws s3 cp "$HOME/export-request.json" "s3://$bucket/$key" && rm "$HOME/export-request.json"

aws ecs run-task \
  --cluster "$(terraform -chdir=infra output -raw ecs_cluster)" \
  --task-definition "$(terraform -chdir=infra output -raw chat_export_task_definition)" \
  --launch-type FARGATE \
  --network-configuration "$(terraform -chdir=infra output -raw job_network_configuration)" \
  --overrides "$(jq -nc --arg key "$key" '{containerOverrides: [{name: "chat-export", command: ["martom", "export-chats", "--request", $key]}]}')"
```

`names` and `addresses` are optional (the order form's values, when the chat has one). The task prints only counts (`exported 2 chats to s3://... (0 not found)`), never ids, names or chat text; its log is in `/ecs/martom-production/job` under the `chat-export` prefix. Each chat lands in `s3://<chat_export_bucket>/chats/<date>/<thread hash>.json` with the customer's and the advisor's messages only.

Then download and **read** an export before you use it: the scrub has known misses (listed in `src/martom/evals/scrub.py`):

```sh
aws s3 cp --recursive "s3://$(terraform -chdir=infra output -raw chat_export_bucket)/chats/" "$HOME/martom-chat-exports/"
```

Keep the copy outside every git checkout (the command refuses `--out` inside one for the same reason) and delete it when you're done. A case worth keeping in the eval set is rewritten by hand and approved before it goes into `evals/` (`evals/README.md`). Against a local database, `uv run martom export-chats --out ~/martom-chat-exports --name "<name>" -- THREAD_ID...` writes to a folder instead. Put the ids after `--`: a thread id can start with "-" (about 1 in 64), which would otherwise read as an option.

## Notes

- **Logs: 30 days at most.** Every CloudWatch log group, and any CloudFront or ALB access log should one ever be turned on (none is now), must keep its data **30 days or less**: the Privatnost page promises that technical logs and IP addresses are deleted after 30 days. `log_retention_days` refuses anything longer.

- **Image requirements:** `martom` on `PATH`, `sh`, `timeout` (coreutils or busybox), and `migrations/` and `knowledge/` in the working directory.
- **SSE through CloudFront:** CloudFront drops an origin connection after 60 s without bytes. The `/api/chat` stream must send something (a token or a `: keep-alive` comment) at least every 60 s.
- **pgvector:** RDS for PostgreSQL 16.5 and later ship pgvector 0.8.0; the engine version is `16`, so RDS picks its current default minor. Migration 007 checks the version itself.
- **No passwords in Terraform:** the database password lives only in the RDS-managed secret, the API keys only in the `app` secret you set by hand. Plan, state and tfvars hold just the two ARNs. To check after an apply: `terraform -chdir=infra state pull | grep -i -E 'password|secret_string'` shows only `manage_master_user_password` and ARNs. The state bucket is still private, encrypted and versioned, because state maps the whole account.

## When Redis dies

Redis is a sidecar in the API task and holds only the search cache (M3-5). It is **not essential** (`ecs.tf`), so when it exits the task and the API keep running:

- Searches run uncached: each one pays for its Haiku parse, embedding and rerank again, so they are slower and cost a little more, but nothing fails. The API logs one WARNING `search cache unavailable; searching uncached` per outage (and tries Redis again every 30 s), then INFO `search cache available again` when it is back.
- `GET /health` still answers 200, with `"status": "degraded"` and `"redis": "error"`, so the load balancer keeps sending traffic. Only Postgres down answers 503.
- ECS restarts the Redis container by itself (restart policy, `restartAttemptPeriod = 60`), at most once a minute, and only if it had run for at least 60 s before it exited. A Redis that dies within a minute of starting stays down until the task is replaced. The restarted Redis starts empty.

How to check:

```sh
curl -s "$(terraform -chdir=infra output -raw api_url)/health"   # "degraded" = Redis down
aws ecs describe-tasks --cluster martom-production \
  --tasks "$(aws ecs list-tasks --cluster martom-production --service-name martom-production-api --query 'taskArns[0]' --output text)" \
  --query 'tasks[0].containers[].{name:name,status:lastStatus,exit:exitCode}'
aws logs tail /ecs/martom-production/api --log-stream-name-prefix redis --since 1h   # Redis's own log
```

If Redis stays down (still "degraded" a few minutes later), start a fresh task: `aws ecs update-service --cluster martom-production --service martom-production-api --force-new-deployment`. The new task starts with an empty cache; nothing is lost but speed.

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
