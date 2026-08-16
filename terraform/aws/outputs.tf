output "cluster_name" {
  description = "EKS cluster name - pass to `aws eks update-kubeconfig`."
  value       = aws_eks_cluster.this.name
}

output "cluster_endpoint" {
  description = "EKS API server endpoint."
  value       = aws_eks_cluster.this.endpoint
}

output "configure_kubectl" {
  description = "Run this to point kubectl/helm at the cluster this module created."
  value       = "aws eks update-kubeconfig --name ${aws_eks_cluster.this.name} --region ${var.region}"
}

output "vpc_id" {
  value = local.vpc_id
}

output "private_subnet_ids" {
  value = local.private_subnet_ids
}

output "public_subnet_ids" {
  value = local.public_subnet_ids
}

output "bastion_public_ip" {
  value = aws_eip.bastion.public_ip
}

output "default_storage_class" {
  value = var.storage_tier == "performance" ? "io2-performance" : "gp3"
}

output "eks_secrets_kms_key_arn" {
  description = "null when enable_secrets_encryption = false."
  value       = var.enable_secrets_encryption ? aws_kms_key.eks_secrets[0].arn : null
}

output "lustre_filesystem_id" {
  description = "null when enable_high_performance_storage = false. See README.md for the post-apply FSx Lustre CSI driver install + StorageClass this ID feeds into."
  value       = var.enable_high_performance_storage ? aws_fsx_lustre_file_system.this[0].id : null
}

output "lustre_mount_name" {
  description = "null when enable_high_performance_storage = false."
  value       = var.enable_high_performance_storage ? aws_fsx_lustre_file_system.this[0].mount_name : null
}

output "cluster_profile" {
  value = var.cluster_profile
}

output "node_group" {
  value = {
    instance_types = local.node_instance_types
    min_size       = local.node_min_size
    max_size       = local.node_max_size
    desired_size   = local.node_desired_size
  }
}
