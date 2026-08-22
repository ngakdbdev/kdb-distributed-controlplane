data "azurerm_client_config" "current" {}

resource "random_id" "suffix" {
  byte_length = 3 # Key Vault / storage account names need global uniqueness this module can't otherwise guarantee
}

locals {
  name = "vantik-${var.environment}"

  common_tags = merge(
    {
      environment = var.environment
      managedBy   = "terraform"
      project     = "vantik"
    },
    var.tags,
  )

  # ---- profile -> concrete sizing (mirrors terraform/aws/main.tf's own
  # table - same three profiles, same meaning, Azure-native SKUs/zone
  # counts in place of AWS instance types/AZ counts). ----
  profiles = {
    ha = {
      zones      = ["1", "2", "3"]
      vm_size    = "Standard_D8s_v5"
      node_min   = 3
      node_max   = 9
      node_count = 3
      lustre_sku = "AMLFS-Durable-Premium-500"
    }
    performance = {
      zones      = ["1", "2", "3"]
      vm_size    = "Standard_F8s_v2"
      node_min   = 3
      node_max   = 12
      node_count = 3
      lustre_sku = "AMLFS-Durable-Premium-1000"
    }
    cost_optimized = {
      zones      = ["1", "2"]
      vm_size    = "Standard_D4s_v5"
      node_min   = 2
      node_max   = 4
      node_count = 2
      lustre_sku = "AMLFS-Durable-Premium-125"
    }
  }

  profile = local.profiles[var.cluster_profile]

  zones          = local.profile.zones
  node_vm_size   = var.node_vm_size != "" ? var.node_vm_size : local.profile.vm_size
  node_min_count = var.node_min_count > 0 ? var.node_min_count : local.profile.node_min
  node_max_count = var.node_max_count > 0 ? var.node_max_count : local.profile.node_max
  node_count     = local.profile.node_count
  lustre_sku     = local.profile.lustre_sku

  use_existing_vnet = var.vnet_id != ""
}
