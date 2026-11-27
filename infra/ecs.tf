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
# | DATABASE_URL             | secret database-url               | secret database-url         |
# | REDIS_URL                | redis://localhost:6379/0 (sidecar)| not set (no Redis)          |
# | OLX_API_BASE_URL         | not set (Settings default)        | https://api.olx.ba          |
# | OLX_SHOP_USERNAME        | not set                           | martom                      |
# | OLX_REQUEST_INTERVAL     | not set                           | 2.0                         |
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
# AGENT_MODEL is listed in .env.example for M4; Settings ignores it until then.

data "aws_caller_identity" "current" {}

locals {
  account_id = data.aws_caller_identity.current.account_id
  image      = "${aws_ecr_repository.app.repository_url}:${var.image_tag}"

  common_environment = {
    APP_ENV            = "production"
    LOG_LEVEL          = "INFO"
    FAST_MODEL         = "claude-haiku-5-5"
    EMBEDDING_PROVIDER = "cohere"
    EMBEDDING_MODEL    = "embed-multilingual-v3.0"
    LANGSMITH_TRACING  = "true"
    LANGSMITH_PROJECT  = var.langsmith_project
  }

  api_environment = merge(local.common_environment, {
    CORS_ALLOWED_ORIGINS = var.cors_allowed_origins
    # The Redis sidecar shares the task's network namespace, so it is on localhost.
    REDIS_URL   = "redis://localhost:6379/0"
    AGENT_MODEL = "claude-sonnet-5-5"
  })

  job_environment = merge(local.common_environment, {
    OLX_API_BASE_URL       = "https://api.olx.ba"
    OLX_SHOP_USERNAME      = "martom"
    OLX_REQUEST_INTERVAL   = "2.0"
    EXTRACTION_CONCURRENCY = "4"
  })

  # ECS wants lists of {name, value} / {name, valueFrom}; build them from the maps above.
  database_url_secret = {
    name      = "DATABASE_URL"
    valueFrom = aws_secretsmanager_secret.database_url.arn
  }
  api_secrets = concat([local.database_url_secret], [
    for key in local.api_secret_keys : { name = key, valueFrom = "${aws_secretsmanager_secret.app.arn}:${key}::" }
  ])
  job_secrets = concat([local.database_url_secret], [
    for key in local.job_secret_keys : { name = key, valueFrom = "${aws_secretsmanager_secret.app.arn}:${key}::" }
  ])

  # The nightly and weekly jobs run the whole pipeline: sync, then extract the listings
  # whose details changed, then embed the products whose text changed. Each step runs even
  # if the one before it failed or was partial (extract and embed only pick up what
  # changed, so they are safe to run). The task exits with the first non-zero exit code,
  # so a failed or partial sync still shows as a failed task; sync_run rows hold the
  # details. SYNC_ARGS is set per schedule (scheduler.tf).
  job_script = <<-EOT
    rc=0
    martom sync $SYNC_ARGS || rc=$?
    martom extract || { s=$?; [ "$rc" -ne 0 ] || rc=$s; }
    martom embed || { s=$?; [ "$rc" -ne 0 ] || rc=$s; }
    exit "$rc"
  EOT
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
    resources = [
      aws_secretsmanager_secret.database_url.arn,
      aws_secretsmanager_secret.app.arn,
    ]
  }
}

resource "aws_iam_role_policy" "task_execution_secrets" {
  name   = "read-app-secrets"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.read_secrets.json
}

# The app itself calls no AWS API, so the tasks get no task role (no AWS permissions).

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

      # Start the API only once Redis answers, so the first health check can pass.
      dependsOn = [{ containerName = "redis", condition = "HEALTHY" }]

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
      name      = "redis"
      image     = "public.ecr.aws/docker/library/redis:7-alpine"
      essential = true
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

  depends_on = [aws_lb_listener_rule.from_cloudfront]
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
      # Default command: the nightly pipeline. Schedules set SYNC_ARGS; one-off tasks
      # override the command (README: `martom migrate`).
      command = ["sh", "-c", local.job_script]

      environment = [for name, value in merge(local.job_environment, { SYNC_ARGS = "--scheduled" }) : { name = name, value = value }]
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
