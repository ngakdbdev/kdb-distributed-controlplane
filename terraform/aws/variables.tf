# variables.tf - every input this module accepts. See README.md for a
# worked example. Nothing here has a real secret default - the two
# genuinely sensitive values (bastion/API access CIDRs) default to "must
# be set" (no default) rather than 0.0.0.0/0, so a bare `terraform apply`
# with no tfvars file fails closed instead of open.

variable "region" {
  description = "AWS region to deploy into."
  type        = string
  default     = "us-east-1"
}

variable "environment" {
  description = "Short environment name, used as a naming/tag prefix for every resource this module creates (e.g. \"prod\", \"pilot1\"). Same constraint KX's own kxi-terraform uses for the equivalent field: keeps generated resource names (which have real length limits, e.g. EKS cluster names, ELB names) well under any cloud limit regardless of how many other name segments get appended."
  type        = string

  validation {
    condition     = can(regex("^[a-z0-9]{1,8}$", var.environment))
    error_message = "environment must be 1-8 characters, lowercase letters and numbers only."
  }
}

# ---------------------------------------------------------------- profile
# The single knob that answers "how much am I paying for redundancy" -
# drives AZ count (and therefore NAT gateway count and EKS control-plane
# spread), node instance sizing, and node group min/max/desired. See
# locals.tf for the exact mapping. This is an INFRASTRUCTURE redundancy/
# cost tradeoff, distinct from (and orthogonal to) the application's own
# per-TickHouse workload profile (control-api/app/tickhouse.py's
# "high-throughput"/"low-latency"/"balanced", which sizes q process
# hardware, not cluster topology) - don't conflate the two.
variable "cluster_profile" {
  description = "\"ha\" (multi-AZ, one NAT gateway per AZ, larger node minimums - matches KX's HA profile), \"performance\" (multi-AZ, compute-optimized node family, sized for throughput over cost), or \"cost_optimized\" (single AZ, single NAT gateway, smallest viable node group - matches KX's Cost-Optimised profile)."
  type        = string
  default     = "ha"

  validation {
    condition     = contains(["ha", "performance", "cost_optimized"], var.cluster_profile)
    error_message = "cluster_profile must be one of: ha, performance, cost_optimized."
  }
}

# ------------------------------------------------------------- networking
variable "vpc_cidr" {
  description = "CIDR block for the VPC this module creates. Only used when vpc_id is blank (see the \"existing VPC\" variables below) - matches KX's own \"New VPC\" vs \"Existing VPC\" choice."
  type        = string
  default     = "10.60.0.0/16"
}

variable "vpc_id" {
  description = "Bring-your-own VPC: set this (and the subnet id lists below) to deploy into an existing VPC instead of creating one. Blank (default) creates a new VPC from vpc_cidr."
  type        = string
  default     = ""
}

variable "existing_private_subnet_ids" {
  description = "Required if vpc_id is set: private subnet IDs (one per AZ) to place the EKS node groups and control plane ENIs into."
  type        = list(string)
  default     = []
}

variable "existing_public_subnet_ids" {
  description = "Required if vpc_id is set: public subnet IDs (one per AZ) for the NAT gateways / any public-facing load balancer."
  type        = list(string)
  default     = []
}

variable "bastion_access_cidr" {
  description = "CIDR allowed SSH access to the bastion host (see bastion.tf). No default on purpose - an operator must decide this, not inherit 0.0.0.0/0. Example: \"203.0.113.4/32\" for a single office IP, or your VPN's egress range."
  type        = string
}

variable "eks_api_access_cidrs" {
  description = "CIDR blocks allowed to reach the EKS API server's public endpoint (kubectl/helm from outside the VPC). Kept separate from bastion_access_cidr since the identity running `terraform apply`/`helm install` isn't necessarily on the same network as whoever SSHes to the bastion. No default - must be set explicitly."
  type        = list(string)
}

