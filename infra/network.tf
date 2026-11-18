# Who can reach the instance: only CloudFront, on the API port. No SSH (shell access
# goes through SSM Session Manager). The default VPC is enough for one instance.

data "aws_vpc" "default" {
  default = true
}

# the default subnets, only in zones that offer the instance type (compute.tf)
data "aws_subnets" "default" {
  filter {
    name   = "vpc-id"
    values = [data.aws_vpc.default.id]
  }
  filter {
    name   = "availability-zone"
    values = data.aws_ec2_instance_type_offerings.available.locations
  }
}

# AWS-maintained list of the addresses CloudFront uses to reach origins. It counts as
# about 55 of the 60 rules a security group may hold, so it gets no siblings here.
data "aws_ec2_managed_prefix_list" "cloudfront" {
  name = "com.amazonaws.global.cloudfront.origin-facing"
}

resource "aws_security_group" "app" {
  name        = "ticket-agent"
  description = "ticket-agent API: port 8000 from CloudFront only"
  vpc_id      = data.aws_vpc.default.id
}

# Every CloudFront customer shares these addresses; the X-Origin-Verify secret
# (api/main.py, verify_origin) proves the request came through our distribution.
resource "aws_vpc_security_group_ingress_rule" "from_cloudfront" {
  description       = "API from CloudFront"
  security_group_id = aws_security_group.app.id
  ip_protocol       = "tcp"
  from_port         = 8000
  to_port           = 8000
  prefix_list_id    = data.aws_ec2_managed_prefix_list.cloudfront.id
}

# Terraform removes AWS's default allow-all outbound rule, so it is added back: the
# instance must reach OpenAI, GHCR, GitHub, S3 and SSM.
resource "aws_vpc_security_group_egress_rule" "all" {
  description       = "All outbound"
  security_group_id = aws_security_group.app.id
  ip_protocol       = "-1"
  cidr_ipv4         = "0.0.0.0/0"
}
