# aks.tf - the AKS cluster, its Key-Vault-backed etcd secrets encryption
# (Azure's equivalent of AWS KMS-backed EKS secrets encryption), and a
# default node pool sized per cluster_profile. Unlike EKS, the Azure Disk
# CSI driver ships enabled by default on AKS (storage_profile below) - no
# separate IRSA-equivalent role/addon install needed for standard storage
# the way terraform/aws/storage.tf needs one for the EBS CSI driver.

resource "azurerm_user_assigned_identity" "cluster" {
  name                = "${local.name}-aks-identity"
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  tags                = local.common_tags
}

# ---------------------------------------------------- Key Vault for etcd KMS
resource "azurerm_key_vault" "secrets" {
  count = var.enable_secrets_encryption ? 1 : 0

  name                       = "kv-${var.environment}-${random_id.suffix.hex}"
  location                   = azurerm_resource_group.this.location
  resource_group_name        = azurerm_resource_group.this.name
  tenant_id                  = data.azurerm_client_config.current.tenant_id
  sku_name                   = "standard"
  purge_protection_enabled   = true
  soft_delete_retention_days = 30
  tags                       = local.common_tags
}

resource "azurerm_key_vault_access_policy" "cluster_identity" {
  count = var.enable_secrets_encryption ? 1 : 0

  key_vault_id = azurerm_key_vault.secrets[0].id
  tenant_id    = data.azurerm_client_config.current.tenant_id
  object_id    = azurerm_user_assigned_identity.cluster.principal_id

  key_permissions = ["Get", "UnwrapKey", "WrapKey"]
}

resource "azurerm_key_vault_key" "etcd" {
  count = var.enable_secrets_encryption ? 1 : 0

  name         = "${local.name}-etcd-encryption"
  key_vault_id = azurerm_key_vault.secrets[0].id
  key_type     = "RSA"
  key_size     = 2048
  key_opts     = ["decrypt", "encrypt", "unwrapKey", "wrapKey"]

  depends_on = [azurerm_key_vault_access_policy.cluster_identity]
}

# ------------------------------------------------------------------ cluster
resource "azurerm_kubernetes_cluster" "this" {
  name                = local.name
  location            = azurerm_resource_group.this.location
  resource_group_name = azurerm_resource_group.this.name
  dns_prefix          = local.name
  kubernetes_version  = var.kubernetes_version != "" ? var.kubernetes_version : null

  identity {
    type         = "UserAssigned"
    identity_ids = [azurerm_user_assigned_identity.cluster.id]
  }

  default_node_pool {
    name                 = "default"
    vm_size              = local.node_vm_size
    vnet_subnet_id       = local.node_subnet_id
    zones                = local.zones
    auto_scaling_enabled = true
    min_count            = local.node_min_count
    max_count            = local.node_max_count
    os_disk_type         = "Managed"
    os_disk_size_gb      = 100

    upgrade_settings {
      max_surge = "33%"
    }

    tags = local.common_tags
  }

  network_profile {
    network_plugin = "azure"
    network_policy = "azure"
    outbound_type  = "userDefinedRouting" # egress goes through the NAT gateway (network.tf), not an AKS-managed LB
  }

  # AKS's own name for etcd secrets-at-rest encryption via a customer
  # Key Vault key - direct equivalent of terraform/aws/eks.tf's
  # `encryption_config` block referencing an aws_kms_key.
  dynamic "key_management_service" {
    for_each = var.enable_secrets_encryption ? [1] : []
    content {
      key_vault_key_id = azurerm_key_vault_key.etcd[0].id
    }
  }

  api_server_access_profile {
    authorized_ip_ranges = var.aks_api_access_cidrs
  }

  storage_profile {
    disk_driver_enabled = true
  }

  tags = local.common_tags

  lifecycle {
    ignore_changes = [default_node_pool[0].node_count] # let the cluster autoscaler own this after first apply
  }

  depends_on = [
    azurerm_subnet_nat_gateway_association.nodes,
    azurerm_key_vault_access_policy.cluster_identity,
  ]
}

# Cluster identity needs Network Contributor on the node subnet to manage
# NICs/load balancer rules for the CNI - AKS won't come up healthy without
# this when using a pre-created (not AKS-managed) VNet/subnet, which is
# always true here (network.tf always creates the subnet itself, even in
# the "new VNet" path).
resource "azurerm_role_assignment" "cluster_network" {
  scope                = local.node_subnet_id
  role_definition_name = "Network Contributor"
  principal_id         = azurerm_user_assigned_identity.cluster.principal_id
}
