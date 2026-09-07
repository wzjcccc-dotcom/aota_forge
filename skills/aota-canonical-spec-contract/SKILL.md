---
name: aota-canonical-spec-contract
description: Canonical AOTA SPEC contract — field matrix, artifact reference schema, validation tiers, freeze/approval authority, and binding invariants.
category: orchestration
tags: [aota, spec, contract, freeze, approval, binding]
---
> **W0 Canonical Migration — aota_forge is authority**
> Legacy `aota-hermes-tools/skills/aota-canonical-spec-contract/SKILL.md` is non-authoritative projection after W0. Semantic parity preserved; `SKILL_IS_AUTHORITY=no`.


# AOTA Canonical SPEC Contract

This is the canonical reference for the AOTA Profile Task SPEC contract.
It defines the field matrix, artifact reference schema, validation tier
discipline, freeze/approval authority, and binding invariants that every
SPEC must satisfy. The orchestration Skill (aota-profile-task-orchestration)
routes SPEC-creation situations here; this Skill does not itself create,
freeze, or dispatch tasks.

## Field matrix

Every frozen SPEC must contain:

| Field | Required | Purpose |
|-------|----------|---------|
| `spec_id` | yes | Unique SPEC identifier (task-id-derived) |
| `spec_kind` | yes | `implementation`, `diagnosis`, `review`, `architecture`, `stewardship` |
| `revision` | yes | Integer revision starting at 1; incremented on update |
| `spec_hash` | yes | SHA-256 of the full SPEC content at freeze time |
| `objective` | yes | Bounded, measurable goal |
| `acceptance_criteria` | yes | Verifiable pass/fail conditions |
| `read_scope` | yes | Glob patterns for allowed reads |
| `write_scope` | yes (implementation) | Glob patterns for allowed writes |
| `forbidden_scope` | yes | Glob patterns for never-touch paths |
| `validation_strategy` | yes | Static/smoke/integration/runtime/E2E tier declaration |
| `validation_commands` | yes (implementation) | Frozen-SPEC-approved command IDs |
| `stop_conditions` | yes | Conditions that halt worker execution |
| `constraints` | yes | Non-negotiable invariants |
| `expected_artifacts` | yes | Files the worker must produce |
| `human_checkpoints` | conditional | Required approval gates (implementation only) |
| `context_refs` | conditional | Referenced prior artifacts or decisions |
| `payload` | yes | Structured scope, requirements, runtime actions |

Payload keys take precedence over legacy top-level scope keys even when
their value is `[]`. The `payload.read_scope`, `payload.write_scope`, and
`payload.forbidden_scope` are the canonical scope source.

## Artifact reference schema

A SPEC may reference prior artifacts through `context_refs`. Each reference
must declare:

- `artifact_type`: SPEC, Plan, handoff, decision, receipt, CARD, RESULT
- `artifact_id`: Exact identifier
- `relationship`: `supersedes`, `follow_up`, `subject_of`, `evidence_for`

A frozen SPEC never modifies a referenced artifact; it only reads it.

## Validation tiers

| Tier | Name | Examples |
|------|------|---------|
| 0 | Static | syntax, importability, config parse, schema parse |
| 1 | Local Smoke | single function, single CLI, small fixture |
| 2 | Integration | multi-module interaction, service API |
| 3 | Runtime | reload/restart, running process, profile loading |
| 4 | Live E2E | real session, profile task startup, handoff, decision |

task-main determines the required validation tier per SPEC. Source PASS at
Tier 0 does NOT imply Tier 3 runtime PASS. Historical E2E does NOT imply
current live PASS.

## Freeze and approval authority

- Only task-main may freeze a SPEC via `aota_task_spec_freeze`.
- Freeze captures the exact `spec_hash` and `spec_sha256` (legacy binding).
- Both `spec_hash` and `spec_sha256` are separate frozen bindings and must
  both remain intact in receipts and handoffs.
- After freeze, no field may be modified. Fixes require a new revision or
  a new SPEC.
- `approval_status` is `not_required` for diagnosis, review, architecture,
  and stewardship. Only `implementation` uses the approval API after freeze.
- `aota_profile_task_approve` binds to the exact revision + hash; a SPEC
  update invalidates a prior approval.

## Pre-submit Checklist

