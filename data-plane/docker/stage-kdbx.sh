#!/bin/sh
# stage-kdbx.sh - unpack KX portal <arch>.zip downloads into the data folder the
# kdb containers mount (default: this folder's kdbx/). Each portal zip contains
# just the binary (l64/q, m64/q, w64/q.exe); q.k is embedded in the binary, so
# no separate q.k is needed. You still add your LICENCE yourself.
#
# Usage:
#   ./stage-kdbx.sh [SRC_DIR] [DEST_DIR]
#     SRC_DIR   folder holding l64.zip / m64.zip / l64arm.zip / w64.zip (default: .)
#     DEST_DIR  data folder to populate (default: <this dir>/kdbx)
set -e

SRC="${1:-.}"
DEST="${2:-$(CDPATH= cd "$(dirname "$0")" && pwd)/kdbx}"
mkdir -p "$DEST"

found=0
for arch in l64 l64arm m64 mac-arm w64; do
  if [ -f "$SRC/$arch.zip" ]; then
    echo "unpacking $arch.zip -> $DEST/$arch/"
    unzip -oq "$SRC/$arch.zip" -d "$DEST"
    found=$((found + 1))
  fi
done

if [ "$found" -eq 0 ]; then
  echo "no <arch>.zip found in '$SRC' (expected e.g. l64.zip, m64.zip, w64.zip)" >&2
  exit 1
fi

if [ ! -f "$DEST/kc.lic" ]; then
  echo "" >&2
  echo "NOTE: no licence found at $DEST/kc.lic - copy your KX licence there (rename" >&2
  echo "      it to kc.lic if it downloaded under a different name) before starting" >&2
  echo "      q. Never commit the licence (this folder is git-ignored)." >&2
fi

echo "staged $found architecture(s): $(ls -1 "$DEST" | tr '\n' ' ')"
