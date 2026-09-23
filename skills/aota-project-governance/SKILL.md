---
name: aota-project-governance
description: Governance 2.0 Project intake and reconciliation guidance
category: governance
tags: [aota, governance, project, intake]
---

# Project Governance

Use this progressive Skill when a user wants to create, inspect, or reconcile
an AF Project. Keep the semantic Project choice with the user; the Control
Plane owns trusted registration, manifest validation, root binding, and
generated MAP/STATUS projection.

Normal path:

1. Confirm the canonical `project_id` and any semantic metadata.
2. Use `aota.invoke` with `operation="governance.project"` and the requested
   `mode`.
3. Treat `project.yaml` and Project Registry evidence as identity facts, not
   as Plan or session authority.
4. Use `help(operation="governance.project")` for exact current inputs and
   typed errors.

Never provide or infer filesystem paths as model authority. Stop on ambiguity,
registry drift, root failure, or a typed authority error.
