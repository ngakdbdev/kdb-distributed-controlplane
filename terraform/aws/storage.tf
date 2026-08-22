# storage.tf - the two standard StorageClasses every install gets (gp3
# default, io2 performance-tier), plus the optional FSx for Lustre
# filesystem for TickHouses that need genuinely high, shared throughput -
# AWS's own equivalent of what KX's doc calls "AWS Managed Lustre" (same
# underlying service).
#
# IMPORTANT: this module does NOT install the FSx Lustre CSI driver itself
# (it's a Helm chart, not an EKS-managed addon like the EBS CSI driver
# below) - see versions.tf's comment on why mixing a helm/kubectl-apply
# step for a driver into the same apply that creates the cluster it needs
# to talk to is the wrong pattern here. README.md has the exact post-apply
# command; this module gets you the filesystem + the IAM policy the driver
# needs, ready for that one manual step.

# ---------------------------------------------------------- EBS CSI driver
resource "aws_iam_role" "ebs_csi" {
  name = "${local.name}-ebs-csi"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [{
      Effect = "Allow"
      Principal = {
        Federated = aws_iam_openid_connect_provider.eks.arn
      }
      Action = "sts:AssumeRoleWithWebIdentity"
      Condition = {
        StringEquals = {
          "${replace(aws_iam_openid_connect_provider.eks.url, "https://", "")}:sub" = "system:serviceaccount:kube-system:ebs-csi-controller-sa"
          "${replace(aws_iam_openid_connect_provider.eks.url, "https://", "")}:aud" = "sts.amazonaws.com"
        }
      }
    }]
  })

  tags = local.common_tags
}

resource "aws_iam_role_policy_attachment" "ebs_csi" {
  role       = aws_iam_role.ebs_csi.name
  policy_arn = "arn:aws:iam::aws:policy/service-role/AmazonEBSCSIDriverPolicy"
}

resource "aws_eks_addon" "ebs_csi" {
  cluster_name                = aws_eks_cluster.this.name
  addon_name                  = "aws-ebs-csi-driver"
  service_account_role_arn    = aws_iam_role.ebs_csi.arn
  resolve_conflicts_on_update = "OVERWRITE"

  depends_on = [aws_eks_node_group.default]
}

# ------------------------------------------------------------ StorageClasses
# Both always exist regardless of storage_tier - that variable only picks
# which one is the cluster-wide DEFAULT (global.storageClassName blank in
# values.yaml falls back to whichever is marked default here). A specific
# TickHouse that needs io2 for one shard's rdb/wdb can still request
# "io2-performance" explicitly via the chart's per-component override even
# if the cluster default is gp3, and vice versa.
resource "kubernetes_storage_class_v1" "gp3" {
  metadata {
    name = "gp3"
    annotations = var.storage_tier == "standard" ? {
      "storageclass.kubernetes.io/is-default-class" = "true"
    } : {}
  }
  storage_provisioner    = "ebs.csi.aws.com"
  reclaim_policy         = "Retain" # hdb/db PVCs hold real trading history - never auto-delete on a scale-down/uninstall
  volume_binding_mode    = "WaitForFirstConsumer"
  allow_volume_expansion = true
  parameters = {
    type      = "gp3"
    encrypted = "true"
  }

  depends_on = [aws_eks_addon.ebs_csi]
}

resource "kubernetes_storage_class_v1" "io2_performance" {
  metadata {
    name = "io2-performance"
    annotations = var.storage_tier == "performance" ? {
      "storageclass.kubernetes.io/is-default-class" = "true"
    } : {}
  }
  storage_provisioner    = "ebs.csi.aws.com"
  reclaim_policy         = "Retain"
  volume_binding_mode    = "WaitForFirstConsumer"
  allow_volume_expansion = true
  parameters = {
    type      = "io2"
    iops      = "16000" # io2 max per-volume IOPS at reasonable size - see README.md if a specific TickHouse needs a different ratio
    encrypted = "true"
  }

  depends_on = [aws_eks_addon.ebs_csi]
}

# ------------------------------------------------------- FSx for Lustre
resource "aws_security_group" "fsx_lustre" {
  count = var.enable_high_performance_storage ? 1 : 0

  name_prefix = "${local.name}-fsx-lustre-"
  description = "Lustre client traffic from the EKS node group only"
  vpc_id      = local.vpc_id

  ingress {
    description     = "Lustre (988) + bulk data (1021-1023) from EKS nodes"
    from_port       = 988
    to_port         = 1023
    protocol        = "tcp"
    security_groups = [aws_eks_cluster.this.vpc_config[0].cluster_security_group_id]
  }

  egress {
    from_port   = 0
    to_port     = 0
    protocol    = "-1"
    cidr_blocks = ["0.0.0.0/0"]
  }

  tags = merge(local.common_tags, { Name = "${local.name}-fsx-lustre" })
}

resource "aws_fsx_lustre_file_system" "this" {
  count = var.enable_high_performance_storage ? 1 : 0

  storage_capacity            = var.lustre_storage_capacity_gib
  subnet_ids                  = [local.private_subnet_ids[0]] # PERSISTENT_2 is single-AZ; see README.md's HA note
  security_group_ids          = [aws_security_group.fsx_lustre[0].id]
  deployment_type             = "PERSISTENT_2"
  per_unit_storage_throughput = local.lustre_throughput_per_tib
  data_compression_type       = "LZ4"

  tags = merge(local.common_tags, {
    Name = "${local.name}-lustre"
    # Real per-TiB throughput this filesystem is sized for, at a glance -
    # matches KX's own doc's "1000 MBps/TiB for HA/Performance, 125
    # MBps/TiB for Cost-Optimised" framing.
    ThroughputMBpsPerTiB = tostring(local.lustre_throughput_per_tib)
  })
}
