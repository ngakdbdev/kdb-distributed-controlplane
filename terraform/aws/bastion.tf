# bastion.tf - a small jump host in the public subnet, matching KX's own
# module (which includes one for exactly the same reason: private-subnet
# resources - here, the EKS nodes and, if enabled, the FSx Lustre
# filesystem - aren't reachable directly from outside the VPC, and a
# bastion is a smaller attack surface than opening node/API ports directly
# to bastion_access_cidr). Not required for kubectl/helm itself (the EKS
# API's public endpoint, restricted to eks_api_access_cidrs in eks.tf,
# covers that) - this is for reaching things that ONLY listen inside the
# VPC, e.g. mounting the Lustre filesystem directly to inspect it.

data "aws_ami" "bastion" {
  most_recent = true
  owners      = ["099720109477"] # Canonical

  filter {
    name   = "name"
    values = ["ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*"]
  }
  filter {
    name   = "architecture"
    values = ["x86_64"]
  }
}

resource "aws_security_group" "bastion" {
  name_prefix = "${local.name}-bastion-"
  description = "SSH from bastion_access_cidr only"
  vpc_id      = local.vpc_id

  ingress {
    description = "SSH"
    from_port   = 22
    to_port     = 22
    protocol    = "tcp"
    cidr_blocks = [var.bastion_access_cidr]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${local.name}-bastion" })
}

resource "aws_instance" "bastion" {
  ami                    = data.aws_ami.bastion.id
  instance_type          = "t3.micro" # a jump host, not a workload - no reason for anything bigger
  subnet_id              = local.public_subnet_ids[0]
  vpc_security_group_ids = [aws_security_group.bastion.id]

  root_block_device {
    volume_type = "gp3"
    volume_size = 20
    encrypted   = true
  }

  metadata_options {
    http_tokens = "required" # IMDSv2 only
  }

  tags = merge(local.common_tags, { Name = "${local.name}-bastion" })
}

resource "aws_eip" "bastion" {
  domain   = "vpc"
  instance = aws_instance.bastion.id
  tags     = merge(local.common_tags, { Name = "${local.name}-bastion" })
}
