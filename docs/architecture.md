# AOTA Forge Architecture Overview

> Descriptive, non-normative overview of the **converged Program final frontier** `d15eb26f87584fbdbe6ea5c2fdfdb942aa73b987` (`wzjcccc-dotcom/aota-hermes-tools#24`, `PROGRAM_STATUS=completed`, `PROGRAM_CLOSED=yes`).  
> Normative authority remains the Program Issue body (`ISSUE_BODY_PLAN_AUTHORITY=yes`) plus the shared governance core `chat_governance/aota-portable-plan-governance.md`.  
> This document is a compact reference — it does **not** create a second governance authority.

---

## 1. Purpose

AOTA Forge is an **executor-neutral, agent-neutral governed work environment** that sits between any capable Agent runtime and the durable AOTA workflow:

```text
Any sufficiently capable Agent
        │
        ▼
AOTA Forge
        │
        ├── Agent Work Role
        ├── Project Policy (AGENTS.md)
        ├── Task Handoff
        ├── Skill
        ├── Governed Work Plane
        ├── Context (ContextProvider seam)
        ├── Result Governance
        └── Risk / Review Workflow
        │
        ▼
same governed AOTA workflow
```

Program direction is frozen (`AGENT_NEUTRAL_WORKFLOW=yes`, `SAME_ROLE_SAME_WORKFLOW_ACROSS_AGENTS=yes`, `AOTA_FORGE_IS_GOVERNED_WORK_ENVIRONMENT=yes`, `AGENT_RUNTIME_IS_REASONING_HOST=yes`):

- Changing Agent runtime must not require redesigning Agent Work Roles, Milestone workflow, Tool contracts, Skill semantics, Task/Result Handoff semantics, review workflow, or Project governance
- Predecessor foundations are reused: Operation/Task/Execution/Capability/Result distinctions, executor-neutral Core, `ExecutionPackage`, `ExecutorAdapter`, Hermes reference path, Context/Tool Provider seams, Result Governance

---

## 2. Agent Work Roles

Defined in `aota_forge/work_plane/roles.py:25-55`; frozen by Program §5 (`AGENT_WORK_ROLE_COUNT=5`):

| Role | Lifecycle | Responsibility |
|------|-----------|----------------|
| `task-main` | long-lived, coordination | user intent continuity, requirement convergence, Milestone planning, Work Item decomposition, Worker orchestration, result reconciliation, repair/replan, risk/user-escalation judgment |
| `analyst` | one-shot, execution-scoped | source reconnaissance, research, feasibility, architecture/Milestone challenge (default mutation authority: none) |
| `coder` | one-shot, execution-scoped | source inspection/mutation, focused validation, debugging, restricted shell, Result Handoff (must not redefine Milestone requirements) |
| `reviewer` | one-shot, execution-scoped | independent acceptance of candidate vs diff vs acceptance criteria/tests/regression (normal mutation-free) |
| `project-steward` | one-shot, execution-scoped | Plan reconciliation, accepted-frontier judgment, evidence review, Milestone closure, cross-task consistency, lifecycle governance |

Invariants:

```text
task-main ≠ one-shot Executor role
AgentWorkRole ≠ core.execution.roles.CanonicalRole  (roles.py:6-16)
Exactly five values: task-main, analyst, coder, reviewer, project-steward
No additional Work Role without distinct context/responsibility evidence
```

---

## 3. task-main and Worker Lifecycle

```text
TASK_MAIN_IS_LONG_LIVED=yes
WORKER_IS_ONE_SHOT=yes
WORKER_SESSION_DISPOSABLE=yes
WORKER_SINGLE_RESPONSIBILITY=yes
WORKER_CONTEXT_BOUNDED=yes
ROLE_ASSIGNED_AT_EXECUTION_TIME=yes
PREDEPLOYED_AGENT_PROFILE_REQUIRED=no
```

Worker is a strong candidate when the responsibility can be frozen into a bounded task with bounded input/context/Tool needs, standardized output, disposable session, and would otherwise pollute task-main context (Program §4, §7).

