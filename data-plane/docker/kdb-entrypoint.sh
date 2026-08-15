#!/bin/sh
# kdb-entrypoint.sh - choose the KX/q binary for THIS container's platform, then
# exec q with the service's args. Two ways to get the binary, tried in order:
#
#   1. LOCAL   - files in the mounted binaries folder ($KX_BINARIES_DIR, default
#                /kdbx). Drop either the KX portal <arch>.zip (the container
#                unzips it into the writable cache) OR a pre-unzipped <arch>/q.
#                q.k is embedded in the binary, so no separate q.k is needed.
#   2. KX-PORTAL - if the binary isn't staged and KX_INSTALL_SOURCE=kx-portal
#                with KX_BEARER_TOKEN set, pull <arch>.zip from portal.dl.kx.com
#                into a writable cache and use that. The token is passed to curl
#                via a mode-600 config file (kept out of the process args) and is
#                never echoed.
#
# Same image runs on amd64 or arm64: the arch is resolved from uname at start.
# You always supply your own LICENCE (kc.lic) - via the data folder or QLIC.
set -e

: "${KX_BINARIES_DIR:=/kdbx}"
: "${KX_CACHE_DIR:=/kdbx-cache}"
: "${KX_INSTALL_SOURCE:=local}"        # local | kx-portal
: "${KX_PORTAL_BASE:=https://portal.dl.kx.com}"
: "${KX_VERSION:=4.1}"
: "${KX_CHANNEL:=~latest~}"

# resolve arch unless pinned via KX_ARCH
if [ -z "$KX_ARCH" ]; then
  case "$(uname -m)" in
    x86_64|amd64)   KX_ARCH=l64 ;;
    aarch64|arm64)  KX_ARCH=l64arm ;;
    *)              KX_ARCH=l64 ;;
  esac
fi

QHOME=""
if [ -x "$KX_BINARIES_DIR/$KX_ARCH/q" ]; then
  QHOME="$KX_BINARIES_DIR"                       # pre-unzipped binary in the folder
elif [ -x "$KX_CACHE_DIR/$KX_ARCH/q" ]; then
  QHOME="$KX_CACHE_DIR"                          # already unpacked/pulled earlier
elif [ -f "$KX_BINARIES_DIR/$KX_ARCH.zip" ]; then
  # you dropped <arch>.zip in the (read-only) binaries folder -> unzip it into
  # the writable cache and run from there. Clear the kx-cache volume to re-unzip.
  echo "kdb-entrypoint: unpacking staged $KX_ARCH.zip -> $KX_CACHE_DIR" >&2
  mkdir -p "$KX_CACHE_DIR"
  unzip -oq "$KX_BINARIES_DIR/$KX_ARCH.zip" -d "$KX_CACHE_DIR"
  QHOME="$KX_CACHE_DIR"
elif [ "$KX_INSTALL_SOURCE" = "kx-portal" ] && [ -n "$KX_BEARER_TOKEN" ]; then
  echo "kdb-entrypoint: $KX_ARCH not staged - pulling from KX portal ($KX_VERSION/$KX_CHANNEL)" >&2
  mkdir -p "$KX_CACHE_DIR"
  url="$KX_PORTAL_BASE/assets/raw/kdb+/$KX_VERSION/$KX_CHANNEL/$KX_ARCH.zip"
  umask 077; cfg=$(mktemp)
  printf 'oauth2-bearer = "%s"\n' "$KX_BEARER_TOKEN" > "$cfg"   # token stays out of argv/ps
  if ! curl -sS --fail-with-body -K "$cfg" "$KX_PORTAL_BASE/auth/me" >/dev/null 2>&1; then
    rm -f "$cfg"; echo "kdb-entrypoint: KX portal auth failed - check KX_BEARER_TOKEN" >&2; exit 1
  fi
  curl -sSL --fail-with-body -K "$cfg" -o "$KX_CACHE_DIR/$KX_ARCH.zip" "$url"
  rm -f "$cfg"
  unzip -oq "$KX_CACHE_DIR/$KX_ARCH.zip" -d "$KX_CACHE_DIR"
  QHOME="$KX_CACHE_DIR"
fi

if [ -z "$QHOME" ] || [ ! -x "$QHOME/$KX_ARCH/q" ]; then
  echo "kdb-entrypoint: no q binary for arch '$KX_ARCH'." >&2
  echo "  Put $KX_ARCH.zip (or a pre-unzipped $KX_ARCH/q) in $KX_BINARIES_DIR," >&2
  echo "  or set KX_INSTALL_SOURCE=kx-portal and KX_BEARER_TOKEN to pull it." >&2
  ls -la "$KX_BINARIES_DIR" 2>/dev/null >&2 || true
  exit 1