Before task-main freezes a SPEC, every one of these high-frequency rules must
pass.  This is a concise checklist derived from the canonical contract; the
contract itself (`_spec_contract.py`) remains the single authoritative source.

1. **`spec_kind` only, no `task_kind`**: The SPEC must use `spec_kind` (one of
   `implementation`, `diagnosis`, `review`, `architecture`, `stewardship`).
   `task_kind` is a deprecated compatibility alias and must not be the sole
   kind field.
2. **`capability_contract` must include required capabilities**: For
   `implementation`, `source_read` and `source_write` must be `true`. All
   capability values must be booleans — never strings, numbers, or `null`.
3. **`runtime_actions` must remain false**: `deploy_allowed`,
   `restart_allowed`, `recreate_allowed` must all be `false`. A SPEC never
   authorizes runtime mutation.
4. **`read_scope` items must be workspace-relative globs**: Every entry must
   be a string starting from the project root (e.g. `skills/**`). Absolute
   paths (`/home/...`) are rejected.
5. **`write_scope` items must be workspace-relative globs**: Same rule as
   `read_scope`. Write targets must be bounded project paths only.
6. **`forbidden_scope` must not overlap `read_scope`**: If a path is in both
   `read_scope` and `forbidden_scope`, the forbidden entry wins and the file
   cannot be read — the SPEC is self-defeating. This is Pitfall 16.
7. **`subject_task_id` is top-level for review SPEC**: A `review` SPEC must
   have `subject_task_id` at the top level. It must not be buried in payload
   or context_refs only.
8. **Review `context_refs` must include `subject_spec` ref**: At least one
   entry in `context_refs` must have `ref_type=subject_spec`. Missing this
   ref causes validation failure.
9. **Artifact ref must use `ref_type` from `REF_TYPES`**: Every artifact
   reference in `context_refs` or payload refs must declare a `ref_type`
   that is a member of the canonical `REF_TYPES` set. See
   `_spec_contract.py` for the current list.
10. **Artifact ref must be an object, never a string**: Passing a bare
    string as an artifact reference is rejected. The ref must be a dict
    with at minimum `ref_type` and `artifact_id`.
11. **Review payload only accepts review-specific fields**:
    `subject_spec_ref`, `subject_result_ref`, `review_dimensions`,
    `required_evidence`, `scope_review_required`,
    `documentation_review_required`. Implementation-only fields are rejected.
12. **Implementation payload only accepts implementation-specific fields**:
    `read_scope`, `write_scope`, `forbidden_scope`,
    `implementation_requirements`, `validation_commands`,
    `validation_strategy`, `runtime_actions`, `human_checkpoints`.
    Review-only or diagnosis-only fields are rejected.
13. **`expected_spec_hash` vs `expected_spec_sha256` distinction**:
    `expected_spec_hash` (in task binding artifacts) is the canonical
    `spec_hash` computed by `canonical_hash()`. `expected_spec_sha256`
    (legacy) is the raw file-content SHA-256. Both are separate frozen
    bindings and must both remain intact. Do not confuse them.
14. **`human_checkpoints` enum values**: Any declared human checkpoint must
    be one of the canonical values: `deploy`, `restart`, `reload`,
    `runtime_write`, `host_write`, `docker`, `migration`,
    `destructive_file_operation`, `secret_change`. Unknown values are
    rejected.
15. **`validation_tier` must be 0–4**: The declared validation tier must be
    an integer in `[0, 1, 2, 3, 4]`. Valid tiers are: 0=Static, 1=Local
    Smoke, 2=Integration, 3=Runtime, 4=Live E2E.
16. **`process_path` must be `fast`, `standard`, or `deep`**: Any other
    value is rejected. The process path determines the validation and
    human-checkpoint depth.
17. **`workspace_decision_id` only accepts workspace-selection authority
    decision**: The `decision_id` in `workspace_context` must reference
    a valid workspace selection decision from task-main. It must not be
    fabricated by a worker.
18. **`summary` non-empty**: The SPEC must have a non-empty `summary`
    string (bounded, ≤8000 chars).
19. **`objective` non-empty**: The SPEC must have a non-empty `objective`
    string (bounded, ≤8000 chars).
20. **`acceptance_criteria` non-empty list**: The SPEC must have at least
    one acceptance criterion. An empty list is rejected for
    `implementation` SPECs.
