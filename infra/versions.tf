terraform {
  # 1.10 brought native S3 state locking (use_lockfile), so no DynamoDB table is needed.
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }

  # Partial configuration: the bucket name (made by infra/bootstrap) is passed at init
  # time with `-backend-config=backend.hcl`, so it never has to be committed.
  backend "s3" {
    key          = "martom-ai/production.tfstate"
    region       = "eu-central-1"
    encrypt      = true
    use_lockfile = true
  }
}

provider "aws" {
  region = var.region

  default_tags {
    tags = {
      Project     = "martom-ai"
      ManagedBy   = "terraform"
      Environment = var.environment
    }
  }
}
