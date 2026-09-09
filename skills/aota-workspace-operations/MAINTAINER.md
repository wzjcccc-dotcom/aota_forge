# Maintainer notes — aota-workspace-operations (outside normal runtime)

- Canonical descriptor authority is `.aota/contracts/operations.yaml`; prior Skill projections derived via parity check preserved here for audit.
- Progressive disclosure proof mechanics (`SkillSearchIndex`/`AllowedSkillUniverse`/`ToolRoleSurface` without loading body, `open_skill` digest verification) is implementation history, not normal usage.
- Provider internals (`WorktreeSandboxBoundary` + `TaskHandoff` + policy evidence, TOCTOU revalidation, symlink containment, output bound accounting) preserved for maintainers.
- `MAINTAINER_CONTENT_IN_NORMAL_LLM_CONTEXT=no`.
