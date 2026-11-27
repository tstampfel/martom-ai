# EventBridge Scheduler starts the job task (ecs.tf) once a night, at 01:00 Europe/Sarajevo:
#
#   every night (nightly-sync):  sync --scheduled -> extract -> embed
#
# There is no weekly full run (#135): the nightly sync re-fetches the oldest 1/7 of the
# listing details itself (rolling refresh), so every listing is refreshed within 7 nights.
# `martom sync` has no lock of its own; each task is killed after 16 h
# (job_timeout_seconds in ecs.tf), so it can't run into the next night's task.
# --scheduled marks the runs that count toward the 10%-guard auto-apply streak (CLAUDE.md,
# `martom sync`).
#
# Schedules start DISABLED (var.schedules_enabled): turn them on after the first image push
# and `martom migrate` (README step 6).

data "aws_iam_policy_document" "scheduler_assume" {
  statement {
    actions = ["sts:AssumeRole"]

    principals {
      type        = "Service"
      identifiers = ["scheduler.amazonaws.com"]
    }

    condition {
      test     = "StringEquals"
      variable = "aws:SourceAccount"
      values   = [local.account_id]
    }
  }
}

resource "aws_iam_role" "scheduler" {
  name               = "${local.prefix}-scheduler"
  assume_role_policy = data.aws_iam_policy_document.scheduler_assume.json
}

data "aws_iam_policy_document" "scheduler_run_job" {
  # Start the job task definition, only in our cluster. The schedule names the family
  # without a revision (always the latest), so allow the bare family ARN as well as every
  # revision (`:*`); both stay limited to this one family.
  statement {
    actions = ["ecs:RunTask"]
    resources = [
      "arn:aws:ecs:${var.region}:${local.account_id}:task-definition/${aws_ecs_task_definition.job.family}",
      "arn:aws:ecs:${var.region}:${local.account_id}:task-definition/${aws_ecs_task_definition.job.family}:*",
    ]

    condition {
      test     = "ArnEquals"
      variable = "ecs:cluster"
      values   = [aws_ecs_cluster.main.arn]
    }
  }

  # RunTask hands the execution role to ECS.
  statement {
    actions   = ["iam:PassRole"]
    resources = [aws_iam_role.task_execution.arn]

    condition {
      test     = "StringEquals"
      variable = "iam:PassedToService"
      values   = ["ecs-tasks.amazonaws.com"]
    }
  }

  # Invocations that still fail after the retries (e.g. RunTask denied, no Fargate
  # capacity) land in the dead-letter queue.
  statement {
    actions   = ["sqs:SendMessage"]
    resources = [aws_sqs_queue.scheduler_dlq.arn]
  }
}

# Dead-letter queue for schedule invocations that couldn't start a task. A message here
# means a night's sync never ran; M6-3 can alarm on the queue depth. Free at this volume
# (SQS free tier).
resource "aws_sqs_queue" "scheduler_dlq" {
  name                      = "${local.prefix}-scheduler-dlq"
  message_retention_seconds = 1209600 # 14 days, the maximum
  sqs_managed_sse_enabled   = true
}

resource "aws_iam_role_policy" "scheduler_run_job" {
  name   = "run-job-task"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler_run_job.json
}

locals {
  schedules = {
    nightly-sync = {
      description = "Nightly OLX sync with its rolling refresh, then extract and embed (every night)"
      expression  = var.nightly_sync_cron
    }
  }
}

resource "aws_scheduler_schedule" "sync" {
  for_each = local.schedules

  name                         = "${local.prefix}-${each.key}"
  description                  = each.value.description
  schedule_expression          = each.value.expression
  schedule_expression_timezone = var.schedule_timezone
  # DISABLED until the first deploy is done, and while the stack is paused (README).
  state = var.schedules_enabled ? "ENABLED" : "DISABLED"

  flexible_time_window {
    mode = "OFF"
  }

  target {
    arn      = aws_ecs_cluster.main.arn
    role_arn = aws_iam_role.scheduler.arn

    ecs_parameters {
      task_definition_arn = aws_ecs_task_definition.job.arn_without_revision # always the latest revision
      launch_type         = "FARGATE"
      task_count          = 1

      network_configuration {
        subnets          = aws_subnet.public[*].id
        security_groups  = [aws_security_group.job.id]
        assign_public_ip = true # no NAT gateway (network.tf)
      }
    }

    dead_letter_config {
      arn = aws_sqs_queue.scheduler_dlq.arn
    }

    # Retries cover a task that couldn't be *started* (e.g. no Fargate capacity), not a
    # sync that ran and failed; a re-run of a whole sync is never automatic.
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }
  }
}
