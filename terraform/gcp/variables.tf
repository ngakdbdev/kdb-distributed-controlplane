variable "project_id" {
  description = "GCP project ID to deploy into. No default - GCP has no notion of \"current project\" the way AWS/Azure CLIs default a region/subscription, so this must always be explicit."
  type        = string
}

variable "region" {
  type    = string
  default = "us-central1"
}

variable "environment" {
  description = "Short environment name, used as a naming/tag prefix for every resource this module creates."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{1,8}$", var.environment))
    error_message = "environment must be 1-8 characters, lowercase letters and numbers only."
  }
}

# See terraform/aws/variables.tf's own comment on cluster_profile - same
# concept, same three values, same "orthogonal to the app's own TickHouse
# workload profile" caveat applies here too.
variable "cluster_profile" {
  description = "\"ha\" (multi-zone regional cluster, larger node minimums), \"performance\" (multi-zone, compute-optimized machine type), or \"cost_optimized\" (single-zone zonal cluster, smallest viable node pool)."
  type        = string
  default     = "ha"

  validation {
    condition     = contains(["ha", "performance", "cost_optimized"], var.cluster_profile)
    error_message = "cluster_profile must be one of: ha, performance, cost_optimized."
  }
}

variable "vpc_cidr" {
  description = "CIDR for the subnet this module creates. Only used when network_id is blank."
  type        = string
  default     = "10.62.0.0/16"
}

variable "network_id" {
  description = "Bring-your-own VPC network: set this (and subnet_id below) to deploy into an existing network instead of creating one."
  type        = string
  default     = ""
}

variable "existing_subnet_id" {
  description = "Required if network_id is set."
  type        = string
  default     = ""
}

variable "bastion_access_cidr" {
  description = "CIDR allowed SSH access to the bastion VM. No default - must be set explicitly, not inherited as 0.0.0.0/0."
  type        = string
}

variable "gke_api_access_cidrs" {
  description = "CIDR blocks allowed to reach the GKE control plane's public endpoint (master_authorized_networks). No default - must be set explicitly."
  type        = list(string)
}

variable "node_machine_type" {
  description = "Override the profile's default GCE machine type for the GKE node pool. Blank (default) uses the profile's own pick."
  type        = string
  default     = ""
}

variable "node_min_count" {
  description = "Per-zone minimum node count (GKE autoscaling is per-zone, not cluster-wide - see gke.tf)."
  type        = number
  default     = 0
}

variable "node_max_count" {
  type    = number
  default = 0
}

variable "kubernetes_version" {
  description = "GKE control-plane version. Blank = GKE's current \"regular\" release channel default."
  type        = string
  default     = ""
}

variable "storage_tier" {
  description = "\"standard\" (pd-balanced) or \"performance\" (hyperdisk-extreme, higher guaranteed IOPS) as the cluster-wide default StorageClass."
  type        = string
  default     = "standard"

  validation {
    condition     = contains(["standard", "performance"], var.storage_tier)
    error_message = "storage_tier must be \"standard\" or \"performance\"."
  }
}

variable "enable_secrets_encryption" {
  description = "Encrypt GKE's application-layer (etcd) secrets with a customer-managed Cloud KMS key - the GCP equivalent of AWS's KMS-backed EKS secrets encryption / Azure's Key-Vault-backed AKS etcd encryption."
  type        = bool
  default     = true
}

# ------------------------------------------------- high-performance filesystem
# Google Cloud Parallelstore - GCP's own real, GA parallel filesystem
# service (DAOS-based) - the direct equivalent of AWS FSx for Lustre /
# Azure Managed Lustre, and of what KX's AWS doc calls "AWS Managed
# Lustre".
variable "enable_high_performance_storage" {
  description = "Provision a Google Cloud Parallelstore instance for latency/throughput-critical TickHouses. See storage.tf for the post-apply CSI driver install step (documented in README.md, not a Terraform resource here - see versions.tf)."
  type        = bool
  default     = false
}

variable "parallelstore_capacity_gib" {
  description = "Parallelstore capacity in GiB. Real GCP minimum is 12000 GiB (~11.7TiB)."
  type        = number
  default     = 12000

  validation {
    condition     = var.parallelstore_capacity_gib >= 12000
    error_message = "Parallelstore requires at least 12000 GiB."
  }
}

variable "tags" {
  description = "Extra GCP resource labels merged onto every resource this module creates (GCP label rules: lowercase keys/values only)."
  type        = map(string)
  default     = {}
}
