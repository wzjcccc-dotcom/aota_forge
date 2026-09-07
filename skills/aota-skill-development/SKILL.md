---
name: aota-skill-development
description: Canonical AOTA Forge workflow for Skill contract, scope, deployment, visibility, cache refresh, and behavior proof.
category: forge
tags: [aota, forge, skill, lifecycle, profile]
---
> **W0 Canonical Migration — aota_forge is authority**
>
> This Skill's semantic authority converges to `aota_forge/skills/aota-skill-development/SKILL.md` (M1/W0).
> Legacy `aota-hermes-tools/skills/aota-skill-development/SKILL.md` is non-authoritative projection after W0.
> Bounded rewrite: removed Hermes host-specific loader path assumptions (`~/.hermes/profiles/.../skills`, `plugins/aota-tools` mount)
> and aligned tool references to AF canonical seams (`workspace.read` / `workspace.search` / `workspace.write` via `aota.invoke`,
> `OperationContractDescriptor` / `work_plane/workspace_tools.py`, `work_plane/skill_registry.py`).
> `SKILL_IS_AUTHORITY=no` preserved — Skill remains guidance, not authority.


# AOTA Skill Development

Trigger for adding, changing, deploying, binding, or validating an AOTA Skill,
Profile active Skill, SOUL reference, or Skill-loader configuration.

Read the development README, Skill Lifecycle SOP, and Runtime Paths/Reloads.
Define owner, trigger, scope, and non-goals; choose global/profile-local
strategy; create canonical source; configure Profile visibility/disabled policy;
update manifest and inventory; verify deployed paths and prompt/context
visibility; refresh importing processes as required; open a new session; then
perform the bounded behavior smoke.

Never treat a SOUL mention, global presence, `SKILL.md` existence, or hash
parity as activation. Do not assume named Profiles inherit global Skills or
that `/reload-skills` clears prompt cache.

`AOTA_SKILL_DEVELOPMENT_SKILL_PASS`

## Skill Lifecycle Closure Rule

A Skill development task MUST NOT be submitted as `completed` until the
Skill lifecycle is demonstrably closed:

1. **Source written and validated**: The SKILL.md file exists at the
   canonical path, all required sections are present, and source
   validation (syntax, structure, cross-references) passes.

2. **Manifest updated**: The Skill is declared in the canonical assembly
   manifest (`deploy/profile-runtime-assembly.yaml`) with the correct
   profile assignment (active or reference).

3. **Profile visibility configured**: The Skill is explicitly allowed or
   disabled in the target Profile's configuration. A Skill that exists on
   disk but is not configured is not lifecycle-complete.

4. **SOUL reference is consistent**: If the Skill is declared in the
   Profile's SOUL.md, the reference must match the canonical name and
   activation class. A stale or incorrect SOUL reference is a lifecycle
   defect.

5. **Closure gate is PASS_SOURCE_SKILL_LIFECYCLE for source-only tasks**:
   A task authorized only for source changes must close at
   `PASS_SOURCE_SKILL_LIFECYCLE`. It must not claim deploy or runtime
   gates. Proceeding beyond the authorized gate is a scope violation.

6. **Deploy and runtime gates are separate**: `PASS_DEPLOYED_SKILL_LIFECYCLE`
   and `PASS_RUNTIME_SKILL_LIFECYCLE` require explicit SPEC authorization
   for managed deploy and process recreation. A source-only SPEC does not
   authorize these gates.

The lifecycle is not closed until all gates authorized by the SPEC are
passed and evidenced. A partial lifecycle (e.g., source written but
manifest not updated) is a `partial` outcome, not `completed`.

## Deployment and Runtime Guidance

- Profile runtime assembly is manifest-driven. The canonical assembly manifest
  (`deploy/profile-runtime-assembly.yaml`) declares active Skills per Profile.
- Profile-local plugin and Skill projections are required when the Hermes loader
  uses profile home (`AF SkillRegistry (static declarative) + work_plane/skill_bootstrap/<name>/plugins/`,
  `AF SkillRegistry (static declarative) + work_plane/skill_bootstrap/<name>/skills/`).
- Config declaration is not runtime availability evidence. A Skill declared in
  config may not be loaded if the file is missing or the cache is stale.
- SOUL declaration is not Skill-load evidence. A Skill referenced in SOUL.md may
  not be loaded if the file is missing, the cache is stale, or the loader
  rejects it.
- Runtime verification requires loaded tool/Skill evidence (tool list, dispatch
  result, Skill-loaded prompt inspection) or actual Profile Task execution.
  Hash parity between source and runtime files is deploy evidence, not runtime
  evidence.
- Managed deploy and recreate requirements: after a managed deploy, all
  importing processes must be recreated. Agent-only recreate is allowed only
  when evidence proves WebUI is unaffected.
- Host/Codex construction vs Hermes runtime verification boundary: source PASS
  does not imply deploy PASS, and deploy PASS does not imply runtime PASS.
- Project Steward owns documentation continuity, not Skill source
  implementation. Skill content and lifecycle belong to the owning Profile.
- task-main must not manually fabricate deployment success. If a deployment
  receipt is missing or runtime evidence contradicts declared state, report
  the discrepancy.
- Runtime-generated files (logs, snapshots, receipts, backups) are not managed
  source and must not be added to the managed manifest.
- No generic `/aota-runtime` access; use bounded tools
  (`aota_runtime_info`, `aota_active_task_artifact_open`).


---
*W0 provenance: migrated from legacy `aota-hermes-tools` (SHA c2e0c4d) to `aota_forge` canonical; `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`.*
