# ECS on Fargate: one cluster, the API service (API + Redis sidecar), and a job task
# definition that EventBridge Scheduler starts (scheduler.tf) and that also runs one-off
# commands such as `martom migrate`.
#
# How each setting reaches the app (src/martom/settings.py reads them all from env vars):
#
# | Env var                  | API                               | Job                         |
# | ------------------------ | --------------------------------- | --------------------------- |
# | APP_ENV                  | production                        | production                  |
# | LOG_LEVEL                | INFO                              | INFO                        |
# | CORS_ALLOWED_ORIGINS     | var.cors_allowed_origins          | not set (API only)          |
# | DATABASE_URL             | postgresql://martom@<rds endpoint>/martom?sslmode=require (no password) | same |
# | PGPASSWORD               | secret: RDS-managed, key password | same                        |
# | REDIS_URL                | redis://localhost:6379/0 (sidecar)| not set (no Redis)          |
# | SEARCH_CACHE_ENABLED     | true                              | not set (API only)          |
# | SEARCH_CACHE_TTL_SECONDS | 3600                              | not set                     |
# | QUERY_CACHE_TTL_SECONDS  | 86400                             | not set                     |
# | OLX_API_BASE_URL         | not set (Settings default)        | https://api.olx.ba          |
# | OLX_SHOP_USERNAME        | not set                           | martom                      |
# | OLX_REQUEST_INTERVAL     | not set                           | var.sync_request_interval (2.8) |
# | OLX_NIGHTLY_REQUEST_BUDGET | not set                         | 950                         |
# | OLX_ROLLING_REFRESH_DAYS | not set                           | 7                           |
# | ANTHROPIC_API_KEY        | secret app:ANTHROPIC_API_KEY      | secret app:ANTHROPIC_API_KEY|
# | FAST_MODEL               | claude-haiku-5-5                  | claude-haiku-5-5            |
# | AGENT_MODEL              | claude-sonnet-5-5                 | not set                     |
# | EXTRACTION_CONCURRENCY   | not set                           | 4                           |
# | EMBEDDING_PROVIDER       | cohere                            | cohere                      |
# | EMBEDDING_MODEL          | embed-multilingual-v3.0           | embed-multilingual-v3.0     |
# | COHERE_API_KEY           | secret app:COHERE_API_KEY         | secret app:COHERE_API_KEY   |
# | ADMIN_TOKEN              | secret app:ADMIN_TOKEN            | not set (API only)          |
# | LANGSMITH_TRACING        | true                              | true                        |
# | LANGSMITH_PROJECT        | var.langsmith_project             | var.langsmith_project       |
# | LANGSMITH_API_KEY        | secret app:LANGSMITH_API_KEY      | secret app:LANGSMITH_API_KEY|
#
# AGENT_MODEL (Settings.agent_model, M4-3) is the advisor's Sonnet model; only the API reads it.

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  image      = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"

  # No password in the URL: libpq (psycopg) takes it from PGPASSWORD, which ECS injects
  # from the RDS-managed secret. The endpoint is host:port.
  database_url = format(
    "postgresql://%s@%s/%s?sslmode=require",
    aws_db_instance.main.username,
    aws_db_instance.main.endpoint,
    aws_db_instance.main.db_name,
  )

  common_environment = {
    DATABASE_URL       = local.database_url
    APP_ENV            = "production"
    LOG_LEVEL          = "INFO"
    FAST_MODEL         = "claude-haiku-5-5"
    EMBEDDING_PROVIDER = "cohere"
    EMBEDDING_MODEL    = "embed-multilingual-v3.0"
    LANGSMITH_TRACING  = "true"
    LANGSMITH_PROJECT  = var.langsmith_project
    # The production LangSmith organization is in the EU region (Tomislav, 28 Nov 2026: the
    # privacy page says LangSmith data stays in the EU). EU API URL from LangSmith's
    # "Regions" table: https://docs.langchain.com/langsmith/cloud#regions. An EU API key
    # only works against this endpoint. Both tasks trace (the API's chats, the job's
    # extraction), so both get it.
    LANGSMITH_ENDPOINT = "https://eu.api.smith.langchain.com"
  }

  api_environment = merge(local.common_environment, {
    CORS_ALLOWED_ORIGINS = var.cors_allowed_origins
    # The Redis sidecar shares the task's network namespace, so it is on localhost.
    REDIS_URL   = "redis://localhost:6379/0"
    AGENT_MODEL = "claude-sonnet-5-5"
    # Search cache (M3-5, search/cache.py). Same as the Settings defaults, set here so
    # production doesn't depend on them silently.
    SEARCH_CACHE_ENABLED     = "true"
    SEARCH_CACHE_TTL_SECONDS = "3600"
    QUERY_CACHE_TTL_SECONDS  = "86400"
  })

  job_environment = merge(local.common_environment, {
    OLX_API_BASE_URL  = "https://api.olx.ba"
    OLX_SHOP_USERNAME = "martom"
    # The nightly sync (at most 950 OLX requests, below) and any one-off
    # `martom sync --full` in this task run at this interval (#135).
    OLX_REQUEST_INTERVAL = tostring(var.sync_request_interval)
    # Rolling refresh of the nightly sync (#135, Tomislav): at most 950 OLX requests of
    # every kind a night (OLX allows 1,000 per fixed hourly window), every active listing
    # re-fetched within 7 nights. Same as the Settings defaults, set here so production
    # doesn't depend on them silently. `--full` has no budget.
    OLX_NIGHTLY_REQUEST_BUDGET = "950"
    OLX_ROLLING_REFRESH_DAYS   = "7"
    EXTRACTION_CONCURRENCY     = "4"
    # Chats are kept 90 days after their last message, then `martom purge-chats` deletes
    # them (Tomislav, #129). The site's privacy notice promises 90 days: never raise it
    # without changing that text.
    CHAT_RETENTION_DAYS = "90"
  })

  # ECS wants lists of {name, value} / {name, valueFrom}; build them from the maps above.
  db_password_secret = {
    name      = "PGPASSWORD"
    valueFrom = "${local.db_password_secret_arn}:password::"
  }
  api_secrets = concat([local.db_password_secret], [
    for key in local.api_secret_keys : { name = key, valueFrom = "${aws_secretsmanager_secret.app.arn}:${key}::" }
  ])
  job_secrets = concat([local.db_password_secret], [
    for key in local.job_secret_keys : { name = key, valueFrom = "${aws_secretsmanager_secret.app.arn}:${key}::" }
  ])

  # The scheduled job runs the whole pipeline: sync, then extract the listings whose
  # details changed, then embed the products whose text changed. There is no weekly full
  # run any more (#135): the nightly sync re-fetches the oldest 1/7 of the details itself.
  # `martom sync --full` stays available as a one-off task (README).
  #
  # Each step runs even if the one before it failed or was partial (extract and embed only
  # pick up what changed, so they are safe to run). The task exits with the first non-zero
  # exit code, so a failed or partial sync still shows as a failed task; sync_run rows hold
  # the details.
  job_script = <<-EOT
    rc=0
    step() { "$@" || { s=$?; [ "$rc" -ne 0 ] || rc=$s; }; }
    step martom sync --scheduled
    step martom extract
    step martom embed
    exit "$rc"
  EOT

  # Hard stop for a scheduled task: 16 h. A task starts at 23:00-01:00 UTC at the latest
  # (01:00 Sarajevo, plus up to 1 h of start retries), so it is gone by 17:00 UTC: before
  # the 18:00 UTC RDS backup window and long before the next night's task. The worst case
  # of one chain is ~8 h (3 h sync + 2 h extract + 3 h embed, infra/README.md
  # "Schedules"), so 16 h leaves room. Needs `timeout` in the image (coreutils or busybox).
  job_timeout_seconds = 57600
}

