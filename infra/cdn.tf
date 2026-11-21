# CloudFront: the public HTTPS front door. Visitors reach https://<id>.cloudfront.net:
#   - the API paths go to the instance on port 8000 with the X-Origin-Verify secret,
#     uncached (every ticket is unique), through the edge function, which answers 503
#     while the instance is off;
#   - everything else is the demo page from the private S3 bucket (site.tf), cached,
#     with security headers. /metrics is not an API path, so it never reaches the API.
# The URL stays the same across infra-down and infra-up.

locals {
  # CloudFront requires an origin even when there is no instance. While offline, the
  # edge function answers every request first, so this placeholder is never contacted.
  origin_domain = var.instance_enabled ? aws_instance.app[0].public_dns : "example.com"

  # API paths, routed to the instance. Must match the edge function's allow-list
  # (edge.js.tftpl) and deploy/nginx.local.conf; tests/test_nginx_local.py checks it.
  api_paths = ["/tickets", "/tickets/*", "/health", "/docs", "/docs/*", "/openapi.json"]
}

# Runs at the edge on every request, before the origin (edge.js.tftpl).
resource "aws_cloudfront_function" "edge" {
  name    = "ticket-agent-edge"
  comment = "Blocks /metrics; answers 503 while the demo is offline"
  runtime = "cloudfront-js-2.0" # 2.0: generated responses may carry a body
  publish = true
  code = templatefile("${path.module}/edge.js.tftpl", {
    online = var.instance_enabled
  })
}

# AWS-managed policy: no caching, and no headers, cookies or query strings in a cache
# key. What reaches the origin is decided by the origin request policy below.
data "aws_cloudfront_cache_policy" "disabled" {
  name = "Managed-CachingDisabled"
}

# AWS-managed policy for static files: cached at the edge (up to a day by default);
# CD invalidates /* after uploading a new page.
data "aws_cloudfront_cache_policy" "optimized" {
  name = "Managed-CachingOptimized"
}

# What CloudFront forwards to the API. Only what it needs, so nothing unexpected from
# the viewer reaches the origin:
#   Content-Type               for POST /tickets bodies
#   CloudFront-Viewer-Address  set by CloudFront (the client cannot fake it); the
#                              rate-limit key in api/main.py, client_ip
# Query strings are forwarded (FastAPI may use them); cookies are not (none are used).
resource "aws_cloudfront_origin_request_policy" "api" {
  name    = "ticket-agent-api"
  comment = "ticket-agent API: Content-Type and the viewer's address, no cookies"

  headers_config {
    header_behavior = "whitelist"
    headers {
      items = ["Content-Type", "CloudFront-Viewer-Address"]
    }
  }

  cookies_config {
    cookie_behavior = "none"
  }

  query_strings_config {
    query_string_behavior = "all"
  }
}

resource "aws_cloudfront_distribution" "api" {
  enabled = true
  comment = "ticket-agent API and demo page"
  # https://<id>.cloudfront.net/ serves the page
  default_root_object = "index.html"
  is_ipv6_enabled     = true # client_ip handles IPv6 viewer addresses
  http_version        = "http2and3"
  # PriceClass_200 includes Asian edges (closer to Vietnam) and costs the same within
  # the always-free tier
  price_class = "PriceClass_200"

  origin {
    origin_id   = "api"
    domain_name = local.origin_domain

    custom_origin_config {
      http_port  = 8000
      https_port = 443 # required by the provider; unused with http-only
      # edge -> instance is plain HTTP: the instance has no certificate. Acceptable
      # for a demo with no user secrets; the fix would be a certificate on the origin
      # or a VPC origin.
      origin_protocol_policy = "http-only"
      origin_ssl_protocols   = ["TLSv1.2"]
      # the maximum without a quota increase; ticket p95 is about 10-30 s
      origin_read_timeout = 60
    }

    # proves to verify_origin (api/main.py) that the request came through this
    # distribution: the security group admits all CloudFront customers
    custom_header {
      name  = "X-Origin-Verify"
      value = random_password.password.result
    }
  }

  # the demo page: a private S3 bucket that only this distribution may read
  origin {
    origin_id                = "site"
    domain_name              = aws_s3_bucket.site.bucket_regional_domain_name
    origin_access_control_id = aws_cloudfront_origin_access_control.site.id
  }

  # everything that is not an API path: the page, cached, with security headers
  default_cache_behavior {
    target_origin_id           = "site"
    viewer_protocol_policy     = "redirect-to-https"
    allowed_methods            = ["GET", "HEAD"]
    cached_methods             = ["GET", "HEAD"]
    cache_policy_id            = data.aws_cloudfront_cache_policy.optimized.id
    response_headers_policy_id = aws_cloudfront_response_headers_policy.page.id
    compress                   = true
  }

  # the API, one behaviour per path pattern, all with the same settings
  dynamic "ordered_cache_behavior" {
    for_each = local.api_paths
    content {
      path_pattern     = ordered_cache_behavior.value
      target_origin_id = "api"
      # browsers opening http://.../docs are redirected. API clients should use
      # https: many clients repeat a redirected POST as a GET.
      viewer_protocol_policy   = "redirect-to-https"
      allowed_methods          = ["DELETE", "GET", "HEAD", "OPTIONS", "PATCH", "POST", "PUT"]
      cached_methods           = ["GET", "HEAD"] # required; nothing is cached anyway
      cache_policy_id          = data.aws_cloudfront_cache_policy.disabled.id
      origin_request_policy_id = aws_cloudfront_origin_request_policy.api.id
      compress                 = true

      function_association {
        event_type   = "viewer-request"
        function_arn = aws_cloudfront_function.edge.arn
      }
    }
  }

  restrictions {
    geo_restriction {
      restriction_type = "none"
    }
  }

  # the free *.cloudfront.net certificate; a custom domain would need an ACM
  # certificate in us-east-1 instead
  viewer_certificate {
    cloudfront_default_certificate = true
  }
}
