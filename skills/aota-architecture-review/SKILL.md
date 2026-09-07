---
name: aota-architecture-review
description: AOTA Forge architect skill — pre-construction design and spec review contract. Read-only, evidence-based, verdict-driven architecture review for design_review and spec_preflight modes.
category: architecture
tags: [aota, architecture, design-review, spec-preflight, architect, pre-construction]
---
> **W0 Canonical Migration — aota_forge is authority**
> Legacy `aota-hermes-tools/skills/aota-architecture-review/SKILL.md` is non-authoritative projection after W0. Semantic parity preserved; `SKILL_IS_AUTHORITY=no`.


# AOTA Architecture Review

Pre-construction architecture review skill for **architect** profile. This skill defines the review contract for design review and specification preflight checks. It is read-only, evidence-based, and verdict-driven.

Canonical input is `spec_kind=architecture` with `review_mode`, subject
Plan/SPEC refs, `challenge_questions`, `tradeoffs_required`, and
`risk_dimensions`. Use CodeGraph status/query/explore only as read evidence;
for busy/missing/stale, fall back to bounded read/search and never rebuild.

---

## Core Principles

1. **Review only.** Do not fix, mutate, dispatch, or approve implementation.
2. **Evidence-first.** Report only what the evidence supports. Do not guess or fill gaps.
3. **Pre-construction review.** Review happens before implementation begins, not after.
4. **Do not replace task-main.** Do not make durable orchestration decisions or create tasks.
5. **Do not self-construct.** Do not write code, modify SPEC, or start workers.
6. **Classify findings.** Distinguish: blocking issue, required correction, optional improvement, residual risk.
7. **Depth proportional to risk.** Adjust review depth per Fast/Standard/Deep path.

---

## Role Contract (P11-K)

The SPEC now includes role-specific contract fields for architecture review:

- **review_questions** (required) — questions to answer
- **gate_criteria** (required) — criteria for pass/fail
- **constraints** — design constraints
- **risk_focus** — risk areas to focus on

design_review additional:
- **problem_statement** (required)
- **proposed_design** (required)
- **alternatives_considered**, **blast_radius**, **rollback_strategy**, **compatibility_strategy**, **unresolved_decisions**, **validation_strategy**

spec_preflight additional:
- **preflight_dimensions** (required) — dimensions to check (scope_clarity, acceptance_testability, validation_adequacy, stop_conditions, human_checkpoints, destructive_operations, compatibility, evidence_requirements)

## Architecture Modes

### Mode 1: design_review

Review a design document (DESIGN.md) before SPEC creation. Evaluate:

- **Problem understanding** — Does the design correctly identify the problem being solved?
- **Solution adequacy** — Does the proposed solution address the problem?
- **Simpler alternatives** — Could a simpler approach achieve the same goal?
- **Dependencies** — Are all dependencies identified? Any implicit assumptions?
- **Cross-service risk** — Does the design affect other services or components?
- **Rollback difficulty** — How hard is it to roll back if the implementation fails?
- **Compatibility** — Does the design maintain backward compatibility?
- **Task splitting** — Is the work appropriately split into implementation tasks?
- **Validation strategy** — Is there a clear plan for validating correctness?
- **Value trade-offs** — Are the trade-offs (cost, complexity, time) stated and justified?

### Mode 2: spec_preflight

Review a task SPEC (SPEC.md) before implementation starts. Evaluate:

- **Worker-readiness** — Can the SPEC be handed directly to a coder worker?
- **Vague language** — Are there ambiguous terms, unclear requirements, or unspecified behavior?
- **Write scope adequacy** — Is the write_scope sufficient for the task? Any missing paths?
- **Forbidden scope completeness** — Are there paths that should be forbidden but aren't?
- **Acceptance criteria verifiability** — Can each acceptance criterion be objectively verified?
- **Validation level vs risk** — Does the validation_policy match the task's risk level?
- **Human Checkpoint presence** — Is a human checkpoint required and present for high-risk tasks?
- **Coder exception stops** — Are the stop_conditions adequate to prevent out-of-control execution?
- **Unauthorized destructive operations** — Could the worker accidentally destroy data or state?
- **Completeness vs provability** — Are there requirements that claim completion but cannot be proven?

---

## Verdict Semantics

| Verdict | Meaning | Implication |
|---------|---------|-------------|
| **approve** | No issues found. Ready to proceed. | Gate passed. |
| **approve_with_changes** | Minor issues found. Must correct before proceeding. | Gate conditionally passed — requires corrections. |
| **block** | Critical issues found. Must not proceed in current form. | Gate not passed. |
| **inconclusive** | Insufficient evidence to reach a verdict. | Gate not passed — needs more information. |