resource "aws_ecs_cluster" "main" {
  name = local.prefix

  # Container Insights costs extra per metric; plain CloudWatch logs are enough for now.
  setting {
    name  = "containerInsights"
    value = "disabled"
  }
}

resource "aws_cloudwatch_log_group" "api" {
  name              = "/ecs/${local.prefix}/api"
  retention_in_days = var.log_retention_days
}

resource "aws_cloudwatch_log_group" "job" {
  name              = "/ecs/${local.prefix}/job"
  retention_in_days = var.log_retention_days
}

# --- IAM: the execution role lets ECS pull the image, write logs and read the secrets ---

data "aws_iam_policy_document" "ecs_tasks_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["ecs-tasks.amazonaws.com"]
    }

    # Only ECS tasks in this account may use the role (confused-deputy protection).
    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "task_execution" {
  name               = "${local.prefix}-task-execution"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

# AWS-managed: ECR pull and CloudWatch Logs writes.
resource "aws_iam_role_policy_attachment" "task_execution" {
  role       = aws_iam_role.task_execution.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonECSTaskExecutionRolePolicy"
}

data "aws_iam_policy_document" "read_secrets" {
  statement {
    actions = ["secretsmanager:GetSecretValue"]
    # The RDS-managed secret uses the AWS-managed Secrets Manager key, so no KMS
    # permission is needed.
    resources = [
      local.db_password_secret_arn,
      aws_secretsmanager_secret.app.arn,
    ]
  }
}

resource "aws_iam_role_policy" "task_execution_secrets" {
  name   = "read-app-secrets"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.read_secrets.json
}

# The API and the job call no AWS API, so they get no task role (no AWS permissions).
# The one exception is the chat export task (chat_export.tf), whose role may only PUT
# into the private export bucket.

# --- API service ---

resource "aws_ecs_task_definition" "api" {
  family                   = "${local.prefix}-api"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.api_cpu
  memory                   = var.api_memory
  execution_role_arn       = aws_iam_role.task_execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64" # CI builds on ubuntu-latest (x86) runners
  }

  container_definitions = jsonencode([
    {
      name      = "api"
      image     = local.image
      essential = true
      command   = ["martom", "api", "--host", "0.0.0.0", "--port", "8000"]

      portMappings = [{ containerPort = 8000, protocol = "tcp" }]

      environment = [for name, value in local.api_environment : { name = name, value = value }]
      secrets     = local.api_secrets

      # Start the API once the Redis container has started. Not "HEALTHY": Redis is only
      # the search cache (M3-5), and a Redis that never becomes healthy must not keep the
      # API from starting; /health answers 200 "degraded" without it.
      dependsOn = [{ containerName = "redis", condition = "START" }]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.api.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "api"
        }
      }
    },
    {
      # Query and embedding cache (M3-5). Cache only: no persistence, so a restarted task
      # starts with an empty cache. The image comes from the ECR Public mirror of the
      # Docker official image (no Docker Hub rate limits).
      #
      # Not essential (Tomislav, #17): if Redis exits, ECS keeps the task and the API
      # running (searches run uncached, /health says "degraded") and restarts only this
      # container. A task's health comes only from its essential containers, so Redis's
      # own healthCheck below no longer affects the task.
      #
      # restartAttemptPeriod = 60 (the minimum, ECS default 300): a container is restarted
      # at most once per period, and only if it had run for a full period before it exited.
      # A Redis that dies within 60 s of starting is NOT restarted and stays down until the
      # task is replaced (infra/README.md, "When Redis dies").
      name      = "redis"
      image     = "public.ecr.aws/docker/library/redis:7-alpine"
      essential = false
      restartPolicy = {
        enabled              = true
        restartAttemptPeriod = 60
      }
      command = [
        "redis-server",
        "--bind", "127.0.0.1",
        "--save", "",
        "--appendonly", "no",
        "--maxmemory", var.redis_maxmemory,
        "--maxmemory-policy", "allkeys-lru",
      ]

      healthCheck = {
        command     = ["CMD", "redis-cli", "ping"]
        interval    = 10
        timeout     = 3
        retries     = 3
        startPeriod = 5
      }

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.api.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "redis"
        }
      }
    },
  ])
}

