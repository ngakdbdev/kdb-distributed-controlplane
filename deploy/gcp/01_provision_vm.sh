#!/usr/bin/env bash
# 01_provision_vm.sh - creates the GCP VM that will run the whole demo stack.
#
# IMPORTANT - read before running:
# GCP has no FPGA instance family (that is AWS F1/Xilinx territory - GCP has
# never offered FPGA-backed VMs). This script uses GCP's actual closest
# equivalents for low latency instead of pretending otherwise:
#   - a compute-optimized C3 machine type (highest per-core clock available)
#   - Tier_1 networking (doubles per-VM network bandwidth, requires gVNIC)
#   - a COMPACT placement policy (packs VMs onto adjacent physical hosts to
#     cut inter-VM network hop latency - matters once you split this single
#     VM into multiple VMs for real multi-node testing)
# For genuine FPGA acceleration you would need AWS F1 instances or an
# on-prem/colo FPGA appliance - that is a separate, non-GCP workstream.
set -euo pipefail

PROJECT_ID="${GCP_PROJECT_ID:?set GCP_PROJECT_ID first}"
ZONE="${GCP_ZONE:-us-central1-a}"
REGION="${GCP_ZONE%-*}"
VM_NAME="${VM_NAME:-kdb-control-plane-demo}"
IMAGE_FAMILY="${IMAGE_FAMILY:-ubuntu-2204-lts}"
IMAGE_PROJECT="${IMAGE_PROJECT:-ubuntu-os-cloud}"

# FREE_TIER=1 - explicit "I'm on/want a free-eligible project" signal.
# e2-micro (burstable, 1GB) is GCP's actual Always Free machine type - not
# a guess - and its Always Free disk allowance is 30GB of STANDARD (not
# SSD) persistent disk, so both the type and disk-type flip here, not just
# the size. Always Free e2-micro is also only genuinely free in
# us-west1/us-central1/us-east1 - it runs fine elsewhere, just isn't free
# there, so this warns (not blocks) on a region mismatch. Skips the
# COMPACT placement policy and Tier_1/gVNIC networking too - e2-micro
# doesn't support gVNIC, and neither is useful for one small box anyway.
FREE_TIER="${FREE_TIER:-0}"
FREE_TIER_MACHINE_TYPE="${FREE_TIER_MACHINE_TYPE:-e2-micro}"
FREE_TIER_DISK_SIZE_GB="${FREE_TIER_DISK_SIZE_GB:-30}"
FREE_TIER_REGIONS="us-west1 us-central1 us-east1"

# Captured before the default is applied, so the auto-fallback below only
# ever kicks in when the CALLER didn't ask for a specific type - an
# explicit MACHINE_TYPE/DISK_SIZE_GB is always respected as-is, never
# second-guessed. (Must capture DISK_SIZE_GB here too, not inline in the
# FREE_TIER branch below - by then the "${DISK_SIZE_GB:-100}" default a
# few lines down has already made it non-empty, so a later
# "${DISK_SIZE_GB:-$FREE_TIER_DISK_SIZE_GB}" would never actually fall
# back to the free-tier size.)
_MACHINE_TYPE_EXPLICIT="${MACHINE_TYPE:-}"
_DISK_SIZE_GB_EXPLICIT="${DISK_SIZE_GB:-}"
MACHINE_TYPE="${MACHINE_TYPE:-c3-standard-8}"   # 8 vCPU / 32GB, compute-optimized
DISK_SIZE_GB="${DISK_SIZE_GB:-100}"
BOOT_DISK_TYPE="pd-ssd"

echo "== setting active project =="
gcloud config set project "$PROJECT_ID"

echo "== enabling required APIs =="
gcloud services enable compute.googleapis.com

if [ "$FREE_TIER" = "1" ]; then
  MACHINE_TYPE="$FREE_TIER_MACHINE_TYPE"
  DISK_SIZE_GB="${_DISK_SIZE_GB_EXPLICIT:-$FREE_TIER_DISK_SIZE_GB}"
  BOOT_DISK_TYPE="pd-standard"
  echo "== FREE_TIER=1: provisioning $MACHINE_TYPE / ${DISK_SIZE_GB}GB pd-standard"
  echo "   (GCP's actual Always Free machine type + disk) - skipping the COMPACT"
  echo "   placement policy and Tier_1/gVNIC networking =="
  case " $FREE_TIER_REGIONS " in
    *" $REGION "*) : ;;
    *) echo "   NOTE: $REGION is not one of GCP's Always Free regions ($FREE_TIER_REGIONS)."
       echo "   $MACHINE_TYPE will run here, it just won't be \$0 - set GCP_ZONE to a zone"
       echo "   in one of those regions (e.g. us-central1-a) for genuinely free." ;;
  esac
