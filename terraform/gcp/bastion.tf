# bastion.tf - see terraform/aws/bastion.tf's own header comment for why
# this exists. GCP's own idiomatic alternative is IAP TCP forwarding
# (`gcloud compute ssh --tunnel-through-iap`, no public IP or open
# firewall port needed at all) - genuinely the better pattern on GCP
# specifically, and worth using instead once you're comfortable with it
# (see README.md) - but this module ships a directly-SSHable bastion for
# parity with the AWS/Azure modules and because IAP needs additional
# project-level IAM/API-enablement setup this module doesn't assume you
# already have.

resource "tls_private_key" "bastion" {
  algorithm = "RSA"
  rsa_bits  = 4096
}

resource "google_compute_instance" "bastion" {
  name         = "${local.name}-bastion"
  machine_type = "e2-micro" # a jump host, not a workload - no reason for anything bigger
  zone         = local.profile.regional ? "${var.region}-a" : local.location

  tags = ["${local.name}-bastion"]

  boot_disk {
    initialize_params {
      image = "ubuntu-os-cloud/ubuntu-2204-lts"
      size  = 20
      type  = "pd-ssd"
    }
  }

  network_interface {
    subnetwork = local.subnet_id
    access_config {} # ephemeral public IP
  }

  metadata = {
    ssh-keys = "vantik:${tls_private_key.bastion.public_key_openssh}"
  }

  labels = local.common_labels
}
