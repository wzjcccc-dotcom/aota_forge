---
name: aota-chatgpt-validation-governance
description: Thin ChatGPT/ Friday validation governance specialization. Defines change surface classification, risk-linked validation floors, the V0-V4 proof model, minimum sufficient proof, evidence non-escalation, and bounded validation budget. References the shared core and never creates a second Plan authority.
---

# AOTA ChatGPT Validation Governance — Planning Specialization v0

This is a thin ChatGPT / Friday **validation planning specialization**. It
does not define Portable Plan semantics, hierarchy, materialization, Steward,
or checkpoint rules. All shared definitions are owned by:

```text
SHARED_CORE=aota-portable-plan-governance.md
VALIDATION_ADAPTER_DUPLICATES_CORE=no
ADAPTER_MAY_REFERENCE_SHARED_CORE=yes
ADAPTER_MAY_REDEFINE_SHARED_CORE=no
```

Authority boundary for this file:

```text
VALIDATION_GOVERNANCE_IS_PLAN_AUTHORITY=no
VALIDATION_GOVERNANCE_IS_OPERATION_AUTHORITY=no
VALIDATION_GOVERNANCE_MUTATES_ACCEPTANCE_CRITERIA=no

FRIDAY_TASK_MAIN_OWNS_VALIDATION_PLANNING=yes
CODEX_SELF_SELECTS_VALIDATION_SCOPE=no

MINIMUM_SUFFICIENT_PROOF=yes
MAXIMUM_POSSIBLE_TESTING=no
```

This file is a planning specialization for:

```text
change classification → risk consideration → validation requirement
→ minimum sufficient proof → acceptance evidence
```

It is not a Shared Core replacement. It does not create a second Plan
authority, a runtime validation engine, an evidence database, a governance
database, or a declarative policy framework.

This specialization governs **Chat planning**. The runtime risk semantic
contract (`aota_forge/work_plane/risk_review.py`) remains the runtime source
of truth for `ProcessDepth` and risk dimensions. This file references those
concepts only; it does not copy, redefine, or extend the runtime contract.

## A. Change Classification

Change Surface classification is **planning evidence**, not operation
authority.

```text
CHANGE_SURFACE_IS_PLANNING_EVIDENCE=yes
CHANGE_SURFACE_IS_OPERATION_AUTHORITY=no
CHANGE_SURFACE_IS_PLAN_AUTHORITY=no
```

A Work Item may carry one or more change surfaces. The v0 taxonomy is
indicative and evolvable; it is not a frozen runtime enum.

```text
CHANGE_SURFACE_TAXONOMY_IS_RUNTIME_ENUM=no
CHANGE_SURFACE_TAXONOMY_IS_EVOLVABLE=yes
```

Common v0 change surfaces:

```text
pure_logic
schema_or_contract
public_interface
persistence
data_migration
authority_or_security_boundary
runtime_wiring
transport
agent_model_boundary
lifecycle
concurrency
configuration
dependency
deployment
documentation
```

Core classification rules:

```text
CHANGE_SURFACE_DRIVES_VALIDATION=yes
LINES_CHANGED_DO_NOT_DEFINE_VALIDATION_DEPTH=yes
MULTIPLE_CHANGE_SURFACES_PER_WORK_ITEM_ALLOWED=yes
```

Classification is not a score. It is a planning input that helps Friday /
task-main decide what must actually be proven.

## B. Risk linkage

This file does not duplicate or reinvent the runtime risk semantic
authority. It does not use numeric risk scores.

```text
RISK_CLASS_IS_AUTHORITY=no
PROCESS_DEPTH_IS_OPERATION_AUTHORITY=no
NUMERIC_RISK_SCORE_USED=no
```

It references the existing `ProcessDepth` concept:

```text
PROCESS_DEPTH=FAST|STANDARD|DEEP
```

