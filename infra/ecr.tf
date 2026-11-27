# One image for everything: the API service, the scheduled jobs and `martom migrate`.
# CD (M6-2) builds and pushes it; until then, push one by hand (README).

resource "aws_ecr_repository" "app" {
  name = "${var.name}-app"
  # MUTABLE so `latest` can move until M6-2 decides on tags per commit.
  image_tag_mutability = "MUTABLE"
  # Images are rebuilt from git, so `terraform destroy` may delete the repository with them.
  force_delete = true

  image_scanning_configuration {
    scan_on_push = true
  }

  encryption_configuration {
    encryption_type = "AES256"
  }
}

# Keep the 10 newest images; storage is $0.10/GB-month.
resource "aws_ecr_lifecycle_policy" "app" {
  repository = aws_ecr_repository.app.name

  policy = jsonencode({
    rules = [{
      rulePriority = 1
      description  = "Keep the 10 newest images"
      selection = {
        tagStatus   = "any"
        countType   = "imageCountMoreThan"
        countNumber = 10
      }
      action = { type = "expire" }
    }]
  })
}