Bootstrap model (`aota_forge/work_plane/bootstrap.py:8-28`, `context_bootstrap_plan.py`, `context_bootstrap_execution.py`):

- **task-main wake:** `TASK_MAIN_WAKE_SOURCE=user`, `TASK_MAIN_INITIAL_MODE=inspection`, `CONSTRUCTION_STARTS_IMMEDIATELY=no`; Forge assembles Project/Plan/context, user does not configure Tool permissions/Git mechanics/runtime paths
- **Worker wake:**
  ```text
  task-main → semantic TaskHandoff → Forge compile/materialize → fresh Agent session
  → Agent Work Role binding → Worker bootstrap bundle → execution → Result Handoff → session discarded
  ```
  `WORKER_WAKE_SOURCE=task-main/Forge`, `WORKER_USER_INTERACTION_REQUIRED=no`; wake prompts stay minimal; authority lives in durable Handoff/project state, not prompt choreography

---

## 4. TaskHandoff

`aota_forge/work_plane/handoff.py:1-470` — **LLM-facing semantic task projection**, not an `ExecutionPackage` replacement:

```text
task-main semantic intent
        ↓
Task Handoff  (aota_forge/work_plane/handoff.py:159-470)
        ↓
Forge compile / materialize
        ↓
existing ExecutionPackage  (M1 ingestion retained)
```

Invariants:

```text
TASK_HANDOFF_REQUIRED=yes
HANDOFF_IS_SEMANTIC_INPUT_PROJECTION=yes
HANDOFF_IS_EXECUTIONPACKAGE_REPLACEMENT=no
HANDOFF_IS_EXECUTION_AUTHORITY=no
```

Six required core fields: `work_role`, `task_kind`, `objective`, `bounded_scope`, `validation_expectations`, `semantic_stop_expectations` plus optional `SemanticReference` collections (`project_ref`, `plan_ref`, `milestone_ref`, `work_item_ref`, `policy_refs`, `context_refs`, `evidence_refs`, `skill_refs`, `process_depth_or_risk_projection_ref`). Mechanical fields (`package_id`, `idempotency_key`, `correlation_id`, …) are forbidden; deterministic `handoff_digest` covers all execution-relevant Handoff fields.

Compiler: `aota_forge/work_plane/compiler.py` + `aota_forge/core/binding/` materializes the Handoff into the canonical `ExecutionPackage` without dual authority.

---

## 5. Governed Tool Plane

Concept (`aota_forge/work_plane/tool_surface.py:1-70`):

```text
AgentWorkRole + existing canonical Tool capability identity + task/policy context
        ↓
Tool visibility / exposure surface (ToolRoleSurface)
        ↓
eager Tool visibility + progressive specialized Tool references
```

The Tool Plane is agent-neutral: the same declarative contract (`OperationContractDescriptor`) underpins both human-facing documentation and machine-facing execution. Tool identity/implementation is shared via the existing `ToolProvider` / `ToolRequest` / `ToolResponse` seam (`aota_forge/core/providers/tool.py`) — **no per-role Tool implementation**.

---

## 6. Eager / Progressive Tool Exposure

Production truth `aota_forge/work_plane/tool_surface.py:85-103`:

```text
ROLE_EAGER_TOOL_SURFACE=yes
SPECIALIZED_TOOL_PROGRESSIVE_DISCLOSURE=yes
DISCOVER_EVERY_TOOL_AT_BOOTSTRAP=no
PROGRESSIVE_TOOL_DISCLOSURE_BOUNDED=yes
PROGRESSIVE_TOOL_REF_BOUNDED=yes
PROGRESSIVE_TOOL_REF_IS_AUTHORITY=no
ROLE_SURFACE_CONTROLS_VISIBILITY_ONLY=yes
ROLE_SURFACE_CANNOT_GRANT_OPERATION_AUTHORITY=yes
TASK_MAIN_HAS_UNLIMITED_TOOL_AUTHORITY=no
```

Semantics:

