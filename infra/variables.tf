# Inputs of the main configuration. Override per run with -var, e.g.
# terraform apply -var instance_enabled=true (what infra-up does).

variable "region" {
  description = "AWS region for the instance, S3, SSM and IAM. CloudFront is global and ignores it."
  type        = string
  default     = "us-east-1"
}

variable "instance_enabled" {
  description = "Whether the EC2 instance exists. false (the default, so a bare apply never starts billing) destroys only the instance; CloudFront, IAM, S3 and SSM stay, and the URL answers 503 \"offline\". infra-up passes true."
  type        = bool
  default     = false
}

variable "instance_type" {
  description = "EC2 instance type. Must be eligible for the AWS Free plan, or AWS refuses to launch it."
  type        = string
  default     = "t3.small"
}

variable "github_repo" {
  description = "GitHub repository as owner/name: the boot script clones it."
  type        = string
  default     = "hsan512/linux-ticket-agent"
}

variable "github_oidc_subject_prefix" {
  description = "Start of the sub claim in this repository's GitHub OIDC tokens. The repository uses immutable subjects, which add the owner's and the repository's numeric IDs, so a renamed or re-created repository with the same name cannot match. Read it with: gh api repos/OWNER/REPO/actions/oidc/customization/sub (sub_claim_prefix)."
  type        = string
  default     = "repo:hsan512@167156700/linux-ticket-agent@1451351959"
}
