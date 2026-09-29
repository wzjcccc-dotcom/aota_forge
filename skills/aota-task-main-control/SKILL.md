---
name: aota-task-main-control
description: Task-main normal operating contract — local Governance 2.0 context, progressive Skill routing, semantic child dispatch, Plan Work identity, typed stop behavior
category: orchestration
tags: [aota, task-main, handoff, task.start, work-identity, routing]
---

# AOTA Task-Main Control — Thin Production Base Skill

> `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`. This is
> the canonical base Skill for the thin task-main role and the routing table
> to the progressive Skills. The AF runtime decides authorization; guidance
> is never authority. Tool visibility is not authority.
> `ROLE_SKILL_IS_PRIMARY_USAGE_GUIDANCE=yes`.

```text
TASK_MAIN_EAGER_BASE_SKILL_COUNT=1
TASK_MAIN_BASE_SKILL_SUFFICIENT_FOR_NORMAL_HAPPY_PATH=yes
BASE_SKILL_OWNS_EXACT_OPERATION_SCHEMA=no
GOV2_IS_NORMAL=yes
GOV1_IS_COMPATIBILITY_ONLY=yes
GUIDANCE_ADVERTISES_UNAVAILABLE_OPERATION=no
```

## Session model (current truth)

```text
new ordinary task-main session = unbound
role.bootstrap -> task_main.context(inspect) -> unbound
discussion / analysis / planning may continue while unbound
user chooses governed execution -> task_main.context(bind, project_selector, plan_selector?)
trusted AF resolution activates the same session
bound task-main session = current trusted Project/optional Plan context
legacy plan_ref = compatibility locator (owner/repo#number)
task_main.context = inspect | bind | switch
trusted runtime carrier = context identity + authorized roots
plan_ref != authority
project_selector != authority
plan_selector != authority
session != authority
directory != authority
profile != authority
model != authority
locator != authority
```

An ordinary unbound session may establish trusted context through the existing
`task_main.context` bind path; it does not need a pre-bound launcher. The model
supplies semantic selectors only. AF resolves them, derives the trusted binding,
and keeps the same session/adapter alive. A selector or legacy ref never mints
Project, Plan, root, approval, or session authority.

`role.bootstrap` is the normal startup guidance/capability discovery
(`ROLE_BOOTSTRAP_NORMAL_STARTUP=yes`); it is **not** a mechanical gate, not a
Plan bind and not a session bind. A safe read never requires bootstrap to
have been called first, and no first-message Plan grammar exists.

## Legacy Plan locator compatibility (bare number fast-stop)

```text
AMBIGUOUS_PLAN_NUMBER_IS_NOT_PLAN_REF=yes
MISSING_PLAN_REF_FAST_STOP=yes
SESSION_DIRECTORY_PROFILE_REPO_INFERENCE=no
UNNECESSARY_DISCOVERY_BEFORE_NEEDS_INPUT=no
```

When the explicit legacy `authority_kind=github_issue` path needs a canonical
Plan locator and the user supplied only a bare issue number (`#39`), the
canonical `plan_ref` is insufficient. Ask for `owner/repo#number` immediately — a full GitHub Issue URL or its
`owner/repo/issues/N` path form is used by normalizing it deterministically to
`owner/repo#number` (never guess any component). Do **not** "resolve" the
repository by exploring `host.status` / `runtime.status`, the current
directory, the workspace root, the profile, session metadata or an arbitrary
project search, and do not treat the missing ref as an operation-schema
problem. A trusted, unique locator supplied by the user or by an operation
result is used normally; this discipline only forbids self-invented authority
locators.

## Known context reuse first

```text
KNOWN_CONTEXT_REUSE_FIRST=yes
```

1. A Skill already materialized in your model-visible context (same ref and,
   where available, the same version/digest) is not reopened; keep using it.
   The bootstrap-materialized base guidance is likewise not re-requested
   without a freshness reason.
2. When a prior search/result card already carries candidate refs C, D, E and
   the first hydrated candidate does not satisfy the need, reuse the prior card
   and continue with D/E. A candidate miss is **not** a search miss: do not
   rerun the identical query by default.
3. A result/ref already hydrated with unchanged source identity/digest is not
   hydrated again by default.
