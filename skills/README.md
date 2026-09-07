# AOTA Forge Canonical Skills — W0

This directory is the **canonical source** for AOTA domain Skills.

```text
AOTA_SKILL_CANONICAL_SOURCE=aota_forge
AOTA_TOOL_CANONICAL_SOURCE=aota_forge
AOTA_HERMES_TOOLS_SKILL_AUTHORITY=no
AOTA_HERMES_TOOLS_TOOL_AUTHORITY=no
SKILL_IS_AUTHORITY=no
```

- Skill content is Agent-facing operational knowledge / guidance.
- Runtime operation authority remains AF typed contracts (`OperationContractDescriptor`, `work_plane/*` providers, `core/contracts/*`).
- `aota-hermes-tools/skills/` may retain physical files only as **non-authoritative projection / host glue / historical evidence**.
- A legacy file may NOT remain the only canonical semantic source.
