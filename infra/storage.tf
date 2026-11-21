# Data that must outlive the instance, and the origin secret.
#
# The data bucket holds what infra-down would otherwise destroy with the instance:
#   qdrant_storage/  the retrieval index (~0.8 GB), uploaded once by hand
#   handoff/         the hand-off queue, backed up by infra-down, restored at boot
# Prometheus and Grafana history is not kept (acceptable for a demo).
#
# OPENAI_API_KEY and GRAFANA_ADMIN_PASSWORD are put in SSM by hand and never pass
# through Terraform, so they stay out of the state and survive teardown.

# account ID, used in bucket names and ARNs across all files of this configuration
data "aws_caller_identity" "current" {}

# not counted by instance_enabled, so infra-down keeps it; prevent_destroy guards
# against a full `terraform destroy`
resource "aws_s3_bucket" "data" {
  bucket = format("ticket-agent-data-%s", data.aws_caller_identity.current.account_id)

  lifecycle {
    prevent_destroy = true
  }
}

resource "aws_s3_bucket_server_side_encryption_configuration" "data_encryption" {
  bucket = aws_s3_bucket.data.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# if a bad backup overwrites handoff/, the previous version can be restored
resource "aws_s3_bucket_versioning" "data_versioning" {
  bucket = aws_s3_bucket.data.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "data_public_access_block" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

# ORIGIN_SECRET: CloudFront sends it as X-Origin-Verify (cdn.tf) and the API compares
# it (api/main.py, verify_origin), so only our distribution gets in. Letters and digits
# only: deploy.sh rejects quotes, and symbols are fragile in headers and .env files;
# 48 of them are about 285 bits. It sits in the state in plain text, unavoidably (the
# CloudFront header is managed here too), hence the private, encrypted state bucket.
resource "random_password" "password" {
  length  = 48
  special = false
}

# read by deploy.sh on the instance and written to .env for the API
resource "aws_ssm_parameter" "origin_secret" {
  name  = "/ticket-agent/ORIGIN_SECRET"
  type  = "SecureString"
  value = random_password.password.result
}
