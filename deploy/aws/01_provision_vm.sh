#!/usr/bin/env bash
# 01_provision_vm.sh - creates the AWS EC2 instance that runs the whole demo stack.
#
# IMPORTANT - read before running:
# By default this uses a compute-optimized, high-clock instance with AWS's real
# low-latency levers - NOT an FPGA instance:
#   - a compute-optimized C7i instance (high sustained per-core clock)
#   - a CLUSTER placement group (packs instances onto the same high-bisection
#     -bandwidth segment to cut inter-instance latency - matters once you split
#     this single box into multiple nodes for real multi-node testing)
#   - ENA enhanced networking (on by default for C7i + modern Ubuntu AMIs)
#
# OPTIONAL FPGA (opt-in, off by default): set ENABLE_FPGA=1 to provision an
# F2 instance (AMD Virtex UltraScale+ FPGA) instead. READ deploy/aws/README.md
# first. This ONLY stands up the FPGA-capable box - the FPGA sits idle until
# YOU build an Amazon FPGA Image (AFI) with the AWS FPGA Development Kit and
# integrate your feed handler to offload to it. kdb+ does not use the FPGA
# automatically, and stock q gains nothing from an F2 over a C7i. Don't imply
# otherwise to a client. F2 also needs a service-quota increase (default limit
# is often 0) and is only in some regions - see the README.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
VM_NAME="${VM_NAME:-kdb-control-plane-demo}"
KEY_NAME="${KEY_NAME:-${VM_NAME}-key}"
PLACEMENT_GROUP="${VM_NAME}-cluster"
SG_NAME="${SG_NAME:-${VM_NAME}-sg}"

# FREE_TIER=1 - explicit "I'm on/want a free-tier-eligible account" signal.
# t3.micro (2 vCPU burstable, 1GB) and 30GB gp3 are AWS's actual Free Tier
# allowances - not a guess. Skips the cluster placement group too (nothing
# to cluster with one box, and burstable families have inconsistent
# placement-group support).
FREE_TIER="${FREE_TIER:-0}"
FREE_TIER_INSTANCE_TYPE="${FREE_TIER_INSTANCE_TYPE:-t3.micro}"
FREE_TIER_DISK_SIZE_GB="${FREE_TIER_DISK_SIZE_GB:-30}"

# Captured before defaults are applied, so the auto-fallback below only
# ever kicks in when the CALLER didn't ask for a specific size - an
# explicit INSTANCE_TYPE/DISK_SIZE_GB is always respected as-is, never
# second-guessed. (Must capture DISK_SIZE_GB here too, not inline in the
# FREE_TIER branch below - by then the "${DISK_SIZE_GB:-100}" default a
# few lines down has already made it non-empty, so a later
# "${DISK_SIZE_GB:-$FREE_TIER_DISK_SIZE_GB}" would never actually fall
# back to the free-tier size.)
_INSTANCE_TYPE_EXPLICIT="${INSTANCE_TYPE:-}"
_DISK_SIZE_GB_EXPLICIT="${DISK_SIZE_GB:-}"
# default: compute-optimized, high-clock, ENA. Bump to c7i.4xlarge for 16/32.
INSTANCE_TYPE="${INSTANCE_TYPE:-c7i.2xlarge}"
DISK_SIZE_GB="${DISK_SIZE_GB:-100}"
export AWS_DEFAULT_REGION="$REGION"

if [ "${ENABLE_FPGA:-0}" = "1" ]; then
  # f2.6xlarge is the smallest / cheapest F2 (1 FPGA, 24 vCPU, 256 GB). The
  # bigger f2.12xlarge / f2.48xlarge carry 4 and 8 FPGAs.
  INSTANCE_TYPE="${FPGA_INSTANCE_TYPE:-f2.6xlarge}"
  echo "!! ENABLE_FPGA=1: provisioning $INSTANCE_TYPE."
  echo "!! The FPGA does nothing until you load your own AFI/bitstream. See README."
elif [ "$FREE_TIER" = "1" ]; then
  INSTANCE_TYPE="$FREE_TIER_INSTANCE_TYPE"
  DISK_SIZE_GB="${_DISK_SIZE_GB_EXPLICIT:-$FREE_TIER_DISK_SIZE_GB}"
  echo "== FREE_TIER=1: provisioning $INSTANCE_TYPE / ${DISK_SIZE_GB}GB (AWS's"
  echo "   actual Free Tier allowances) - skipping the cluster placement group =="