21. **`validation_commands` structure conformance**: Every entry in
    `validation_commands` must be a dict with at minimum `command`,
    `execution_class`, `authorized_profile`, `expected_exit`,
    `expected_evidence`, and `operator_boundary`. Missing fields or
    non-dict entries are rejected.
22. **`validation_commands` execution_class validity**: Each declared
    `execution_class` must match a known command class
    (`python_compileall`, `python_module_compile`, `node_check`,
    `ruff_check`, `mypy_check`, `pytest_isolated`, `project_script`).
    Unknown or fabricated execution classes are rejected.
23. **`validation_commands` profile authorization**: The
    `authorized_profile` on each command must match the SPEC's
    `resolved_profile`. Cross-profile command authorization is rejected.
24. **`validation_commands` operator boundary**: Commands with
    `operator_boundary=true` require explicit operator approval in the
    SPEC. Commands that cross the operator boundary without declaration
    are a blocking defect.

This checklist is not a second schema authority. The canonical source
(`plugin/aota-tools/_spec_contract.py`) owns every field definition, type,
and validation rule. When a checklist item and the contract differ, the
contract wins.

## Binding invariants

Every worker reads its frozen `SPEC`, `SCOPE`, and bounded `BINDING` through
`aota_active_task_artifact_open` before project-tier work. The three results
must agree on:

- `workspace_id`
- `task_id` / `start_id`
- `project_id`
- `resolved_profile`
- `spec_id` / `spec_revision`

Missing, mismatched, or denied reads fail closed with `needs_input` or
`blocked`. Workers never guess project IDs, switch Profiles, or fall back to
terminal/file reads. Subject reads are available only when the frozen
reviewer/debugger/architect binding explicitly permits them.

`scope.json` is an atomic, immutable projection bound to the SPEC id,
revision, hashes, task/start identity, and `scope_digest`. Task start
captures `workspace-baseline.json`. Finalization treats trusted,
identity-bound `scope-events.jsonl` as primary worker evidence.

## Validation commands structure

Every `validation_commands` entry in an implementation SPEC must be a dict
with the following fields:

| Field | Required | Purpose |
|-------|----------|---------|
| `command` | yes | The exact command ID string (e.g., `ruff_check`) |
| `execution_class` | yes | Known command class: `python_compileall`, `python_module_compile`, `node_check`, `ruff_check`, `mypy_check`, `pytest_isolated`, `project_script` |
| `authorized_profile` | yes | Profile allowed to execute this command (e.g., `coder`) |
| `expected_exit` | yes | Expected exit code (0 for success, non-zero for expected failure) |
| `expected_evidence` | yes | What evidence the command is expected to produce (e.g., `pass_output`, `coverage_report`, `lint_report`) |
| `operator_boundary` | yes | Boolean: `true` if the command requires operator approval, `false` if fully automated |

The `validation_commands` structure is the single validation framework
authority for AOTA SPECs. There is no second validation framework
authority. No other field, tool, or contract may define alternative
validation commands, override the command registry, or authorize
additional execution classes outside this structure. Any attempt to
introduce a parallel validation framework — through a different SPEC
field, a plugin, a Skill, or a runtime extension — is a contract
violation and must be rejected at SPEC validation time.

## Routing integration

The orchestration Skill includes a minimal Skill Loading Map that routes
SPEC-creation situations to this Skill. This Skill does not duplicate the
full orchestration flow; it is a reference document for the SPEC contract
itself.

## Activation classification

- Source class: `canonical_managed`
- Activation class: `reference` (loaded by task-main; not in active_skills)
- Owning profile: task-main
- The Skill is available as a reference for SPEC contract questions; it is
  not loaded in every Profile's prompt context.

## Deployment and Runtime Guidance

- Profile runtime assembly is manifest-driven. The canonical assembly
  manifest (`deploy/profile-runtime-assembly.yaml`) declares active and
  reference Skills per Profile.
- Source PASS does NOT imply runtime PASS. Runtime parity for changed
  source is PENDING_DEPLOY until next managed deploy.
- No runtime mutation, no managed deploy, no restart, no recreate, no Git
  commit, no CodeGraph mutation performed by this Skill.

`AOTA_CANONICAL_SPEC_CONTRACT_SKILL_PASS`

---
*W0 provenance: migrated from legacy to `aota_forge` canonical; `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`.*