# ------------------------------------------------------------------ nodes
variable "node_instance_types" {
  description = "Override the profile's default EC2 instance type(s) for the EKS managed node group. Leave empty (default) to use the profile's own pick - see locals.tf."
  type        = list(string)
  default     = []
}

variable "node_min_size" {
  description = "Override the profile's default node group minimum. 0 = use the profile default."
  type        = number
  default     = 0
}

variable "node_max_size" {
  description = "Override the profile's default node group maximum. 0 = use the profile default."
  type        = number
  default     = 0
}

variable "node_desired_size" {
  description = "Override the profile's default node group desired count. 0 = use the profile default."
  type        = number
  default     = 0
}

variable "kubernetes_version" {
  description = "EKS control-plane Kubernetes version. See helm/kdb-control-plane's own docs/predeploy-kubernetes.md - the chart needs 1.27+ for autoscaling/v2 (HPA)."
  type        = string
  default     = "1.30"
}

# --------------------------------------------------------------- storage
variable "enable_secrets_encryption" {
  description = "Encrypt Kubernetes Secrets in etcd with a dedicated customer-managed KMS key. Matches KX's own \"Kubernetes secrets encryption: Yes (Recommended)\" toggle - on by default here for the same reason."
  type        = bool
  default     = true
}

variable "storage_tier" {
  description = "\"standard\" (gp3 EBS - fine for most TickHouses) or \"performance\" (io2 EBS, higher guaranteed IOPS - for a latency-sensitive TickHouse's rdb/wdb PVCs). Sets the DEFAULT StorageClass; both are always created regardless (see storage.tf) so a values.yaml can request either per-component via global.storageClassName."
  type        = string
  default     = "standard"

  validation {
    condition     = contains(["standard", "performance"], var.storage_tier)
    error_message = "storage_tier must be \"standard\" or \"performance\"."
  }
}

# ------------------------------------------------- high-performance filesystem
# AWS's actual equivalent of what KX's own doc calls "AWS Managed Lustre" -
# same underlying service (Amazon FSx for Lustre), not a from-scratch
# reimplementation. Off by default: it has a real minimum footprint (1.2TiB)
# and cost regardless of use, so it's opt-in for the TickHouses that
# genuinely need shared, very-high-throughput storage (e.g. an hdb serving
# wide historical scans to many concurrent gateway queries) rather than a
# blanket default every cluster pays for.
variable "enable_high_performance_storage" {
  description = "Provision an Amazon FSx for Lustre filesystem for latency/throughput-critical TickHouses. See storage.tf for the exact resource and README.md for the post-apply CSI driver install step (deliberately not a Terraform resource in this module - see versions.tf's comment on why)."
  type        = bool
  default     = false
}

variable "lustre_storage_capacity_gib" {
  description = "FSx for Lustre capacity in GiB. AWS's real minimum for the PERSISTENT_2 deployment type is 1200 GiB - matches KX's own documented \"1.2TiB minimum\" almost exactly (same underlying service)."
  type        = number
  default     = 1200

  validation {
    condition     = var.lustre_storage_capacity_gib >= 1200
    error_message = "FSx for Lustre PERSISTENT_2 requires at least 1200 GiB."
  }
}

variable "lustre_throughput_per_tib" {
  description = "FSx for Lustre per-unit-storage throughput (MB/s per TiB). AWS supports 125/250/500/1000 for PERSISTENT_2 - same tiers KX's own doc lists (1000 for their HA/Performance profile, 125 for Cost-Optimised). Defaults to matching cluster_profile's own tier if left at 0 - see locals.tf."
  type        = number
  default     = 0

  validation {
    condition     = contains([0, 125, 250, 500, 1000], var.lustre_throughput_per_tib)
    error_message = "lustre_throughput_per_tib must be one of: 0 (use the cluster_profile default), 125, 250, 500, 1000."
  }
}

# ----------------------------------------------------------------- tags
variable "tags" {
  description = "Extra tags merged onto every resource this module creates, on top of the Environment/ManagedBy tags it always applies."
  type        = map(string)
  default     = {}
}
