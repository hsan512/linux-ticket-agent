# Who may do what. Three identities:
#   - the EC2 instance: read its secrets and data, back up the hand-off queue
#   - GitHub CD (gh-deploy): run deploy.sh on the instance, record the release, upload
#     the page
#   - GitHub infra-up/down (gh-infra): run Terraform
# GitHub workflows log in with short-lived OIDC tokens, so no AWS keys are stored in
# GitHub. Policies are built with aws_iam_policy_document (checked by Terraform)
# rather than JSON strings.

locals {
  account_id  = data.aws_caller_identity.current.account_id
  github_oidc = "token.actions.githubusercontent.com"
}

# --- the instance role ---------------------------------------------------------

data "aws_iam_policy_document" "ec2_assume" {
  statement {
    actions = ["sts:AssumeRole"]
    principals {
      type        = "Service"
      identifiers = ["ec2.amazonaws.com"]
    }
  }
}

resource "aws_iam_role" "instance" {
  name               = "ticket-agent-instance"
  description        = "The ticket-agent EC2 instance: SSM access, its secrets, the data bucket"
  assume_role_policy = data.aws_iam_policy_document.ec2_assume.json
}

# SSM Session Manager (shell without SSH) and Run Command (how CD reaches deploy.sh)
resource "aws_iam_role_policy_attachment" "ssm_core" {
  role       = aws_iam_role.instance.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonSSMManagedInstanceCore"
}

data "aws_iam_policy_document" "instance" {
  # deploy.sh reads OPENAI_API_KEY, GRAFANA_ADMIN_PASSWORD, ORIGIN_SECRET. SecureStrings
  # use the AWS-managed aws/ssm key, which needs no kms:Decrypt grant here.
  statement {
    sid       = "ReadSecrets"
    actions   = ["ssm:GetParameter"]
    resources = ["arn:aws:ssm:${var.region}:${local.account_id}:parameter/ticket-agent/*"]
  }

  # the boot script downloads the Qdrant index and the last hand-off backup
  statement {
    sid       = "ListDataBucket"
    actions   = ["s3:ListBucket"]
    resources = [aws_s3_bucket.data.arn]
  }
  statement {
    sid       = "ReadData"
    actions   = ["s3:GetObject"]
    resources = ["${aws_s3_bucket.data.arn}/*"]
  }

  # infra-down backs up the hand-off queue; the instance may write nowhere else
  statement {
    sid       = "BackUpHandoff"
    actions   = ["s3:PutObject"]
    resources = ["${aws_s3_bucket.data.arn}/handoff/*"]
  }
}

resource "aws_iam_role_policy" "instance" {
  name   = "ticket-agent-instance"
  role   = aws_iam_role.instance.id
  policy = data.aws_iam_policy_document.instance.json
}

# EC2 attaches a role to an instance through an instance profile
resource "aws_iam_instance_profile" "instance" {
  name = "ticket-agent-instance"
  role = aws_iam_role.instance.name
}

# --- GitHub OIDC ---------------------------------------------------------------

# Lets AWS trust tokens that GitHub Actions issues to a running workflow. AWS checks
# GitHub's certificate itself, so no thumbprint is needed.
resource "aws_iam_openid_connect_provider" "github" {
  url            = "https://${local.github_oidc}"
  client_id_list = ["sts.amazonaws.com"]
}

# Trust for a role that only workflows of this repository, running in the given
# GitHub environment, may assume. The token's "sub" claim names both, with numeric
# IDs (immutable subjects), e.g. repo:owner@123/name@456:environment:production.
data "aws_iam_policy_document" "github_assume" {
  for_each = toset(["production", "infra"])

  statement {
    actions = ["sts:AssumeRoleWithWebIdentity"]
    principals {
      type        = "Federated"
      identifiers = [aws_iam_openid_connect_provider.github.arn]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc}:aud"
      values   = ["sts.amazonaws.com"]
    }
    condition {
      test     = "StringEquals"
      variable = "${local.github_oidc}:sub"
      values   = ["${var.github_oidc_subject_prefix}:environment:${each.key}"]
    }
  }
}

# --- gh-deploy: CD -------------------------------------------------------------

resource "aws_iam_role" "gh_deploy" {
  name                 = "ticket-agent-gh-deploy"
  description          = "GitHub CD (environment production): deploy.sh, RELEASE_SHA, page upload"
  assume_role_policy   = data.aws_iam_policy_document.github_assume["production"].json
  max_session_duration = 3600
}

data "aws_iam_policy_document" "gh_deploy" {
  # SendCommand is checked against both the document and the instance, so each gets
  # its own statement: only AWS-RunShellScript, only on our tagged instance.
  statement {
    sid       = "RunShellScriptDocument"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ssm:${var.region}::document/AWS-RunShellScript"]
  }
  statement {
    sid       = "OnTicketAgentInstance"
    actions   = ["ssm:SendCommand"]
    resources = ["arn:aws:ec2:${var.region}:${local.account_id}:instance/*"]
    condition {
      test     = "StringEquals"
      variable = "aws:ResourceTag/App"
      values   = ["ticket-agent"]
    }
  }

  # CD records the release that passed CI; the boot script deploys it
  statement {
    sid       = "RecordRelease"
    actions   = ["ssm:PutParameter"]
    resources = ["arn:aws:ssm:${var.region}:${local.account_id}:parameter/ticket-agent/RELEASE_SHA"]
  }

  # CD uploads the page and makes CloudFront serve the new files at once
  statement {
    sid       = "UploadPage"
    actions   = ["s3:PutObject", "s3:DeleteObject"]
    resources = ["${aws_s3_bucket.site.arn}/*"]
  }
  statement {
    sid       = "ListPage"
    actions   = ["s3:ListBucket"] # aws s3 sync --delete compares with what is there
    resources = [aws_s3_bucket.site.arn]
  }
  statement {
    sid       = "InvalidatePage"
    actions   = ["cloudfront:CreateInvalidation"]
    resources = [aws_cloudfront_distribution.api.arn]
  }

  # find the instance and read the command's result; these accept no narrower resource
  statement {
    sid       = "FindInstanceAndReadResult"
    actions   = ["ec2:DescribeInstances", "ssm:GetCommandInvocation"]
    resources = ["*"]
  }
}

resource "aws_iam_role_policy" "gh_deploy" {
  name   = "ticket-agent-gh-deploy"
  role   = aws_iam_role.gh_deploy.id
  policy = data.aws_iam_policy_document.gh_deploy.json
}

# --- gh-infra: infra-up / infra-down -------------------------------------------

# Terraform here creates IAM roles, CloudFront, EC2, S3 and SSM, so a narrow policy
# would be long and break with every new resource. AdministratorAccess is limited
# instead by who can assume the role: only this repository's workflows in the GitHub
# environment "infra", which can require a manual approval.
resource "aws_iam_role" "gh_infra" {
  name                 = "ticket-agent-gh-infra"
  description          = "GitHub infra-up/infra-down (environment infra): run Terraform"
  assume_role_policy   = data.aws_iam_policy_document.github_assume["infra"].json
  max_session_duration = 3600
}

resource "aws_iam_role_policy_attachment" "gh_infra_admin" {
  role       = aws_iam_role.gh_infra.name
  policy_arn = "arn:aws:iam::aws:policy/AdministratorAccess"
}
