# Secrets Manager holds every secret the containers read. ECS injects them as environment
# variables when a task starts, so the app reads them from the environment like any other
# setting (src/martom/settings.py); nothing secret is in the task definition.
#
# Two secrets ($0.40/month each):
#
# 1. martom/<env>/database-url: Terraform writes it, built from the generated RDS
#    password. The password is therefore in the Terraform state, which is why the state
#    bucket is encrypted, versioned and private.
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

resource "aws_secretsmanager_secret" "database_url" {
  name                    = "${var.name}/${var.environment}/database-url"
  description             = "DATABASE_URL for the API and the jobs (written by Terraform)"
  recovery_window_in_days = 7
}

resource "aws_secretsmanager_secret_version" "database_url" {
  secret_id = aws_secretsmanager_secret.database_url.id
  secret_string = format(
    "postgresql://%s:%s@%s/%s?sslmode=require",
    aws_db_instance.main.username,
    random_password.db.result,
    aws_db_instance.main.endpoint, # host:port
    aws_db_instance.main.db_name,
  )
}

resource "aws_secretsmanager_secret" "app" {
  name                    = "${var.name}/${var.environment}/app"
  description             = "API keys and tokens as one JSON object; the value is set by hand, never by Terraform"
  recovery_window_in_days = 7
}
