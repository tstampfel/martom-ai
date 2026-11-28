# Umami: self-hosted, cookieless visit statistics (#156, #163). docs/analytics.md explains
# the setup; infra/README.md ("Umami") has the deploy runbook.
#
#   browser on martom-savjetnik.pages.dev
#     │ GET /script.js, POST /api/send (HTTPS, cross-origin; Umami answers CORS itself)
#     ▼
#   CloudFront "umami" distribution  https://<other id>.cloudfront.net   (Umami at the root)
#     │ same VPC origin as the API, plus a secret X-Martom-Origin header
#     ▼
#   internal ALB: listener rule "header matches the secret" → Umami target group (:3000);
#                 every other request keeps the default action → the API
#     ▼
#   ECS service "umami" on FARGATE_SPOT, official image, 0.25 vCPU / 1 GB, desired 1
#     │ :5432
#     ▼
#   the same RDS instance, its own database `umami` and login role `umami`
#
# Why a second distribution and not a /umami/* path on the API's (Tomislav, 28 Nov 2026,
# option C): Umami's BASE_PATH is a build-time setting, so a sub-path needs a custom image
# build. Its own hostname runs the official image unchanged, with the admin UI working.
#
# Why the header must be a secret: the API distribution forwards every viewer header to the
# ALB (cloudfront.tf, AllViewerExceptHostHeader), so a fixed, public header value would let
# anyone reach Umami through the API's address. Only the umami distribution knows the value
# (var.umami_origin_secret, never committed); CloudFront also overwrites a viewer header of
# the same name on that distribution.

locals {
  umami_origin_header = "X-Martom-Origin"
  umami_port          = 3000
  umami_origin_id     = "alb-umami"
}

# --- Secrets: one JSON object, set by hand (README, "Umami"), never by Terraform ---
#
# Keys: DATABASE_URL (with the umami role's password), APP_SECRET, UMAMI_DB_PASSWORD (the
# same password, for the database set-up task), ADMIN_PASSWORD (kept here only; Umami has no
# setting for it, Tomislav types it into the admin UI once) and ORIGIN_HEADER_SECRET (the
# value of var.umami_origin_secret, a recovery copy of infra/terraform.tfvars).
resource "aws_secretsmanager_secret" "umami" {
  name                    = "${var.name}/${var.environment}/umami"
  description             = "Umami database URL, app secret, admin password and origin header secret as one JSON object; set by hand, never by Terraform"
  recovery_window_in_days = 7
}

data "aws_iam_policy_document" "read_umami_secret" {
  statement {
    actions   = ["secretsmanager:GetSecretValue"]
    resources = [aws_secretsmanager_secret.umami.arn]
  }
}

resource "aws_iam_role_policy" "task_execution_umami_secret" {
  name   = "read-umami-secret"
  role   = aws_iam_role.task_execution.id
  policy = data.aws_iam_policy_document.read_umami_secret.json
}

# --- FARGATE_SPOT for the cluster ---
#
# The API service and the job tasks keep launch_type FARGATE (on-demand); only the Umami
# service asks for FARGATE_SPOT through its capacity provider strategy. When AWS takes the
# Spot capacity back, ECS starts a replacement task: a few minutes without statistics,
# nothing else is affected.
resource "aws_ecs_cluster_capacity_providers" "main" {
  cluster_name       = aws_ecs_cluster.main.name
  capacity_providers = ["FARGATE", "FARGATE_SPOT"]
}

resource "aws_cloudwatch_log_group" "umami" {
  name              = "/ecs/${local.prefix}/umami"
  retention_in_days = var.log_retention_days
}

# --- The Umami service ---

resource "aws_ecs_task_definition" "umami" {
  #checkov:skip=CKV_AWS_336:the official Umami image (Next.js) writes its cache and generated files inside the container, and Fargate has no tmpfs
  family                   = "${local.prefix}-umami"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = var.umami_cpu
  memory                   = var.umami_memory
  execution_role_arn       = aws_iam_role.task_execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "umami"
      image     = var.umami_image
      essential = true

      portMappings = [{ containerPort = local.umami_port, protocol = "tcp" }]

      # At start the image applies its own Prisma migrations to the `umami` database and,
      # on the very first start, creates the admin login admin / umami: change it at once
      # (README, "Umami", step 4).
      environment = [
        { name = "DISABLE_TELEMETRY", value = "1" }, # no anonymous usage pings to Umami
        { name = "DISABLE_UPDATES", value = "1" },   # no version check; upgrades are a var.umami_image change
      ]
      secrets = [
        for key in ["DATABASE_URL", "APP_SECRET"] :
        { name = key, valueFrom = "${aws_secretsmanager_secret.umami.arn}:${key}::" }
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.umami.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "umami"
        }
      }
    },
  ])
}

resource "aws_ecs_service" "umami" {
  name            = "${local.prefix}-umami"
  cluster         = aws_ecs_cluster.main.id
  task_definition = aws_ecs_task_definition.umami.arn
  desired_count   = var.umami_desired_count

  capacity_provider_strategy {
    capacity_provider = "FARGATE_SPOT"
    weight            = 1
  }

  # Stop the old task before starting the new one: a deploy costs a minute of statistics,
  # and Umami never holds two connection pools at once (src/martom/db.py, UMAMI_CONNECTIONS).
  deployment_minimum_healthy_percent = 0
  deployment_maximum_percent         = 100

  # A task that exits or fails its health checks is replaced by ECS ("restart on
  # failure"); a deploy whose tasks keep failing rolls back.
  deployment_circuit_breaker {
    enable   = true
    rollback = true
  }

  # The first start runs Umami's database migrations; don't count health checks before then.
  health_check_grace_period_seconds = 180

  network_configuration {
    subnets          = aws_subnet.public[*].id
    security_groups  = [aws_security_group.umami.id]
    assign_public_ip = true # no NAT gateway (network.tf): pulls the image from ghcr.io
  }

  load_balancer {
    target_group_arn = aws_lb_target_group.umami.arn
    container_name   = "umami"
    container_port   = local.umami_port
  }

  depends_on = [aws_lb_listener_rule.umami, aws_ecs_cluster_capacity_providers.main]
}

