variable "region" {
  description = "AWS region. eu-central-1 (Frankfurt): customer data stays in the EU."
  type        = string
  default     = "eu-central-1"
}

variable "environment" {
  description = "Environment name, used in resource names and tags."
  type        = string
  default     = "production"
}

variable "name" {
  description = "Prefix for resource names."
  type        = string
  default     = "martom"
}

variable "vpc_cidr" {
  description = "Address range of the VPC."
  type        = string
  default     = "10.40.0.0/16"
}

# --- API service ---

variable "image_tag" {
  description = "Tag of the app image in ECR, used by the API and the scheduled jobs. CD (M6-2) sets it per deploy."
  type        = string
  default     = "latest"
}

variable "api_desired_count" {
  description = "Number of API tasks. Use 0 for the first apply, before an image has been pushed to ECR."
  type        = number
  default     = 1
}

# Fargate sizes: CPU units (1024 = 1 vCPU) and MiB. The API task holds two containers (API +
# Redis sidecar) that share these numbers.
variable "api_cpu" {
  description = "API task CPU units (256 = 0.25 vCPU). The API mostly waits on Claude, Cohere and Postgres."
  type        = number
  default     = 256
}

variable "api_memory" {
  description = "API task memory in MiB, shared by the API and the Redis sidecar."
  type        = number
  default     = 1024
}

variable "redis_maxmemory" {
  description = "Redis sidecar memory cap (redis-server --maxmemory); least-recently-used keys are evicted above it."
  type        = string
  default     = "128mb"
}

variable "cors_allowed_origins" {
  description = "CORS_ALLOWED_ORIGINS for the API. Settings only accepts the pages.dev site and localhost."
  type        = string
  default     = "http://localhost:5173,https://martom-savjetnik.pages.dev"
}

variable "langsmith_project" {
  description = "LangSmith project the production traces go to."
  type        = string
  default     = "martom-ai-production"
}

# --- Scheduled jobs (sync -> extract -> embed) ---

variable "job_cpu" {
  description = "Job task CPU units. extract sends 4 Claude calls at a time, so it is network-bound."
  type        = number
  default     = 512
}

variable "job_memory" {
  description = "Job task memory in MiB."
  type        = number
  default     = 1024
}

variable "schedule_timezone" {
  description = "Time zone of the cron expressions below (the shop's time zone)."
  type        = string
  default     = "Europe/Sarajevo"
}

variable "purge_chats_cron" {
  description = "Nightly chat clean-up (`martom purge-chats`, #129). Default: 00:15, so it is done before the 01:00 sync chain starts (see scheduler.tf)."
  type        = string
  default     = "cron(15 0 * * ? *)"
}

variable "nightly_sync_cron" {
  description = "Nightly sync, every night (EventBridge Scheduler cron: minutes hours day-of-month month day-of-week year). Default: 01:00."
  type        = string
  default     = "cron(0 1 * * ? *)"
}

# OLX allows 1,000 requests per fixed hourly window (#105); past that it answers 429 with
# a long Retry-After. The nightly sync sends at most OLX_NIGHTLY_REQUEST_BUDGET (950)
# requests of every kind (#135), at 2.8 s (Tomislav, #135): 950 x 2.8 s is ~44 min,
# well inside the 3 h deadline. Manual runs outside this task keep the Settings default
# of 2.0 s unless OLX_REQUEST_INTERVAL says otherwise.
variable "sync_request_interval" {
  description = "OLX_REQUEST_INTERVAL (seconds between OLX requests) for every sync in the job task: the nightly sync and one-off `martom sync --full` runs."
  type        = number
  default     = 2.8

  validation {
    condition     = var.sync_request_interval >= 1.3
    error_message = "The OLX politeness rule never allows an interval below 1.3 seconds."
  }
}

variable "schedules_enabled" {
  description = "Run the scheduled jobs. Off by default so nothing runs before an image is pushed and the database is migrated (README); also set to false while RDS is stopped."
  type        = bool
  default     = false
}

# --- Database ---

variable "db_instance_class" {
  description = "RDS instance class. db.t4g.micro (2 vCPU burstable, 1 GiB) is the smallest Graviton class."
  type        = string
  default     = "db.t4g.micro"
}

