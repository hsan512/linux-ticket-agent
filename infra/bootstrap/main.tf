# The S3 bucket that holds the main configuration's state (infra/). A separate
# configuration because the main one needs the bucket before it can store state in it.
# Applied once, by hand: cd infra/bootstrap && terraform apply. Its own state stays in
# a local file (gitignored): it tracks one bucket and no secrets, and if it is lost the
# bucket stays (prevent_destroy) and can be imported again.

terraform {
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
  }
}

provider "aws" {
  region = "us-east-1"
}

data "aws_caller_identity" "current" {}

# Bucket names are global across all AWS accounts; the account ID keeps this one free.
# prevent_destroy: deleting it would make Terraform forget everything in infra/.
resource "aws_s3_bucket" "tfstate_s3" {
  bucket = format("ticket-agent-tfstate-%s", data.aws_caller_identity.current.account_id)

  lifecycle {
    prevent_destroy = true
  }
}

# The state holds ORIGIN_SECRET in plain text, so it is encrypted at rest (SSE-S3)...
resource "aws_s3_bucket_server_side_encryption_configuration" "tfstate_s3_encryption" {
  bucket = aws_s3_bucket.tfstate_s3.id

  rule {
    apply_server_side_encryption_by_default {
      sse_algorithm = "AES256"
    }
  }
}

# ...versioned, so a state broken by a failed apply can be restored...
resource "aws_s3_bucket_versioning" "tfstate_s3_versioning" {
  bucket = aws_s3_bucket.tfstate_s3.id

  versioning_configuration {
    status = "Enabled"
  }
}

# ...and can never be made public, even by a later mistaken policy or ACL.
resource "aws_s3_bucket_public_access_block" "tfstate_s3_public_access_block" {
  bucket = aws_s3_bucket.tfstate_s3.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}