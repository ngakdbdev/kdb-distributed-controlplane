# versions.tf - provider pinning. Two providers only in THIS root module
# (aws + kubernetes for the StorageClass objects that live on the cluster
# this same apply creates) - deliberately NOT a helm/kubectl provider here.
# Installing the FSx Lustre CSI driver (a Helm chart, not an EKS-managed
# addon) against a cluster created in the SAME apply is the classic
# Terraform footgun (the helm/kubernetes providers need a live API
# endpoint that doesn't exist until the eks_cluster resource has already
# applied, so a single `terraform apply` either fails on a fresh cluster or
# silently depends on provider-init ordering that isn't guaranteed) - see
# storage.tf's own comment on why that install step is a documented
# post-apply `helm install`, not a resource here.
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
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

provider "aws" {
  region = var.region
}

# Configured against the cluster THIS module creates (aws_eks_cluster.this)
# via an exec-based auth plugin (aws eks get-token) so there's no static,
# long-lived credential written to state - the token is minted fresh on
# every provider call. Requires the AWS CLI to be on the machine running
# `terraform apply` (same requirement the deploy/*/  single-VM scripts
# already have).
provider "kubernetes" {
  host                   = aws_eks_cluster.this.endpoint
  cluster_ca_certificate = base64decode(aws_eks_cluster.this.certificate_authority[0].data)

  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "aws"
    args        = ["eks", "get-token", "--cluster-name", aws_eks_cluster.this.name, "--region", var.region]
  }
}