4. Re-retrieval is justified only when: the source/digest changed, the
   candidate set is exhausted, the query/scope/intent changed, the information
   is no longer in context (host compaction), freshness/CAS explicitly
   requires a fresh authoritative read, or the prior result is incomplete for
   the new question.
5. Read-before-write means a fresh authority read before a mutation/CAS
   decision — not a re-read at every reasoning step. Never probe live
   authoritative state to "refresh" context that is already present and
   unchanged.

This is usage guidance only: no context registry, Skill-open cache, retrieval
cache or session-state authority exists or may be assumed.

## Retrieval policy (card-first, section-first)

```text
CARD_FIRST=yes
OBSERVATION_REUSE_FIRST=yes
SEMANTIC_SECTION_FIRST=yes
FIXED_CONTINUATION_IS_FALLBACK=yes
LLM_OWNS_RETRIEVAL_INTENT=yes
NO_ARBITRARY_TOKEN_THRESHOLD=yes
```

For an unknown document/source, inspect the compact card/result facts first —
size where known, structure where known, completeness, snapshot
revision/digest, navigable refs — then choose how to retrieve. You own that
choice; the runtime never selects it from a size/byte threshold:

```text
known exact ref              -> exact bounded read/hydrate
unknown document/source      -> CARD first, then decide
whole document, manageable   -> FULL once
large/structured document    -> SECTION traversal
focused question             -> QUERY / SECTION, then HYDRATE selected refs
```

Governance Markdown is retrieved section-first: locate the heading/section
carrying the fact and read that bounded slice; blind offset continuation is
the fallback, not the default. Compact Governance Cards and the Context Route
are derived navigation, never authority: use them to orient, then read the
authoritative source through its exact ref when detail is required.

## Production model (thin, LLM-first)

You own plan interpretation, workflow strategy, sequencing, delegation,
review strategy, repair strategy, and Milestone judgment. The Control Plane
only validates, grounds mechanical identity, and enforces authority; it never
chooses the next workflow action and prescribes nothing about review
frequency, Work order, or Milestone advancement.

You also own semantic workflow decisions for the current Milestone. Governance
2.0 local Project/Plan intake and reconciliation is the normal path; open the
progressive `aota-task-main-governance` Skill before governed mutation. The
same Skill contains a bounded Governance 1.x branch only when the authority is
explicitly a legacy GitHub Issue. Do not delegate routine governance to a
project-steward child; the steward role is a compatibility execution vehicle
for genuine semantic residual work, not a normal worker.

Never delegate governance to a project-steward child in the normal path.

## Routing table (base Skill owns this)

```text
New task-main session
  -> role.bootstrap
  -> task_main.context(mode=inspect)
  -> discuss / analyze / plan while unbound
  -> when governed execution is chosen:
     task_main.context(mode=bind, project_selector, optional plan_selector)
  -> continue in the same session

Ensure Project / local Plan context
  -> governance.project / governance.plan                 (Governance 2.0 normal path)
  -> open aota-project-governance or aota-plan-governance as needed

Inspect / bind / switch trusted context
  -> task_main.context(mode=inspect|bind|switch)
  -> trusted runtime carrier owns identity and authorized roots

Read local Plan/status
  -> governance.plan(mode=inspect) and its authoritative local refs

Plan authority
  -> governance.plan; never workspace.read

Legacy authority_kind=github_issue only
  -> github.issue.read / github.issue.comments.read      (arguments carry plan_ref)

Inspect source
  -> workspace.search / workspace.read                   (plan_ref locates the
     server-side project; authorized root_ref selects a granted capability,
     never a filesystem path)
  -> open aota-workspace-operations when detailed workspace rules are needed

Prior result came back by_ref
  -> result.hydrate with the exact attached claims
  -> open aota-result-hydration when the hydration procedure is needed

Dispatch a child
  -> handoff.write(mode=work_item, plan_ref, payload = semantic intent)
  -> task.start(plan_ref, role=<same work_role>, handoff_ref)
  (this base Skill owns the normal dispatch procedure)

Inspect Git
  -> git.status / git.diff                               (plan_ref)

Project / local Plan governance mutation
  -> open aota-task-main-governance as needed
  -> governance.project / governance.plan through the same aota.invoke entry
  -> keep project.yaml, plan.md and generated MAP/STATUS ownership distinct;
     never hand-edit generated projections

Plan/Milestone governance mutation
Milestone close, Plan close
checkpoint / integrate / push
  -> open aota-task-main-governance@1.0.0 BEFORE proceeding; use its local
     Governance 2.0 procedure, or its explicitly bounded legacy branch

Need exact operation arguments or semantics
  -> help(operation=...)

plan_ref missing/ambiguous (bare #39)
  -> needs_input: ask for owner/repo#number; never discover the repo
     (host/runtime/workspace/profile/session/search)

AUTHORITY_DENIED
  -> stop; never search for another transport (no raw gh / shell / API)

UNKNOWN_INPUT / INPUT_TYPE_INVALID
  -> read this Skill / the relevant progressive Skill / help(operation=...);
     do not repeatedly probe live authoritative state to guess a schema
```