- `visible != authorized` and `not visible yet != forbidden` — role surface controls visibility only; mutation/shell/filesystem/git authorization is decided by operation authority + sandbox + task scope + policy, never by visibility
- Agent bootstrap exposes only a **bounded eager/common surface** (`MAX_EAGER_CAPABILITIES=16`); specialized Tools are bounded `ToolCapabilityRef` refs (logical name + minimal display metadata + optional 64-hex digest) deterministically de-duplicated and sorted, cross-set distinct (`eager` vs `progressive` never overlaps)
- Progressive refs are not authority; `ToolRoleSurface.authorize()` is absent / fails closed (`tool_surface.py:508-509`)

---

## 7. Skill Resolution & Progressive Disclosure

Production truth `aota_forge/work_plane/skill.py`, `skill_registry.py`, `skill_resolution.py`, `skill_bootstrap.py:1-52`:

**Identity:** `SkillIdentity` (`skill_id`, `version`, `digest`, `provenance`) — immutable, four fields, bounded, `SKILL_IS_AUTHORITY=no`, `SKILL_GRANTS_TOOL_AUTHORITY=no` (`skill.py:28-30`).

**Registry:** `StaticSkillRegistry` — static declarative index; no filesystem discovery, no marketplace, no DB; key is `(namespace.value, skill_id, version)` (namespace scoped to `AgentWorkRole`); exact-version lookup only (`skill_registry.py:41-67`).

**Resolution:** `skill_resolution.py:2-44` — consumes an **already-authorized** `AllowedSkillUniverse` (the authority gate is *input*, not W3 logic); deterministic `pinned > required > role_default > recommended` precedence; mandatory (`pinned`/`required`/`role_default`) fail closed on outside-universe/missing/digest-mismatch/foreign-namespace; `recommended` degrades non-authoritatively.

**Bootstrap integration:** `skill_bootstrap.py:88-102`:

```text
pinned       → eager      (verified open via aota_forge/work_plane/skill_content.py, digest-checked)
required     → eager
role_default → eager
recommended  → progressive  (logical Skill ref, not content_ref, not hydrated at bootstrap)
```

Core purpose: start the Agent with only the procedural context required *now* (`EXPECTED_TO_BE_USED_IN_CURRENT_EXECUTION=eager`), keep possibly-useful knowledge `progressive`, avoid preloading every Skill. Specialized procedure hydrates on demand.

---

## 8. Bootstrap & Context Lifecycle

**Bootstrap contract** `aota_forge/work_plane/bootstrap.py:1-28`:

```text
BootstrapBudget + bounded BootstrapComponent (kind, delivery, material|ref, digest, provenance)
→ BootstrapBundle (task_main vs worker, deterministic canonical_bytes, digest)
```

`BOOTSTRAP_BUDGET_REQUIRED=yes`, `BOOTSTRAP_COMPONENTS_BOUNDED=yes`; `EXPECTED_TO_BE_USED_IN_CURRENT_EXECUTION=eager` vs `POSSIBLY_USEFUL=progressive`; `EXACT_BOOTSTRAP_LIMITS_NOT_UMBRELLA_CONSTANTS=yes`; bundle is not authority.

- `task_main` bundle candidates: Agent Work Role / SOUL, project identity, applicable `AGENTS.md`, current Plan projection, current Milestone, accepted frontier, bounded bootstrap context, `task-main` bootstrap Skill, risk-gate Skills, high-frequency Tool surface
- `Worker` bundle candidates: Role / SOUL, `TaskHandoff`, applicable `AGENTS.md`, bounded task-relevant context, Role bootstrap, required Skills, high-frequency Role Tools

All components are bounded (`MAX_COMPONENT_COUNT=16`, `MAX_MATERIALIZED_LENGTH=32KiB`, `MAX_BUNDLE_CANONICAL_BYTES_HARD=128KiB`).

**Context bootstrap** `aota_forge/work_plane/context_bootstrap_plan.py`, `context_bootstrap_execution.py`:

