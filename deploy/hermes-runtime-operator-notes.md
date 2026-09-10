# AOTA Forge Hermes Runtime — Operator Deployment Notes (M1)

Bounded operator/deployment contract for the M1 Hermes runtime binding and the
shared restricted AOTA MCP transport. This documents the config *shape* and
authority boundaries, not a host-specific copy recipe. Paths shown in angle
brackets or marked *example* are illustrative only.

## 1. RuntimeConfig is explicit and operator-owned

Production composition accepts runtime binding **only** from a trusted
operator channel:

1. `AOTA_FORGE_RUNTIME_CONFIG` — path to a bounded JSON file (≤ 64 KiB, no
   symlink, strict unknown-key rejection), or
2. an explicit `runtime_config=` injection at the composition seam (e.g. the
   W3 one-shot slice entrypoint).

There is no source-owned deployment default: the production source never
infers a provider, model, profile, or executable. `operator_runtime_config`
fields are `executor`, `executable`, `concurrency` (default 1, bounded),
`provider`, `model`, optional `toolsets`, and per-role `bindings`.

```text
RUNTIME_CONFIG_AUTHORITY=operator_owned
SOURCE_OWNED_DEPLOYMENT_FALLBACK=no
```

## 2. Missing or invalid config fails closed

No operator config (env unset and no explicit injection), a missing/unreadable
config file, unknown keys, an unknown work role, an incomplete role set,
invalid concurrency, or a missing/non-executable `executable` all raise
`RuntimeConfigError` **before** any host client is constructed. No Worker
process is ever started on a fail-closed path.

```text
MISSING_RUNTIME_CONFIG_FAILS_CLOSED=yes
```

## 3. Profile relationship

| Work role | Hermes profile |
| --- | --- |
| `analyst` / `coder` / `reviewer` / `project-steward` | `aota-worker` (one shared Worker profile) |
| `task-main` | `aota-task-main` (separate runtime binding) |

`task-main` is not a Worker `CanonicalRole` and is never dispatched through
the Worker surface. Semantic WorkRole mapping lives in AF (`TaskHandoff` /
`AgentWorkRole`); the profile names above are runtime deployment binding only.

```text
ONE_SHARED_WORKER_PROFILE=yes
WORK_ROLE_IS_HERMES_PROFILE=no
```

## 4. Provider / model are operator deployment choices

Provider and model come from the operator config (top level and/or per-role
pins) and are translated mechanically into the Hermes invocation
(`--provider`, `-m`). A `null` pin defers to the Hermes profile's own
`config.yaml`. Example operator pins used on the reference host: provider
`opencode-go`, model `deepseek-v4-flash` — deployment choices, not source
constants.

## 5. `reasoning_effort` is Hermes profile/operator config

`reasoning_effort` (e.g. `high`) is set in the Hermes profile config
(`agent.reasoning_effort`) or per operator invocation; it is deliberately
**not** part of the AF RuntimeConfig contract and never enters TaskHandoff or
Core.

## 6. One shared AOTA MCP server

The single `aota` MCP server exposes exactly the three typed Worker operations
`workspace.search`, `workspace.read`, `workspace.write`. No per-role MCP
servers, no generic string CLI, no second authority plane; MCP is thin
transport and AF remains semantic authority.

```text
ONE_SHARED_AOTA_MCP=yes
MCP_IS_AUTHORITY_ENGINE=no
```

## 7. Hermes MCP child environment is sanitized

