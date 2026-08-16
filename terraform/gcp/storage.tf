# storage.tf - see terraform/aws/storage.tf's own header comment; same
# structure here: two always-created StorageClasses (standard/performance
# persistent disks) plus an optional Google Cloud Parallelstore instance
# for TickHouses that need genuinely high, shared throughput -
# Parallelstore is GCP's own real, GA, DAOS-based parallel filesystem
# service, the direct equivalent of AWS FSx for Lustre / Azure Managed
# Lustre and of what KX's AWS doc calls "AWS Managed Lustre".
#
# The GCE Persistent Disk CSI driver ships enabled by default on GKE
# (Standard clusters, current GKE versions) - like Azure's Disk CSI driver
# and unlike AWS's EBS CSI driver, no separate addon/IAM-binding step is
# needed for the two StorageClasses below. Parallelstore's own CSI driver
# IS a separate install though - see README.md for the exact post-apply
# command, same reasoning as the AWS/Azure modules' high-perf filesystem
# sections.

resource "kubernetes_storage_class_v1" "pd_balanced" {
  metadata {
    name = "pd-balanced"
    annotations = var.storage_tier == "standard" ? {
      "storageclass.kubernetes.io/is-default-class" = "true"
    } : {}
  }
  storage_provisioner    = "pd.csi.storage.gke.io"
  reclaim_policy         = "Retain" # hdb/db PVCs hold real trading history - never auto-delete on a scale-down/uninstall
  volume_binding_mode    = "WaitForFirstConsumer"
  allow_volume_expansion = true
  parameters = {
    type = "pd-balanced"
  }
}

resource "kubernetes_storage_class_v1" "hyperdisk_extreme" {
  metadata {
    name = "hyperdisk-extreme-performance"
    annotations = var.storage_tier == "performance" ? {
      "storageclass.kubernetes.io/is-default-class" = "true"
    } : {}
  }
  storage_provisioner    = "pd.csi.storage.gke.io"
  reclaim_policy         = "Retain"
  volume_binding_mode    = "WaitForFirstConsumer"
  allow_volume_expansion = true
  parameters = {
    type                       = "hyperdisk-extreme"
    provisioned-iops-on-create = "100000" # GCP's independently-configurable-IOPS disk tier - the Parallelstore-adjacent analog of AWS's io2-performance StorageClass
  }
}

# ------------------------------------------------- Google Cloud Parallelstore
resource "google_parallelstore_instance" "this" {
  count = var.enable_high_performance_storage ? 1 : 0

  instance_id  = "${local.name}-parallelstore"
  location     = local.profile.regional ? "${var.region}-a" : local.location # Parallelstore instances are zonal even under a regional GKE cluster
  capacity_gib = var.parallelstore_capacity_gib

  network = local.network_id

  labels = local.common_labels
}
