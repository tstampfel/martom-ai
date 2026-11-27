# CloudFront gives the API a free HTTPS address (https://<id>.cloudfront.net) and sits in
# front of the ALB.
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

locals {
  alb_origin_id = "alb"
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

    custom_origin_config {
      http_port              = 80
      https_port             = 443
      origin_protocol_policy = "http-only" # the ALB has no certificate (no custom domain)
      origin_ssl_protocols   = ["TLSv1.2"]
      # Longest wait for the next bytes from the API without a quota increase. A streamed
      # answer must send something (a token or a keep-alive comment) at least every 60 s.
      origin_read_timeout      = 60
      origin_keepalive_timeout = 60
    }

    # The ALB forwards only requests that carry this header (alb.tf).
    custom_header {
      name  = local.origin_verify_header
      value = random_password.origin_verify.result
    }
  }

  default_cache_behavior {
    target_origin_id       = local.alb_origin_id
    viewer_protocol_policy = "https-only" # a redirect would turn POST /api/chat into a GET
    allowed_methods        = ["GET", "HEAD", "OPTIONS", "PUT", "POST", "PATCH", "DELETE"]
    cached_methods         = ["GET", "HEAD"]
    compress               = false

    cache_policy_id          = data.aws_cloudfront_cache_policy.caching_disabled.id
    origin_request_policy_id = data.aws_cloudfront_origin_request_policy.all_viewer_except_host.id
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # The free *.cloudfront.net certificate. A custom domain later means an ACM certificate
  # in us-east-1 plus an alias here.
  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