Hermes launches MCP stdio children with its filtered/sanitized environment
(safe baseline keys + the server config's own `env` block), not a raw copy of
the parent process environment.

## 8. Trusted MCP child binding seam (per-server env)

The shared server's *trusted* pre-resolved binding reaches the MCP child
through exactly one mechanical channel: the opaque transport locator
`AOTA_PRE_RESOLVED_BINDING`. AF runtime composition creates the
signed/digest-bound pre-resolved binding envelope **before** MCP dispatch
and exports only its locator; the Hermes host/profile forwards the locator
mechanically into the MCP child, which verifies/loads the already-resolved
binding and dispatches to canonical Core. The locator is transport, not
authority:

```text
AOTA_PRE_RESOLVED_BINDING_IS_TRANSPORT_LOCATOR=yes
AOTA_PRE_RESOLVED_BINDING_IS_AUTHORITY_SOURCE=no
ENVIRONMENT_IS_AUTHORITY_SOURCE=no
TRUSTED_BINDING_AUTHORITY_SOURCE=AF_RUNTIME_COMPOSITION_PLUS_VERIFIED_BINDING_CONTENT
HOST_ENV_ONLY_TRANSPORTS_OPAQUE_LOCATOR=yes
HOST_CREATES_BINDING=no
HOST_INTERPRETS_BINDING=no
HOST_DECIDES_AUTHORITY=no
```

The approved per-server env seam in the Hermes profile forwards the current
process value (Hermes `${VAR}` interpolation resolves from the Hermes
process environment; an unset variable keeps the literal placeholder and
fails closed downstream — there is no placeholder special-casing in AF):

```yaml
# canonical shape (profiles/aota-worker/config.yaml, profiles/task-main/config.yaml)
mcp_servers:
  aota:
    command: /usr/bin/python3        # *example* interpreter path
    args: ["-m", "aota_forge.composition.worker_vertical_slice", "--mcp-server"]
    env:
      PYTHONPATH: ${AOTA_FORGE_REPO_ROOT}   # operator-selected AF checkout
      AOTA_PRE_RESOLVED_BINDING: ${AOTA_PRE_RESOLVED_BINDING}  # opaque locator, per-launch runtime data
      AOTA_W3_TOOL_TRACE: ${AOTA_W3_TOOL_TRACE}  # diagnostics only, never authority
      # (task-main profile additionally forwards AOTA_TASK_MAIN_BOOTSTRAP /
      # AOTA_TASK_MAIN_TRACE as routing/diagnostics; never as authority)
```

The AF launcher/host exports the locator to the Hermes process; the locator
never appears as a model-facing tool argument. The locator value is
per-launch runtime data (envelope path), never static profile authority, so
normal task-main/Worker launch requires no human to insert it:

```text
LIVE_PROFILE_UPDATE_REQUIRES_MANUAL_OPERATOR_EDIT=no
```

The old `AOTA_W3_*` authority-like fields (`AOTA_W3_MCP_ROOT`,
`AOTA_W3_PROJECT_ID`, `AOTA_W3_WORKTREE_ID`, `AOTA_W3_TASK_ID`,
`AOTA_W3_HANDOFF_JSON`) are **not** production authority and are removed
from the canonical production profile contract:

```text
OLD_AOTA_W3_ENV_IS_PRODUCTION_AUTHORITY=no
```

## 9. Unresolved or missing binding fails closed

If the opaque locator is missing, blank, or points at a missing/tampered
envelope, the MCP child raises `MissingRuntimeContextError` before serving
any tool. There is **no cwd or source-tree fallback**, no fallback to old
`AOTA_W3_*` fields, and no legacy discovery: an unbound server never
answers `tools/list`. Old `AOTA_W3_*` values alone — including
placeholder-like `${VAR}` literals — cannot restore authority and cannot
affect binding selection:

```text
MISSING_BINDING_FAILS_CLOSED=yes
OLD_ENV_CHANNEL_FALLBACK_ON_MISSING_BINDING=no
LEGACY_DISCOVERY_AUTOMATIC_FALLBACK=no
HERMES_PLACEHOLDER_FILTER_SPECIAL_CASE=no
```

## 10. Worker native terminal/shell toolsets are mechanically disabled

Every Worker binding in the operator config must pin the toolset allowlist to
exactly the shared MCP server:

```json
{"coder": {"profile": "aota-worker", "toolsets": ["aota"]}, "...": { "...": "..." }}
```

An incomplete or widened pin fails config validation. The allowlist is
translated into the Hermes one-shot invocation as `-t aota`, which scopes the
session tool surface (and the tool-search bridge's callable universe) to the
shared AOTA MCP tools only. Raw terminal/shell tools (`terminal`,
`process_manage`, `execute_code`) and unrestricted native filesystem tools
(`read_file`, `write_file`, `patch`, `search_files`) are mechanically absent
from a dispatched Worker session — enforcement is invocation-level, not
prompt-level.

```text
RAW_TERMINAL_EXPOSED=no
RAW_SHELL_USED=no
```

Truthful scope note: the Hermes tool-search bridge tools
(`tool_search`/`tool_describe`/`tool_call`) remain always present but resolve
only within the session-scoped toolset universe above; Hermes'
session/memory/system machinery inside the binary is not an AF tool surface
and is out of scope for this boundary. The accepted concern — unrestricted
execution/filesystem bypass around governed workspace operations — is closed.

## 11. Not yet activated

```text
persistent task-main session: NOT activated in M1
restart-safe durable execution / exact-session re-entry: NOT activated (M2/M3)
```

The M1 slice is a bounded one-shot governed Worker; completion delivery,
recovery, and the persistent coordinator are M2/M3 scope and no runtime path
crosses a user approval gate.
