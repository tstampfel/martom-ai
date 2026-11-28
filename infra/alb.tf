# Application Load Balancer in front of the API tasks.
#
# The ALB is *internal*: it sits in the private subnets with private addresses only, so
# nothing on the internet can reach it. CloudFront reaches it through a VPC origin
# (cloudfront.tf), which sends origin traffic over the AWS network into the VPC instead of
# across the public internet. That also means the ALB needs no public IPv4 addresses
# (~$7/month saved) and no shared-secret header to tell CloudFront apart from everyone else.
#
# The CloudFront → ALB leg is HTTP: there is no custom domain, so there is no certificate
# for the ALB. It runs only inside AWS's network (VPC origin → private ALB address); the
# viewer → CloudFront leg is HTTPS.

resource "aws_lb" "api" {
  name               = "${local.prefix}-api"
  load_balancer_type = "application"
  internal           = true
  security_groups    = [aws_security_group.alb.id]
  subnets            = aws_subnet.private[*].id

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

  # GET /health answers 503 when Postgres is down (src/martom/api/health.py). A Redis
  # that is down answers 200 "degraded": Redis is only the search cache (M3-5), so losing
  # it must not take the task out of the target group.
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

  default_action {
    type             = "forward"
    target_group_arn = aws_lb_target_group.api.arn
  }
}
