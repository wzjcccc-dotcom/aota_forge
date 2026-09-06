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

The shared server's *trusted* per-worker binding (task, project, worktree,
handoff, trace) must be supplied through the approved per-server env seam
configured in the Hermes profile, e.g.:

```yaml
# *example* Hermes profile config (profiles/aota-worker/config.yaml)
mcp_servers:
  aota:
    command: /usr/bin/python3        # *example* interpreter path
    args: ["-m", "aota_forge.composition.worker_vertical_slice", "--mcp-server"]
    env:
      PYTHONPATH: ${AOTA_FORGE_REPO_ROOT}   # operator-selected AF checkout
      AOTA_W3_MCP_ROOT: ${AOTA_W3_MCP_ROOT}
      AOTA_W3_PROJECT_ID: ${AOTA_W3_PROJECT_ID}
      AOTA_W3_WORKTREE_ID: ${AOTA_W3_WORKTREE_ID}
      AOTA_W3_TASK_ID: ${AOTA_W3_TASK_ID}
      AOTA_W3_HANDOFF_JSON: ${AOTA_W3_HANDOFF_JSON}
      AOTA_W3_TOOL_TRACE: ${AOTA_W3_TOOL_TRACE}
```

The AF one-shot composition exports these variables to the Hermes process;
they never appear as model-facing tool arguments.

## 9. Unresolved or missing binding fails closed

If any binding variable is missing, blank, malformed (e.g. invalid handoff
JSON), or the `${AOTA_FORGE_REPO_ROOT}` ref is left unresolved (variable not
exported), the MCP child raises before serving any tool. There is **no cwd or
source-tree fallback**: an unbound server never answers `tools/list`.

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