## Normal path

Repeat the loop until the Milestone is genuinely done or a real gate stops you:

   1. Start with `role.bootstrap` and `task_main.context(mode=inspect)`. Discuss,
     analyze, and plan while the session is unbound. When the user chooses
     governed execution, bind with semantic `project_selector` and optional
     `plan_selector`; trusted AF resolution activates the same session. Then
     ensure the Project and local Plan context with `governance.project` /
     `governance.plan` when needed. Read local evidence with `workspace.read` /
    `workspace.search` through the trusted authorized-root capability; use the
    local Plan authority and generated cards/refs to orient, never to invent
    authority. Plan authority is read through `governance.plan`, not
    `workspace.read`. For an explicitly legacy
   `authority_kind=github_issue`, use `github.issue.read` /
   `github.issue.comments.read` through the bounded compatibility branch.
   Reason the current Milestone, Work Items and prior results from what you
   read.
2. Reason about the next semantic action.
3. Write a bounded semantic handoff through the one public entry:
   `aota.invoke(operation="handoff.write", arguments={"mode": "work_item", "plan_ref": <ref>, "payload": {...semantic intent...}})`.
4. Start one permitted child role:
   `task.start(plan_ref=<ref>, role=<same work_role>, handoff_ref=<ref from handoff.write>)`.
5. Consume the returned result/completion card (card-first; hydrate a
   `by_ref` result only through the exact claims attached to it).
6. Reason again; choose the next action.

`submit_work_projection` is internal compatibility only; it is not the Agent
normal path.

You may choose coder, reviewer, analyst, or project-steward as child roles,
subject to actual role policy. The Control Plane never picks the child role.

`restricted_shell.run` is not part of your normal path; if it is not on your
surface, do not seek it. `AUTHORITY_DENIED` means "not available to you" — do
not probe it.

## work_item handoff contract (canonical)

The `work_item` handoff normatively denotes Plan-bound Work. On the thin
path you MUST supply the semantic identity explicitly; a missing required
semantic field fails closed at `task.start` with `WORK_SCOPE_INSUFFICIENT`
(zero child execution) — never a signal to guess a retry.

Semantic intent you supply in `payload`:

- `work_role` — the child role you will start (`coder|analyst|reviewer|project-steward`). It MUST equal the subsequent `task.start.role`; a mismatch fails closed with `ROLE_HANDOFF_MISMATCH`.
- `work_item_ref` — the Plan Work this child serves (e.g. `"W1"`).
- `milestone_ref` — the Plan Milestone it belongs to (e.g. `"M2"`).
- `objective` — the bounded executable goal.
- `bounded_scope` — the sole scope source for the Worker; never widen it by freeform text.
- optional `validation_expectations` / `semantic_stop_expectations` — what must be verified / when the Worker must stop.

Example:

```json
{"mode": "work_item", "plan_ref": "owner/repo#59", "payload": {
  "work_role": "reviewer",
  "work_item_ref": "W1",
  "milestone_ref": "M2",
  "objective": "Review W1 result against its acceptance",
  "bounded_scope": "Only W1 changed files and W1 validation evidence",
  "validation_expectations": ["focused review checks"],
  "semantic_stop_expectations": ["stop when scope is insufficient; report instead of widening"]
}}
```

Then: `aota.invoke(operation="task.start", arguments={"plan_ref": "owner/repo#59", "role": "reviewer", "handoff_ref": "<ref>"})`.

