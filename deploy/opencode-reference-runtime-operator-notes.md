# AF OpenCode Reference Runtime — operator notes (AF #56 M1)

Bounded deployment truth for the dedicated AF OpenCode reference server.
This runtime is a pinned, isolated, loopback-only host used for AF
contract/API/permission probing. It is NOT the interactive OpenCode server
(127.0.0.1:4095) and has no UI.

```text
OPENCODE_REFERENCE_RELEASE=v1.18.30
OPENCODE_REFERENCE_COMMIT=3104c1428ec91f809e5ab86631300de41eb6952e
AF_OPENCODE_REFERENCE_HOST=127.0.0.1
AF_OPENCODE_REFERENCE_PORT=4096
AF_OPENCODE_REFERENCE_BASE_URL=http://127.0.0.1:4096
REFERENCE_BINARY_AUTO_UPDATE=no
REFERENCE_SERVER_SHARED_WITH_INTERACTIVE=no
THIRD_OPENCHAMBER=no
```

## 1. Versioned binary materialization (immutable operator path)

Do not point the service at a mutable global install. Materialize the exact
binary into a versioned operator-owned path and verify identity before launch:

```sh
BASE=$HOME/.local/share/aota-forge/opencode-reference
SRC=$HOME/.local/lib/node_modules/opencode-ai/bin/opencode.exe   # operator install, must be 1.18.30
mkdir -p "$BASE/v1.18.30/bin"
cp "$SRC" "$BASE/v1.18.30/bin/opencode"
chmod 555 "$BASE/v1.18.30/bin/opencode"
sha256sum "$SRC" "$BASE/v1.18.30/bin/opencode"                    # must match
"$BASE/v1.18.30/bin/opencode" --version                           # must print 1.18.30
```

`v1.18.30/verify-identity.sh` pins the expected sha256 and version; the
systemd unit runs it as `ExecStartPre`, so a drifted binary fails the launch.

## 2. Isolated namespaces (XDG + explicit config)

```text
$BASE/runtime/xdg-config   XDG_CONFIG_HOME  -> opencode/opencode.json
$BASE/runtime/xdg-data     XDG_DATA_HOME    -> opencode/opencode.db + opencode/auth.json
$BASE/runtime/xdg-state    XDG_STATE_HOME
$BASE/runtime/xdg-cache    XDG_CACHE_HOME
$BASE/runtime/workdir      service WorkingDirectory (ambient cwd)
```

`OPENCODE_DISABLE_PROJECT_CONFIG=1` prevents ambient project config pickup.
The interactive server keeps its default `~/.local/share/opencode` namespace;
the reference server never reads/writes it (distinct DB files verified).

Secrets: the reference data dir may carry a reduced operator `auth.json`
(provider entries needed by this runtime only, mode 0600). Never commit it,
never print it, never embed it in the unit file.

## 3. systemd user unit

```ini
[Unit]
Description=AF OpenCode Reference Server (pinned v1.18.30, isolated namespace)
Wants=network-online.target
After=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/latios/.local/share/aota-forge/opencode-reference/runtime/workdir
Environment=HOME=/home/latios
Environment=PATH=/home/latios/.local/bin:/home/latios/.local/node-v22.19.0-linux-x64/bin:/usr/local/bin:/usr/bin:/bin
Environment=XDG_CONFIG_HOME=/home/latios/.local/share/aota-forge/opencode-reference/runtime/xdg-config
Environment=XDG_DATA_HOME=/home/latios/.local/share/aota-forge/opencode-reference/runtime/xdg-data
Environment=XDG_STATE_HOME=/home/latios/.local/share/aota-forge/opencode-reference/runtime/xdg-state
Environment=XDG_CACHE_HOME=/home/latios/.local/share/aota-forge/opencode-reference/runtime/xdg-cache
Environment=OPENCODE_DISABLE_PROJECT_CONFIG=1
ExecStartPre=/home/latios/.local/share/aota-forge/opencode-reference/v1.18.30/verify-identity.sh
ExecStart=/home/latios/.local/share/aota-forge/opencode-reference/v1.18.30/bin/opencode serve --hostname 127.0.0.1 --port 4096
Restart=always
RestartSec=5
TimeoutStopSec=30

[Install]
WantedBy=default.target
```

Port selection: prefer 4096 when free; otherwise the first free port from a
small bounded operator range. Do not reuse 4095/3000/3001.

## 4. Minimal reference config (no interactive config clone)

`runtime/xdg-config/opencode/opencode.json` contains only:

* `permission`: native tools denied (`bash`, `edit`, `read`, `glob`, `grep`,
  `list`, `task`, `external_directory`, `todowrite`, `question`, `webfetch`,
  `websearch`, `lsp`, `doom_loop`), AOTA MCP tool key allowed;
* `tools`: `{"skill": false}` (visibility filter for the built-in skill tool);
* `mcp.aota`: the existing AF/AOTA stdio MCP server (no second AOTA server);
* `provider`: operator-selected provider blocks only.

Unrelated interactive plugins/MCP servers are NOT inherited.

## 5. Reproduction probes (tests/scripts convention)

```sh
# bounded deterministic stub model (probe instrument)
AF_STUB_CAPTURE_FILE=/tmp/afstub.jsonl python3 scripts/m1_w2_openai_stub_server.py

# API / busy / abort / permission+MCP probes (writes bounded JSON evidence)
AF_EVIDENCE_DIR=<evidence-dir> AF_PROBE_ROOT=<probe-root> \
  python3 scripts/m1_w2_opencode_reference_probe.py base|busy|abort|permission
```

Quick checks:

```sh
curl -s http://127.0.0.1:4096/global/health      # {"healthy":true,"version":"1.18.30"}
curl -s http://127.0.0.1:4096/mcp                # {"aota":{"status":"connected"}}
```

## 6. Boundaries

```text
EXISTING_INTERACTIVE_OPENCODE_CHANGED=no
OPENCHAMBER_MUTATION=no
OPENCODE_REFERENCE_SOURCE_MUTATION=no
OPENCODE_UPSTREAM_PATCH_REQUIRED=no
REFERENCE_SERVER_LOOPBACK_ONLY=yes
```
