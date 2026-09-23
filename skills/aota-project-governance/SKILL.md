---
name: aota-project-governance
description: Governance 2.0 Project intake, identity and reconciliation guidance
category: governance
tags: [aota, governance, project, intake]
---

# Project Governance

Use this progressive Skill when a user wants to create, inspect, or reconcile
an AF Project. Governance 2.0 is the normal new-Project path. Keep the
semantic Project choice with the user; the Control Plane owns trusted
registration, manifest validation, root binding, durable store access, and
generated projection lifecycle.

```text
PROJECT_GOVERNANCE_SKILL_PROGRESSIVE=yes
PROJECT_IDENTITY_SOURCE=trusted_manifest_and_registry
PROJECT_MANIFEST_OWNS_PLAN_STATE=no
ROOT_REF_IS_PATH=no
MAP_STATUS_MANUAL_MAINTENANCE=no
PROJECT_STEWARD_IS_NORMAL_WORKER=no
```

## Authority and modes

- The manifest and trusted Project Registry establish Project identity and physical registration.
- `root_ref` is a trusted capability reference, not a filesystem path or a user-granted authority token.
- `governance.project` is the single Agent-facing intake family. Its modes are `init`, `inspect`, and `reconcile`.
- `init` registers the Project and materializes the initial generated projections.
- `inspect` reads current evidence without rewriting projections.
- `reconcile` is for an observed registration or projection drift; it must be grounded in current trusted evidence.
- MAP/STATUS, Cards, and ContextRoute are generated navigation/projection artifacts. Never edit them manually and never treat them as Plan authority.

Normal path:

1. Confirm the canonical `project_id`; if it is missing or ambiguous, ask rather than infer a path or repository.
2. Obtain the user's semantic metadata and approval when `init` or reconciliation changes durable governance state.
3. Use `aota.invoke` with `operation="governance.project"` and the requested mode.
4. Verify the returned status, identity evidence, and projection/reference outputs before continuing.
5. Reuse the returned card/refs; hydrate only an attached reference when detail is required.

Never provide or infer filesystem paths as model authority. Stop on ambiguity,
registry drift, root failure, missing approval, stale/unknown durable outcome,
or a typed authority error. Do not fall back to a project-steward worker for a
normal Project intake.
