#!/usr/bin/env bash
# AF #58 M2 — install the bounded AF interactive ingress seam into the
# EXISTING shared OpenChamber install.
#
# The seam is disabled unless AF_INTERACTIVE_INGRESS_ENABLED=true is present
# in the service environment; the 3000/3001/4095 instances do not set it and
# keep the pristine behavior.  The patch is minimal, marker-delimited and
# reversible (uninstall.sh restores the pristine backup).
#
# Usage:
#   install.sh [--check]
#
# Exit codes: 0 installed/already installed, 2 environment mismatch, 3 patch failure.
set -euo pipefail

OC_ROOT="${OC_ROOT:-/home/latios/.local/lib/node_modules/@openchamber/web}"
SOURCE_DIR="$(cd "$(dirname "$0")" && pwd)"
PROXY="$OC_ROOT/server/lib/opencode/proxy.js"
MODULE_TARGET="$OC_ROOT/server/lib/af-interactive-ingress.js"
STATE_DIR="${AF_OC_STATE_DIR:-/home/latios/.local/share/aota-forge/openchamber-af-interactive}"
BACKUP="$STATE_DIR/proxy.js.orig"
IMPORT_MARKER="AF58_M2_INGRESS_IMPORT"
BLOCK_MARKER="AF58_M2_INGRESS"

MODE="${1:-install}"

if [[ ! -f "$PROXY" ]]; then
  echo "proxy.js not found: $PROXY" >&2
  exit 2
fi
if [[ "$MODE" == "--check" ]]; then
  if grep -q "$BLOCK_MARKER" "$PROXY"; then
    echo "AF interactive ingress seam: installed"
  else
    echo "AF interactive ingress seam: not installed"
  fi
  exit 0
fi

mkdir -p "$STATE_DIR"

if ! grep -q "$BLOCK_MARKER" "$PROXY"; then
  cp -p "$PROXY" "$BACKUP"
  echo "[install] pristine proxy.js backed up -> $BACKUP"
elif [[ ! -f "$BACKUP" ]]; then
  echo "[install] proxy.js is already patched but no pristine backup exists; refusing" >&2
  exit 3
fi

cp -f "$SOURCE_DIR/af-interactive-ingress.js" "$MODULE_TARGET"
echo "[install] module -> $MODULE_TARGET"

python3 - "$PROXY" <<'PY'
import sys
from pathlib import Path

proxy_path = Path(sys.argv[1])
text = proxy_path.read_text(encoding="utf-8")
if "AF58_M2_INGRESS" in text:
    print("[install] proxy.js already carries the AF M2 seam; no change")
    raise SystemExit(0)

import_line = "import { createAfInteractiveIngress } from '../af-interactive-ingress.js';"
anchor_import = "import { getWorktreeBootstrapStatus } from '../git/service.js';"
if anchor_import not in text:
    raise SystemExit("anchor import not found; refusing to patch")
text = text.replace(anchor_import, anchor_import + "\n" + import_line, 1)

anchor_block = "  app.use('/api', apiProxy);"
if anchor_block not in text:
    raise SystemExit("anchor registration not found; refusing to patch")
block = (
    "  // AF58_M2_INGRESS — bounded AF interactive ingress (disabled unless\n"
    "  // AF_INTERACTIVE_INGRESS_ENABLED=true for the AF Reference instance).\n"
    "  const afInteractiveIngress = createAfInteractiveIngress({ env: process.env });\n"
    "  if (afInteractiveIngress) {\n"
    "    afInteractiveIngress.register(app);\n"
    "  }\n"
    "  // AF58_M2_INGRESS_END\n"
)
text = text.replace(anchor_block, block + anchor_block, 1)
proxy_path.write_text(text, encoding="utf-8")
print("[install] proxy.js patched")
PY

node --check "$PROXY"
echo "[install] proxy.js syntax OK"
sha256sum "$PROXY" "$MODULE_TARGET"
