---
name: aota-plan-governance
description: Governance 2.0 local Plan authority, intake and bounded lifecycle guidance
category: governance
tags: [aota, governance, plan, local-authority, intake]
---

# Plan Governance

Use this progressive Skill when a user wants to initialize, inspect, amend, or
close a local Governance 2.0 Plan. Governance 2.0 local authority is the
normal new-Plan path; a legacy GitHub Issue is compatibility input, not a new
Plan authority. The user owns semantic proposal and approval; the Control
Plane owns the trusted Plan document, Project Governance record, journal/CAS,
readback, and generated projection lifecycle.

```text
PLAN_GOVERNANCE_SKILL_PROGRESSIVE=yes
GOV2_IS_NORMAL=yes
NEW_PLAN_REQUIRES_GITHUB_ISSUE=no
PLAN_MD_IS_AUTHORITY=yes
MAP_STATUS_ARE_PROJECTIONS_ONLY=yes
PLAN_AMEND_SECOND_MUTATION_PATH=no
```

## Authority distinction

- `project_id` identifies the trusted Project; `plan_id` identifies the canonical local Plan.
- A human Plan number and a legacy GitHub Issue number are not the canonical local `plan_id`.
- `plan.md` plus the durable journal/CAS is local Plan authority. Cards, ContextRoute, MAP and STATUS are derived navigation/projections.
- The Control Plane fills trusted roots, bindings, revisions, and authority claims. Never turn a path, session, profile, or free-form locator into authority.

Normal path:

1. Confirm canonical `project_id` and `plan_id`, the semantic proposal, and any user approval required for a mutation.
2. Use `aota.invoke` with `operation="governance.plan"` and one of `init`, `inspect`, `amend`, or `close`.
3. Verify the returned durable status, revision, authority evidence, and projection/reference outputs.
4. Use `help(operation="governance.plan")` for exact current inputs and typed errors; do not copy a schema into this Skill.

Mode intent:

- `init` materializes an approved candidate as the first local Plan revision and creates its initial projections.
- `inspect` is read-only and is the first step when current authority or lifecycle state is unknown.
- `amend` is a semantic whole-candidate update guarded by a fresh revision/authority precondition and decision reference.
- `close` is a governed lifecycle transition, not a body rewrite; verify closure and projection readback.

Amendment and close operations must stop on missing approval, stale revision,
stale authority, ambiguous identity, invalid candidate, or unknown durable
outcome. Never create a second Plan mutation path, a `plan_amend` operation,
or manual MAP/STATUS edits. Legacy Gov1 migration is a separate future
compatibility workflow and is not activated by this Skill.
