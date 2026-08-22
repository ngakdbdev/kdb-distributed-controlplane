#!/bin/sh
# kdb-entrypoint.sh - choose the KX/q binary for THIS container's platform, then
# exec q with the service's args.
#
# The binary is ALWAYS pulled from the KX portal using KX_BEARER_TOKEN - there
# is no "drop a zip in a local folder" path anymore. That local-staging model
# only ever worked on the one machine someone had manually unzipped a binary
# on; it silently didn't survive a fresh clone, a fresh VM, or a real cloud
# deploy (nothing to stage it there), which is exactly the failure mode this
# eliminates. The ONLY thing kept locally is a per-container CACHE
# ($KX_CACHE_DIR) of a binary THIS container already pulled itself, purely so
# a restart doesn't re-download - never something a person is expected to
# populate by hand.
#
# Same image runs on amd64 or arm64: the arch is resolved from uname at start.
# LICENCE is separate from the binary and still has two supported forms -
# KDB_LICENSE_B64 (inline, base64) or KX_LICENSE_PATH (a file some other
# mechanism mounted for you - a Kubernetes Secret, a secrets-manager sidecar,
# etc.) - see the QLIC handling below. Neither of those is "a zip folder";
# both are how a real license gets to a real deployment without a human
# copying a file onto a box first.
set -e

: "${KX_CACHE_DIR:=/kdbx-cache}"
: "${KX_PORTAL_BASE:=https://portal.dl.kx.com}"
: "${KX_CHANNEL:=~latest~}"

if [ -z "$KX_BEARER_TOKEN" ]; then
  echo "kdb-entrypoint: KX_BEARER_TOKEN is not set." >&2
  echo "  The KX/q binary is pulled from the KX portal at container start -" >&2
  echo "  there is no local-file fallback. Get a bearer token from the KX" >&2
  echo "  Developer Portal (https://portal.dl.kx.com) and set KX_BEARER_TOKEN" >&2
  echo "  in .env (or the Kubernetes Secret feeding it)." >&2
  exit 1
fi

# resolve arch unless pinned via KX_ARCH
if [ -z "$KX_ARCH" ]; then
  uname_m="$(uname -m)"
  case "$uname_m" in
    x86_64|amd64)   KX_ARCH=l64 ;;
    aarch64|arm64)  KX_ARCH=l64arm ;;
    *)
      # This container image only ever runs on Linux (Dockerfile.kdb is
      # debian:bookworm-slim), so uname -m here reflects the CONTAINER's
      # arch, not the Windows/macOS host running Docker Desktop above it -
      # in practice this only fires for genuinely uncommon targets (32-bit
      # ARM, riscv64, ppc64le, s390x). Guessing l64 here used to be silent;
      # it still guesses l64 (unchanged behavior for anyone this already
      # worked for), but now says so loudly, since a wrong guess fails
      # further down with a much less obvious "no q binary for arch 'l64'"
      # or a bare exec-format error instead of pointing at the real cause.
      echo "kdb-entrypoint: unrecognized uname -m '$uname_m' - guessing KX_ARCH=l64." >&2
      echo "  If this container is NOT x86_64, set KX_ARCH explicitly (e.g. l64arm)" >&2
      echo "  rather than relying on this guess." >&2
      KX_ARCH=l64
      ;;
  esac
fi

QHOME=""
if [ -x "$KX_CACHE_DIR/$KX_ARCH/q" ]; then
  QHOME="$KX_CACHE_DIR"                          # this container already pulled it - reuse, don't re-download