elif [ -z "$_INSTANCE_TYPE_EXPLICIT" ]; then
  # No explicit size, not FPGA, not an explicit FREE_TIER ask - best-effort
  # check of whether THIS account can even fit the compute-optimized
  # default before attempting run-instances at all. Real AWS Service
  # Quotas, not a guess: L-1216C47A is "Running On-Demand Standard (A, C,
  # D, H, I, M, R, T, Z) instances", measured in vCPUs - exactly what a C7i
  # launch draws against. New/free-tier accounts commonly start this low
  # enough that an 8-vCPU c7i.2xlarge won't fit. Soft-fails (leaves
  # INSTANCE_TYPE untouched) on ANY error here - a permissions gap or a
  # transient Service Quotas API issue must never block a deploy that
  # might otherwise have worked; run-instances remains the final word.
  echo "== checking this account's vCPU quota can fit $INSTANCE_TYPE =="
  NEEDED_VCPUS="$(aws ec2 describe-instance-types --instance-types "$INSTANCE_TYPE" \
    --query 'InstanceTypes[0].VCpuInfo.DefaultVCpus' --output text 2>/dev/null || true)"
  QUOTA_VCPUS="$(aws service-quotas get-service-quota \
    --service-code ec2 --quota-code L-1216C47A \
    --query 'Quota.Value' --output text 2>/dev/null || true)"
  if [ -n "$NEEDED_VCPUS" ] && [ "$NEEDED_VCPUS" != "None" ] \
     && [ -n "$QUOTA_VCPUS" ] && [ "$QUOTA_VCPUS" != "None" ] \
     && awk -v q="$QUOTA_VCPUS" -v n="$NEEDED_VCPUS" 'BEGIN{exit !(q<n)}' 2>/dev/null; then
    echo "   this account's On-Demand Standard vCPU quota (${QUOTA_VCPUS}) is below"
    echo "   what $INSTANCE_TYPE needs (${NEEDED_VCPUS}) - looks like a free-tier or"
    echo "   brand-new account. Falling back to $FREE_TIER_INSTANCE_TYPE."
    echo "   Force the original size anyway with:"
    echo "     INSTANCE_TYPE=$INSTANCE_TYPE ./01_provision_vm.sh"
    INSTANCE_TYPE="$FREE_TIER_INSTANCE_TYPE"
    DISK_SIZE_GB="${_DISK_SIZE_GB_EXPLICIT:-$FREE_TIER_DISK_SIZE_GB}"
    FREE_TIER=1
  else
    echo "   quota check inconclusive or sufficient - continuing with $INSTANCE_TYPE"
  fi
fi

# AMI resolution - three layers, in order, because this is the #1 reported
# failure point of this script: (1) an explicit override, for when you
# already know the AMI you want or auto-resolution keeps failing;
# (2) the SSM public-parameter lookup (fast, usually works); (3) a direct
# EC2 describe-images query against Canonical's official AWS account as a
# fallback, since the SSM parameter path has moved/lagged in the past and
# some IAM policies scope ssm:GetParameters away from public parameters
# while still allowing ec2:DescribeImages. Every layer is checked for a
# real result before moving on - silently passing an empty/"None" AMI_ID
# into run-instances was the actual root cause of confusing failures here,
# not the lookup method itself.
if [ -n "${AMI_ID:-}" ]; then
  echo "== using explicitly set AMI_ID=$AMI_ID (skipping auto-resolution) =="
else
  echo "== resolving latest Ubuntu 22.04 AMI for $REGION (SSM public parameter) =="
  AMI_ID="$(aws ssm get-parameters \
    --names /aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id \
    --query 'Parameters[0].Value' --output text 2>/dev/null || true)"

  if [ -z "$AMI_ID" ] || [ "$AMI_ID" = "None" ]; then
    echo "   SSM lookup returned nothing for $REGION - falling back to a direct"
    echo "   EC2 describe-images query against Canonical's AMI catalog."
    AMI_ID="$(aws ec2 describe-images \
      --owners 099720109477 \
      --filters "Name=name,Values=ubuntu/images/hvm-ssd/ubuntu-jammy-22.04-amd64-server-*" \
                "Name=state,Values=available" \
                "Name=architecture,Values=x86_64" \
      --query 'sort_by(Images, &CreationDate)[-1].ImageId' --output text 2>/dev/null || true)"
  fi

  if [ -z "$AMI_ID" ] || [ "$AMI_ID" = "None" ]; then
    cat >&2 <<EOF

ERROR: could not resolve an Ubuntu 22.04 AMI for region '$REGION' automatically.

This usually means one of:
  - your IAM identity lacks ssm:GetParameters and/or ec2:DescribeImages
    permission (try: aws sts get-caller-identity, then check the attached
    policy covers both - public SSM parameters and Canonical's AMIs don't
    need special cross-account permissions, just these two actions allowed
    at all)
  - a transient AWS API issue - wait a minute and re-run
  - (rare) a brand-new region Canonical hasn't finished publishing to yet

