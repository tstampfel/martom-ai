# CloudFront gives the API a free HTTPS address (https://<id>.cloudfront.net) and reaches
# the internal ALB through a VPC origin (alb.tf explains why).
#
# Nothing is cached: every path, including POST /api/chat, uses the managed
# "CachingDisabled" policy, which also turns compression off, so CloudFront passes each
# Server-Sent Events chunk on as soon as the API writes it instead of buffering it.
# The managed "AllViewerExceptHostHeader" policy forwards every header (Authorization for
# /admin, Origin for CORS), cookie and query string; the Host header is left out so the
# ALB sees its own name.

data "aws_cloudfront_cache_policy" "caching_disabled" {
  name = "Managed-CachingDisabled"
}

data "aws_cloudfront_origin_request_policy" "all_viewer_except_host" {
  name = "Managed-AllViewerExceptHostHeader"
}

# Adds HSTS, X-Content-Type-Options, X-Frame-Options, Referrer-Policy and X-XSS-Protection
# to every response. It sets no CORS headers, so the API's own CORS answers pass unchanged.
data "aws_cloudfront_response_headers_policy" "security_headers" {
  name = "Managed-SecurityHeadersPolicy"
}

locals {
  alb_origin_id = "alb"
}

# Lets CloudFront connect to the internal ALB from inside the VPC. AWS creates a
# service-managed security group for it; the ALB security group allows the CloudFront
# origin-facing prefix list (security_groups.tf), which covers that traffic too.
resource "aws_cloudfront_vpc_origin" "alb" {
  vpc_origin_endpoint_config {
    name                   = "${local.prefix}-alb"
    arn                    = aws_lb.api.arn
    http_port              = 80
    https_port             = 443
    origin_protocol_policy = "http-only" # the ALB has no certificate (no custom domain)

    origin_ssl_protocols {
      items    = ["TLSv1.2"]
      quantity = 1
    }
  }
}

resource "aws_cloudfront_distribution" "api" {
  enabled         = true
  comment         = "${local.prefix} API"
  is_ipv6_enabled = true
  # Edge servers in Europe and North America only: the cheapest price class.
  price_class = "PriceClass_100"

  origin {
    origin_id   = local.alb_origin_id
    domain_name = aws_lb.api.dns_name

    vpc_origin_config {
      vpc_origin_id = aws_cloudfront_vpc_origin.alb.id
      # Longest wait for the next bytes from the API without a quota increase. A streamed
      # answer must send something (a token or a keep-alive comment) at least every 60 s.
      origin_read_timeout      = 60
      origin_keepalive_timeout = 60
    }
  }

  default_cache_behavior {
    target_origin_id       = local.alb_origin_id
    viewer_protocol_policy = "https-only" # a redirect would turn POST /api/chat into a GET
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    compress               = false

    cache_policy_id            = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id   = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
    response_headers_policy_id = data.aws_cloudfront_response_headers_policy.security_headers.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # The free *.cloudfront.net certificate. A custom domain later means an ACM certificate
  # in us-east-1 plus an alias here (and could add an HTTPS listener on the ALB).
  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
