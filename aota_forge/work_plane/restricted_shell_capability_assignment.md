# M2/W2 Restricted Shell — Role / Capability Assignment (for serialized convergence)

> **Status:** Planning record for `aota/issue-37-m2-w2-restricted-shell-activation` convergence with `M2/W1`.
> This file is **informational only** and does not grant authority. Authority remains in `RestrictedShellAuthorityEvidence` + `BoundedRestrictedShellProvider` + trusted `WorktreeSandboxBoundary`/`TaskHandoff`/policy. No transport wiring is performed on this branch.

```text
MCP_TRANSPORT_MUTATION=no
HERMES_TOOLS_MUTATION=no
SHARED_TOOL_ROLE_SURFACE_WIRING_MUTATION=no
TRUSTED_WORKER_BINDING_MUTATION=no
```

## Intent

`restricted_shell.run` is a **residual fallback capability**, not a primary interface. It must be available through the single-entry transport `aota.invoke` only as progressive fallback after specialized operations are proven insufficient.

```
specialized AOTA operation unavailable
  ↓
Agent progressively discovers restricted-shell fallback (load aota-restricted-shell Skill)
  ↓
aota.invoke(operation="restricted_shell.run", arguments={command_id, args, timeout})
  ↓
AF checks logical capability + trusted command catalog + worktree authority
  ↓
one-shot bounded subprocess (shell=False, worktree cwd, timeout, bounded env, bounded output)
  ↓
ToolResponse → existing ToolResultGovernance → ToolResultProjection
```

## Authorized logical capability set (M2/W2 proposal for convergence)

```text
AUTHORIZED_ROLE_CAPABILITY_SET (proposed, not yet wired):

- coder:            restricted_shell.run = YES (progressive, residual fallback)
- analyst:          restricted_shell.run = YES (progressive, residual fallback)
- reviewer:         restricted_shell.run = NO  (read-only governance; specialized ops sufficient)
- project-steward:  restricted_shell.run = NO  (lifecycle governance; specialized ops sufficient)
- task-main:        restricted_shell.run = NO  (coordinator; must not gain shell merely because workers need fallback)

Rationale:
- Worker implementation/diagnostic roles (coder, analyst) legitimately encounter tasks where
  workspace.search/read/write + test.run + git.* are insufficient and a bounded residual
  (echo/ls/sleep) is the thinnest viable fallback (e.g., worktree-scoped listing when search
  scope is ambiguous, timeout proof, bounded echo for diagnostics).
- Reviewer and project-steward operate on governed evidence and lifecycle contracts; shell
  would add unnecessary surface without first-principles need.
- task-main is a coordinator/orchestrator; granting shell would violate least-privilege and
  the explicit invariant `task-main must not gain shell capability merely because Worker
  profiles need fallback` unless accepted source/policy explicitly requires it. No such
  accepted policy currently requires it.
- All roles continue to require full authority evidence (sandbox + handoff + policy + descriptor)
  even when logically visible; visibility != authority.
```

## Transport / surface note (deferred to convergence)

- **Current branch (M2/W2 core):** `TrustedWorkerBinding` and `ToolRoleSurface` remain bounded to
  `workspace.*` only (`SUPPORTED_OPERATIONS = {"workspace.search","workspace.read","workspace.write"}`).
  `aota.invoke` does **not** yet expose `restricted_shell.run` through MCP. This is intentional
  (`MCP_TRANSPORT_MUTATION=no`) to avoid parallel hot-file conflict with `M2/W1`.
- **Serialized convergence (after W1):** A single ordered convergence branch will:
  1. Extend `ToolRoleSurface` progressive refs for authorized Worker roles to include
     `restricted_shell.run` (progressive, not eager; bounded; deterministic).
  2. Extend `TrustedWorkerBinding` to carry `RestrictedShellAuthorityEvidence` (or equivalent)
     alongside existing `WorkspaceAuthorityEvidence`, with `tool_surface` / `handoff` role
     match and per-role capability checks.
  3. Extend `_SharedAotaMcpAdapter.invoke` / `mcp_transport.py` dispatch to handle
     `restricted_shell.run` via `BoundedRestrictedShellProvider` under the same governed
     result path (`_sanitize → project_tool_result → governed MCP projection`), preserving
     `TOOL_RESULT_GOVERNANCE_PRODUCTION_PATH=yes`, `LARGE_RESULT_BY_REF`, `OUTCOME_EXPLICIT`, etc.
  4. Keep `task-main` binding **without** shell authority (explicit negative test in convergence).
  5. Keep `aota-hermes-tools` and `deploy/profile-runtime-assembly.yaml` projections aligned
     via existing projection tooling, not direct mutation on this branch.

## Verification (this branch)

- Existing `BoundedRestrictedShellProvider` reused; no fork/replace.
- Catalog remains `["echo","ls","sleep"]`, bounded, no git/test/mutation/shell/interpreter bypass.
- All execution remains WorktreeSandboxBoundary-bound, `WORKTREE_BOUND_CWD=yes`, `CALLER_SUPPLIED_ARBITRARY_CWD=no`, path args validated.
- Timeout required, process group termination verified via actual `sleep 5` / `timeout 1` path.
- Stdout/stderr/total bounded (32KiB/32KiB/64KiB) with truthful truncation metadata, no silent truncation.
- Skill `aota-restricted-shell` is progressive fallback (`RESTRICTED_SHELL_DEFAULT_EAGER=no`, `RESTRICTED_SHELL_PROGRESSIVE_FALLBACK=yes`, `SKILL_IS_AUTHORITY=no`), command IDs mirror catalog exactly.

## Non-goals on this branch

- Do not create `aota.invoke` dispatch for `restricted_shell.run` yet (await convergence).
- Do not modify `aota_forge/mcp_transport.py`, `aota_forge/work_plane/tool_surface.py` activation wiring, `aota-hermes-tools/**`, or `deploy/profile-runtime-assembly.yaml`.
- Do not build network isolation or generic shell expansions.

---
*Record for Friday convergence: return W2 core result and await W1 core + serialized M2 activation convergence before Agent-facing activation.*