resource "aws_ecs_service" "api" {
  name            = "${local.prefix}-api"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.api.arn
  desired_count   = var.api_desired_count
  launch_type     = "FARGATE"

  # Rolling deploy with one task: start the new task, wait until it is healthy, then stop
  # the old one (100% minimum, 200% maximum).
  deployment_minimum_healthy_percent = 100
  deployment_maximum_percent         = 200

  # Roll back automatically when new tasks keep failing to start or to pass health checks.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # Python imports take a while on 0.25 vCPU; don't count failed health checks at startup.
  health_check_grace_period_seconds = 90

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.api.id]
    assign_public_ip = true # no NAT gateway (network.tf)
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.api.arn
    container_name   = "api"
    container_port   = 8000
  }

  depends_on = [aws_lb_listener.http]
}

# --- Job task definition (scheduled sync pipeline and one-off commands) ---

resource "aws_ecs_task_definition" "job" {
  family                   = "${local.prefix}-job"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.job_cpu
  memory                   = var.job_memory
  execution_role_arn       = aws_iam_role.task_execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "job"
      image     = local.image
      essential = true
      # Default command: the scheduled pipeline, killed after job_timeout_seconds. One-off
      # tasks override the command (README: `martom migrate`, `martom sync --full`).
      command = ["timeout", tostring(local.job_timeout_seconds), "sh", "-c", local.job_script]

      environment = [for name, value in local.job_environment : { name = name, value = value }]
      secrets     = local.job_secrets

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.job.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "job"
        }
      }
    },
  ])
}
