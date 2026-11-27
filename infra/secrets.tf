# Secrets Manager holds every secret the containers read. ECS injects them as environment
# variables when a task starts, so the app reads them from the environment like any other
# setting (src/martom/settings.py); nothing secret is in the task definition.
#
# Two secrets ($0.40/month each):
#
# 1. The RDS master password: RDS creates and owns this secret (rds.tf,
#    manage_master_user_password). Terraform never sees the password, only the ARN. ECS
#    reads its "password" key into PGPASSWORD; DATABASE_URL itself has no password, and
#    psycopg (libpq) reads PGPASSWORD by itself.
#
# 2. martom/<env>/app: one JSON object with the API keys and tokens. Terraform creates
#    the empty secret only; Tomislav sets its value once, by hand (README). Terraform never
#    sees or stores these values. ECS reads one key at a time (`<arn>:KEY::`), and a task
#    won't start if a key it asks for is missing from the JSON.

locals {
  # Keys of the app secret that each container asks for. Adding a new secret setting
  # (e.g. ORDER_PHONE in M5-6, TURNSTILE_SECRET_KEY in M5-7) means: add its key to the
  # JSON in Secrets Manager first, then here.
  api_secret_keys = ["ANTHROPIC_API_KEY", "COHERE_API_KEY", "LANGSMITH_API_KEY", "ADMIN_TOKEN"]
  job_secret_keys = ["ANTHROPIC_API_KEY", "COHERE_API_KEY", "LANGSMITH_API_KEY"]
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.name}/${var.environment}/app"
  description             = "API keys and tokens as one JSON object; the value is set by hand, never by Terraform"
  recovery_window_in_days = 7
}

locals {
  db_password_secret_arn = aws_db_instance.main.master_user_secret[0].secret_arn
}

# RDS rotates its managed secret every 7 days unless told otherwise, and turning rotation
# off is not supported for it. A rotation changes the database password at once, while a
# running task keeps the old PGPASSWORD it got at start-up; the API opens a new connection
# per request, so it would fail until its task restarts. So the schedule is once a year,
# and the README ("Database password") says how to rotate by hand at a quiet time with a
# forced redeploy right after, before the scheduled date comes round.
resource "aws_secretsmanager_secret_rotation" "db_password" {
  secret_id          = local.db_password_secret_arn
  rotate_immediately = false

  rotation_rules {
    automatically_after_days = 365
  }
}
