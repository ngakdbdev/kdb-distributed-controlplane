#!/usr/bin/env bash
# 02_configure_networking.sh - creates the security group and opens only what
# the demo needs: SSH, HTTP (web UI), and the control API port for debugging.
# Run this BEFORE 01_provision_vm.sh (which looks the SG up by name).
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
VM_NAME="${VM_NAME:-kdb-control-plane-demo}"
SG_NAME="${SG_NAME:-${VM_NAME}-sg}"
export AWS_DEFAULT_REGION="$REGION"

# default VPC by default; override VPC_ID to use another
VPC_ID="${VPC_ID:-$(aws ec2 describe-vpcs --filters Name=isDefault,Values=true \
  --query 'Vpcs[0].VpcId' --output text)}"

echo "== creating security group '$SG_NAME' in $VPC_ID =="
SG_ID="$(aws ec2 create-security-group \
  --group-name "$SG_NAME" \
  --description "kdb+ control plane demo" \
  --vpc-id "$VPC_ID" \
  --query 'GroupId' --output text 2>/dev/null || \
  aws ec2 describe-security-groups --group-names "$SG_NAME" \
    --query 'SecurityGroups[0].GroupId' --output text)"
echo "   SG: $SG_ID"

add_rule() {  # port, cidr, description
  aws ec2 authorize-security-group-ingress \
    --group-id "$SG_ID" \
    --ip-permissions "IpProtocol=tcp,FromPort=$1,ToPort=$1,IpRanges=[{CidrIp=$2,Description=$3}]" \
    2>/dev/null || echo "   rule for port $1 already present, continuing"
}

echo "== allow SSH (restrict ALLOWED_SSH_CIDR to your own IP in production) =="
add_rule 22 "${ALLOWED_SSH_CIDR:-0.0.0.0/0}" "ssh"

echo "== allow HTTP for the web UI =="
add_rule 80 "0.0.0.0/0" "web-ui"

echo "== allow direct control-api access for debugging (tighten/remove for real client demos) =="
add_rule 8000 "${ALLOWED_ADMIN_CIDR:-0.0.0.0/0}" "control-api"

echo
echo "== done - security group ready. Note: kdb+ IPC ports (5010-5050) are"
echo "   deliberately NOT exposed - they stay on the Docker bridge network and"
echo "   are only reachable from other containers on the instance."
