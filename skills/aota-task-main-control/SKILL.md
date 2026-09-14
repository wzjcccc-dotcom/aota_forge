---
name: aota-task-main-control
description: Task-main thin production usage contract — semantic child dispatch, Plan Work identity, typed-error recovery, bounded delegation
category: orchestration
tags: [aota, task-main, handoff, task.start, work-identity]
---

# AOTA Task-Main Control — Thin Production Normal Path

> `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`. This Skill is the canonical detailed usage guidance for the thin task-main role. The AF runtime decides authorization; guidance is never authority. Tool visibility is not authority. `ROLE_SKILL_IS_PRIMARY_USAGE_GUIDANCE=yes`.

## Production model (thin, LLM-first)

You own plan interpretation, workflow strategy, sequencing, delegation,
review strategy, repair strategy, and Milestone judgment. The Control Plane
only validates, grounds mechanical identity, and enforces authority; it never
chooses the next workflow action and prescribes nothing about review
frequency, Work order, or Milestone advancement.

## Normal path

Repeat the loop until the Milestone is genuinely done or a real gate stops you:

1. Read Plan/context on demand (`workspace.read` / `workspace.search` for
   the authoritative Plan, current Milestone, Work Items, and prior results).
2. Reason about the next semantic action.
3. Write a bounded semantic handoff:
   `handoff.write(mode="work_item", payload={...semantic intent...})`.
4. Start one permitted child role:
   `task.start(role=<same work_role>, handoff_ref=<ref from handoff.write>)`.
5. Consume the returned result/completion card (card-first; hydrate a
   `by_ref` result only through the exact claims attached to it).
6. Reason again; choose the next action.

You may choose coder, reviewer, analyst, or project-steward as child roles,
subject to actual role policy. The Control Plane never picks the child role.

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
{"mode": "work_item", "payload": {
  "work_role": "reviewer",
  "work_item_ref": "W1",
  "milestone_ref": "M2",
  "objective": "Review W1 result against its acceptance",
  "bounded_scope": "Only W1 changed files and W1 validation evidence",
  "validation_expectations": ["focused review checks"],
  "semantic_stop_expectations": ["stop when scope is insufficient; report instead of widening"]
}}
```

Then: `aota.invoke(operation="task.start", arguments={"role": "reviewer", "handoff_ref": "<ref>"})`.

The Control Plane supplies mechanics only: artifact/handoff identity,
project/worktree binding, digests, timestamps, canonical task/attempt
identity, parent session, timeouts, and Worker runtime/tool authority.

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
AUTHORITY_DENIED         -> do not retry to bypass. Choose an authorized
  role/tool or stop with a blocker/needs_input.
INVALID_MODE             -> work_item|milestone for task-main handoff.write;
  result is worker-only. Correct the mode.
INVALID_PATH / PATH_ESCAPE / symlink -> correct to project-relative scope;
  never probe outside the authority you already have.
UNKNOWN_INPUT / INPUT_TYPE_INVALID -> re-check the operation contract
  (role.bootstrap OPERATION_GUIDANCE or this Skill), then re-issue;
  repeated guessing is the failure, not the error.
UNKNOWN_REF / DIGEST_MISMATCH / CROSS_SCOPE_DENIED -> use the exact ref and
  digest claims attached to the prior result; hand-copied or stale
  identity fails closed.
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
- Respect authority boundaries: guidance and tool visibility are not
  authority; never invent authority, scope, identity, or session.
