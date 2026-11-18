# Settings for Terraform itself, read by `terraform init` before any resource: the
# Terraform version, the providers to download (pinned in .terraform.lock.hcl), and
# where the state lives. Run everything in infra/ with AWS_PROFILE=ticket-agent.

terraform {
  # 1.10: first version with S3-native state locking (use_lockfile)
  required_version = ">= 1.10"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 6.0"
    }
    # generates ORIGIN_SECRET (storage.tf)
    random = {
      source  = "hashicorp/random"
      version = "~> 3.0"
    }
  }

  # Remote state in the bucket from infra/bootstrap, shared by this machine and the
  # GitHub workflows. Backend settings cannot use variables, hence the literals.
  # use_lockfile: S3-native lock, so two applies cannot run at once.
  backend "s3" {
    bucket       = "ticket-agent-tfstate-103888845841"
    key          = "ticket-agent/terraform.tfstate"
    region       = "us-east-1"
    encrypt      = true
    use_lockfile = true
  }

}

provider "aws" {
  region = var.region

  # on every resource: cost tracking per app, and the deploy role only acts on
  # instances tagged App=ticket-agent. No profile here: AWS_PROFILE locally,
  # OIDC credentials in GitHub Actions.
  default_tags {
    tags = {
      App       = "ticket-agent"
      ManagedBy = "terraform"
    }
  }
}