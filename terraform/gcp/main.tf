locals {
  name = "vantik-${var.environment}"

  common_labels = merge(
    {
      environment = var.environment
      managed-by  = "terraform"
      project     = "vantik"
    },
    var.tags,
  )

  # ---- profile -> concrete sizing (mirrors terraform/aws/main.tf and
  # terraform/azure/main.tf's own tables - same three profiles, same
  # meaning, GCP-native machine types/zone counts in place of AWS instance
  # types/AZ counts or Azure VM sizes/zones). ----
  profiles = {
    ha = {
      zone_count                           = 3
      regional                             = true
      machine_type                         = "n2-standard-8"
      node_min                             = 1 # per-zone - GKE autoscaling is per-zone, so 1-3 here means 3-9 cluster-wide across 3 zones
      node_max                             = 3
      node_count                           = 1
      pstore_throughput_mb_per_sec_per_tib = 1000
    }
    performance = {
      zone_count                           = 3
      regional                             = true
      machine_type                         = "c2-standard-8"
      node_min                             = 1
      node_max                             = 4
      node_count                           = 1
      pstore_throughput_mb_per_sec_per_tib = 1000
    }
    cost_optimized = {
      zone_count                           = 1
      regional                             = false
      machine_type                         = "e2-standard-4"
      node_min                             = 2
      node_max                             = 4
      node_count                           = 2
      pstore_throughput_mb_per_sec_per_tib = 125
    }
  }

  profile = local.profiles[var.cluster_profile]

  node_machine_type = var.node_machine_type != "" ? var.node_machine_type : local.profile.machine_type
  node_min_count    = var.node_min_count > 0 ? var.node_min_count : local.profile.node_min
  node_max_count    = var.node_max_count > 0 ? var.node_max_count : local.profile.node_max
  node_count        = local.profile.node_count

  use_existing_network = var.network_id != ""

  # GKE "regional" clusters (control plane replicated across 3 zones in the
  # region) for ha/performance; "zonal" (single zone, single control-plane
  # replica) for cost_optimized - same tradeoff as the AWS module's NAT
  # gateway count / Azure module's zone count, applied to GKE's own
  # regional-vs-zonal cluster type concept.
  location = local.profile.regional ? var.region : "${var.region}-a"
}