- `ContextBootstrapPlan` (pure planning, no I/O): composes `TaskHandoff.context_refs` + `WorkingTruthProjection.context_refs` into bounded `ContextBootstrapIntent`s bound to the existing `ContextRequest` contract (`aota_forge/core/providers/context.py`), with `eager` vs `progressive` delivery intent; `FULL_PLAN_BODY_BOOTSTRAP_REQUIRED=no`, `FULL_GOVERNANCE_HISTORY_BOOTSTRAP_REQUIRED=no`; budget evidence: `WITHIN_BUDGET` may allow current eager, otherwise all progressive
- `ContextBootstrapExecution`: distinct lanes — provider fetch (`ContextProvider.fetch`) vs governed selective hydration (`selective_hydration.py`); both reuse existing seams; provider-neutral, no background prefetch, no unbounded pagination; `ContextProvider` and `BootstrapBundle` limits reused

Full Plan/history bootstrap is never required; context is fetched/hydrated **on demand** through provider-selective lanes.

---

## 9. Worker Result CARD & Selective Hydration

**CARD** `aota_forge/work_plane/result_card.py:1-33`:

```text
Worker domain result
→ CanonicalResult (aota_forge/core/execution/results.py)
→ ResultGovernanceProjection (aota_forge/core/result_governance)
→ Worker Result CARD view (aota_forge/work_plane/result_card.py:234-373)
→ task-main
```

Invariants:

```text
CANONICAL_RESULT_RETAIN=yes
RESULT_GOVERNANCE_RETAIN=yes
WORKER_RESULT_CARD_IS_RESULT_AUTHORITY=no
WORKER_RESULT_CARD_IS_COMPACT_PROJECTION=yes
THIRD_RESULT_ONTOLOGY_CREATED=no
```

Compact fields (`CARD`): `task_ref`, `agent_work_role`, `summary` (≤1024), `outcome` (governance-backed from `ResultGovernanceProjection.outcome`), `blocking_finding_count` / `non_blocking_finding_count`, `result_handoff_ref`, `primary_evidence_refs` / `output_artifact_refs` (bounded `GovernedReference`), `next_hint`, optional `semantic_stop` / `mechanical_failure` (W2 reuse, at most one, never on `SUCCESS`, never grants retry).

**CARD-first / reference-first / hydrate-on-demand**:

- task-main reconciles the **CARD** first; raw transcript never returns by default
- `ResultHandoffRef` (`aota_forge/work_plane/result_card.py:71-127`) (`ref=canonical_task_id`, `digest=correlation_id`) is a bounded traceability ref (not authority) linking the CARD back to the canonical/governed result
- Richer details hydrate **only when needed** via `selective_hydration.py` (`hydrate_one` / `hydrate_many`, `hydrate_artifact_ref`, `hydrate_tool_output_via_selective`): project/worktree reauthorization, digest verification, batch bounded (`MAX_HYDRATED_BYTES=4096`, `MAX_HYDRATION_BATCH=8`), cross-scope/tampered fail-closed, `EAGER_HYDRATE_ALL_REFS=no`

task-main never needs to poll the full Worker result; `ResultHandoffRef` enables selective projection.

---

## 10. Execution Adapter Boundary

Abstract contract `aota_forge/core/execution/adapter.py` → concrete adapters:

- `aota_forge/adapters/execution/reference.py` (reference/fake)
- `aota_forge/adapters/hermes/executor.py` (Hermes translation + `HermesHostClient` protocol)

Adapter is transport-agnostic and injectable: every Hermes concern (`HermesHostClient`, `hermes-host` launcher, profile mapping) stays adapter-private. Core knows only `ExecutorAdapter` + `ExecutorCapabilities` + `ExecutionPackage` + `CanonicalResult`.

---

## 11. Hermes Reference Integration

Production composition `aota_forge/composition/execution.py:1-65`:

```text
ExecutionDispatcher → ExecutorRegistry(HermesAdapter)
    launcher = /home/latios/.local/bin/hermes-host
    mapping  = {"coder": "coder"}                    — truthful: only coder is active
    capabilities = { supported_canonical_roles=("coder",), max_timeout=300, concurrency=1, ... }
```

The mechanical translation layer (`aota_forge/adapters/hermes/executor.py:56-87`) is distinct from runtime activation:

