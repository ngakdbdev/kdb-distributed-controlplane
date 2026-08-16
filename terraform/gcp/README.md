# terraform/gcp — GKE cluster for Vantik at scale

GCP equivalent of `terraform/aws` and `terraform/azure` — same structure,
same three `cluster_profile` values. Read `terraform/aws/README.md` first
if you haven't — the remote-state guidance, cost warning, and prerequisites
concept all apply here identically; this file only covers what's
GCP-specific.

> **⚠️ This creates real, billed GCP resources** (a GKE cluster, GCE node
> pool, Cloud NAT, optionally a Parallelstore instance — which has a real
> ~11.7TiB minimum capacity/cost regardless of use). Review
> `terraform plan` before applying.

## Prerequisites

- Terraform >= 1.5.0, `gcloud` CLI (`gcloud auth application-default login`
  first), `kubectl`, `helm`
- `gke-gcloud-auth-plugin` (for the `exec`-based kubernetes provider auth
  in `versions.tf` — `gcloud components install gke-gcloud-auth-plugin`)
- A GCP project with the Kubernetes Engine, Compute Engine, Cloud KMS, and
  (if `enable_high_performance_storage`) Parallelstore APIs enabled, and
  quota for the node pool's machine type/count (see `main.tf`'s
  `locals.profiles`)

## Apply

```bash
cd terraform/gcp
cp terraform.tfvars.example terraform.tfvars   # fill in project_id, bastion_access_cidr, gke_api_access_cidrs
terraform init
terraform plan
terraform apply
```

```bash
$(terraform output -raw configure_kubectl)   # gcloud container clusters get-credentials ...
kubectl get nodes
```

### If you enabled `enable_high_performance_storage`

Same pattern as the AWS/Azure modules — the instance is created by this
apply, its CSI driver is a separate install (Parallelstore's CSI driver is
distributed as YAML manifests, not a Helm chart):

```bash
kubectl apply -f https://raw.githubusercontent.com/GoogleCloudPlatform/parallelstore-csi-driver/main/deploy/kubernetes/manifests/csi-parallelstore-driver.yaml

kubectl apply -f - <<EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: parallelstore
provisioner: parallelstore.csi.storage.gke.io
parameters:
  instanceId: $(terraform output -raw parallelstore_instance_id)
reclaimPolicy: Retain
EOF
```

Point a specific TickHouse's high-throughput components at it rather than
making it the cluster default (same reasoning as the other two clouds'
READMEs — real minimum footprint most TickHouses don't need).

## Install Vantik

```bash
cd ../../helm/kdb-control-plane
helm install vantik . \
  -f values-gcp.yaml \
  --set global.storageClassName=$(terraform -chdir=../../terraform/gcp output -raw default_storage_class) \
  --set secrets.jwtSecret=$(openssl rand -hex 32) \
  --set secrets.watchdogSharedSecret=$(openssl rand -hex 32)
```

## Bastion SSH

Same generated-key pattern as the Azure module:

```bash
terraform output -raw bastion_ssh_private_key > bastion_key.pem
chmod 400 bastion_key.pem
ssh -i bastion_key.pem vantik@$(terraform output -raw bastion_external_ip)
```

GCP's own better-fit alternative once you're comfortable with it: Identity-
Aware Proxy TCP forwarding needs no public IP or open firewall port at all
(`gcloud compute ssh <instance> --tunnel-through-iap`) — this module ships
a directly-SSHable bastion instead for parity with the AWS/Azure modules,
since IAP needs project-level IAM/API setup this module doesn't assume you
already have.

## Tearing down

```bash
helm uninstall vantik -n kdb-control-plane   # PVCs use reclaimPolicy Retain - see storage.tf
terraform destroy
```