else
  echo "kdb-entrypoint: pulling $KX_ARCH from KX portal (kdb-x/$KX_CHANNEL)" >&2
  mkdir -p "$KX_CACHE_DIR"
  # Product path is "kdb-x/kdb-x" - NOT "kdb+". The portal also serves a
  # "kdb+" product (classic commercial kdb+ line), which looks superficially
  # identical (same version-ish/channel/arch.zip shape) but is a DIFFERENT
  # product requiring a different, incompatible commercial license format
  # (k4.lic) - confirmed live: pulling from kdb+ produced a binary that
  # rejected a known-good, byte-verified KDB-X Community Edition license
  # with "license error: k4.lic" no matter how the license was delivered,
  # because the binary itself was never going to accept that license
  # format. kdb-x/kdb-x has no separate KX_VERSION path segment - KX_CHANNEL
  # alone selects the release (a dated string, or the ~latest~ alias).
  url="$KX_PORTAL_BASE/assets/raw/kdb-x/kdb-x/$KX_CHANNEL/$KX_ARCH.zip"
  umask 077; cfg=$(mktemp)
  printf 'oauth2-bearer = "%s"\n' "$KX_BEARER_TOKEN" > "$cfg"   # token stays out of argv/ps
  if ! curl -sS --fail-with-body -K "$cfg" "$KX_PORTAL_BASE/auth/me" >/dev/null 2>&1; then
    rm -f "$cfg"
    echo "kdb-entrypoint: KX portal auth failed - check KX_BEARER_TOKEN is valid and unexpired." >&2
    exit 1
  fi
  if ! curl -sSL --fail-with-body -K "$cfg" -o "$KX_CACHE_DIR/$KX_ARCH.zip" "$url"; then
    rm -f "$cfg" "$KX_CACHE_DIR/$KX_ARCH.zip"
    echo "kdb-entrypoint: KX portal pull failed for kdb-x/$KX_CHANNEL/$KX_ARCH.zip -" >&2
    echo "  check KX_CHANNEL is valid for your KX portal entitlement." >&2
    exit 1
  fi
  rm -f "$cfg"
  unzip -oq "$KX_CACHE_DIR/$KX_ARCH.zip" -d "$KX_CACHE_DIR"
  rm -f "$KX_CACHE_DIR/$KX_ARCH.zip"
  QHOME="$KX_CACHE_DIR"
fi

if [ ! -x "$QHOME/$KX_ARCH/q" ]; then
  echo "kdb-entrypoint: KX portal pull for $KX_ARCH did not produce an executable q at $QHOME/$KX_ARCH/q." >&2
  echo "  Check KX_CHANNEL matches a real KX portal asset for this arch." >&2
  ls -la "$QHOME" 2>/dev/null >&2 || true
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

# This KX-X build ignores QLIC (and QCFG-pointed config files) as a way to
# aim it at an arbitrary license path - confirmed live, both genuinely set
# to a real, byte-verified-valid license file: q still printed "license
# error: no license loaded" either way. The ONLY thing that actually works
# is a file literally named kc.lic sitting in QHOME itself (QHOME being
# auto-derived by q from the binary's own path - one level up from the
# arch dir, i.e. $KX_CACHE_DIR here - not something this script needs to
# tell it). So: whichever source the license comes from, land it at
# $KX_CACHE_DIR/kc.lic and don't bother exporting QLIC/QCFG at all.
#
# $KX_CACHE_DIR is one volume shared by every kdb service in this stack, so
# on a fleet-wide restart multiple containers write kc.lic at roughly the
# same moment - observed live, one container read a half-written kc.lic
# from another container's in-progress write and failed with "license
# error: kc.lic" (self-healed on Docker's automatic restart, but a real
# race, not a fluke). Write to a per-container tmp file first, then mv it
# into place - mv is an atomic rename on the same filesystem, so every
# reader always sees either the old complete file or the new complete one,
# never a partial write. The tmp name must be unique PER CONTAINER, not
# just per-process: every container's entrypoint runs as PID 1 in its own
# namespace, so "$$" is "1" in all of them and is NOT actually unique on
# this shared volume - confirmed live, two containers collided on the same
# .kc.lic.1 tmp path and one lost the race with "mv: cannot stat" (exited
# under set -e, then succeeded on Docker's automatic restart). $HOSTNAME
# is the container ID by default and IS unique per container.
lic_file="$KX_CACHE_DIR/kc.lic"
lic_tmp="$KX_CACHE_DIR/.kc.lic.$(hostname).$$"
if [ -n "$KDB_LICENSE_B64" ]; then
  if ! printf '%s' "$KDB_LICENSE_B64" | base64 -d > "$lic_tmp" 2>/dev/null; then
    rm -f "$lic_tmp"
    echo "kdb-entrypoint: KDB_LICENSE_B64 is set but does not decode as valid base64." >&2
    exit 1
  fi
  mv "$lic_tmp" "$lic_file"
elif [ -n "$KX_LICENSE_PATH" ]; then
  if [ ! -f "$KX_LICENSE_PATH" ]; then
    echo "kdb-entrypoint: KX_LICENSE_PATH=$KX_LICENSE_PATH does not exist." >&2
    exit 1
  fi
  cp "$KX_LICENSE_PATH" "$lic_tmp"
  mv "$lic_tmp" "$lic_file"
else
  echo "kdb-entrypoint: no license configured - set KDB_LICENSE_B64 (inline," >&2
  echo "  base64-encoded license) or KX_LICENSE_PATH (a file mounted by some" >&2
  echo "  other mechanism - a Kubernetes Secret, a secrets-manager sidecar)." >&2
  exit 1
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

echo "kdb-entrypoint: arch=$KX_ARCH QHOME=$QHOME -> $QBIN $*" >&2
exec "$QBIN" "$@"
