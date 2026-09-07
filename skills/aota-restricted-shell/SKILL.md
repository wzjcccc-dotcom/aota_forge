---
name: aota-restricted-shell
description: AOTA restricted shell residual fallback — bounded argv via aota.invoke with progressive Skill disclosure.
category: forge
tags: [aota, restricted-shell, fallback, progressive-disclosure]
---

# AOTA Restricted Shell — Residual Fallback

> **Canonical Skill — aota_forge is authority**
> ```text
> AOTA_SKILL_CANONICAL_SOURCE=aota_forge
> SKILL_IS_AUTHORITY=no
> TOOL_SCHEMA_SECOND_AUTHORITY=no
> SKILL_SECOND_AUTHORITY=no
> ```
> This Skill is **informational only**. It tells the model **how to request** the restricted shell fallback via `aota.invoke`. AF runtime decides whether it is valid/authorized. The Skill never grants permission, project/worktree authority, trusted binding, approval state, or provider selection.

This Skill is the **progressive disclosure** Knowledge for M2 residual path:
`restricted_shell.run`.
The Agent initially knows only the Skill name and compact description. Full argument schemas are loaded on demand by reading this Skill.

## When fallback is appropriate

- **Specialized AOTA operations first.** Use `workspace.search` / `workspace.read` / `workspace.write` / `git.*` / `test.run` when a specialized governed operation exists.
- **Restricted shell only as residual fallback.** Load `aota-restricted-shell` only when specialized operations are unavailable or deterministically insufficient for the current task.
- **Do not treat shell as primary interface.** Agent must not default to shell for routine file, git, or test work.
- **Progressive fallback:** Agent progressively discovers this fallback only after specialized path is proven insufficient, not eagerly at bootstrap.

> ```text
> RESTRICTED_SHELL_IS_RESIDUAL_FALLBACK=yes
> RESTRICTED_SHELL_PRIMARY_INTERFACE=no
> RESTRICTED_SHELL_DEFAULT_EAGER=no
> RESTRICTED_SHELL_PROGRESSIVE_FALLBACK=yes
> SPECIALIZED_OPERATION_FIRST=yes
> ```

## Discovery (no eager catalog)

- **Skill ID**: `aota-restricted-shell`
- **Version**: `1.0.0`
- **Discovery hint**: Search for `restricted_shell` or `shell` in the Skill registry, or resolve the progressive Tool reference `restricted_shell.run`. No full shell command catalog is eagerly loaded in bootstrap.
- **Load**: `open_skill(registry, namespace, skill_id, version, reader)` with authorized reader over `content_ref`.

## Operations — semantic shape (authoritative descriptor is `.aota/contracts/operations.yaml`)

> The following projection is **derived** from the canonical `OperationContractDescriptor` via deterministic parity check. Skill text does NOT create runtime descriptors.

```json
{
  "operations": [
    {
      "name": "restricted_shell.run",
      "description": "Governed restricted shell fallback (worktree-scoped, bounded argv, no shell)",
      "inputs": [
        {"name": "command_id", "type": "str", "required": true},
        {"name": "args", "type": "list?", "required": false},
        {"name": "timeout", "type": "int?", "required": false}
      ],
      "restrictions": "worktree-scoped cwd, trusted bounded catalog (no arbitrary executable), argv-style (shell=False), timeout 1..30s required, bounded stdout/stderr 32KiB each / 64KiB total, no pipe/redirection/substitution/chaining, path args validated, env deny-by-default, one-shot no PTY, no persistent session",
      "example": "aota.invoke(operation=\"restricted_shell.run\", arguments={\"command_id\": \"echo\", \"args\": [\"hello\"], \"timeout\": 5})"
    }
  ]
}
```

## Human-readable details

### restricted_shell.run
- **Purpose**: Bounded one-shot subprocess for residual diagnostics when no specialized AOTA operation applies (read-only fallback).
- **Arguments**:
  - `command_id` `str` **required** — trusted catalog id, max 64, NUL-free, no path separators, must be in `["echo", "ls", "sleep"]`.
  - `args` `list?` **optional** — bounded argv strings, max 16 args, each max 256, total argv bytes ≤4096, NUL-free, no shell expansion. For path-capable `ls`, each arg is worktree-relative, no absolute, no `..`, no backslash, symlink escape fail-closed.
  - `timeout` `int?` **optional** — 1..30 seconds, bounded; omitted → 5. No unbounded execution.
- **Trusted command catalog (bounded, immutable)**:
  - `echo` — `["/bin/echo"]`, max_args 8, `is_path_capable=False`, allowed_options `{"-n"}`. Literal echo, no side effect beyond stdout. Shell metachars (`; | $ () `` &&`) are literal argv, not interpreted.
  - `ls` — `["/bin/ls"]`, max_args 1, `is_path_capable=True`, allowed_options `{}`. Worktree-scoped listing only. Paths validated via `resolve_worktree_resource`.
  - `sleep` — `["/bin/sleep"]`, max_args 1, `is_path_capable=False`, allowed_options `{}`. Bounded sleep for timeout proof, duration 0..30 numeric string.
  - Not in catalog → `UNKNOWN_COMMAND` fail-closed. No `git`, `pytest`, `rm`, `cp`, `mv`, `sed`, `sh`, `bash`, `python`, `node`, `curl`, `wget`, `interpreter`, or shell.
