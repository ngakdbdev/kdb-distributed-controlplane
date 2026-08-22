# eks.tf - the EKS control plane, its KMS-backed secrets encryption, a
# managed node group sized per cluster_profile, and the OIDC/IRSA plumbing
# the EBS CSI driver addon needs to assume an IAM role instead of using
# node-level credentials.

resource "aws_kms_key" "eks_secrets" {
  count = var.enable_secrets_encryption ? 1 : 0

  description             = "EKS secrets encryption for ${local.name} - encrypts Kubernetes Secrets at rest in etcd (matches KX's own \"Kubernetes secrets encryption\" toggle)."
  deletion_window_in_days = 30
  enable_key_rotation     = true
  tags                    = merge(local.common_tags, { Name = "${local.name}-eks-secrets" })
}

resource "aws_kms_alias" "eks_secrets" {
  count = var.enable_secrets_encryption ? 1 : 0

  name          = "alias/${local.name}-eks-secrets"
  target_key_id = aws_kms_key.eks_secrets[0].key_id
}

# ------------------------------------------------------------- cluster IAM
resource "aws_iam_role" "cluster" {
  name = "${local.name}-eks-cluster"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "eks.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "cluster_policy" {
  role       = aws_iam_role.cluster.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSClusterPolicy"
}

# --------------------------------------------------------------- cluster
resource "aws_eks_cluster" "this" {
  name     = local.name
  role_arn = aws_iam_role.cluster.arn
  version  = var.kubernetes_version

  vpc_config {
    subnet_ids              = concat(local.private_subnet_ids, local.public_subnet_ids)
    endpoint_private_access = true
    endpoint_public_access  = true
    public_access_cidrs     = var.eks_api_access_cidrs
  }

  # Same "control plane logging" KX's own doc calls out - all 5 EKS log
  # types, retained via aws_cloudwatch_log_group below rather than the
  # default (unmanaged, 90-day-per-KX-doc) group EKS creates on its own.
  enabled_cluster_log_types = ["api", "audit", "authenticator", "controllerManager", "scheduler"]

  dynamic "encryption_config" {
    for_each = var.enable_secrets_encryption ? [1] : []
    content {
      resources = ["secrets"]
      provider {
        key_arn = aws_kms_key.eks_secrets[0].arn
      }
    }
  }

  tags = local.common_tags

  depends_on = [
    aws_iam_role_policy_attachment.cluster_policy,
    aws_cloudwatch_log_group.eks,
  ]
}

# Pre-created explicitly (rather than left to EKS's own default, unmanaged
# group) so retention is actually enforced - EKS's own control-plane log
# group has no retention limit unless you manage it, per KX's doc noting
# their own 90-day policy for the same reason.
resource "aws_cloudwatch_log_group" "eks" {
  name              = "/aws/eks/${local.name}/cluster"
  retention_in_days = 90
  tags              = local.common_tags
}

resource "aws_iam_openid_connect_provider" "eks" {
  url             = aws_eks_cluster.this.identity[0].oidc[0].issuer
  client_id_list  = ["sts.amazonaws.com"]
  thumbprint_list = [data.tls_certificate.eks_oidc.certificates[0].sha1_fingerprint]
  tags            = local.common_tags
}

data "tls_certificate" "eks_oidc" {
  url = aws_eks_cluster.this.identity[0].oidc[0].issuer
}

# ----------------------------------------------------------- node group IAM
resource "aws_iam_role" "node_group" {
  name = "${local.name}-eks-nodes"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect    = "Allow"
      Principal = { Service = "ec2.amazonaws.com" }
      Action    = "sts:AssumeRole"
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "node_worker" {
  role       = aws_iam_role.node_group.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKSWorkerNodePolicy"
}

resource "aws_iam_role_policy_attachment" "node_cni" {
  role       = aws_iam_role.node_group.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEKS_CNI_Policy"
}

resource "aws_iam_role_policy_attachment" "node_ecr" {
  role       = aws_iam_role.node_group.name
  policy_arn = "arn:aws:iam::aws:policy/AmazonEC2ContainerRegistryReadOnly"
}

# -------------------------------------------------------------- node group
# Deliberately one managed node group per profile, not a fleet of pools -
# this chart's data plane scales on shardCount (see helm/kdb-control-plane/
# values.yaml's own comment), not node count/type diversity, so there's no
# real need for e.g. a separate spot pool or a second instance family here.
# If a specific TickHouse genuinely needs a different node shape, add a
# second aws_eks_node_group block and target it with the chart's own
# nodeSelectors (values.yaml) rather than complicating this one.
resource "aws_eks_node_group" "default" {
  cluster_name    = aws_eks_cluster.this.name
  node_group_name = "default"
  node_role_arn   = aws_iam_role.node_group.arn
  subnet_ids      = local.private_subnet_ids

  instance_types = local.node_instance_types

  scaling_config {
    min_size     = local.node_min_size
    max_size     = local.node_max_size
    desired_size = local.node_desired_size
  }

  update_config {
    max_unavailable = 1
  }

  disk_size = 100

  labels = {
    "vantik.io/cluster-profile" = var.cluster_profile
  }

  tags = local.common_tags

  depends_on = [
    aws_iam_role_policy_attachment.node_worker,
    aws_iam_role_policy_attachment.node_cni,
    aws_iam_role_policy_attachment.node_ecr,
  ]

  lifecycle {
    ignore_changes = [scaling_config[0].desired_size] # let the cluster autoscaler/HPA own this after first apply
  }
}
