# Printed after apply (terraform output). The role ARNs go into GitHub as secrets for
# the workflows; url is the public address of the demo.

output "url" {
  description = "Public HTTPS address of the API; stays the same across infra-down and infra-up"
  value       = "https://${aws_cloudfront_distribution.api.domain_name}"
}

output "distribution_id" {
  description = "CloudFront distribution ID, for invalidations or the console"
  value       = aws_cloudfront_distribution.api.id
}

output "instance_id" {
  description = "EC2 instance ID (null while instance_enabled is false); used for SSM sessions and Run Command"
  value       = try(aws_instance.app[0].id, null)
}

output "data_bucket" {
  description = "S3 bucket for the Qdrant index (qdrant_storage/) and hand-off backups (handoff/)"
  value       = aws_s3_bucket.data.bucket
}

output "deploy_role_arn" {
  description = "Role for the CD workflow (GitHub environment production)"
  value       = aws_iam_role.gh_deploy.arn
}

output "infra_role_arn" {
  description = "Role for the infra-up and infra-down workflows (GitHub environment infra)"
  value       = aws_iam_role.gh_infra.arn
}
