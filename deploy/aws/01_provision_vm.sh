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
DISK_SIZE_GB="${DISK_SIZE_GB:-100}"
PLACEMENT_GROUP="${VM_NAME}-cluster"
SG_NAME="${SG_NAME:-${VM_NAME}-sg}"

# default: compute-optimized, high-clock, ENA. Bump to c7i.4xlarge for 16/32.
INSTANCE_TYPE="${INSTANCE_TYPE:-c7i.2xlarge}"
if [ "${ENABLE_FPGA:-0}" = "1" ]; then
  # f2.6xlarge is the smallest / cheapest F2 (1 FPGA, 24 vCPU, 256 GB). The
  # bigger f2.12xlarge / f2.48xlarge carry 4 and 8 FPGAs.
  INSTANCE_TYPE="${FPGA_INSTANCE_TYPE:-f2.6xlarge}"
  echo "!! ENABLE_FPGA=1: provisioning $INSTANCE_TYPE."
  echo "!! The FPGA does nothing until you load your own AFI/bitstream. See README."
fi

export AWS_DEFAULT_REGION="$REGION"

echo "== resolving latest Ubuntu 22.04 AMI (Canonical, via SSM public parameter) =="
AMI_ID="$(aws ssm get-parameters \
  --names /aws/service/canonical/ubuntu/server/22.04/stable/current/amd64/hvm/ebs-gp2/ami-id \
  --query 'Parameters[0].Value' --output text)"
echo "   AMI: $AMI_ID"

echo "== ensuring an SSH key pair ($KEY_NAME) =="
if ! aws ec2 describe-key-pairs --key-names "$KEY_NAME" >/dev/null 2>&1; then
  aws ec2 create-key-pair --key-name "$KEY_NAME" \
    --query 'KeyMaterial' --output text > "${KEY_NAME}.pem"
  chmod 400 "${KEY_NAME}.pem"
  echo "   wrote private key to ${KEY_NAME}.pem - keep it safe, it is not recoverable"
else
  echo "   key pair already exists (assuming you hold ${KEY_NAME}.pem)"
fi

echo "== ensuring a CLUSTER placement group =="
aws ec2 create-placement-group --group-name "$PLACEMENT_GROUP" --strategy cluster \
  2>/dev/null || echo "   placement group already exists, continuing"

echo "== looking up the security group created by 02_configure_networking.sh =="
SG_ID="$(aws ec2 describe-security-groups --group-names "$SG_NAME" \
  --query 'SecurityGroups[0].GroupId' --output text 2>/dev/null || true)"
if [ -z "$SG_ID" ] || [ "$SG_ID" = "None" ]; then
  echo "   security group '$SG_NAME' not found."
  echo "   Run ./02_configure_networking.sh first (it creates the SG + rules)."
  exit 1
fi
echo "   SG: $SG_ID"

echo "== launching the instance =="
INSTANCE_ID="$(aws ec2 run-instances \
  --image-id "$AMI_ID" \
  --instance-type "$INSTANCE_TYPE" \
  --key-name "$KEY_NAME" \
  --security-group-ids "$SG_ID" \
  --placement "GroupName=$PLACEMENT_GROUP" \
  --block-device-mappings "DeviceName=/dev/sda1,Ebs={VolumeSize=${DISK_SIZE_GB},VolumeType=gp3}" \
  --metadata-options "HttpTokens=required,HttpEndpoint=enabled" \
  --tag-specifications "ResourceType=instance,Tags=[{Key=Name,Value=$VM_NAME},{Key=app,Value=kdb-control-plane-demo}]" \
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