# --- One-off database set-up: role and database `umami` on the shared RDS instance ---
#
# Terraform can't reach the private RDS instance, so this task does it (README, "Umami",
# step 2). It runs psql from the official Postgres image as the master user, re-runs safely,
# and takes both passwords from Secrets Manager, so none is in a command line or a task
# override. The umami role gets its own database only: no superuser, no CONNECT on `martom`,
# and at most 5 connections (src/martom/db.py, UMAMI_CONNECTIONS).
resource "aws_ecs_task_definition" "umami_db_setup" {
  family                   = "${local.prefix}-umami-db-setup"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.task_execution.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "umami-db-setup"
      image     = "public.ecr.aws/docker/library/postgres:16-alpine"
      essential = true
      command   = ["sh", "-c", local.umami_db_setup_script]

      environment = [
        { name = "PGHOST", value = aws_db_instance.main.address },
        { name = "PGPORT", value = tostring(aws_db_instance.main.port) },
        { name = "PGUSER", value = aws_db_instance.main.username },
        { name = "PGDATABASE", value = aws_db_instance.main.db_name },
        { name = "PGSSLMODE", value = "require" },
      ]
      secrets = [
        local.db_password_secret,
        { name = "UMAMI_DB_PASSWORD", valueFrom = "${aws_secretsmanager_secret.umami.arn}:UMAMI_DB_PASSWORD::" },
      ]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.umami.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "db-setup"
        }
      }
    },
  ])
}

locals {
  # psql reads the SQL from stdin, where :'pw' is quoted safely as a literal; \gexec runs
  # CREATE ROLE / CREATE DATABASE only when they don't exist yet (neither has IF NOT EXISTS).
  # Every run sets the password again, so it also applies a changed UMAMI_DB_PASSWORD.
  umami_db_setup_script = <<-EOT
    set -eu
    psql -v ON_ERROR_STOP=1 -v pw="$UMAMI_DB_PASSWORD" <<'SQL'
    SELECT 'CREATE ROLE umami' WHERE NOT EXISTS (SELECT FROM pg_roles WHERE rolname = 'umami') \gexec
    ALTER ROLE umami WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE CONNECTION LIMIT 5 PASSWORD :'pw';
    SELECT 'CREATE DATABASE umami OWNER umami' WHERE NOT EXISTS (SELECT FROM pg_database WHERE datname = 'umami') \gexec
    REVOKE ALL ON DATABASE umami FROM PUBLIC;
    REVOKE CONNECT ON DATABASE martom FROM PUBLIC;
    SQL
    echo "umami database and role are ready"
  EOT
}

# --- Load balancer: target group and the header rule ---

resource "aws_lb_target_group" "umami" {
  name                 = "${local.prefix}-umami"
  port                 = local.umami_port
  protocol             = "HTTP"
  target_type          = "ip"
  vpc_id               = aws_vpc.main.id
  deregistration_delay = 15

  # Umami's own liveness endpoint: answers {"ok": true} without touching the database
  # (src/app/api/heartbeat/route.ts in Umami 3.5).
  health_check {
    path                = "/api/heartbeat"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

# Only requests carrying the secret header reach Umami. The listener's default action
# (alb.tf) still sends everything else to the API, unchanged.
resource "aws_lb_listener_rule" "umami" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10

  condition {
    http_header {
      http_header_name = local.umami_origin_header
      values           = [var.umami_origin_secret]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.umami.arn
  }
}

# --- CloudFront: Umami's own distribution and hostname ---

data "aws_cloudfront_cache_policy" "caching_optimized" {
  name = "Managed-CachingOptimized"
}

resource "aws_cloudfront_distribution" "umami" {
  enabled         = true
  comment         = "${local.prefix} Umami (visit statistics)"
  is_ipv6_enabled = true
  price_class     = "PriceClass_100"

  # The API's VPC origin and ALB, with the secret header that the listener rule matches.
  origin {
    origin_id   = local.umami_origin_id
    domain_name = aws_lb.api.dns_name

    vpc_origin_config {
      vpc_origin_id            = aws_cloudfront_vpc_origin.alb.id
      origin_read_timeout      = 30
      origin_keepalive_timeout = 5
    }

    custom_header {
      name  = local.umami_origin_header
      value = var.umami_origin_secret
    }
  }

  # Admin UI and the collect endpoint (POST /api/send): never cached. Every viewer header
  # goes through (Origin for CORS, the admin's Authorization); Host is left out.
  default_cache_behavior {
    target_origin_id       = local.umami_origin_id
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    cache_policy_id            = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security_headers.id
  }

  # The tracker script is the same file for every visitor: cache it at the edge.
  ordered_cache_behavior {
    path_pattern           = "/script.js"
    target_origin_id       = local.umami_origin_id
    viewer_protocol_policy = "redirect-to-https"
    allowed_methods        = ["GET", "HEAD"]
    cached_methods         = ["GET", "HEAD"]
    compress               = true

    cache_policy_id            = data.aws_cloudfront_cache_policy.caching_optimized.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security_headers.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
