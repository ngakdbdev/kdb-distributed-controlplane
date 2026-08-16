# versions.tf - see terraform/aws/versions.tf's own comment for why the
# high-performance-filesystem CSI driver (here: Google Cloud Parallelstore's
# CSI driver) is a documented post-apply step, not a resource in this
# module - same reasoning applies identically on GCP.
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    google = {
      source  = "hashicorp/google"
      version = ">= 6.15.0, < 7.0.0" # google_parallelstore_instance isn't in provider 5.x (confirmed via `terraform validate`) - 6.15 is a conservative floor, not a confirmed exact introduction version; bump if `terraform init` picks an older 6.x that still lacks it
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.25"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

provider "google" {
  project = var.project_id
  region  = var.region
}

# Configured against the cluster THIS module creates, same exec-based-
# token pattern as terraform/aws and terraform/azure - no static credential
# in state.
provider "kubernetes" {
  host                   = "https://${google_container_cluster.this.endpoint}"
  cluster_ca_certificate = base64decode(google_container_cluster.this.master_auth[0].cluster_ca_certificate)

  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "gke-gcloud-auth-plugin"
  }
}
