variable "location" {
  description = "Azure region to deploy into."
  type        = string
  default     = "eastus"
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
  description = "\"ha\" (multi-zone, larger node minimums), \"performance\" (multi-zone, compute-optimized node SKU), or \"cost_optimized\" (single-zone, smallest viable node pool)."
  type        = string
  default     = "ha"

  validation {
    condition     = contains(["ha", "performance", "cost_optimized"], var.cluster_profile)
    error_message = "cluster_profile must be one of: ha, performance, cost_optimized."
  }
}

variable "vnet_cidr" {
  description = "CIDR block for the VNet this module creates. Only used when vnet_id is blank."
  type        = string
  default     = "10.61.0.0/16"
}

variable "vnet_id" {
  description = "Bring-your-own VNet: set this (and subnet_id below) to deploy into an existing VNet instead of creating one."
  type        = string
  default     = ""
}

variable "existing_subnet_id" {
  description = "Required if vnet_id is set: subnet ID for the AKS node pool."
  type        = string
  default     = ""
}

variable "bastion_access_cidr" {
  description = "CIDR allowed SSH access to the bastion VM. No default - must be set explicitly, not inherited as 0.0.0.0/0."
  type        = string
}

variable "aks_api_access_cidrs" {
  description = "CIDR blocks allowed to reach the AKS API server's public endpoint. No default - must be set explicitly."
  type        = list(string)
}

variable "node_vm_size" {
  description = "Override the profile's default VM size for the AKS node pool. Blank (default) uses the profile's own pick - see locals.tf."
  type        = string
  default     = ""
}

variable "node_min_count" {
  type    = number
  default = 0
}

variable "node_max_count" {
  type    = number
  default = 0
}

variable "kubernetes_version" {
  description = "AKS control-plane Kubernetes version. Blank = AKS's current default GA version."
  type        = string
  default     = ""
}

variable "storage_tier" {
  description = "\"standard\" (Premium_LRS managed disk) or \"performance\" (PremiumV2_LRS, higher guaranteed IOPS/throughput) as the cluster-wide default StorageClass."
  type        = string
  default     = "standard"

  validation {
    condition     = contains(["standard", "performance"], var.storage_tier)
    error_message = "storage_tier must be \"standard\" or \"performance\"."
  }
}

variable "enable_secrets_encryption" {
  description = "Encrypt AKS's etcd secrets with a customer-managed Key Vault key (AKS's \"Key Management Service\" / KMS etcd encryption feature) - the Azure equivalent of AWS's KMS-backed EKS secrets encryption."
  type        = bool
  default     = true
}

# ------------------------------------------------- high-performance filesystem
# Azure Managed Lustre File System (AMLFS) - Azure's own real, GA service
# that's the direct equivalent of what KX's AWS doc calls "AWS Managed
# Lustre" (and what terraform/aws provisions via aws_fsx_lustre_file_system).
variable "enable_high_performance_storage" {
  description = "Provision an Azure Managed Lustre File System for latency/throughput-critical TickHouses. See storage.tf for the post-apply CSI driver install step (documented in README.md, not a Terraform resource here - see versions.tf)."
  type        = bool
  default     = false
}

variable "lustre_storage_capacity_tib" {
  description = "AMLFS capacity in TiB. Real Azure minimum depends on the chosen SKU tier - see storage.tf's sku_name comment."
  type        = number
  default     = 4
}

# ------------------------------------------------------------------- tags
variable "tags" {
  type    = map(string)
  default = {}
}
