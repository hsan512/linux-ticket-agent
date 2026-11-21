# The demo page (frontend/: index.html, style.css, app.js) in a private S3 bucket,
# served by the same CloudFront distribution as the API (cdn.tf). The page stays up
# while the instance is off: only the API paths answer "offline". The files come from
# git and are uploaded by CD (or `aws s3 sync`), not by Terraform.

resource "aws_s3_bucket" "site" {
  bucket = format("ticket-agent-site-%s", data.aws_caller_identity.current.account_id)
  # the content is a copy of frontend/ in git, so the bucket may be emptied on delete
  force_destroy = true
}

# Private: nobody reads the bucket directly, only CloudFront (policy below).
resource "aws_s3_bucket_public_access_block" "site" {
  bucket = aws_s3_bucket.site.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_s3_bucket_server_side_encryption_configuration" "site" {
  bucket = aws_s3_bucket.site.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# Origin Access Control: CloudFront signs its requests to S3 (SigV4), so the bucket
# can stay private and still be served.
resource "aws_cloudfront_origin_access_control" "site" {
  name                              = "ticket-agent-site"
  description                       = "CloudFront reads the private site bucket"
  origin_access_control_origin_type = "s3"
  signing_behavior                  = "always"
  signing_protocol                  = "sigv4"
}

# Read access for CloudFront, and only for our distribution: without the SourceArn
# condition, any CloudFront distribution in any account could read the bucket.
data "aws_iam_policy_document" "site" {
  statement {
    sid       = "CloudFrontReadsThePage"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]
    principals {
      type        = "Service"
      identifiers = ["cloudfront.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "AWS:SourceArn"
      values   = [aws_cloudfront_distribution.api.arn]
    }
  }
}

resource "aws_s3_bucket_policy" "site" {
  bucket = aws_s3_bucket.site.id
  policy = data.aws_iam_policy_document.site.json
  # the public access block must exist first, or AWS may reject the policy
  depends_on = [aws_s3_bucket_public_access_block.site]
}

# Security headers for the page only (default behaviour in cdn.tf), never for the
# API paths: Swagger UI at /docs loads its scripts from a CDN and would break. The CSP
# must equal deploy/nginx.local.conf and the page's meta tag
# (tests/test_nginx_local.py checks all three).
resource "aws_cloudfront_response_headers_policy" "page" {
  name    = "ticket-agent-page"
  comment = "CSP, no framing, nosniff, HSTS for the demo page"

  security_headers_config {
    content_security_policy {
      content_security_policy = "default-src 'none'; script-src 'self'; style-src 'self'; img-src 'self'; connect-src 'self'; base-uri 'none'; form-action 'self'; frame-ancestors 'none'"
      override                = true
    }
    content_type_options {
      override = true
    }
    frame_options {
      frame_option = "DENY"
      override     = true
    }
    referrer_policy {
      referrer_policy = "strict-origin-when-cross-origin"
      override        = true
    }
    strict_transport_security {
      access_control_max_age_sec = 31536000
      include_subdomains         = true
      override                   = true
    }
  }
}
