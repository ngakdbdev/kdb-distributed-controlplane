# terraform/azure — AKS cluster for Vantik at scale

Azure equivalent of `terraform/aws` — same structure, same three
`cluster_profile` values, same "this provisions the cluster, `helm install`
puts Vantik on it" split. Read `terraform/aws/README.md` first if you
haven't — everything there about remote state, the cost warning, and the
prerequisites concept applies here identically; this file only covers
what's Azure-specific.

> **⚠️ This creates real, billed Azure resources** (an AKS cluster, VM
> node pool, NAT Gateway, Key Vault, optionally an Azure Managed Lustre
> filesystem — which has a real minimum capacity/cost regardless of use).
> Review `terraform plan` before applying.

## Prerequisites

- Terraform >= 1.5.0, Azure CLI (`az login` first), `kubectl`, `helm`
- `kubelogin` (for the `exec`-based kubernetes provider auth in
  `versions.tf` — `az aks install-cli` or your package manager)
- An Azure subscription with quota for the node pool's VM size/count (see
  `main.tf`'s `locals.profiles`) plus 1 AKS cluster, 1 NAT Gateway, 1 Key
  Vault

## Apply

```bash
cd terraform/azure
cp terraform.tfvars.example terraform.tfvars   # fill in bastion_access_cidr + aks_api_access_cidrs
terraform init
terraform plan
terraform apply
```

```bash
$(terraform output -raw configure_kubectl)   # az aks get-credentials ...
kubectl get nodes
```

### If you enabled `enable_high_performance_storage`

Same pattern as AWS FSx — the filesystem is created by this apply, its CSI
driver is a separate Helm install:

```bash
helm repo add azurelustre https://raw.githubusercontent.com/Azure/azurelustre-csi-driver/main/charts
helm install azurelustre-csi-driver azurelustre/azurelustre-csi-driver -n kube-system

kubectl apply -f - <<EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: azure-lustre
provisioner: azurelustre.csi.azure.com
parameters:
  sku-name: $(terraform output -raw lustre_filesystem_id >/dev/null; echo "$(grep lustre_sku ../azure/main.tf | head -1)")
  resource-group-name: $(terraform output -raw resource_group)
reclaimPolicy: Retain
EOF
```

Point a specific TickHouse's high-throughput components at it rather than
making it the cluster default (same reasoning as `terraform/aws/README.md`
— real minimum footprint most TickHouses don't need).

## Install Vantik

```bash
cd ../../helm/kdb-control-plane
helm install vantik . \
  -f values-azure.yaml \
  --set global.storageClassName=$(terraform -chdir=../../terraform/azure output -raw default_storage_class) \
  --set secrets.jwtSecret=$(openssl rand -hex 32) \
  --set secrets.watchdogSharedSecret=$(openssl rand -hex 32)
```

## Bastion SSH key

Unlike the AWS module (which uses an operator-supplied key pair), this
module generates the bastion's SSH key pair itself and stores the private
half in Terraform state as a sensitive output — retrieve it once, save it
somewhere real, then treat Terraform state as the only remaining copy:

```bash
terraform output -raw bastion_ssh_private_key > bastion_key.pem
chmod 400 bastion_key.pem
ssh -i bastion_key.pem azureuser@$(terraform output -raw bastion_public_ip)
```

## Tearing down

```bash
helm uninstall vantik -n kdb-control-plane   # PVCs use reclaimPolicy Retain - see storage.tf
terraform destroy
```
