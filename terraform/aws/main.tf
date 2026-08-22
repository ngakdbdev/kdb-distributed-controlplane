# main.tf - shared locals + data sources. The profile -> concrete sizing
# mapping lives here as the one place that decision gets made, so vpc.tf/
# eks.tf/storage.tf all just read locals.profile instead of each re-
# deriving "is this the ha profile" logic independently.

data "aws_availability_zones" "available" {
  state = "available"
}

data "aws_caller_identity" "current" {}

locals {
  name = "vantik-${var.environment}"

  common_tags = merge(
    {
      Environment = var.environment
      ManagedBy   = "terraform"
      Project     = "vantik"
    },
    var.tags,
  )

  # ---- profile -> concrete sizing --------------------------------------
  # az_count: how many AZs to spread subnets/nodes/NAT gateways across.
  # nat_gateway_count: "ha"/"performance" get one NAT per AZ (a NAT outage
  # in one AZ doesn't take out every other AZ's egress); "cost_optimized"
  # gets exactly one NAT gateway total (single point of failure, but a
  # meaningful chunk of a small deployment's monthly bill - the same
  # tradeoff KX's own Cost-Optimised profile makes).
  profiles = {
    ha = {
      az_count          = 3
      nat_gateway_count = 3
      instance_types    = ["m6i.2xlarge"]
      node_min          = 3
      node_max          = 9
      node_desired      = 3
      lustre_throughput = 500
    }
    performance = {
      az_count          = 3
      nat_gateway_count = 3
      instance_types    = ["c6i.2xlarge"]
      node_min          = 3
      node_max          = 12
      node_desired      = 3
      lustre_throughput = 1000
    }
    cost_optimized = {
      az_count          = 2
      nat_gateway_count = 1
      instance_types    = ["m6i.xlarge"]
      node_min          = 2
      node_max          = 4
      node_desired      = 2
      lustre_throughput = 125
    }
  }

  profile = local.profiles[var.cluster_profile]

  az_count          = local.profile.az_count
  azs               = slice(data.aws_availability_zones.available.names, 0, local.az_count)
  nat_gateway_count = local.profile.nat_gateway_count

  node_instance_types = length(var.node_instance_types) > 0 ? var.node_instance_types : local.profile.instance_types
  node_min_size       = var.node_min_size > 0 ? var.node_min_size : local.profile.node_min
  node_max_size       = var.node_max_size > 0 ? var.node_max_size : local.profile.node_max
  node_desired_size   = var.node_desired_size > 0 ? var.node_desired_size : local.profile.node_desired

  lustre_throughput_per_tib = var.lustre_throughput_per_tib > 0 ? var.lustre_throughput_per_tib : local.profile.lustre_throughput

  use_existing_vpc = var.vpc_id != ""
}