and the existing risk dimension concepts (`blast_radius`,
`reversibility`, `uncertainty`, `architecture_impact`, `authority_impact`,
`external_effects`, `data_integrity`, `runtime_impact`, `shared_state`).
Those concepts remain owned by the runtime risk semantic contract; this
specialization only consumes them for planning.

Chat governance determines a Validation Floor from:

```text
Risk
+
Change Surface
+
Acceptance Type
        ↓
Validation Floor
```

```text
VALIDATION_FLOOR_FROM_RISK_AND_SURFACE_AND_ACCEPTANCE=yes
EXECUTOR_MAY_DOWNGRADE_VALIDATION_FLOOR=no
WORKER_MAY_DOWNGRADE_VALIDATION_FLOOR=no
```

Only Friday / task-main may set or raise the validation floor for a planned
acceptance. Codex and Workers execute the assigned proof; they do not
re-select or lower it.

## C. Validation levels

Chat governance v0 uses a simplified five-level proof model. The level
describes **what is proven**, not which tool ran.

```text
V0=cheap/static gate
V1=local/unit/contract proof
V2=component integration proof
V3=real production-path vertical proof
V4=end-to-end/dogfood proof
```

### V0 — cheap/static gate

Examples:

```text
syntax
parse
compile/build
import/load
lint/typecheck when relevant
```

Proves the cheapest structural correctness only. V0 does not prove behavior.

### V1 — local/unit/contract proof

Examples:

```text
unit
focused contract
bounded deterministic behavior
fail-closed cases
```

`pytest` / `jest` / `cargo test` / `go test` and similar runners are
**tools**, not proof levels. Passing V1 proves bounded local behavior under
the test harness, not integration or production behavior.

```text
TEST_FRAMEWORK_IS_PROOF_LEVEL=no
TEST_FRAMEWORK_IS_TOOL=yes
```

### V2 — component integration proof

Proves that multiple real components integrate correctly.

```text
V2_MAY_CLAIM_PRODUCTION_PATH=no
V2_DOES_NOT_PROVE_PRODUCTION_PATH=yes
```

V2 still does not automatically establish that the production path holds.

### V3 — real production-path vertical proof

V3 is a **Real Vertical Slice**. It must cross the true production
architecture seam required by the acceptance. For an AF Agent runtime
acceptance this may require, as applicable:

```text
real launcher
real Hermes Agent
real aota.invoke
real relevant runtime binding
real Worker where applicable
real completion/re-entry where applicable
```

V3 work should be as small as possible — for example a single Work Item or a
hello fixture — while still crossing the real seam.

### V4 — end-to-end/dogfood proof

V4 is full E2E / dogfood. It is used to prove a complete workflow, not for
routine component verification.

```text
V3_REQUIRES_REAL_PRODUCTION_PATH_EVIDENCE=yes
V4_REQUIRES_FULL_WORKFLOW_EVIDENCE=yes
```

## D. Proof-driven Acceptance

For each important Acceptance Criterion, Milestone planning may record:

```text
AC
CHANGE_SURFACE
RISK/PROCESS_DEPTH
REQUIRED_PROOF
VALIDATION_METHOD
```

After construction, evidence is filled in / judged:

```text
ACTUAL_EVIDENCE
PROOF_STATUS
```

This does not require embedding a large test matrix in the Plan body. A
simple Work Item can stay very short:

```text
CHANGE_SURFACE=pure_logic
PROCESS_DEPTH=FAST
REQUIRED_PROOF=V1
```

Governance core:

```text
ACCEPTANCE_REQUIRES_MATCHING_PROOF=yes
LOWER_LEVEL_PROOF_CANNOT_SATISFY_HIGHER_LEVEL_REQUIREMENT=yes
```

Example:

```text
AC=Hermes task-main model sees authoritative Work source
REQUIRED_PROOF=V3
```

Then `pytest` object-level proof may only serve as **supporting evidence**;
it cannot close that AC.

```text
SUPPORTING_EVIDENCE_CANNOT_CLOSE_HIGHER_LEVEL_AC=yes
```