```text
Hermes Worker execution adapter        ↔   mechanical translation (coder slice produced)
persistent task-main runtime activation ↔   autonomous task-main loop (not activated)
```

Truthfulness: the current production slice is **coder-only**; Hermes consumption of the dynamic `ToolRoleSurface`, `Skill-triggered runtime Tool Surface refresh`, and the broader multi-role fleet are **not** production-activated — they remain candidates/references. This documentation does **not** claim a fully autonomous persistent task-main runtime has been activated; such a claim would require live source change plus a formal accepted authority, which does not exist at `d15eb26`.

MCP status is truthful: `MCP is a candidate/future transport adapter` — no production MCP server is implemented.

---

## 12. Transport-Neutral Interfaces

AF Core / Tool Plane is not CLI-, MCP-, HTTP-, or Agent-runtime-centric:

```text
Agent Runtime
    ↓
transport adapter
    ├─ CLI                         (implemented: aota_forge/cli/__main__.py)
    ├─ future MCP                  (candidate, not production-implemented)
    └─ future / native adapter
    ↓
AOTA Forge contracts / Work Plane / providers
```

- **CLI** (`aota_forge/cli/__main__.py:1-17`): pure transport adapter — parse transport args → construct semantic input → resolve explicitly allowed operator-trusted resources (`AOTA_FORGE_ADAPTER_CONFIG`) → call the same canonical `Unified Ingress` / dispatch projection → map envelope to frozen exit codes; no project/contract/authority logic is owned by CLI
- **MCP**: if no production implementation exists (grep of `aota_forge` finds none), it must be recorded as **candidate/future transport adapter**, not as completed implementation; this task does **not** introduce an MCP server

---

## 13. Current Operational Boundary

### Implemented / Exists (source-verified)

- Agent Work Role model (`aota_forge/work_plane/roles.py`, `lifecycle.py`, `handoff.py` TaskHandoff)
- `TaskHandoff` → `ExecutionPackage` compiler + execution-time Work Role binding
- `ToolRoleSurface` with `ROLE_EAGER_TOOL_SURFACE` / `SPECIALIZED_TOOL_PROGRESSIVE_DISCLOSURE` (`tool_surface.py`)
- `workspace.search` / `workspace.read` bounded governed read plane (`workspace_tools.py`)
- `workspace.write` bounded governed mutation with `ArtifactReference` digestion + `GOVERNED_REFERENCE` bridge (`workspace_mutation.py`)
- `workspace_mutation.Skill` identity/registry/resolution/bootstrap with eager/progressive (`skill.py`, `skill_registry.py`, `skill_resolution.py`, `skill_bootstrap.py`)
- `Worker Result CARD` (`result_card.py`) + selective hydration (`selective_hydration.py`) with `ResultHandoffRef` hydrate-on-demand
- `ContextProvider` contract (`aota_forge/core/providers/context.py`) + `ContextBootstrapPlan` / `ContextBootstrapExecution` lifecycle (`BOOTSTRAP_BUDGET_REQUIRED`, `CONTEXT_BOOTSTRAP_PLAN_CONTRACT`)
- `ExecutorAdapter` abstraction + Hermes Worker execution slice (coder-only, `aota_forge/composition/execution.py:18-38`)
- `ContextProvider` / provider-neutral seams, `ToolProvider` reuse
- Token-efficiency mechanisms (below) and deterministic bounded budgets/digests

### Not Yet Production-Activated (evaluated from live source)

Each claim below is **not** observed as an operationally-activated runtime in `main` (`d15eb26`) and requires a new authority to claim otherwise:

```text
persistent task-main runtime binding
full autonomous Milestone control loop activation
Hermes consumption of dynamic Role Tool Surface
production MCP transport
Skill-triggered runtime Tool Surface refresh
full multi-role Hermes production mapping  (production mapping remains {"coder": "coder"})
full ACF integration (ACF_FULL_INTEGRATION_REQUIRED=no; only the ContextProvider seam exists)
```

Implemented contract / evaluator / proof **≠** operationally-activated runtime. Do not promote a proof/evaluator to a runtime claim.

