# The EC2 instance that runs the compose stack. It exists only while instance_enabled
# is true (infra-up); infra-down destroys it and nothing else. Everything it needs on
# first boot comes from the boot script (boot.sh.tftpl), SSM and the data bucket, so
# a rebuilt instance needs no manual setup.

# Canonical publishes the newest Ubuntu 24.04 AMI ID here (amd64 for t3)
data "aws_ssm_parameter" "ubuntu_ami" {
  name = "/aws/service/canonical/ubuntu/server/24.04/stable/current/amd64/hvm/ebs-gp3/ami-id"
}

# Availability zones that offer the instance type (us-east-1e has no t3); network.tf
# limits the default subnets to these
data "aws_ec2_instance_type_offerings" "available" {
  location_type = "availability-zone"
  filter {
    name   = "instance-type"
    values = [var.instance_type]
  }
}

resource "aws_instance" "app" {
  count = var.instance_enabled ? 1 : 0

  # .value is marked sensitive and would hide the (public) AMI ID in every plan
  ami           = data.aws_ssm_parameter.ubuntu_ami.insecure_value
  instance_type = var.instance_type
  # sorted, so the choice is the same on every run
  subnet_id                   = sort(data.aws_subnets.default.ids)[0]
  vpc_security_group_ids      = [aws_security_group.app.id]
  iam_instance_profile        = aws_iam_instance_profile.instance.name
  associate_public_ip_address = true # CloudFront reaches the origin over the internet

  # images (~3 GB), the Qdrant index (~0.8 GB) and logs; goes with the instance
  root_block_device {
    volume_size           = 20
    volume_type           = "gp3"
    encrypted             = true
    delete_on_termination = true
  }

  # IMDSv2 only. Hop limit 1: containers cannot reach the metadata service, so a
  # compromised container cannot take the instance role's credentials. deploy.sh runs
  # on the host and still can.
  metadata_options {
    http_tokens                 = "required"
    http_put_response_hop_limit = 1
  }

  # t3 defaults to "unlimited", which bills extra for sustained high CPU; "standard"
  # throttles instead, so there are no surprise charges
  credit_specification {
    cpu_credits = "standard"
  }

  # runs once, on first boot: installs Docker, clones the repo, fetches the data,
  # runs deploy.sh. A changed script means a new instance, since it would not run
  # again on the old one.
  user_data = templatefile("${path.module}/boot.sh.tftpl", {
    data_bucket = aws_s3_bucket.data.bucket
    github_repo = var.github_repo
    region      = var.region
  })
  user_data_replace_on_change = true

  # App=ticket-agent comes from default_tags (versions.tf); gh-deploy relies on it
  tags = {
    Name = "ticket-agent"
  }

  # a new Ubuntu release would otherwise replace the server on the next apply
  lifecycle {
    ignore_changes = [ami]
  }
}