## E. Evidence non-escalation

Evidence semantics may not be escalated beyond their observation boundary.

```text
TEST_PASS_DOES_NOT_IMPLY_PRODUCTION_PASS=yes
MOCK_PASS_DOES_NOT_IMPLY_REAL_COMPONENT_PASS=yes
FAKE_EXECUTOR_PASS_DOES_NOT_PROVE_REAL_DISPATCH=yes
IN_MEMORY_COMPLETION_DOES_NOT_PROVE_PRODUCTION_WAKEUP=yes
INTERNAL_OBJECT_PRESENT_DOES_NOT_PROVE_MODEL_VISIBLE=yes
```

Every material evidence item must be bounded by:

```text
PROVES=
DOES_NOT_PROVE=
```

v0 does not create a full Evidence DB or schema. Friday and reviewers simply
must not upgrade an evidence claim beyond what the evidence observed. If a
production acceptance needs:

```text
real model
real transport
real persistence
real lifecycle
real external side effect
```

then the corresponding real component must be present in the proof path.

```text
PRODUCTION_ACCEPTANCE_REQUIRES_REAL_COMPONENT_IN_PROOF_PATH=yes
```

## F. Validation Budget

Running `focused + broad regression + full suite` as a routine triple is
forbidden.

```text
ROUTINE_FOCUSED_PLUS_BROAD_PLUS_FULL_SUITE=no
FULL_SUITE_DEFAULT=no
```

The governing rule is:

```text
MINIMUM_SUFFICIENT_PROOF=yes
```

Every added validation must satisfy at least one of:

```text
covers_new_acceptance
covers_new_risk
covers_changed_surface
raises_proof_level
tests_required_failure_boundary
```

If all are no:

```text
DO_NOT_RUN
```

Full suite is default-no and is only considered for explicit triggers, for
example:

```text
high-blast-radius shared core
canonical ingress / authority-wide change
schema-wide migration
unknown regression blast radius
release/program closure where justified
```

A full suite run never substitutes for V3/V4.

```text
FULL_SUITE_CANNOT_REPLACE_V3_V4=yes
VALIDATION_BUDGET_MUST_BE_BOUNDED=yes
VALIDATION_STOPPING_RULE_REQUIRED=yes
```

## G. Real Vertical trigger

The following change surfaces, when acceptance involves their real
operational behavior, default to requiring at least V3:

```text
runtime_wiring
transport
agent_model_boundary
lifecycle
authority_or_security_boundary
persistence
concurrency
deployment
```

```text
REAL_OPERATIONAL_SURFACE_DEFAULT_REQUIRED_PROOF=V3
```

This is not a mechanical rule that every such Work Item must run a separate
V3. The normal strategy is:

```text
cheap proof during W
↓
integrated candidate
↓
one bounded Milestone-level V3 when it can cover the relevant seams
```

This avoids per-W expensive ceremony.

```text
PER_W_REAL_VERTICAL_CEREMONY_DEFAULT=no
BOUNDED_MILESTONE_LEVEL_V3_DEFAULT=yes
```

## H. Language / tool neutrality

Validation governance is language- and tool-neutral. These are all
validation implementation tools:

```text
Python pytest
JS/TS vitest/jest
Rust cargo test
Go go test
Java/JUnit
```

Governance cares about:

```text
what is proven
at what level
under what environment
```

not about binding validation to Python.

```text
VALIDATION_GOVERNANCE_LANGUAGE_NEUTRAL=yes
VALIDATION_GOVERNANCE_PYTHON_SPECIFIC=no
```

## Planning outputs (advisory shape)

A Milestone Validation Plan may be represented minimally as, per AC:

```text
AC_ID
CHANGE_SURFACE
PROCESS_DEPTH
REQUIRED_PROOF
VALIDATION_METHOD
```

and after construction:

```text
ACTUAL_EVIDENCE
PROOF_STATUS
```

This shape is planning evidence only. It is not a runtime schema and creates
no new authority.
