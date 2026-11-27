output "state_bucket" {
  description = "Put this name in infra/backend.hcl (bucket = \"...\") before `terraform init` in infra/."
  value       = aws_s3_bucket.state.id
}