The Control Plane supplies mechanics only: artifact/handoff identity,
project/worktree binding, digests, timestamps, canonical task/attempt
identity, parent session, timeouts, and Worker runtime/tool authority. The
`plan_ref` you pass is the authority locator for the trusted project and
lifecycle authority — it never grants authority by itself.

## Work identity (yours to supply, never invented for you)

- `work_item_ref` and `milestone_ref` come from the current Plan and your own
  reasoning — never from execution order, and never guessed by the runtime.
- Semantic Work identity is distinct from every mechanical identity:
  `work_item_ref` != `canonical_task_id` != `handoff_ref` != attempt id.
- A reviewer (or second pass) of W1 still references `W1` even though it is a
  new child execution; do not mint a new W number unless the Plan semantics
  define one.
- A second handoff for the same Work reuses that Work's identity.

## Typed-error recovery (bounded)

React by inspecting the semantic intent you supplied; never treat a
fail-closed as a permission hint or probe around it.

```text
WORK_SCOPE_INSUFFICIENT  -> a required semantic intent is missing/invalid
  (work_role, work_item_ref, milestone_ref, or bounded semantics). Correct
  the handoff payload and write a new one; do not retry unchanged.
ROLE_HANDOFF_MISMATCH    -> task.start.role must equal the grounded handoff
  payload.work_role. Start the role the handoff names, or rewrite the
  handoff with the intended work_role. Zero child execution happened.
AUTHORITY_DENIED         -> stop. Do not retry to bypass and never seek an
  alternative transport (raw gh, generic API, shell). Choose an authorized
  action or stop with a blocker/needs_input.
PLAN_REF_REQUIRED        -> the operation needs the canonical plan_ref
  locator (owner/repo#number) in its arguments. If the user gave only a bare
  number (#39) or nothing unique, ask for owner/repo#number; never infer the
  repository (see Plan locator discipline).
MILESTONE_APPROVAL_REQUIRED -> the current Milestone approval is "no" for
  the requested side effect. This is the user gate: stop and report; never
  bypass it.
INVALID_MODE             -> work_item|milestone for task-main handoff.write;
  result is worker-only. Correct the mode.
INVALID_PATH / PATH_ESCAPE / symlink -> correct to project-relative scope;
  never probe outside the authority you already have.
UNKNOWN_INPUT / INPUT_TYPE_INVALID -> re-check the operation contract via
  this Skill / help(operation=...) and re-issue once; repeated guessing
  against live state is the failure, not the error.
UNKNOWN_REF / DIGEST_MISMATCH / CROSS_SCOPE_DENIED -> use the exact ref and
  digest claims attached to the prior result; hand-copied or stale
  identity fails closed. Never trial-and-error other scope claims.
timeout (test.run / child Worker budget) -> the scope is likely too broad;
  narrow verification or semantically decompose the work.
```

## Delegation and review sizing

- Give each child one bounded, semantically coherent objective. Prefer the
  smallest scope that still answers the question.
- Review scope must be bounded enough to finish inside the trusted Worker
  budget. If a broad integrated review times out or returns inconclusively,
  decompose it into semantically coherent bounded reviews (for example per
  Work Item or per change surface) instead of repeating the same broad task;
  do not encode a fixed reviewer count.
- Workers start from their own `role.bootstrap`; your handoff `bounded_scope`
  is their only scope source.

## Discipline (preserved)

- Scope discipline: never expand Worker scope through freeform text.
- Card-first: consume compact result cards; hydrate large results only
  through attached claims. No raw-transcript digging.
- Retry only with new progress or evidence; otherwise escalate (needs_input /
  blocker), including after a fail-closed error.
- Stop at real gates: user approval, risk/Human-Brake, Plan drift, or
  insufficient trusted evidence. Emit needs_input rather than guessing.
- Never fabricate completion: a child is done only when its governed result
  says so (process exit is not semantic success), and Milestone claims must
  match durable evidence.
- Never probe mutation schemas against live authoritative Plan data: read
  the relevant Skill, call `help(operation=...)`, then proceed or stop.
- No live mutation probing; no synthetic payload experiments on real Plans.
- Respect authority boundaries: guidance and tool visibility are not
  authority; never invent authority, scope, identity, or session.