variable "db_engine_version" {
  description = "Postgres major version. RDS picks its default minor; pgvector 0.8 (needed by migration 007) ships with 16.5 and later."
  type        = string
  default     = "16"
}

variable "db_allocated_storage" {
  description = "Initial RDS storage in GiB (gp3; 20 is the minimum)."
  type        = number
  default     = 20
}

variable "db_max_allocated_storage" {
  description = "RDS storage autoscaling ceiling in GiB."
  type        = number
  default     = 50
}

variable "db_backup_retention_days" {
  description = "Days of automated RDS backups (free up to the size of the database)."
  type        = number
  default     = 7
}

variable "db_deletion_protection" {
  description = "Block deleting the database. Set to false (and apply) only when tearing everything down on purpose."
  type        = bool
  default     = true
}

variable "log_retention_days" {
  description = "Days CloudWatch keeps container logs."
  type        = number
  default     = 30

  # The Privatnost page promises that technical logs and IP addresses are kept 30 days
  # (Tomislav, 28 Nov 2026). Every log group uses this variable.
  validation {
    # 1, 3, 5, 7, 14 and 30 are the only CloudWatch retention values up to 30 days.
    condition     = contains([1, 3, 5, 7, 14, 30], var.log_retention_days)
    error_message = "Logs may be kept at most 30 days (1, 3, 5, 7, 14 or 30, the values CloudWatch accepts): the Privatnost page promises that technical logs and IP addresses are deleted after 30 days."
  }
}

# --- Umami (umami.tf, docs/analytics.md) ---

variable "umami_image" {
  description = "Official Umami image (Postgres build), pinned. Upgrade by changing the tag after reading Umami's release notes."
  type        = string
  default     = "ghcr.io/umami-software/umami:3.5.0"
}

variable "umami_desired_count" {
  description = "Number of Umami tasks. Use 0 until its database exists and its secret is set (README, \"Umami\")."
  type        = number
  default     = 1
}

variable "umami_cpu" {
  description = "Umami task CPU units (256 = 0.25 vCPU)."
  type        = number
  default     = 256
}

variable "umami_memory" {
  description = "Umami task memory in MiB."
  type        = number
  default     = 1024
}

# The ALB sends a request to Umami only when it carries this value in X-Martom-Origin
# (umami.tf explains why it must be secret). No default: never commit it. Keep it in the
# git-ignored infra/terraform.tfvars, with a copy in the Umami secret (README, "Umami"); it
# ends up in the (private) state, like every CloudFront setting.
variable "umami_origin_secret" {
  description = "Secret value of the X-Martom-Origin header between the Umami CloudFront distribution and the ALB (openssl rand -hex 32)."
  type        = string
  sensitive   = true

  validation {
    condition     = can(regex("^[A-Za-z0-9]{32,128}$", var.umami_origin_secret))
    error_message = "Use 32 to 128 letters and digits, e.g. openssl rand -hex 32 (an ALB header condition allows at most 128 characters)."
  }
}

# --- Private chat export (#158, chat_export.tf) ---

variable "chat_export_admin_arns" {
  description = <<-EOT
    Who besides the export task may use the chat export bucket: Tomislav's IAM user ARN
    (arn:aws:iam::<account>:user/<name>) and, when he signs in with SSO, the permission
    set's role ARN (arn:aws:iam::<account>:role/aws-reserved/sso.amazonaws.com/<region>/AWSReservedSSO_<set>_<id>;
    aws:PrincipalArn is the role, not the session). It must include whoever runs
    `terraform apply`, or Terraform itself is refused on the bucket. No default: set it
    in terraform.tfvars.
  EOT
  type        = list(string)

  validation {
    condition = length(var.chat_export_admin_arns) > 0 && alltrue([
      for arn in var.chat_export_admin_arns : can(regex("^arn:aws:iam::[0-9]{12}:(user|role)/.+$", arn))
    ])
    error_message = "List at least one IAM user or role ARN (arn:aws:iam::<12-digit account>:user/... or :role/...)."
  }
}
