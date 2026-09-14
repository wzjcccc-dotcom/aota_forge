---
name: aota-spec-driven-implementation
description: Spec-driven bounded implementation with validation integrity
category: forge
tags: [aota, coder, implementation]
---

# AOTA Spec-Driven Implementation — Coder Normal

> `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`. Implementation guidance only. AF runtime decides authorization.

## Normal workflow

1. Read frozen SPEC fully. Confirm goal, `read_scope`, `write_scope`, `forbidden_scope`, acceptance criteria, validation policy, stop conditions, evidence required.
2. Implement strictly within `write_scope`. No scope expansion, no speculative refactoring, no cross-project touch. Stop on conflict, unknown dirty, or needed create/delete/move/deps outside scope.
3. Validate: run every `validation_commands` in order, check `expected_exit`, capture `expected_evidence`. Syntax/import sanity plus bounded behavior check. All must pass before `completed`; single failure → `partial`/`failed`, not `completed`.
4. Bounded self-repair in-session on validation failure with new evidence/changed hypothesis; escalate on scope/architecture/authority/acceptance change, ambiguity, or repeated no-progress.
5. Report compact implementation evidence (artifacts, validation performed, justified test mods, limitations). Never claim unexecuted validation as PASS.

## Test guidance (eager, no open needed)

`aota.invoke(operation="test.run", arguments={"runner": "pytest", "targets": ["tests/test_x.py"], "timeout": 30})`. Trusted `pytest` only, worktree cwd, timeout 1..300. Use for normal validation. Test mods require justification (path, kind, reason, evidence). Never weaken acceptance to pass. Never silently weaken existing tests.

## Tool use

`workspace.search` to locate, `workspace.read` to inspect, then `workspace.write` bounded 4096 (`create_only|replace_existing|create_or_replace`) strictly within scope; inspect before mutating where appropriate. There is no delete/move operation — if the work needs one, stop and report as a blocker rather than improvising through other tools. `restricted_shell.run` exists only when your binding actually grants it and only as a residual fallback when specialized ops are insufficient; `AUTHORITY_DENIED` there is a boundary, not an invitation to probe. No unrestricted shell.

## Completion (required governed return)

Process exit is never semantic completion. Terminate your Work exactly once through the governed path:

1. `handoff.write {"mode": "result", "payload": {"summary": "...", ...}}` — `summary` is the required usable text; also carry what changed and the validation evidence you actually ran (compact). Workers may only write `result` mode.
2. `task.return {"status": "completed|blocked|failed", "result_ref": "<result handoff ref>"}` — `INVALID_STATUS`, `UNKNOWN_REF`, or a foreign `result_ref` fail closed.

`blocked`/`failed` still require the same result+`task.return` path with the factual blocker; never silently exit.

## Stop

`needs_input` on missing SPEC/approval/scope. `failed` on blocking validation failure. Never accept own work; coder validation is not acceptance.
