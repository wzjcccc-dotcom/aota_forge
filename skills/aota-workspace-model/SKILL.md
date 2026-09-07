---
name: aota-workspace-model
description: Registered filesystem authority and PCF project identity model.
category: forge
---
> **W0 Canonical Migration — aota_forge is authority**
> Legacy `aota-hermes-tools/skills/aota-workspace-model/SKILL.md` is non-authoritative projection after W0. Semantic parity preserved; `SKILL_IS_AUTHORITY=no`.


# AOTA Workspace Model

`workspace_id` is a canonical registry key for a filesystem authority root.
`project_id` is an identity inside that workspace and is validated by its
manifest. They may match but are never assumed equal. IDs are never generated
by workers. `/aota-runtime` stores control-plane artifacts and is not a
project-tier workspace.


---
*W0 provenance: migrated from legacy to `aota_forge` canonical; `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`.*