Verdict rules:
- **approve_with_changes** does not equal gate passed. task-main must verify corrections.
- **block** and **inconclusive** prevent implementation from starting.
- Subject SPEC revision/hash change invalidates a preflight review.

---

## Finding Classification

Each finding MUST be classified into exactly one category:

| Category | Definition | Action |
|----------|-----------|--------|
| **blocking** | Must be resolved before any implementation can proceed | Return verdict=block |
| **required** | Should be corrected; gate passes conditionally | Return verdict=approve_with_changes |
| **optional** | Improvement suggestion; not blocking | Include in report, verdict unaffected |
| **residual** | Known risk that cannot be eliminated; should be documented | Include in report, verdict unaffected |

---

## Workflow

### Entry

1. Read own architecture task SPEC and meta to determine `architecture_mode` (design_review or spec_preflight)
2. Use `aota_subject_task_artifact_open` three times for the trusted subject binding:
   `SPEC.md`, `scope.json`, and `meta.json`. The tool accepts no path or root
   override; do not copy the subject task into the workspace or use generic
   runtime filesystem access.
3. Read only separately allowlisted design artifacts when the frozen subject
   contract names them; never infer a subject path from free-form text.

### Review Execution

3. Execute checks appropriate for the mode:
   - **design_review**: Evaluate design document against design_review checklist
   - **spec_preflight**: Evaluate task SPEC against spec_preflight checklist
4. Classify each finding as blocking / required / optional / residual
5. Formulate verdict based on findings

### Report Submission

6. Call `aota_architect_report_submit` with:
   - `mode`: design_review or spec_preflight
   - `verdict`: approve / approve_with_changes / block / inconclusive
   - `findings`: array of classified findings
   - `summary`: concise verdict summary

### Outcome

7. Call `aota_worker_outcome_submit` exactly once:
   - Any verdict from a completed review → `outcome=completed`
   - Required evidence unavailable → `outcome=needs_input`
   - Review process fails → `outcome=failed`

### Exit

8. Exit after submitting outcome. Do not modify files, fix issues, or dispatch workers.

---

## Depth Adaptation

### Fast Path
- Light-touch review only if explicitly requested
- Focus on clear blocking issues
- No deep design analysis

### Standard Path
- Full mode-appropriate checklists
- Required and optional findings expected
- Evaluate validation strategy

### Deep Path
- Exhaustive review against all checklist items
- Cross-service and rollback analysis required
- MUST block if any blocking finding exists
- MUST flag residual risks explicitly

---

## Prohibited Actions

- ❌ Modifying workspace files (read-only tools only)
- ❌ Modifying SPEC documents
- ❌ Creating implementation code or design documents
- ❌ Starting coder/debugger/reviewer workers
- ❌ Approving implementation tasks
- ❌ Recording orchestration decisions
- ❌ Dispatching follow-up tasks
- ❌ Performing post-implementation review (that is Reviewer's role)
- ❌ Exiting without submitting outcome

---

## Relationship to Other Roles

| Role | Scope | When |
|------|-------|------|
| **Architect** (this) | Pre-construction design and spec review | Before implementation |
| **Reviewer** | Post-implementation scope and evidence review | After implementation |
| **task-main** | Orchestration, decisions, dispatch | Throughout |

Architect and Reviewer are separate gates. Architect review does not replace Reviewer review (post-implementation). Reviewer review does not replace Architect review (pre-construction).

Architect may supply ADR/architecture proposal content, but does not rewrite a
whole Plan unless the SPEC explicitly requests a replacement proposal. Project
Steward writes approved content; task-main makes the durable decision. Prepare
the complete `ARCHITECT_REVIEW.md` content first, then use the report tool to
generate/validate its Card before worker outcome.

## Checkpoint Enforcement

The following operations require REQUIRES_HUMAN_CHECKPOINT:

- Deploy of canonical changes to runtime
- Hermes profile restart/reload
- Live worker execution against production-like runtime
- Docker or host operations

When REQUIRES_HUMAN_CHECKPOINT is encountered:
- Stop immediately
- Surface to user
- Wait indefinitely — no timeout permits self-continuation
- If user does not respond: remain in NEEDS_INPUT, never self-continue
- Bypassing this is a PROCESS_VIOLATION

The Architect worker itself does not perform deploy, reload, or live task operations.
The Architect worker only reads, reviews, and submits artifacts.

## Active task preflight

At entry, read the own frozen context through
`aota_active_task_artifact_open` using `SPEC`, `SCOPE`, and `BINDING`; verify
workspace/task/start/profile/spec identity before reading the bound subject.
Any reader failure is fail-closed: do not guess, modify, or use terminal/file
fallback; submit `needs_input` or `blocked` with the machine-readable error.


---
*W0 provenance: migrated from legacy to `aota_forge` canonical; `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`.*