- **AF restrictions**:
  - Worktree-bound `cwd` from trusted `WorktreeSandboxBoundary`; caller-supplied `cwd`/`env`/`executable`/`command` string rejected; env deny-by-default bounded allowlist (`PATH`, `HOME`, `LANG`, `TMPDIR`, etc.), dangerous keys (`LD_PRELOAD`, `GH_TOKEN`, `SSH_AUTH_SOCK`) never inherited; `ARGC/ARGV` style `shell=False`, no `PIPE`/`REDIRECTION`/`COMMAND_SUBSTITUTION`/`COMMAND_CHAINING`, process group termination on timeout (`start_new_session=True`, `killpg`); output bounded (`MAX_STDOUT_BYTES=32KiB`, `MAX_STDERR_BYTES=32KiB`, `MAX_TOTAL_OUTPUT_BYTES=64KiB`) with truthful `stdout_truncated`/`stderr_truncated`/`total_truncated` (never silent); one-shot, no PTY, no persistent session; result via existing `ToolResponse` → existing `ToolResultGovernance` (`ToolResultProjection`); `NEW_SHELL_RESULT_ONTOLOGY_CREATED=no`, `NEW_NETWORK_SUBSYSTEM_CREATED=no`.
  - Specialized-operation bypass protection (server-side provider/catalog): `GIT_BYPASS_VIA_RESTRICTED_SHELL=no`, `TEST_RUNNER_BYPASS_VIA_RESTRICTED_SHELL=no`, `WORKSPACE_MUTATION_BYPASS_VIA_RESTRICTED_SHELL=no`. Skill text alone does not enforce; provider/catalog makes it true.
- **Example**:
  ```python
  # List worktree subdirectory (bounded, worktree-scoped)
  aota.invoke(operation="restricted_shell.run", arguments={"command_id": "ls", "args": ["src"], "timeout": 5})
  # Bounded echo (metachars literal, not shell)
  aota.invoke(operation="restricted_shell.run", arguments={"command_id": "echo", "args": ["hello; echo pwned"], "timeout": 5})
  # Timeout proof (terminates)
  aota.invoke(operation="restricted_shell.run", arguments={"command_id": "sleep", "args": ["10"], "timeout": 1})
  ```
- **Output**: `ToolResponse` success payload `{command_id, args, timeout, cwd, exit_code, stdout, stderr, duration_ms, stdout_truncated, ...}` bounded; nonzero `exit_code` is success payload (not provider failure) with truthful truncation metadata; timeout → `SHELL_TIMEOUT` typed failure.
- **Forbidden shapes** (all fail-closed):
  ```python
  # raw shell strings, arbitrary executable, caller cwd/env all rejected
  command="rm -rf ..."          # no
  shell="..."                   # no
  executable="/bin/bash"        # no
  cwd="/..."                    # no
  env={arbitrary...}            # no
  ```

## Authority separation (read before write)

- Skill teaches **how to request** the fallback operation. It does **not** grant:
  - `permission grants`
  - `project/worktree authority`
  - `trusted binding`
  - `approval state`
  - `provider selection authority`
  - `runtime operation creation`
  - `network capability`
- AF runtime (`validate_inputs`, `WorktreeSandboxBoundary`, `TaskHandoff`, `RestrictedShellAuthorityEvidence`, `BoundedRestrictedShellProvider`) decides validity and authorization. Unknown `command_id`, prohibited option, oversized input, path escape, authority mismatch, or missing trusted worktree all fail closed with typed errors (`UNKNOWN_COMMAND`, `ARGUMENT_POLICY_DENIED`, `PATH_TRAVERSAL_REJECTED`, `INVALID_TIMEOUT`, `SHELL_TIMEOUT`, etc.).

## Progressive disclosure proof

- Reference Skill is discoverable via `SkillSearchIndex` / `AllowedSkillUniverse` / `ToolRoleSurface` progressive refs **without** loading full Skill body.
- Full Skill body (argument schemas, command catalog, restrictions, examples) is loaded on demand via `open_skill` with digest verification.
- Full shell command catalog is **not** eagerly loaded in bootstrap/bundle; only `aota.invoke` typed dispatch + Skill reference is eager.
- Worker baseline knows only: *specialized AOTA operations first; Restricted Shell exists as residual fallback; load `aota-restricted-shell` Skill only when specialized operations are insufficient.*

## Network truth

- Command catalog itself excludes network-capable commands (`curl`, `wget` not present).
- `NEW_NETWORK_SUBSYSTEM_CREATED=no`, `NETWORK_ISOLATION_ENFORCED=no` — truthfully reported, not claimed. Do not claim OS network isolation.
- `NETWORK_POLICY_MODE=fail_closed_no_network_commands_exposed_no_enforcement_claim`.

---
*Teams: prefer specialized AOTA operations first; load `aota-restricted-shell` Skill only as residual fallback when specialized operations are insufficient. Full catalog not eagerly exposed. Skill is not authority.*

