---
name: aota-canonical-spec-pitfalls
description: Canonical AOTA SPEC pitfalls — common failure modes, anti-patterns, and stop conditions for SPEC creation and freeze.
category: orchestration
tags: [aota, spec, pitfalls, anti-patterns, validation]
---
> **W0 Canonical Migration — aota_forge is authority**
> Legacy `aota-hermes-tools/skills/aota-canonical-spec-pitfalls/SKILL.md` is non-authoritative projection after W0. Semantic parity preserved; `SKILL_IS_AUTHORITY=no`.


# AOTA Canonical SPEC Pitfalls

This is the canonical reference for common SPEC pitfalls and anti-patterns.
It documents failure modes that task-main, workers, and reviewers must avoid
when creating, freezing, or executing a SPEC. The orchestration Skill routes
SPEC-retry situations here; this Skill does not itself create or freeze SPECs.

## Pitfall 1: Scope expansion after freeze

A frozen SPEC's `read_scope`, `write_scope`, and `forbidden_scope` are
immutable. A worker must not expand scope beyond the frozen declaration. If
additional access is needed, stop with `needs_input` and request a new SPEC
revision or a new SPEC.

## Pitfall 2: Runtime projection as canonical source

The runtime projection (`~/.hermes/...`) is NOT the canonical source. Workers
must never modify runtime paths as a substitute for updating repository
source. The repository source (`skills/<id>/SKILL.md`, `plugin/aota-tools/`)
is always authoritative.

## Pitfall 3: Inventory as deployment authority

`deploy/aota-lifecycle-inventory.yaml` is governance metadata only
(`deployment_authority=false`). It MUST NOT be used alone to decide source
or destination. The managed manifest (`deploy/aota-forge-plan-files.yaml`)
and profile runtime assembly (`deploy/profile-runtime-assembly.yaml`) are
the deployment authorities.

## Pitfall 4: Path length authority inference

Do not infer that a longer or more detailed version of a file is the
authoritative one. Content authority is determined by source class and
explicit declaration, not by line count or verbosity. A shorter canonical
source may be authoritative over a longer runtime-only version.

## Pitfall 5: Unconfirmed usage deletion

A Skill with `usage_status=unconfirmed` must NOT be deleted. Deletion
requires confirmed usage evidence: SOUL reference, orchestration Skill
reference, `.usage.json`, conversation/tool evidence, runtime assembly
declaration, other-Skill reference, or deployment receipt.

## Pitfall 6: Approval bypass for implementation

Implementation SPECs require explicit human approval (`aota_profile_task_approve`)
after freeze and before task start. Diagnosis, review, architecture, and
stewardship SPECs have `approval_status=not_required` and do not call the
approval API.

## Pitfall 7: Stale preflight binding

An architect preflight bound to an old SPEC revision/SHA is stale and must
be rerun. A preflight is only valid for the exact frozen revision/SHA it
was performed against.

## Pitfall 8: Auto-dispatch from worker outcome

A worker's `recommended_next_action` is advice, never a durable decision.
`execution_completed` and Reviewer `pass` never automatically close a Work
Item or dispatch the next task. task-main makes the durable decision.

## Pitfall 9: Parallel main graph duplication

The orchestration Skill must not duplicate the full field matrix, artifact
reference schema, full lifecycle Phase, all pitfalls, or full reviewer
contract into its content. It routes to reference Skills instead.

## Pitfall 10: Isolated probe polling without marker

Isolated probe polling is allowed ONLY when the
`POLLING_ALLOWED_FOR_ISOLATED_PROBE_ONLY` marker is present. Without the
marker, isolated probe polling is rejected with
`isolated_probe_polling_without_marker`.

## Pitfall 11: Recovery without reason

Recovery wait mode requires an explicit reason string. Recovery without a
reason is rejected with `recovery_without_reason`.

## Pitfall 12: API Server supports_async_delivery misclassification

API Server `supports_async_delivery=False` means the API Server transport
does not support wakeup notifications. It is a `non_wakeup_transport` wait
mode, NOT a handoff wakeup channel. Do not miswrite it as
`wakeup_capable_normal`.

## Pitfall 13: Structured-error blind retry without contract reload

When a tool returns a structured error, the worker must NOT immediately retry
with modified parameters. The `TOOL_STRUCTURED_ERROR_REQUIRES_CONTRACT_RELOAD_BEFORE_RETRY`
invariant (see `aota-tool-failure-fallback` Skill) requires: stop retrying →
load the applicable contract → inspect the exact error → retry at most once →
otherwise `needs_input`. Blind parameter changes without contract consultation
produce the same or new structured errors and waste bounded retry budget.

- **Routing reference**: `aota-tool-failure-fallback` Skill, section
  "Tool Structured Error Fallback Rules".
- **Stop condition**: Two consecutive structured errors without evidence-based
  correction.

## Pitfall 14: Repeated schema guessing after two structured errors

When the same tool returns two consecutive structured errors, the worker has
triggered `repeated_schema_guess_detected`. The worker is guessing the tool
schema or parameter shape without consulting the contract. No third blind
retry is permitted. The required sequence is: pause → load the applicable
contract/Skill → inspect both errors → issue exactly one evidence-backed
retry → otherwise `needs_input`.

- **Routing reference**: `aota-tool-failure-fallback` Skill, section
  "No Continuous Parameter Guessing".
- **Root cause**: The worker is treating the tool as a black box instead of
  consulting the canonical contract that defines its accepted parameters.
- **Enforcement**: Skill/SOUL/telemetry level; a tool-layer wrapper may
  provide a runtime guard counting consecutive structured errors per tool.

## Routing integration

The orchestration Skill includes a minimal Skill Loading Map that routes
SPEC-retry and pitfall-avoidance situations to this Skill. This Skill does
not duplicate the orchestration flow; it is a reference document for SPEC
pitfalls.

## Activation classification

- Source class: `canonical_managed`
- Activation class: `reference` (loaded by task-main; not in active_skills)
- Owning profile: task-main

## Deployment and Runtime Guidance

- Source PASS does NOT imply runtime PASS. Runtime parity for changed
  source is PENDING_DEPLOY until next managed deploy.
- No runtime mutation, no managed deploy, no restart, no recreate, no Git
  commit, no CodeGraph mutation performed by this Skill.

`AOTA_CANONICAL_SPEC_PITFALLS_SKILL_PASS`

---
*W0 provenance: migrated from legacy to `aota_forge` canonical; `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`.*
