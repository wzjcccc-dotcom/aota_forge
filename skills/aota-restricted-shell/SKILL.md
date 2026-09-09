---
name: aota-restricted-shell
description: Residual restricted shell fallback via aota.invoke (progressive only)
category: forge
tags: [aota, restricted-shell, fallback]
---

# AOTA Restricted Shell — Residual Fallback

> `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`. Fallback only. Specialized ops first. AF runtime decides authorization.

## When

Use only when `workspace.*`/`test.run` deterministically insufficient. Never primary for routine file/git/test work. Progressive only; never eager.

## Operation

`aota.invoke(operation="restricted_shell.run", arguments={"command_id": "ls", "args": ["src"], "timeout": 5})`. Catalog: `echo` (literal, no side effect), `ls` (worktree-scoped listing), `sleep` (timeout proof). No `git`, `pytest`, `rm`, `python`, `sh`, network. `command_id` required in catalog else `UNKNOWN_COMMAND`. `args` bounded 16×256, total ≤4096. `timeout` 1..30 (default 5). `shell=False`, no pipe/redirection/substitution, worktree cwd, bounded env, process-group timeout kill, bounded stdout/stderr 32KiB each.

## Restrictions

Worktree-bound cwd (no caller cwd/env/executable). Path args worktree-relative, symlink fail-closed. `GIT_BYPASS=no`, `TEST_BYPASS=no`, `WORKSPACE_MUTATION_BYPASS=no` enforced server-side.

## Stop

`needs_input` when specialized op could apply but was not tried. Never default to shell.
