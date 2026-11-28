output "api_url" {
  description = "Public HTTPS address of the API (set it as the frontend's API base URL)."
  value       = "https://${aws_cloudfront_distribution.api.domain_name}"
}

output "ecr_repository_url" {
  description = "Push the app image here (docker push <url>:<tag>)."
  value       = aws_ecr_repository.app.repository_url
}

output "ecs_cluster" {
  description = "ECS cluster name, for `aws ecs run-task` (one-off commands such as martom migrate)."
  value       = aws_ecs_cluster.main.name
}

output "job_task_definition" {
  description = "Task definition family for one-off commands."
  value       = aws_ecs_task_definition.job.family
}

output "job_network_configuration" {
  description = "The --network-configuration value for `aws ecs run-task`."
  value = jsonencode({
    awsvpcConfiguration = {
      subnets        = aws_subnet.public[*].id
      securityGroups = [aws_security_group.job.id]
      assignPublicIp = "ENABLED"
    }
  })
}

output "app_secret_name" {
  description = "Secrets Manager secret whose JSON value (API keys, ADMIN_TOKEN) you set by hand."
  value       = aws_secretsmanager_secret.app.name
}

output "db_endpoint" {
  description = "RDS endpoint (host:port); reachable only from inside the VPC."
  value       = aws_db_instance.main.endpoint
}

output "scheduler_dlq_url" {
  description = "SQS dead-letter queue for schedule invocations that couldn't start a task."
  value       = aws_sqs_queue.scheduler_dlq.url
}

output "db_password_secret_arn" {
  description = "ARN of the RDS-managed secret holding the master password (only the ARN is in Terraform)."
  value       = aws_db_instance.main.master_user_secret[0].secret_arn
}

output "chat_export_bucket" {
  description = "Private bucket `martom export-chats` writes scrubbed chats to (objects deleted within 90 days)."
  value       = aws_s3_bucket.chat_export.bucket
}

output "chat_export_task_definition" {
  description = "Task definition family for `martom export-chats` (README, \"Exporting chats\")."
  value       = aws_ecs_task_definition.chat_export.family
}
