# AF #58 M2 — OpenChamber :3002 interactive ingress seam

Bounded, env-gated integration that lets an OpenChamber AF Reference
(`100.123.10.71:3002`) New Chat with the `aota-task-main` profile acquire an
exact trusted AF task-main binding **before** the first model prompt.

```text
OpenChamber :3002 (this instance only)
  POST /api/session                     -> AF reserve (mechanical instance namespace)
  POST /api/session/:id/prompt_async    -> AF bind (trusted verification + binding)
                                        -> only then the original message is dispatched
  other /api/session/:id/*              -> directory rewrite to the AF instance namespace
```

## Feature gate (disabled by default)

The seam exists in the shared OpenChamber install but returns `null` unless
the instance environment contains:

```text
AF_INTERACTIVE_INGRESS_ENABLED=true
AF_INTERACTIVE_PROFILE=aota-task-main
AF_INTERACTIVE_PYTHON=/usr/bin/python3
AF_INTERACTIVE_REPO_ROOT=/home/latios/workspace/aota_forge
AF_INTERACTIVE_WORKSPACE_ROOT=/home/latios/workspace/.aota/interactive
AF_INTERACTIVE_RUNTIME_CONFIG=/home/latios/.config/aota-forge/runtime-opencode-reference.json
AF_INTERACTIVE_REGISTRY=/home/latios/.config/aota-forge/workspace-registry.json
```

Only `openchamber-af-reference.service` (:3002) sets these; `openchamber.service`
(:3000) and `openchamber-web.service` (:3001) do not, so they keep the
pristine behavior. See `startup.env.example`.

## Install / uninstall (operator, explicit)

```bash
# installs the module + marker-delimited patch into the shared install and
# saves a pristine proxy.js backup under
# ~/.local/share/aota-forge/openchamber-af-interactive/
./install.sh

# restores the pristine proxy.js and removes the module
./uninstall.sh
```

Both scripts run `node --check` and print sha256 sums. The install is
idempotent; it never overwrites the pristine backup once created.

## Boundaries

* No second server, daemon, gateway or auth service: one one-shot CLI
  (`python -m aota_forge.interactive_ingress.cli`) per reserve/bind.
* No authority logic in JavaScript: every trust decision is made by the AF
  composition (live Plan read, trusted project/source/root resolution,
  digest-bound binding envelope). The JS layer only routes and fails closed.
* On any bind failure the operator message is NOT dispatched.
* Direct API traffic to OpenCode `:4096` is not affected.