fi

export QHOME

# Adaptive secondary threads (kdb+ -s): rdb/wdb/hdb opt in by setting
# KDB_THREADS (see docker-compose.yml) instead of baking a thread count into
# the image at compose-generation time - that number would be wrong the
# moment this stack is deployed onto a bigger or smaller box than whoever
# ran gen_topology.py. "auto" sizes it from THIS container's own visible
# CPU count at boot; 0 opts out; a positive integer pins an explicit count.
# Services that never set KDB_THREADS (tp, gateway, idb) are untouched -
# they don't peach, so reserving secondary threads for them would only cost
# memory/thread overhead for no benefit.
if [ -n "$KDB_THREADS" ]; then
  if [ "$KDB_THREADS" = "auto" ]; then
    n=$(nproc 2>/dev/null || getconf _NPROCESSORS_ONLN 2>/dev/null || echo 2)
    n=$((n - 1))                    # leave a core for the main thread
    [ "$n" -lt 1 ] && n=1
    # cap at 4: confirmed empirically against this KX-X build/licence -
    # asking q for more than its licensed secondary-thread max makes it
    # print "Max number of secondary threads 4" and exit immediately
    # (crash-loops under restart:unless-stopped). Bump this only after
    # confirming a higher licensed max on the box you're deploying to.
    [ "$n" -gt 4 ] && n=4
    KDB_THREADS="$n"
  fi
  if [ "$KDB_THREADS" != "0" ]; then
    has_s_flag=0
    for a in "$@"; do
      if [ "$a" = "-s" ]; then has_s_flag=1; fi
    done
    if [ "$has_s_flag" = 0 ]; then
      echo "kdb-entrypoint: adaptive threads -> -s $KDB_THREADS" >&2
      set -- "$@" "-s" "$KDB_THREADS"
    fi
  fi
fi

# KDB-X can load license directly from KDB_LICENSE_B64. Do not force QLIC in
# that mode, because a file-based QLIC can override and fail this runtime path.
if [ -z "$KDB_LICENSE_B64" ]; then
  export QLIC="${QLIC:-${KX_LICENSE_PATH:-$KX_BINARIES_DIR/kc.lic}}"
else
  unset QLIC
fi
QBIN="$QHOME/$KX_ARCH/q"

# Optional CPU/NUMA pinning via numactl. Set KDB_CPUSET (e.g. "0-3" or
# "0,2,4,6") to bind this process to specific cores, and/or KDB_NUMA_NODE
# (e.g. "0") to bind its memory allocations to one NUMA node. Neither is
# auto-derived: there's no generic, portable way to learn a host's real
# core-to-NUMA-node layout from inside a container without host
# introspection (`numactl --hardware` on the host tells you; see
# docs/hardening.md's NUMA section), so this is operator-set per component,
# not guessed. Unset (the default) = no pinning, identical behavior to
# before this existed. Missing numactl warns and continues WITHOUT pinning
# rather than failing the container outright - a mis-set/unavailable pinning
# knob shouldn't take down a process that would otherwise run fine unpinned.
if [ -n "$KDB_CPUSET" ] || [ -n "$KDB_NUMA_NODE" ]; then
  if command -v numactl >/dev/null 2>&1; then
    numa_args=""
    [ -n "$KDB_CPUSET" ] && numa_args="--physcpubind=$KDB_CPUSET"
    [ -n "$KDB_NUMA_NODE" ] && numa_args="$numa_args --membind=$KDB_NUMA_NODE"
    echo "kdb-entrypoint: NUMA pinning -> numactl $numa_args -- $QBIN $*" >&2
    # shellcheck disable=SC2086  # numa_args is intentionally word-split (0-2 flags)
    exec numactl $numa_args -- "$QBIN" "$@"
  else
    echo "kdb-entrypoint: KDB_CPUSET/KDB_NUMA_NODE set but numactl isn't installed in this image - continuing WITHOUT pinning. Rebuild the data-plane image (numactl is in Dockerfile.kdb's apt-get line) to enable it." >&2
  fi
fi

echo "kdb-entrypoint: arch=$KX_ARCH source=$KX_INSTALL_SOURCE QHOME=$QHOME -> $QBIN $*" >&2
exec "$QBIN" "$@"