Workaround: find a known-good AMI ID yourself - AWS Console -> EC2 ->
AMI Catalog -> search "Ubuntu 22.04" -> filter to region '$REGION' - then
re-run with it set explicitly:
  AMI_ID=ami-xxxxxxxxxxxxxxxxx ./01_provision_vm.sh

EOF
    exit 1
  fi
fi
echo "   AMI: $AMI_ID"

echo "== checking '$INSTANCE_TYPE' is offered in $REGION =="
OFFERED="$(aws ec2 describe-instance-type-offerings \
  --location-type region \
  --filters "Name=instance-type,Values=$INSTANCE_TYPE" \
  --query 'InstanceTypeOfferings[0].InstanceType' --output text 2>/dev/null || true)"
if [ -z "$OFFERED" ] || [ "$OFFERED" = "None" ]; then
  cat >&2 <<EOF

ERROR: instance type '$INSTANCE_TYPE' is not offered in region '$REGION'.
Newer compute-optimized families (C7i included) roll out to regions on
their own schedule - not every region has every generation yet.

Fix: either pick a region that has it -
  aws ec2 describe-instance-type-offerings --location-type region \\
    --filters Name=instance-type,Values=$INSTANCE_TYPE \\
    --query 'InstanceTypeOfferings[].Location' --output text
or pick an instance type this region does have and re-run with it set:
  INSTANCE_TYPE=c6i.2xlarge ./01_provision_vm.sh

EOF
  exit 1
fi

echo "== ensuring an SSH key pair ($KEY_NAME) =="
if ! aws ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1; then
  aws ec2 create-key-pair --key-name "$KEY_NAME" \
    --query 'KeyMaterial' --output text > "${KEY_NAME}.pem"
  chmod 400 "${KEY_NAME}.pem"
  echo "   wrote private key to ${KEY_NAME}.pem - keep it safe, it is not recoverable"
else
  echo "   key pair already exists (assuming you hold ${KEY_NAME}.pem)"
fi

PLACEMENT_ARGS=()
if [ "$FREE_TIER" = "1" ]; then
  echo "== skipping the cluster placement group (FREE_TIER=1 - nothing to"
  echo "   cluster with one small box, and burstable families have"
  echo "   inconsistent placement-group support) =="
else
  echo "== ensuring a CLUSTER placement group =="
  aws ec2 create-placement-group --group-name "$PLACEMENT_GROUP" --strategy cluster \
    2>/dev/null || echo "   placement group already exists, continuing"
  PLACEMENT_ARGS=(--placement "GroupName=$PLACEMENT_GROUP")
fi

echo "== looking up the security group created by 02_configure_networking.sh =="
SG_ID="$(aws ec2 describe-security-groups --group-names "$SG_NAME" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)"
if [ -z "$SG_ID" ] || [ "$SG_ID" = "None" ]; then
  echo "   security group '$SG_NAME' not found."
  echo "   Run ./02_configure_networking.sh first (it creates the SG + rules)."
  exit 1
fi
echo "   SG: $SG_ID"

TIER_TAG=""
[ "$FREE_TIER" = "1" ] && TIER_TAG=",{Key=tier,Value=free}"

echo "== launching the instance ($INSTANCE_TYPE) =="
INSTANCE_ID="$(aws ec2 run-instances \
  --image-id "$AMI_ID" \
  --instance-type "$INSTANCE_TYPE" \
  --key-name "$KEY_NAME" \
  --security-group-ids "$SG_ID" \
  ${PLACEMENT_ARGS[@]+"${PLACEMENT_ARGS[@]}"} \
  --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=${DISK_SIZE_GB},VolumeType=gp3}" \
  --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$VM_NAME},{Key=app,Value=kdb-control-plane-demo}${TIER_TAG}]" \
  --query 'Instances[0].InstanceId' --output text)"
echo "   instance: $INSTANCE_ID"

echo "== waiting for it to reach 'running' =="
aws ec2 wait instance-running --instance-ids "$INSTANCE_ID"

echo "== allocating + associating an Elastic IP =="
ALLOC_ID="$(aws ec2 allocate-address --domain vpc \
  --tag-specifications "ResourceType=elastic-ip,Tags=[{Key=Name,Value=${VM_NAME}-ip}]" \
  --query 'AllocationId' --output text)"
aws ec2 associate-address --instance-id "$INSTANCE_ID" --allocation-id "$ALLOC_ID" >/dev/null

PUBLIC_IP="$(aws ec2 describe-addresses --allocation-ids "$ALLOC_ID" \
  --query 'Addresses[0].PublicIp' --output text)"

echo
echo "== done =="
echo "Instance ID: $INSTANCE_ID"
echo "Public IP:   $PUBLIC_IP"
echo "SSH:         ssh -i ${KEY_NAME}.pem ubuntu@${PUBLIC_IP}"
