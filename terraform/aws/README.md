# terraform/aws — EKS cluster for Vantik at scale

Provisions the Kubernetes cluster `helm/kdb-control-plane` installs onto —
VPC, EKS control plane + managed node group, KMS-backed secrets encryption,
gp3 + io2 StorageClasses, an optional high-throughput FSx for Lustre
filesystem, and a bastion host. This is the **cluster infrastructure**
layer; it does not install Vantik itself — that's a separate `helm install`
step once this apply finishes (see [step 4](#4-install-vantik) below).

This is the enterprise/scale path. If you just want a single box to demo
the product, use `deploy/aws/` instead (docker-compose on one EC2 instance)
— see the root [docs/README.md](../../docs/README.md)'s "which path" table.

> **⚠️ This creates real, billed AWS resources** (an EKS control plane, EC2
> node group, NAT gateway(s), optionally an FSx Lustre filesystem — which
> alone is priced per-GiB-per-month with a real 1.2TiB minimum regardless
> of use). Review the plan output before applying, and run
> `terraform destroy` when you're done with anything you don't intend to
> keep running.

## 1. Prerequisites

- Terraform >= 1.5.0
- AWS CLI, configured with credentials that can create VPC/EKS/IAM/KMS/EC2/
  FSx resources (a full admin policy is the simplest starting point for a
  first apply; narrow it once you know exactly what your account needs —
  unlike `deploy/aws/`'s single-VM path, this module's resource surface is
  large enough that hand-writing a minimal least-privilege policy up front
  is more likely to be wrong than iteratively tightening one)
- `kubectl` and `helm` (for step 4, after this module finishes)
- A real AWS account with quota for at least: 1 EKS cluster, the node
  group's instance count for your chosen `cluster_profile` (2-3 by
  default — see `main.tf`'s `locals.profiles`), 1-3 NAT gateways, 1-3
  Elastic IPs

## 2. State — use a remote backend for anything beyond your own laptop

This module ships with **no backend block** (local state) so a first
`terraform init` works with zero setup. For a real deployment, add a
backend before your first `apply`, not after:

```hcl
# backend.tf (not included - add this yourself, bucket/table per your account)
terraform {
  backend "s3" {
    bucket         = "your-terraform-state-bucket"
    key            = "vantik/aws/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "your-terraform-lock-table"  # state locking - two applies racing each other on unlocked state is how you get a corrupted cluster
    encrypt        = true
  }
}
```

## 3. Apply

```bash
cd terraform/aws
cp terraform.tfvars.example terraform.tfvars   # fill in bastion_access_cidr + eks_api_access_cidrs at minimum
terraform init
terraform plan    # READ THIS before applying - it lists every resource that will actually be created
terraform apply
```

Takes 15-20 minutes (EKS control-plane provisioning is the slow part, not
this module's own logic). On success:

```bash
$(terraform output -raw configure_kubectl)   # aws eks update-kubeconfig ...
kubectl get nodes                            # should show your node group, Ready
```

### If you enabled `enable_high_performance_storage`

The FSx for Lustre filesystem itself is created by this apply, but its CSI
driver is a Helm chart, not an EKS-managed addon (see `storage.tf`'s own
comment on why that's a deliberate, separate step rather than a
`helm_release` resource in this same module). After the apply:

```bash
helm repo add aws-fsx-csi-driver https://kubernetes-sigs.github.io/aws-fsx-csi-driver
helm install aws-fsx-csi-driver aws-fsx-csi-driver/aws-fsx-csi-driver -n kube-system

kubectl apply -f - <<EOF
apiVersion: storage.k8s.io/v1
kind: StorageClass
metadata:
  name: fsx-lustre
provisioner: fsx.csi.aws.com
parameters:
  subnetId: $(terraform output -json private_subnet_ids | jq -r '.[0]')
  securityGroupIds: $(terraform output -raw lustre_filesystem_id >/dev/null; aws fsx describe-file-systems --file-system-ids $(terraform output -raw lustre_filesystem_id) --query 'FileSystems[0].NetworkInterfaceIds' --output text)
reclaimPolicy: Retain
EOF
```

Then point a specific TickHouse's high-throughput components at it via the
chart's per-component `resources`/storage overrides (see
`helm/kdb-control-plane/values.yaml`) rather than making it the cluster
default — it has a real 1.2TiB minimum footprint, so most TickHouses are
better served by the standard gp3/io2 classes this module always creates.

## 4. Install Vantik

```bash
cd ../../helm/kdb-control-plane
helm install vantik . \
  -f values-aws.yaml \
  --set global.storageClassName=$(terraform -chdir=../../terraform/aws output -raw default_storage_class) \
  --set secrets.jwtSecret=$(openssl rand -hex 32) \
  --set secrets.watchdogSharedSecret=$(openssl rand -hex 32)
```

See `docs/predeploy-kubernetes.md` for the full pre-install checklist
(secrets, database, KX-X license) — everything there still applies; this
module only got you the cluster it's installing onto.

## 5. Tearing down

```bash
helm uninstall vantik -n kdb-control-plane   # first - PVCs use reclaimPolicy Retain (see storage.tf), so this
                                               # does NOT delete your trading history; delete those PVs
                                               # explicitly if you actually want the data gone
terraform destroy                             # then the cluster itself
```

## Variable reference

See `variables.tf` (every variable has a full description inline) and
`terraform.tfvars.example`. The one non-obvious concept: `cluster_profile`
(`ha` | `performance` | `cost_optimized`) is an **infrastructure**
redundancy/cost tradeoff — AZ count, NAT gateway count, node sizing — and
is a completely different, orthogonal knob from the *application's* own
per-TickHouse workload profile (`control-api/app/tickhouse.py`'s
`high-throughput`/`low-latency`/`balanced`, which sizes individual q
process hardware). Don't conflate the two when reading either one's name.