elif [ -z "$_MACHINE_TYPE_EXPLICIT" ]; then
  # No explicit type and not an explicit FREE_TIER ask - best-effort check
  # of whether THIS project's regional CPU quota can even fit the
  # compute-optimized default before attempting `instances create` at all.
  # Real GCP quota data, not a guess: the "CPUS" metric on `regions
  # describe` is exactly what a c3-standard-8 (8 vCPU) draws against.
  # New/free-tier projects commonly start this low enough that it won't
  # fit. Soft-fails (leaves MACHINE_TYPE untouched) on ANY error here - a
  # permissions gap or a transient API issue must never block a deploy
  # that might otherwise have worked; `instances create` remains the final
  # word.
  echo "== checking this project's regional CPU quota can fit $MACHINE_TYPE =="
  NEEDED_VCPUS="$(gcloud compute machine-types describe "$MACHINE_TYPE" --zone "$ZONE" \
    --format='value(guestCpus)' 2>/dev/null || true)"
  QUOTA_TSV="$(gcloud compute regions describe "$REGION" \
    --flatten="quotas[]" --filter="quotas.metric=CPUS" \
    --format="value(quotas.limit,quotas.usage)" 2>/dev/null || true)"
  LIMIT_VCPUS="$(echo "$QUOTA_TSV" | awk '{print $1}')"
  USAGE_VCPUS="$(echo "$QUOTA_TSV" | awk '{print $2}')"
  if [ -n "$NEEDED_VCPUS" ] && [ -n "$LIMIT_VCPUS" ] && [ -n "$USAGE_VCPUS" ] \
     && awk -v l="$LIMIT_VCPUS" -v u="$USAGE_VCPUS" -v n="$NEEDED_VCPUS" \
       'BEGIN{exit !((l-u)<n)}' 2>/dev/null; then
    echo "   available regional CPU headroom ($((LIMIT_VCPUS - USAGE_VCPUS))) is below"
    echo "   what $MACHINE_TYPE needs (${NEEDED_VCPUS}) - looks like a free-tier or"
    echo "   brand-new project. Falling back to $FREE_TIER_MACHINE_TYPE."
    echo "   Force the original type anyway with:"
    echo "     MACHINE_TYPE=$MACHINE_TYPE ./01_provision_vm.sh"
    MACHINE_TYPE="$FREE_TIER_MACHINE_TYPE"
    DISK_SIZE_GB="${_DISK_SIZE_GB_EXPLICIT:-$FREE_TIER_DISK_SIZE_GB}"
    BOOT_DISK_TYPE="pd-standard"
    FREE_TIER=1
  else
    echo "   quota check inconclusive or sufficient - continuing with $MACHINE_TYPE"
  fi
fi

# Machine-type availability per zone is the #1 reported failure point of
# this script - compute-optimized C3 has materially narrower zone coverage
# than general-purpose families, and picking a zone that doesn't have it
# otherwise surfaces as a buried "not available" error from `instances
# create`. Check first, with a clear fix.
echo "== checking '$MACHINE_TYPE' is offered in $ZONE =="
OFFERED="$(gcloud compute machine-types list \
  --filter="name=$MACHINE_TYPE AND zone:$ZONE" \
  --format='value(name)' 2>/dev/null || true)"
if [ -z "$OFFERED" ]; then
  cat >&2 <<EOF

ERROR: machine type '$MACHINE_TYPE' is not offered in zone '$ZONE'.

Fix: either pick a zone that has it -
  gcloud compute machine-types list --filter="name=$MACHINE_TYPE" \\
    --format='value(zone)'
or pick a machine type this zone does have and re-run with it set:
  MACHINE_TYPE=n2-standard-8 ./01_provision_vm.sh

EOF
  exit 1
fi

EXTRA_VM_ARGS=()
TAGS="kdb-control-plane-demo"
if [ "$FREE_TIER" = "1" ]; then
  echo "== FREE_TIER=1: skipping the COMPACT placement policy + Tier_1/gVNIC =="
  TAGS="$TAGS,tier-free"
else
  echo "== creating a COMPACT placement policy (adjacent-host packing) =="
  gcloud compute resource-policies create group-placement \
    "${VM_NAME}-placement" \
    --region "$REGION" \
    --collocation COLLOCATED \
    || echo "placement policy already exists, continuing"
  EXTRA_VM_ARGS=(
    --network-interface=nic-type=GVNIC
    --network-performance-configs=total-egress-bandwidth-tier=TIER_1
    --resource-policies="${VM_NAME}-placement"
  )
fi

echo "== creating the VM ($MACHINE_TYPE) =="
gcloud compute instances create "$VM_NAME" \
  --zone "$ZONE" \
  --machine-type "$MACHINE_TYPE" \
  --image-family="$IMAGE_FAMILY" \
  --image-project="$IMAGE_PROJECT" \
  --boot-disk-size "${DISK_SIZE_GB}GB" \
  --boot-disk-type="$BOOT_DISK_TYPE" \
  ${EXTRA_VM_ARGS[@]+"${EXTRA_VM_ARGS[@]}"} \
  --tags="$TAGS" \
  --metadata=enable-oslogin=true

echo "== reserving a static external IP =="
gcloud compute addresses create "${VM_NAME}-ip" --region "$REGION" || echo "IP already reserved, continuing"
gcloud compute instances add-access-config "$VM_NAME" \
  --zone "$ZONE" \
  --address "$(gcloud compute addresses describe "${VM_NAME}-ip" --region "$REGION" --format='get(address)')" \
  || echo "access config may already be set, check manually if this failed"

echo "== done - VM external IP: =="
gcloud compute instances describe "$VM_NAME" --zone "$ZONE" \
  --format='get(networkInterfaces[0].accessConfigs[0].natIP)'
