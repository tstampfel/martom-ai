# Private export of scrubbed customer chats (#158): `martom export-chats` writes here.
#
# Real customer chats are never committed to the public repo, even scrubbed (Tomislav,
# 28 Nov 2026). A scrubbed chat Tomislav wants to study goes only to this bucket:
#
# - same EU region as the rest of the stack (var.region, eu-central-1): exports never
#   leave the EU;
# - all public access blocked, ACLs off (bucket owner enforced);
# - encrypted at rest with S3-managed keys (SSE-S3, free), and the bucket policy refuses
#   any request that isn't HTTPS;
# - versioning off (never enabled here), with noncurrent versions expired after 1 day
#   anyway, in case someone turns it on by hand;
# - every object deleted 90 days after it was written. The Privatnost page promises
#   "Očišćeni primjeri razgovora: najduže 90 dana" (canvas 3aab), so the expiry is a
#   literal here, never a variable, and tests/test_infra_chat_export.py fails if it is
#   ever above 90;
# - only Tomislav (var.chat_export_admin_arns) and the export task role may touch it: the
#   bucket policy denies every other principal, whatever IAM says.
#
# Production runs the export as a one-off task (README, "Exporting chats"), because the
# database is reachable only inside the VPC. The task role may only PUT under chats/; it
# can't list, read or delete what earlier exports wrote.

locals {
  chat_export_bucket = "${local.prefix}-chat-export-${local.account_id}"
}

resource "aws_s3_bucket" "chat_export" {
  bucket = local.chat_export_bucket
}

resource "aws_s3_bucket_public_access_block" "chat_export" {
  bucket = aws_s3_bucket.chat_export.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_ownership_controls" "chat_export" {
  bucket = aws_s3_bucket.chat_export.id

  rule {
    object_ownership = "BucketOwnerEnforced"
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "chat_export" {
  bucket = aws_s3_bucket.chat_export.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

resource "aws_s3_bucket_lifecycle_configuration" "chat_export" {
  bucket = aws_s3_bucket.chat_export.id

  rule {
    id     = "expire-scrubbed-chats"
    status = "Enabled"

    filter {}

    # The published promise: at most 90 days. Never raise it without changing the
    # Privatnost page first (tests/test_infra_chat_export.py checks it).
    expiration {
      days = 90
    }

    # Only matters if versioning is ever turned on: a deleted or overwritten export
    # must not live on as an old version.
    noncurrent_version_expiration {
      noncurrent_days = 1
    }

    abort_incomplete_multipart_upload {
      days_after_initiation = 1
    }
  }
}

data "aws_iam_policy_document" "chat_export_bucket" {
  # Everyone but Tomislav's user and the export task role is refused, whatever their
  # IAM policies allow. The account root can still delete this policy (S3 always lets
  # it), which is the way back in if the admin ARN is ever wrong (README).
  statement {
    sid     = "OnlyTomislavAndTheExportTask"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.chat_export.arn,
      "${aws_s3_bucket.chat_export.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "ArnNotEquals"
      variable = "aws:PrincipalArn"
      values   = concat(var.chat_export_admin_arns, [aws_iam_role.chat_export_task.arn])
    }
  }

  statement {
    sid     = "HttpsOnly"
    effect  = "Deny"
    actions = ["s3:*"]
    resources = [
      aws_s3_bucket.chat_export.arn,
      "${aws_s3_bucket.chat_export.arn}/*",
    ]

    principals {
      type        = "*"
      identifiers = ["*"]
    }

    condition {
      test     = "Bool"
      variable = "aws:SecureTransport"
      values   = ["false"]
    }
  }
}

resource "aws_s3_bucket_policy" "chat_export" {
  bucket = aws_s3_bucket.chat_export.id
  policy = data.aws_iam_policy_document.chat_export_bucket.json

  # The public access block must exist first, or S3 may judge the policy on its own.
  depends_on = [aws_s3_bucket_public_access_block.chat_export]
}

# --- The export task: its own role and task definition ---

resource "aws_iam_role" "chat_export_task" {
  name               = "${local.prefix}-chat-export-task"
  assume_role_policy = data.aws_iam_policy_document.ecs_tasks_assume.json
}

data "aws_iam_policy_document" "chat_export_write" {
  statement {
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.chat_export.arn}/chats/*"]
  }
}

resource "aws_iam_role_policy" "chat_export_write" {
  name   = "write-chat-exports"
  role   = aws_iam_role.chat_export_task.id
  policy = data.aws_iam_policy_document.chat_export_write.json
}

resource "aws_ecs_task_definition" "chat_export" {
  family                   = "${local.prefix}-chat-export"
  requires_compatibilities = ["FARGATE"]
  network_mode             = "awsvpc"
  cpu                      = 256
  memory                   = 512
  execution_role_arn       = aws_iam_role.task_execution.arn
  # The only task with AWS permissions: PUT under chats/ in the export bucket.
  task_role_arn = aws_iam_role.chat_export_task.arn

  runtime_platform {
    operating_system_family = "LINUX"
    cpu_architecture        = "X86_64"
  }

  container_definitions = jsonencode([
    {
      name      = "chat-export"
      image     = local.image
      essential = true
      # Placeholder: every run overrides the command with the thread ids to export
      # (README, "Exporting chats").
      command = ["martom", "export-chats", "--help"]

      environment = [
        for name, value in merge(local.common_environment, {
          CHAT_EXPORT_BUCKET = aws_s3_bucket.chat_export.bucket
          AWS_REGION         = var.region
          # The export doesn't trace: a chat must not reach LangSmith through it.
          LANGSMITH_TRACING = "false"
        }) : { name = name, value = value }
      ]
      # The database password only: the export calls no model.
      secrets = [local.db_password_secret]

      logConfiguration = {
        logDriver = "awslogs"
        options = {
          awslogs-group         = aws_cloudwatch_log_group.job.name
          awslogs-region        = var.region
          awslogs-stream-prefix = "chat-export"
        }
      }
    },
  ])
}
