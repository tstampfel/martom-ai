# Who may talk to whom:
#
#   CloudFront (VPC origin) --80--> internal ALB --8000--> API task --5432--> RDS
#                                     job task --5432--> RDS
#   API and job tasks --443--> the internet (ECR, Secrets Manager, CloudWatch, OLX, Claude, Cohere, LangSmith)
#
# The Redis sidecar listens on localhost inside the API task, so no rule mentions 6379.

# AWS-managed list of the CloudFront addresses that connect to origins.
data "aws_ec2_managed_prefix_list" "cloudfront_origin_facing" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "alb" {
  name        = "${local.prefix}-alb"
  description = "Internal load balancer: HTTP from CloudFront VPC origins only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "alb_from_cloudfront" {
  security_group_id = aws_security_group.alb.id
  description       = "HTTP from CloudFront edge servers"
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront_origin_facing.id
  ip_protocol       = "tcp"
  from_port         = 80
  to_port           = 80
}

resource "aws_vpc_security_group_egress_rule" "alb_to_api" {
  security_group_id            = aws_security_group.alb.id
  description                  = "To the API containers"
  referenced_security_group_id = aws_security_group.api.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_security_group" "api" {
  name        = "${local.prefix}-api"
  description = "API tasks: port 8000 from the load balancer only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "api_from_alb" {
  security_group_id            = aws_security_group.api.id
  description                  = "API port from the load balancer"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 8000
  to_port                      = 8000
}

resource "aws_vpc_security_group_egress_rule" "api_https" {
  security_group_id = aws_security_group.api.id
  description       = "HTTPS to AWS APIs, Claude, Cohere and LangSmith"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "api_to_db" {
  security_group_id            = aws_security_group.api.id
  description                  = "Postgres"
  referenced_security_group_id = aws_security_group.db.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

# Scheduled jobs and one-off tasks (martom migrate): no inbound traffic at all.
resource "aws_security_group" "job" {
  name        = "${local.prefix}-job"
  description = "Scheduled and one-off tasks: outbound only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_egress_rule" "job_https" {
  security_group_id = aws_security_group.job.id
  description       = "HTTPS to AWS APIs, OLX, Claude, Cohere and LangSmith"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "job_to_db" {
  security_group_id            = aws_security_group.job.id
  description                  = "Postgres"
  referenced_security_group_id = aws_security_group.db.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_security_group" "db" {
  name        = "${local.prefix}-db"
  description = "RDS Postgres: port 5432 from the API and job tasks only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "db_from_api" {
  security_group_id            = aws_security_group.db.id
  description                  = "Postgres from the API tasks"
  referenced_security_group_id = aws_security_group.api.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_ingress_rule" "db_from_job" {
  security_group_id            = aws_security_group.db.id
  description                  = "Postgres from the job tasks"
  referenced_security_group_id = aws_security_group.job.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

# --- Umami (umami.tf): port 3000 from the load balancer, Postgres, HTTPS out ---
#
#   CloudFront (umami distribution) --80--> internal ALB --3000--> Umami task --5432--> RDS
#   Umami task --443--> the internet (ghcr.io image pull, Secrets Manager, CloudWatch)

resource "aws_vpc_security_group_egress_rule" "alb_to_umami" {
  security_group_id            = aws_security_group.alb.id
  description                  = "To the Umami container"
  referenced_security_group_id = aws_security_group.umami.id
  ip_protocol                  = "tcp"
  from_port                    = 3000
  to_port                      = 3000
}

resource "aws_security_group" "umami" {
  name        = "${local.prefix}-umami"
  description = "Umami task: port 3000 from the load balancer only"
  vpc_id      = aws_vpc.main.id
}

resource "aws_vpc_security_group_ingress_rule" "umami_from_alb" {
  security_group_id            = aws_security_group.umami.id
  description                  = "Umami port from the load balancer"
  referenced_security_group_id = aws_security_group.alb.id
  ip_protocol                  = "tcp"
  from_port                    = 3000
  to_port                      = 3000
}

resource "aws_vpc_security_group_egress_rule" "umami_https" {
  security_group_id = aws_security_group.umami.id
  description       = "HTTPS to ghcr.io and AWS APIs"
  cidr_ipv4         = "0.0.0.0/0"
  ip_protocol       = "tcp"
  from_port         = 443
  to_port           = 443
}

resource "aws_vpc_security_group_egress_rule" "umami_to_db" {
  security_group_id            = aws_security_group.umami.id
  description                  = "Postgres"
  referenced_security_group_id = aws_security_group.db.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}

resource "aws_vpc_security_group_ingress_rule" "db_from_umami" {
  security_group_id            = aws_security_group.db.id
  description                  = "Postgres from the Umami task"
  referenced_security_group_id = aws_security_group.umami.id
  ip_protocol                  = "tcp"
  from_port                    = 5432
  to_port                      = 5432
}
