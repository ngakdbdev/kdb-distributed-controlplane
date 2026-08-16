output "cluster_name" {
  value = google_container_cluster.this.name
}

output "configure_kubectl" {
  value = "gcloud container clusters get-credentials ${google_container_cluster.this.name} --project ${var.project_id} --${local.profile.regional ? "region" : "zone"} ${local.location}"
}

output "network_id" {
  value = local.network_id
}

output "subnet_id" {
  value = local.subnet_id
}

output "bastion_external_ip" {
  value = google_compute_instance.bastion.network_interface[0].access_config[0].nat_ip
}

output "bastion_ssh_private_key" {
  value     = tls_private_key.bastion.private_key_pem
  sensitive = true
}

output "default_storage_class" {
  value = var.storage_tier == "performance" ? "hyperdisk-extreme-performance" : "pd-balanced"
}

output "kms_key_id" {
  description = "null when enable_secrets_encryption = false."
  value       = var.enable_secrets_encryption ? google_kms_crypto_key.gke_secrets[0].id : null
}

output "parallelstore_instance_id" {
  description = "null when enable_high_performance_storage = false. See README.md for the post-apply CSI driver install step."
  value       = var.enable_high_performance_storage ? google_parallelstore_instance.this[0].instance_id : null
}

output "cluster_profile" {
  value = var.cluster_profile
}

output "node_pool" {
  value = {
    machine_type = local.node_machine_type
    min_count    = local.node_min_count
    max_count    = local.node_max_count
    regional     = local.profile.regional
  }
}
