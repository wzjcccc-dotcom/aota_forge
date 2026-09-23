---
name: aota-plan-governance
description: Governance 2.0 local Plan intake and bounded lifecycle guidance
category: governance
tags: [aota, governance, plan, local-authority, intake]
---

# Plan Governance

Use this progressive Skill when a user wants to initialize, inspect, amend, or
close a local Governance 2.0 Plan. The user owns semantic proposal and
approval; the Control Plane owns the trusted Plan document, Project Governance
record, journal/CAS, readback, and generated projection lifecycle.

Normal path:

1. Confirm the canonical `project_id`, `plan_id`, candidate semantics, and any
   required revision precondition.
2. Use `aota.invoke` with `operation="governance.plan"` and the requested
   `mode`.
3. Treat `plan.md` as Plan authority. MAP/STATUS, Cards, and ContextRoute are
   derived projections only.
4. Use `help(operation="governance.plan")` for exact current inputs and typed
   errors.

Amendment and close operations must stop on stale revision, stale authority,
ambiguous identity, or unknown durable outcome. Never create a second Plan
mutation path or a `plan_amend` operation.
