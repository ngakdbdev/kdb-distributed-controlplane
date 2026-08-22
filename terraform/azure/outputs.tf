output "cluster_name" {
  value = azurerm_kubernetes_cluster.this.name
}

output "resource_group" {
  value = azurerm_resource_group.this.name
}

output "configure_kubectl" {
  value = "az aks get-credentials --resource-group ${azurerm_resource_group.this.name} --name ${azurerm_kubernetes_cluster.this.name}"
}

output "vnet_id" {
  value = local.use_existing_vnet ? var.vnet_id : azurerm_virtual_network.this[0].id
}

output "node_subnet_id" {
  value = local.node_subnet_id
}

output "bastion_public_ip" {
  value = azurerm_public_ip.bastion.ip_address
}

output "bastion_ssh_private_key" {
  value     = tls_private_key.bastion.private_key_pem
  sensitive = true
}

output "default_storage_class" {
  value = var.storage_tier == "performance" ? "managed-premium-v2-performance" : "managed-premium"
}

output "key_vault_id" {
  description = "null when enable_secrets_encryption = false."
  value       = var.enable_secrets_encryption ? azurerm_key_vault.secrets[0].id : null
}

output "lustre_filesystem_id" {
  description = "null when enable_high_performance_storage = false. See README.md for the post-apply CSI driver install step."
  value       = var.enable_high_performance_storage ? azurerm_managed_lustre_file_system.this[0].id : null
}

output "cluster_profile" {
  value = var.cluster_profile
}

output "node_pool" {
  value = {
    vm_size   = local.node_vm_size
    min_count = local.node_min_count
    max_count = local.node_max_count
    zones     = local.zones
  }
}
