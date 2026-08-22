# free_tier.sh - cloud-agnostic detection of whether the box
# 04_deploy_stack.sh is running on is free-tier-class (too small for the
# full multi-shard, LLM-enabled stack), and what to do about it. Sourced by
# deploy/{aws,azure,gcp}/04_deploy_stack.sh - written once here instead of
# three times because the detection itself is NOT cloud-specific: it reads
# the box's own actual CPU/RAM, not a cloud API. That's deliberately more
# robust than asking each cloud "what tier is this account" - it also
# correctly handles a VM created by hand (console, not these scripts),
# resized after creation, or a box on a cloud these scripts don't cover.
#
# Real AWS/Azure/GCP free-tier VM sizes (t3.micro/t2.micro, Standard_B1s,
# e2-micro) are ~1 vCPU / 1GB RAM - nowhere near this stack's default
# footprint (2 shards x 5 kdb+ processes each, gateway, control-api, +ollama
# at ~2.4GB alone - see docker-compose.yml's ollama comment). Below
# FREE_TIER_MIN_MEM_MB we switch to the lean profile: 1 shard, ollama off.
# Not a guess - deploy/{aws,azure,gcp}/01_provision_vm.sh's own account-quota
# checks feed into the SAME FREE_TIER=1 signal this reads, so a box those
# scripts steered onto a free-tier size lands here automatically too.
FREE_TIER_MIN_MEM_MB="${FREE_TIER_MIN_MEM_MB:-3584}"   # ~3.5GB

# Sets LEAN_MODE=1/0 - call this, then check "$LEAN_MODE". No side effects
# (safe to call before .env exists) - see apply_lean_mode for the part that
# actually changes anything.
detect_lean_mode() {
  local mem_kb mem_mb cpus
  mem_kb="$(awk '/MemTotal/{print $2}' /proc/meminfo 2>/dev/null || echo 0)"
  mem_mb=$(( mem_kb / 1024 ))
  cpus="$(nproc 2>/dev/null || echo 1)"

  if [ "${FREE_TIER:-0}" = "1" ]; then
    echo "== FREE_TIER=1 set - using the lean profile regardless of detected specs =="
    LEAN_MODE=1
  elif [ "$mem_mb" -gt 0 ] && [ "$mem_mb" -lt "$FREE_TIER_MIN_MEM_MB" ]; then
    echo "== detected ${mem_mb}MB RAM / ${cpus} vCPU on this box - below the"
    echo "   ${FREE_TIER_MIN_MEM_MB}MB lean-mode threshold =="
    echo "   Falling back to the lean profile: 1 shard, ollama (NL2Q) off."
    echo "   Override with FREE_TIER_MIN_MEM_MB=<mb> if this box has more real"
    echo "   headroom than it looks, or FREE_TIER=0 to force the full stack"
    echo "   anyway (likely to OOM at this size)."
    LEAN_MODE=1
  else
    LEAN_MODE=0
  fi
}

# Applies LEAN_MODE=1: regenerates docker-compose.yml/shards.json for 1
# shard and clears the "llm" profile from .env's COMPOSE_PROFILES, so the
# `docker compose build`/`up -d` that follows in 04_deploy_stack.sh picks
# both up. Must run AFTER .env is confirmed to exist (04_deploy_stack.sh's
# own "No .env found" bootstrap check runs first). No-op when LEAN_MODE=0 -
# never touches a topology someone already sized on purpose.
apply_lean_mode() {
  if [ "$LEAN_MODE" != "1" ]; then return 0; fi

  echo "== regenerating docker-compose.yml for 1 shard (lean mode) =="
  python3 scripts/gen_topology.py --shards 1 --compose docker-compose.yml \
    --shards-json data-plane/shards.json --eod-hour 0 --idb-retention-days 5

  echo "== clearing the 'llm' compose profile (ollama) in .env for this box =="
  if grep -q '^COMPOSE_PROFILES=' .env; then
    sed -i.bak 's/^COMPOSE_PROFILES=.*/COMPOSE_PROFILES=/' .env && rm -f .env.bak
  else
    echo "COMPOSE_PROFILES=" >> .env
  fi
  echo "   NL2Q's natural-language-to-q box (Query workspace) will show as"
  echo "   unavailable until you size up and set COMPOSE_PROFILES=llm in .env"
  echo "   yourself - everything else (tickerplant, one shard's RDB/WDB/IDB/"
  echo "   HDB, gateway, control-api, web-ui) runs as normal, just on 1 shard"
  echo "   instead of the default 2."
}
