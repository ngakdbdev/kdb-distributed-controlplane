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
# You always supply your own LICENCE (k4.lic) - via the data folder or QLIC.
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
export QLIC="${QLIC:-${KX_LICENSE_PATH:-$KX_BINARIES_DIR/k4.lic}}"
QBIN="$QHOME/$KX_ARCH/q"

echo "kdb-entrypoint: arch=$KX_ARCH source=$KX_INSTALL_SOURCE QHOME=$QHOME -> $QBIN $*" >&2
exec "$QBIN" "$@"
