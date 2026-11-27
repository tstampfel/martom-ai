# EventBridge Scheduler starts the job task (ecs.tf) twice:
#
#   nightly:  `martom sync --scheduled`        every day at 01:00 Europe/Sarajevo
#   weekly:   `martom sync --full --scheduled` Monday 05:00, i.e. the night from Sunday
#             to Monday, 4 h after that night's nightly run
#
# Both then run `martom extract` and `martom embed`. --scheduled marks the runs that count
# toward the 10%-guard auto-apply streak (CLAUDE.md, `martom sync`).
#
# Non-overlap: `martom sync` has no lock of its own, so the gap between the two schedules
# is what keeps them apart. A sync stops itself at its 3 h deadline (as `partial`), and
# the extract + embed after a nightly only process what changed, so 4 h is enough. Keep
# that gap if you change either cron (variables.tf).

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
  # Start the job task definition (any revision), only in our cluster.
  statement {
    actions   = ["ecs:RunTask"]
    resources = ["arn:aws:ecs:${var.region}:${local.account_id}:task-definition/${aws_ecs_task_definition.job.family}:*"]

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
}

resource "aws_iam_role_policy" "scheduler_run_job" {
  name   = "run-job-task"
  role   = aws_iam_role.scheduler.id
  policy = data.aws_iam_policy_document.scheduler_run_job.json
}

locals {
  schedules = {
    nightly-sync = {
      description = "Nightly OLX sync, then extract and embed"
      expression  = var.nightly_sync_cron
      sync_args   = "--scheduled"
    }
    weekly-full-sync = {
      description = "Weekly full OLX re-fetch (Sunday night), then extract and embed"
      expression  = var.weekly_full_sync_cron
      sync_args   = "--full --scheduled"
    }
  }
}

resource "aws_scheduler_schedule" "sync" {
  for_each = local.schedules

  name                         = "${local.prefix}-${each.key}"
  description                  = each.value.description
  schedule_expression          = each.value.expression
  schedule_expression_timezone = var.schedule_timezone
  # DISABLED while the stack is paused (README: stop RDS between test phases).
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

    # Passed to RunTask as overrides: only SYNC_ARGS differs between the two schedules.
    input = jsonencode({
      containerOverrides = [{
        name        = "job"
        environment = [{ name = "SYNC_ARGS", value = each.value.sync_args }]
      }]
    })

    # Retries cover a task that couldn't be *started* (e.g. no Fargate capacity), not a
    # sync that ran and failed; a re-run of a whole sync is never automatic.
    retry_policy {
      maximum_event_age_in_seconds = 3600
      maximum_retry_attempts       = 2
    }
  }
}
