# gke.tf - the GKE cluster, its Cloud-KMS-backed application-layer secrets
# encryption, and a dedicated (non-default) node pool sized per
# cluster_profile. Removing GKE's own default node pool and managing one
# explicitly (google_container_node_pool below) is the standard production
# pattern - the default pool can't be resized/retired independently of the
# cluster itself.

resource "google_kms_key_ring" "this" {
  count = var.enable_secrets_encryption ? 1 : 0

  name     = "${local.name}-keyring"
  location = var.region
}

resource "google_kms_crypto_key" "gke_secrets" {
  count = var.enable_secrets_encryption ? 1 : 0

  name            = "${local.name}-gke-secrets"
  key_ring        = google_kms_key_ring.this[0].id
  rotation_period = "7776000s" # 90 days

  lifecycle {
    prevent_destroy = false # set true yourself once this is a real, long-lived environment - see README.md
  }
}

# The GKE service agent needs to be allowed to use this key BEFORE the
# cluster references it - a real ordering dependency Terraform can't infer
# from the encryption_config block alone (the SA doesn't exist as a
# referenceable resource until Terraform has seen the project's own
# service-identity data source).
data "google_project" "current" {}

resource "google_kms_crypto_key_iam_member" "gke_secrets" {
  count = var.enable_secrets_encryption ? 1 : 0

  crypto_key_id = google_kms_crypto_key.gke_secrets[0].id
  role          = "roles/cloudkms.cryptoKeyEncrypterDecrypter"
  member        = "serviceAccount:service-${data.google_project.current.number}@container-engine-robot.iam.gserviceaccount.com"
}

resource "google_container_cluster" "this" {
  name     = local.name
  location = local.location
  project  = var.project_id

  network    = local.network_id
  subnetwork = local.subnet_id

  # Managed separately below (google_container_node_pool.default) - see
  # this file's header comment on why.
  remove_default_node_pool = true
  initial_node_count       = 1

  min_master_version = var.kubernetes_version != "" ? var.kubernetes_version : null

  ip_allocation_policy {
    cluster_secondary_range_name  = "pods"
    services_secondary_range_name = "services"
  }

  private_cluster_config {
    enable_private_nodes    = true
    enable_private_endpoint = false # public control-plane endpoint, restricted below - matches AWS/Azure modules' own public-but-CIDR-restricted API access
  }

  master_authorized_networks_config {
    dynamic "cidr_blocks" {
      for_each = var.gke_api_access_cidrs
      content {
        cidr_block   = cidr_blocks.value
        display_name = "authorized-${cidr_blocks.key}"
      }
    }
  }

  workload_identity_config {
    workload_pool = "${var.project_id}.svc.id.goog"
  }

  dynamic "database_encryption" {
    for_each = var.enable_secrets_encryption ? [1] : []
    content {
      state    = "ENCRYPTED"
      key_name = google_kms_crypto_key.gke_secrets[0].id
    }
  }

  resource_labels = local.common_labels

  depends_on = [google_kms_crypto_key_iam_member.gke_secrets]
}

resource "google_service_account" "nodes" {
  account_id   = "${local.name}-gke-nodes"
  display_name = "Vantik GKE node pool (${var.environment})"
}

resource "google_project_iam_member" "nodes" {
  for_each = toset([
    "roles/logging.logWriter",
    "roles/monitoring.metricWriter",
    "roles/monitoring.viewer",
    "roles/artifactregistry.reader",
  ])
  project = var.project_id
  role    = each.value
  member  = "serviceAccount:${google_service_account.nodes.email}"
}

resource "google_container_node_pool" "default" {
  name     = "default"
  cluster  = google_container_cluster.this.id
  location = local.location

  autoscaling {
    min_node_count = local.node_min_count
    max_node_count = local.node_max_count
  }

  node_config {
    machine_type    = local.node_machine_type
    disk_size_gb    = 100
    disk_type       = "pd-ssd"
    service_account = google_service_account.nodes.email
    oauth_scopes    = ["https://www.googleapis.com/auth/cloud-platform"]
    labels          = { "vantik-io-cluster-profile" = var.cluster_profile }
    workload_metadata_config {
      mode = "GKE_METADATA"
    }
  }

  lifecycle {
    ignore_changes = [initial_node_count]
  }
}
