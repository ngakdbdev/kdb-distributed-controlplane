# versions.tf - see terraform/aws/versions.tf's own comment for why the
# high-performance-filesystem CSI driver (here: Azure Managed Lustre's
# CSI driver) is a documented post-apply Helm step, not a resource in
# this module - same reasoning applies identically on Azure.
terraform {
  required_version = ">= 1.5.0"

  required_providers {
    azurerm = {
      source  = "hashicorp/azurerm"
      version = "~> 4.0"
    }
    kubernetes = {
      source  = "hashicorp/kubernetes"
      version = "~> 2.25"
    }
    random = {
      source  = "hashicorp/random"
      version = "~> 3.6"
    }
    tls = {
      source  = "hashicorp/tls"
      version = "~> 4.0"
    }
  }
}

provider "azurerm" {
  features {
    key_vault {
      purge_soft_delete_on_destroy    = false # etcd encryption key - don't let a destroy silently hard-delete recoverable key material
      recover_soft_deleted_key_vaults = true
    }
  }
}

# Configured against the cluster THIS module creates, same pattern as
# terraform/aws - exec-based token via `az`, no static credential in state.
provider "kubernetes" {
  host                   = azurerm_kubernetes_cluster.this.kube_config.0.host
  cluster_ca_certificate = base64decode(azurerm_kubernetes_cluster.this.kube_config.0.cluster_ca_certificate)

  exec {
    api_version = "client.authentication.k8s.io/v1beta1"
    command     = "kubelogin"
    args        = ["get-token", "--login", "azurecli", "--server-id", "6dae42f8-4368-4678-94ff-3960e28e3630"]
  }
}
