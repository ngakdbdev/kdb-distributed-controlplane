# network.tf - "New VPC" path, mirroring terraform/aws/vpc.tf and
# terraform/azure/network.tf's own New/Existing choice. GKE VPC-native
# clusters need two secondary IP ranges on the subnet (pods, services) in
# addition to the primary node range - that's GCP-specific, no AWS/Azure
# equivalent in the other two modules.

resource "google_compute_network" "this" {
  count = local.use_existing_network ? 0 : 1

  name                    = "${local.name}-vpc"
  auto_create_subnetworks = false
}

resource "google_compute_subnetwork" "nodes" {
  count = local.use_existing_network ? 0 : 1

  name          = "${local.name}-nodes"
  network       = google_compute_network.this[0].id
  region        = var.region
  ip_cidr_range = cidrsubnet(var.vpc_cidr, 4, 0)

  secondary_ip_range {
    range_name    = "pods"
    ip_cidr_range = cidrsubnet(var.vpc_cidr, 2, 1) # /14-equivalent slice - room for real pod counts across the node pool
  }
  secondary_ip_range {
    range_name    = "services"
    ip_cidr_range = cidrsubnet(var.vpc_cidr, 6, 32)
  }

  private_ip_google_access = true # nodes can reach Google APIs (GCR/Artifact Registry image pulls, etc.) without a public IP
}

resource "google_compute_router" "this" {
  count = local.use_existing_network ? 0 : 1

  name    = "${local.name}-router"
  network = google_compute_network.this[0].id
  region  = var.region
}

resource "google_compute_router_nat" "this" {
  count = local.use_existing_network ? 0 : 1

  name                               = "${local.name}-nat"
  router                             = google_compute_router.this[0].name
  region                             = var.region
  nat_ip_allocate_option             = "AUTO_ONLY"
  source_subnetwork_ip_ranges_to_nat = "ALL_SUBNETWORKS_ALL_IP_RANGES"
}

resource "google_compute_firewall" "bastion_ssh" {
  count = local.use_existing_network ? 0 : 1

  name    = "${local.name}-allow-bastion-ssh"
  network = google_compute_network.this[0].id

  allow {
    protocol = "tcp"
    ports    = ["22"]
  }
  source_ranges = [var.bastion_access_cidr]
  target_tags   = ["${local.name}-bastion"]
}

locals {
  network_id = local.use_existing_network ? var.network_id : google_compute_network.this[0].id
  subnet_id  = local.use_existing_network ? var.existing_subnet_id : google_compute_subnetwork.nodes[0].id
}
