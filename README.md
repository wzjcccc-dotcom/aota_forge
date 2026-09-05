# AOTA Forge

Canonical source repository for the executor-neutral AOTA Forge Core, Governed Agent Work Plane, Unified Control Plane, canonical CLI, and executor adapters.

AOTA Forge is a **governed work environment** for agent execution — not a raw shell wrapper. Any sufficiently capable Agent runtime runs *through* Forge contracts rather than around them:

```text
Any Agent Runtime
    ↓
transport adapter (CLI / future MCP / future native)
    ↓
AOTA Forge contracts / Work Plane / providers
    ↓
same governed AOTA workflow
```

Changing Agent runtime does **not** require redesigning Agent Work Roles, Milestone workflow, Tool contracts, Skill semantics, Task/Result Handoff semantics, review workflow, or Project governance.

## Program Authority

Source authority is this repository, `aota_forge`.

Program architecture authority is **completed / closed**:

- **Umbrella Program:** [wzjcccc-dotcom/aota-hermes-tools#24](https://github.com/wzjcccc-dotcom/aota-hermes-tools/issues/24) — *AOTA Forge Governed Agent Work Plane & Role Runtime* — `PROGRAM_STATUS=completed`, `PLAN_COMPLETED=yes`
- **Predecessor:** [wzjcccc-dotcom/aota-hermes-tools#16](https://github.com/wzjcccc-dotcom/aota-hermes-tools/issues/16) (Generic Architecture Umbrella) — retained as foundation, not current authority
- **Legacy Plan:** [wzjcccc-dotcom/aota-hermes-tools#9](https://github.com/wzjcccc-dotcom/aota-hermes-tools/issues/9) — retained as implementation/migration evidence only
- **Program final known-good:** `d15eb26f87584fbdbe6ea5c2fdfdb942aa73b987` (converged `2472b53` + `2b8e680`, `PROGRAM_FINAL_KNOWN_GOOD=d15eb26`)
- **Completed Subplans:** S1, S2, S3, S4, S5, S6 — no active Governed Agent Work Plane Subplan remains; future expansion requires a new explicitly authorized Plan/Program

Canonical Chat governance lives in `chat_governance/GOVERNANCE_INDEX.md` → `aota-portable-plan-governance.md` (shared core) + `aota-chatgpt-project-planning.md` / `aota-chatgpt-parallel-development.md` / `aota-chatgpt-worktree-governance.md` (thin adapters).

## Agent Work Roles (exactly five)

Defined in `aota_forge/work_plane/roles.py:25-55` and frozen by Program #24 §5:

```text
task-main        — long-lived semantic coordination (user intent, Milestone planning,
                   Worker orchestration, result reconciliation, risk/user-escalation)
analyst          — read-heavy bounded Worker (reconnaissance, feasibility, challenge)
coder            — implementation Worker (mutation, focused validation, restricted shell)
reviewer         — independent acceptance Worker (candidate vs diff vs acceptance)
project-steward  — governance/history Worker (frontier, evidence, Milestone closure)
```

`task-main` is **not** a one-shot Executor role — it is the long-lifecycle coordination role. Workers are execution-scoped, one-shot, disposable contexts. `AgentWorkRole` ≠ `core.execution.roles.CanonicalRole`.

## task-main / Worker Interaction

Normal philosophy (`aota_forge/work_plane/handoff.py`, `aota_forge/work_plane/result_card.py:1-33`):

```text
task-main
→ bounded search/read/write capability
→ Task SPEC / TaskHandoff (aota_forge/work_plane/handoff.py:159-170)
→ Worker execution (fresh session, role-bound)
→ CanonicalResult / ResultGovernance
→ compact Worker Result CARD (aota_forge/work_plane/result_card.py:234-373)
→ task-main reconciliation
→ richer result hydration only when needed (hydrate-on-demand)
```

Notable invariants:

- `WORKER_RESULT_CARD_IS_RESULT_AUTHORITY=no`, `WORKER_RESULT_CARD_IS_COMPACT_PROJECTION=yes`
- **CARD-first / reference-first / hydrate-on-demand** — task-main reconciles the bounded CARD; richer evidence/artifact content is fetched selectively via `ResultHandoffRef` (`aota_forge/work_plane/result_card.py:71-127`) and `selective_hydration.py`, not by polling full Worker transcript by default

## Governed Work Plane

The Work Plane is agent-neutral and transport-neutral. Logical families are product surfaces (not necessarily new Core contracts): `handoff`, `skill`, `workspace`, `test`, `git / project lifecycle`, `artifact`, `restricted shell`, `result`, `project / plan / governance`, `context`.

Governance boundaries are preserved on every operation:

```text
WorktreeSandboxBoundary + TaskHandoff/task scope + applicable AGENTS policy + operation authority
```

## Tool Plane — Eager + Progressive

Implemented in `aota_forge/work_plane/tool_surface.py:1-70`, `aota_forge/work_plane/workspace_tools.py`, `aota_forge/work_plane/workspace_mutation.py`:

- Tool **identity** (`OperationContractDescriptor.name`) and **implementation** (`ToolProvider`) are shared across roles — no per-role Tool implementation
- `ToolRoleSurface` controls **visibility**, not authority: `ROLE_SURFACE_CONTROLS_VISIBILITY_ONLY=yes`, `ROLE_SURFACE_CANNOT_GRANT_OPERATION_AUTHORITY=yes` (`aota_forge/work_plane/tool_surface.py:90-94`)
- `visible != authorized` and `not visible yet != forbidden`
- `ROLE_EAGER_TOOL_SURFACE=yes`, `SPECIALIZED_TOOL_PROGRESSIVE_DISCLOSURE=yes`, `DISCOVER_EVERY_TOOL_AT_BOOTSTRAP=no`
- Agent bootstrap exposes only a **bounded eager/common surface**; specialized Tools are bounded `ToolCapabilityRef` progressive refs (no authority, optional integrity digest)
- `task-main` does **not** have unlimited Tool authority (`TASK_MAIN_HAS_UNLIMITED_TOOL_AUTHORITY=no`)

## Workspace Search / Read / Write

Governed operations (`aota_forge/work_plane/workspace_tools.py:1-77`, `aota_forge/work_plane/workspace_mutation.py:1-62`):

```text
workspace.search / workspace.read  = bounded, project/worktree-scoped governed read plane
workspace.write                    = bounded governed mutation (write descriptor, atomic replace)
```

Not unrestricted terminal/filesystem access. Every invocation requires:

- trusted `WorktreeSandboxBoundary` (physical root bound before discovery)
- structured `TaskHandoff` task scope (not a filesystem ACL)
- applicable AGENTS policy candidates (`AGENTS_APPLICABILITY_IS_FILESYSTEM_AUTHORITY=no`)
- trusted operation-authority evidence (`WorkspaceAuthorityEvidence` / `WorkspaceMutationAuthority`)

Enforced: bounded output (`MAX_READ_BYTES=32KiB`, `MAX_SEARCH_RESULTS=50`), symlink escape fail-closed, cross-project/worktree fail-closed, stale-evidence revalidation at use, TOCTOU explicitly not eliminated (`TOCTOU_BOUNDARY_TRUTHFUL=yes`).

## Skill System — Progressive Disclosure

Implemented in `aota_forge/work_plane/skill.py`, `skill_registry.py`, `skill_resolution.py`, `skill_bootstrap.py:1-52`:

- `SkillIdentity` (`skill_id`, `version`, `digest`, `provenance`) is descriptive only (`SKILL_IS_AUTHORITY=no`)
- `StaticSkillRegistry` is a bounded declarative index (no filesystem discovery, no marketplace)
- Resolution is inside an **already-authorized** `AllowedSkillUniverse` — Skill never grants authority
- Bootstrap is progressive: `pinned → eager`, `required → eager`, `role_default → eager`, `recommended → progressive` (`aota_forge/work_plane/skill_bootstrap.py:98-102`)
- Purpose: start the Agent with only the procedural context required *now*; hydrate specialized Skills on demand — no preload of every Skill

## Bootstrap & Context Lifecycle

Implemented in `aota_forge/work_plane/bootstrap.py`, `context_bootstrap_plan.py`, `context_bootstrap_execution.py`:

- **Bounded bootstrap** — `BootstrapBudget` / `BootstrapBundle` with `BOOTSTRAP_BUDGET_REQUIRED=yes`, `BOOTSTRAP_COMPONENTS_BOUNDED=yes`; delivery is `eager` (expected now) vs `progressive` (possibly useful); full Plan body / full governance history bootstrap is **not** required
- **ContextProvider seam** — provider-neutral `ContextRequest → ContextProvider.fetch → ContextResponse` (`aota_forge/core/providers/context.py`) plus `ContextBootstrapPlan` (pure selection/binding) and `context_bootstrap_execution` (provider fetch + governed selective hydration as distinct lanes)
- ACF remains a **future candidate provider**; Forge currently exposes the seam/budget/progressive pattern, not a full ACF integration (`FULL_ACF_INTEGRATION_REQUIRED=no` by Program)

## TaskHandoff

`aota_forge/work_plane/handoff.py:159-470` — bounded immutable semantic projection from task-main intent into Forge materialization (not an `ExecutionPackage` replacement). Six required core fields (`work_role`, `task_kind`, `objective`, `bounded_scope`, `validation_expectations`, `semantic_stop_expectations`) plus optional `SemanticReference` refs; mechanical IDs (`package_id`, `idempotency_key`, …) are forbidden; deterministic `handoff_digest`.

## Worker Result CARD

`aota_forge/work_plane/result_card.py:234-658` — compact projection from authoritative `CanonicalResult` + `ResultGovernanceProjection` + optional `SemanticStop`/`MechanicalFailure` (W2 reuse, no third ontology). Caller cannot override `governance_projection.outcome`; `CARD outcome` is governance-backed; at most one stop classification, never on `SUCCESS`; `ResultHandoffRef` (`ref=canonical_task_id`, optional `digest=correlation_id`) enables selective hydration of richer details.

## Execution Adapter Boundary / Hermes Reference Slice

- **Abstraction:** `aota_forge/core/execution/adapter.py` (`ExecutorAdapter`), `aota_forge/adapters/hermes/executor.py`
- **Composition:** `aota_forge/composition/execution.py` — production `HermesAdapter` wired as reference slice
- **Truthful slice:** production `PRODUCTION_HERMES_PROFILE_MAPPING = {"coder": "coder"}` and `supported_canonical_roles=("coder",)` (`aota_forge/composition/execution.py:18-38`) — only the **coder Worker execution adapter** is currently production-activated; multi-role Hermes mapping, dynamic Role Tool Surface consumption, and full bootstrap integration remain **not** activated unless a new live authority proves otherwise
- **Persistent task-main runtime** (`persistent task-main runtime binding`, `full autonomous Milestone control loop`) is **not** production-activated; the current Hermes slice is an offline-injectable mechanical translation, not an autonomous fleet

## Transport-Neutral Architecture

```
Agent Runtime
    ↓
transport adapter
    ├─ CLI (implemented: aota_forge/cli/__main__.py)
    ├─ future MCP (candidate, not production-implemented)
    └─ future / native adapter
    ↓
AOTA Forge contracts / Work Plane / providers
```

AF Core / Tool Plane is not CLI- or MCP-centric. CLI is a transport adapter: it parses transport args → builds semantic input → resolves operator-trusted resources → calls the same Unified Ingress → projects the canonical envelope (see `aota_forge/cli/__main__.py:1-17`).

**MCP is a candidate/future transport adapter** — no production MCP server is implemented in this repository.

## Current Operational Boundary

### Implemented / Exists (source-verified)

`Agent Work Role model`, `TaskHandoff`, `ToolRoleSurface`, `workspace.search` / `workspace.read`, `workspace.write` (atomic replace + `ArtifactReference`), `Skill registry` / `resolution` / `bootstrap` (eager/progressive), `Worker Result CARD` + selective hydration, `ContextProvider contract` + context bootstrap selection/execution, `ExecutorAdapter` abstraction + Hermes Worker slice (coder-only), `CLI transport`, deterministic bounded budgets/digests.

### Not Yet Production-Activated (evaluated from live source)

`persistent task-main runtime binding`, `full autonomous Milestone control loop activation`, `Hermes consumption of dynamic Role Tool Surface`, `production MCP transport`, `Skill-triggered runtime Tool Surface refresh`, `full multi-role Hermes production mapping`, `full ACF integration` — contracts/evaluators/proofs may exist, but they are **not** operationally-activated runtimes.

Implemented contract ≠ operationally-activated runtime.

## Token-Efficiency Design Intent

Bounded cost is an explicit design goal, implemented via:

```text
bounded eager bootstrap (BootstrapBudget) + small initial Tool surface (ROLE_EAGER_TOOL_SURFACE)
+ progressive specialized Tool disclosure (bounded ToolCapabilityRef)
+ progressive Skill hydration (pinned/required/role_default eager, recommended progressive)
+ CARD-first Worker reconciliation + reference-first richer access (ResultHandoffRef)
+ bounded search/read results + context hydrate-on-demand
```

Goal: minimize unnecessary model context, repeated full-result reads, and Tool schema exposure while retaining deterministic governance and authority boundaries. No unmeasured percentage claim is made.

## Layout (updated)

- `aota_forge/` — Forge Core + Governed Work Plane (Core package is **not** M1 read-only ingress only; S1–S6 converged)
- `aota_forge/work_plane/` — Work Roles, bootstrap, handoff, Tool/Workspace/Skill, CARD, selective hydration, context bootstrap, lifecycle
- `aota_forge/core/` — execution, contracts, ingress, providers
- `aota_forge/adapters/hermes/` — Hermes Worker translation + host protocol
- `aota_forge/composition/` — production execution composition (coder-only slice)
- `aota_forge/cli/` — canonical CLI transport adapter (`M5-5`)
- `chat_governance/` — canonical Chat governance index + shared core + thin adapters
- `deploy/evidence/` — migration and milestone evidence
- `docs/architecture.md` — descriptive architecture overview (non-normative, not a second governance authority)

## Docs

- Descriptive architecture: `docs/architecture.md` (overview, not Plan authority)
- Governance: `chat_governance/GOVERNANCE_INDEX.md`
- Program: `aota-hermes-tools#24` (final known-good `d15eb26f87584fbdbe6ea5c2fdfdb942aa73b987`)

## Source Migration

See `deploy/evidence/issues/9/m1-repository-migration.json` for deterministic migration provenance from `aota-hermes-tools`.
