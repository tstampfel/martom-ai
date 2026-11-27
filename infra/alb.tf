# Application Load Balancer in front of the API tasks.
#
# There is no custom domain, so there is no TLS certificate for the ALB: CloudFront ends
# HTTPS at its *.cloudfront.net address and talks plain HTTP to the ALB. Two guards keep
# everyone else out:
#   1. the ALB security group accepts only CloudFront's origin-facing addresses, and
#   2. the listener forwards only requests carrying a secret header that CloudFront adds,
#      so another CloudFront distribution (someone else's) can't use our ALB either.

resource "random_password" "origin_verify" {
  length  = 48
  special = false
}

locals {
  origin_verify_header = "X-Origin-Verify"
}

resource "aws_lb" "api" {
  name               = "${local.prefix}-api"
  load_balancer_type = "application"
  internal           = false
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.public[*].id

  # SSE answers stay open while the agent works. CloudFront closes an origin connection
  # after 60 s without bytes (origin_read_timeout); the ALB must wait longer than that.
  idle_timeout               = 120
  drop_invalid_header_fields = true
  enable_deletion_protection = false
}

resource "aws_lb_target_group" "api" {
  name        = "${local.prefix}-api"
  port        = 8000
  protocol    = "HTTP"
  target_type = "ip" # Fargate tasks register by IP (awsvpc networking)
  vpc_id      = aws_vpc.main.id

  # Give in-flight requests 30 s to finish when a task is replaced during a deploy.
  deregistration_delay = 30

  # GET /health answers 503 when Postgres or Redis is down (src/martom/api/health.py).
  health_check {
    path                = "/health"
    matcher             = "200"
    interval            = 30
    timeout             = 5
    healthy_threshold   = 2
    unhealthy_threshold = 3
  }
}

resource "aws_lb_listener" "http" {
  load_balancer_arn = aws_lb.api.arn
  port              = 80
  protocol          = "HTTP"

  # Anything without the CloudFront header gets a 403.
  default_action {
    type = "fixed-response"

    fixed_response {
      content_type = "text/plain"
      message_body = "Forbidden"
      status_code  = "403"
    }
  }
}

resource "aws_lb_listener_rule" "from_cloudfront" {
  listener_arn = aws_lb_listener.http.arn
  priority     = 10

  condition {
    http_header {
      http_header_name = local.origin_verify_header
      values           = [random_password.origin_verify.result]
    }
  }

  action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}
