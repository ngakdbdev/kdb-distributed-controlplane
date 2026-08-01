#!/usr/bin/env bash
# 99_teardown.sh - deletes everything this deployment created, so the demo
# doesn't quietly keep billing after you're done. F2 instances in particular
# are expensive - do not leave one running.
set -euo pipefail

REGION="${AWS_REGION:-us-east-1}"
VM_NAME="${VM_NAME:-kdb-control-plane-demo}"
KEY_NAME="${KEY_NAME:-${VM_NAME}-key}"
SG_NAME="${SG_NAME:-${VM_NAME}-sg}"
PLACEMENT_GROUP="${VM_NAME}-cluster"
export AWS_DEFAULT_REGION="$REGION"

echo "== finding the instance by Name tag =="
INSTANCE_ID="$(aws ec2 describe-instances \
  --filters "Name=tag:Name,Values=$VM_NAME" "Name=instance-state-name,Values=pending,running,stopping,stopped" \
  --query 'Reservations[0].Instances[0].InstanceId' --output text 2>/dev/null || true)"

if [ -n "$INSTANCE_ID" ] && [ "$INSTANCE_ID" != "None" ]; then
  echo "== terminating $INSTANCE_ID =="
  aws ec2 terminate-instances --instance-ids "$INSTANCE_ID" >/dev/null
  aws ec2 wait instance-terminated --instance-ids "$INSTANCE_ID"
else
  echo "   no running instance found, continuing"
fi

echo "== releasing the Elastic IP =="
ALLOC_ID="$(aws ec2 describe-addresses \
  --filters "Name=tag:Name,Values=${VM_NAME}-ip" \
  --query 'Addresses[0].AllocationId' --output text 2>/dev/null || true)"
if [ -n "$ALLOC_ID" ] && [ "$ALLOC_ID" != "None" ]; then
  aws ec2 release-address --allocation-id "$ALLOC_ID" || true
fi

echo "== deleting the security group =="
aws ec2 delete-security-group --group-name "$SG_NAME" 2>/dev/null || \
  echo "   SG not deleted (may not exist, or still referenced) - check manually"

echo "== deleting the placement group =="
aws ec2 delete-placement-group --group-name "$PLACEMENT_GROUP" 2>/dev/null || true

echo "== leaving the key pair '$KEY_NAME' in place (delete it yourself if done) =="
echo "   aws ec2 delete-key-pair --key-name $KEY_NAME   # and rm ${KEY_NAME}.pem"

echo "== done - check the EC2 console to confirm nothing is still running =="