---

## 14. Known Activation Gap & Token-Efficiency Design

### Activation gap

The Program delivered governed contracts, proofs, and a **coder-only** reference slice, not a full fleet. The residual gap is intentional: a second real Agent runtime integration was **`SECOND_REAL_AGENT_RUNTIME_REQUIRED=no`** / **`SHOULD_WHERE_FEASIBLE`**; the Program acceptance gate accepted offline-injectable heterogeneous fake/adversarial proofs in lieu of a second live fleet. Future activation (persistent `task-main`, full control loop, dynamic Tool Surface consumption, MCP transport, multi-role mapping) requires a **new explicitly authorized Plan/Program** (`NEXT_ACTION: Program completed. … no active Governed Agent Work Plane Subplan remains.`).

### Token-efficiency design intent

The bounded bootstrap / CARD-first architecture is explicitly cost-driven:

```text
bounded eager bootstrap (BootstrapBudget, MAX_COMPONENT_COUNT, MAX_MATERIALIZED_LENGTH)
small initial Tool surface (ROLE_EAGER_TOOL_SURFACE, MAX_EAGER_CAPABILITIES)
progressive specialized Tool disclosure (bounded ToolCapabilityRef, PROGRESSIVE_TOOL_REF_BOUNDED)
progressive Skill hydration (pinned/required/role_default eager, recommended progressive)
CARD-first Worker result reconciliation (WORKER_RESULT_CARD_IS_COMPACT_PROJECTION)
reference-first richer result access (ResultHandoffRef)
bounded search/read results (MAX_READ_BYTES, MAX_SEARCH_RESULTS, MAX_TOTAL_OUTPUT_BYTES)
context hydrate-on-demand (ContextBootstrap lanes, selective_hydration)
```

Goal:

```text
minimize unnecessary model context
minimize repeated full-result reads
minimize unnecessary Tool schema exposure
retain deterministic governance and authority boundaries
```

No unmeasured token-saving percentage is claimed.

---

## Appendix — Canonical Source Map

| Concern | Production source |
|---------|-------------------|
| Agent Work Roles | `aota_forge/work_plane/roles.py` |
| TaskHandoff | `aota_forge/work_plane/handoff.py` |
| Result CARD + ResultHandoffRef | `aota_forge/work_plane/result_card.py` |
| Selective hydration | `aota_forge/work_plane/selective_hydration.py` |
| Tool Surface | `aota_forge/work_plane/tool_surface.py` |
| Workspace read/search | `aota_forge/work_plane/workspace_tools.py` |
| Workspace write | `aota_forge/work_plane/workspace_mutation.py` |
| Skill identity/registry/resolution/bootstrap | `aota_forge/work_plane/skill.py`, `skill_registry.py`, `skill_resolution.py`, `skill_bootstrap.py`, `skill_content.py` |
| Bootstrap / Context bootstrap | `aota_forge/work_plane/bootstrap.py`, `context_bootstrap_plan.py`, `context_bootstrap_execution.py`, `context_lifecycle.py` |
| Hermes adapter / composition | `aota_forge/adapters/hermes/executor.py`, `aota_forge/composition/execution.py` |
| CLI transport | `aota_forge/cli/__main__.py`, `aota_forge/cli/commands/execution.py` |
| Context provider seam | `aota_forge/core/providers/context.py` |
| Governance | `chat_governance/GOVERNANCE_INDEX.md` → `aota-portable-plan-governance.md` |
| Program authority | `wzjcccc-dotcom/aota-hermes-tools#24` (final known-good `d15eb26f87584fbdbe6ea5c2fdfdb942aa73b987`) |

> **Provenance:** `PROJECT_ID=aota_forge`, `PROGRAM_AUTHORITY=wzjcccc-dotcom/aota-hermes-tools#24`, `PROGRAM_FINAL_KNOWN_GOOD=d15eb26f87584fbdbe6ea5c2fdfdb942aa73b987`, `EXPECTED_MAIN=d15eb26f87584fbdbe6ea5c2fdfdb942aa73b987`.
