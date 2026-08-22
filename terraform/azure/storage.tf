# storage.tf - see terraform/aws/storage.tf's own header comment; same
# structure here: two always-created StorageClasses (standard/performance
# managed disks) plus an optional Azure Managed Lustre File System (AMLFS)
# for TickHouses that need genuinely high, shared throughput - Azure's own
# real, GA equivalent of what KX's AWS doc calls "AWS Managed Lustre".
#
# The Azure Disk CSI driver itself is already enabled on every AKS cluster
# by default (see aks.tf's storage_profile block) - unlike AWS, there's no
# separate CSI-addon-plus-IRSA-role step needed for the two StorageClasses
# below. AMLFS's own CSI driver IS a separate Helm install though - see
# README.md for the exact post-apply command, same reasoning as
# terraform/aws/storage.tf's FSx section.

resource "kubernetes_storage_class_v1" "managed_premium" {
  metadata {
    name = "managed-premium"
    annotations = var.storage_tier == "standard" ? {
      "storageclass.kubernetes.io/is-default-class" = "true"
    } : {}
  }
  storage_provisioner    = "disk.csi.azure.com"
  reclaim_policy         = "Retain" # hdb/db PVCs hold real trading history - never auto-delete on a scale-down/uninstall
  volume_binding_mode    = "WaitForFirstConsumer"
  allow_volume_expansion = true
  parameters = {
    skuName   = "Premium_LRS"
    kind      = "managed"
    encrypted = "true"
  }
}

resource "kubernetes_storage_class_v1" "managed_premium_v2" {
  metadata {
    name = "managed-premium-v2-performance"
    annotations = var.storage_tier == "performance" ? {
      "storageclass.kubernetes.io/is-default-class" = "true"
    } : {}
  }
  storage_provisioner    = "disk.csi.azure.com"
  reclaim_policy         = "Retain"
  volume_binding_mode    = "WaitForFirstConsumer"
  allow_volume_expansion = true
  parameters = {
    # PremiumV2_LRS - independently configurable IOPS/throughput per disk
    # (not just size-tiered like Premium_LRS) - the Azure managed-disk
    # analog of terraform/aws's io2-performance StorageClass.
    skuName   = "PremiumV2_LRS"
    kind      = "managed"
    encrypted = "true"
  }
}

# ------------------------------------------------- Azure Managed Lustre
resource "azurerm_managed_lustre_file_system" "this" {
  count = var.enable_high_performance_storage ? 1 : 0

  name                   = "${local.name}-lustre"
  resource_group_name    = azurerm_resource_group.this.name
  location               = azurerm_resource_group.this.location
  sku_name               = local.lustre_sku # AMLFS-Durable-Premium-{125,250,500,1000} - same MBps/TiB tiers as AWS FSx/KX's own doc
  storage_capacity_in_tb = var.lustre_storage_capacity_tib
  zones                  = [local.zones[0]] # single-zone, matching terraform/aws's FSx PERSISTENT_2 single-AZ note

  subnet_id = local.node_subnet_id

  # Azure requires an explicit weekly patch window for AMLFS - Sunday
  # 02:00 UTC picked as a low-traffic default; override in a real
  # deployment if that lands during your own trading hours somewhere.
  maintenance_window {
    day_of_week        = "Sunday"
    time_of_day_in_utc = "02:00"
  }

  tags = merge(local.common_tags, {
    lustreSku = local.lustre_sku
  })
}
