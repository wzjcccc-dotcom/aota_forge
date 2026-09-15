#!/usr/bin/env bash
# AF #58 M2 — uninstall the bounded AF interactive ingress seam from the
# shared OpenChamber install (restores the pristine proxy.js backup).
#
# Usage: uninstall.sh
set -euo pipefail

OC_ROOT="${OC_ROOT:-/home/latios/.local/lib/node_modules/@openchamber/web}"
PROXY="$OC_ROOT/server/lib/opencode/proxy.js"
MODULE_TARGET="$OC_ROOT/server/lib/af-interactive-ingress.js"
STATE_DIR="${AF_OC_STATE_DIR:-/home/latios/.local/share/aota-forge/openchamber-af-interactive}"
BACKUP="$STATE_DIR/proxy.js.orig"

if [[ -f "$BACKUP" ]]; then
  cp -p "$BACKUP" "$PROXY"
  echo "[uninstall] pristine proxy.js restored from $BACKUP"
else
  echo "[uninstall] no backup found; refusing to touch proxy.js" >&2
  exit 2
fi
rm -f "$MODULE_TARGET"
node --check "$PROXY"
echo "[uninstall] proxy.js syntax OK"
sha256sum "$PROXY"
