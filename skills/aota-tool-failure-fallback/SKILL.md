---
name: aota-tool-failure-fallback
description: Safe AOTA fallback guidance without privilege escalation.
category: forge
---
> **W0 Canonical Migration — aota_forge is authority**
>
> This Skill's semantic authority converges to `aota_forge/skills/aota-tool-failure-fallback/SKILL.md` (M1/W0).
> Legacy `aota-hermes-tools/skills/aota-tool-failure-fallback/SKILL.md` is non-authoritative projection after W0.
> Bounded rewrite: removed Hermes host-specific loader path assumptions (`~/.hermes/profiles/.../skills`, `plugins/aota-tools` mount)
> and aligned tool references to AF canonical seams (`workspace.read` / `workspace.search` / `workspace.write` via `aota.invoke`,
> `OperationContractDescriptor` / `work_plane/workspace_tools.py`, `work_plane/skill_registry.py`).
> `SKILL_IS_AUTHORITY=no` preserved — Skill remains guidance, not authority.


# AOTA Tool Failure Fallback

Missing workspace context falls back to workspace list, Steward recommendation,
task-main durable selection, or user clarification. Do not suggest switching
to a default Profile, terminal, or unrestricted file access to read an
attachment or discover a workspace.

For Profile Task launcher failures, use the task-local bounded redacted log and
canonical fallback finalizer. Preserve the frozen binding, failure stage, exit
code, and receipt reference in the failure handoff; do not require a worker
CARD/RESULT that could not have been produced.

Credential failures use `failure_stage=credential_bootstrap` and one of
`credential_missing`, `credential_authority_invalid`, or
`credential_provider_unsupported`. Diagnostics may identify provider, key env
name, source type, global-root summary, and existence booleans only; never
write API keys, OAuth tokens, `auth.json`, or full `.env` content.

Completion wakeups consume canonical receipt evidence. They report `done` only
when the receipt says `status=done`, `exit_code=0`, and `outcome=completed`;
missing receipt is `failed` when a nonzero exit is observed and otherwise
`unknown` when process completion cannot be established. Wakeups include task,
start, profile, receipt/handoff presence, and failure stage without secrets.

Scope population failures use `failure_stage=scope_population` and
`error_classification=invalid_frozen_scope` (or
`workspace_baseline_unavailable`), set `worker_started=false`, and preserve the
same bounded failure receipt/handoff path. A launcher scope/spec/task digest
mismatch is `scope_binding_mismatch` and stops before worker execution.

## Tool Structured Error Fallback Rules

When any AOTA tool returns a structured error (a JSON envelope with `status`
and `error` fields, not a simple string or traceback), the worker MUST follow
the `TOOL_STRUCTURED_ERROR_REQUIRES_CONTRACT_RELOAD_BEFORE_RETRY` invariant:

1. **Stop retrying immediately**: Do not loop on the same arguments. A
   structured error means the tool received and rejected the request — not a
   transient network failure.
2. **Load `aota-tool-failure-fallback`**: Ensure this Skill is loaded (it
   should already be active or reference-loaded by the orchestration Skill
   Loading Map).
3. **Load the applicable contract or recipe**: Depending on the tool class:
   - SPEC-related tools → load `aota-canonical-spec-contract` and
     `aota-canonical-spec-pitfalls`.
   - Task lifecycle tools → load `aota-task-lifecycle`.
   - Profile task status/start tools → load `aota-task-lifecycle`.
   - File tools → load the scope/path validation rules from the frozen SPEC.
4. **Inspect the exact structured error**: Read the `error` field. Identify the
   specific rejection code (e.g. `forbidden_scope_denied`,
   `invalid_spec_kind`, `capability_ceiling_exceeded`). Do not guess the fix
   from the error class alone.
5. **Retry at most once with evidence**: After consulting the contract, issue
   exactly one corrected call. The retry must correct a specific, identified
   issue — not change unrelated parameters or guess a different tool.
6. **Otherwise `needs_input`**: If the retry also fails with a structured
   error, or if the error cannot be resolved from the contract alone, submit
   `aota_worker_outcome_submit(outcome='needs_input', reason='<specific
   error>')` and stop. Never fall through to a third blind retry.

This rule applies regardless of tool class or error category. A structured
error is a definitive rejection; it is never a signal to try adjacent
parameters or to bypass the tool.

## No Continuous Parameter Guessing

When the same tool returns two consecutive structured errors, the worker has
triggered the `repeated_schema_guess_detected` stop condition:

- Two consecutive structured errors from the same tool ID → the worker is
  guessing parameters without consulting the contract.
- No third blind retry is allowed. The next action MUST be loading the
  applicable contract/Skill and inspecting the exact error, NOT issuing
  another raw tool call.
- If the second error differs from the first, the worker has corrected one
  mistake but introduced another — this is still `repeated_schema_guess_detected`
  because the fix was not evidence-based.
- Enforcement: this rule applies at the Skill/SOUL/telemetry level. If the
  tool layer provides a shared wrapper that counts consecutive structured
  errors per tool, that wrapper may serve as a runtime guard, but the Skill
  rule is the canonical authority.

After `repeated_schema_guess_detected`, the worker must pause, reload
contracts, and retry at most once with explicit evidence of the correction.
If the evidence-backed retry fails, submit `needs_input`.

## Deployment and Runtime Guidance

- Profile runtime assembly is manifest-driven. The canonical assembly manifest
  (`deploy/profile-runtime-assembly.yaml`) declares active Skills per Profile.
- Profile-local plugin and Skill projections are required when the Hermes loader
  uses profile home (`AF SkillRegistry (static declarative) + work_plane/skill_bootstrap/<name>/plugins/`,
  `AF SkillRegistry (static declarative) + work_plane/skill_bootstrap/<name>/skills/`).
- Config declaration is not runtime availability evidence. A toolset declared
  in config may fail to register at runtime.
- SOUL declaration is not Skill-load evidence. A Skill referenced in SOUL.md may
  not be loaded if the file is missing or the cache is stale.
- Runtime verification requires loaded tool/Skill evidence or actual Profile
  Task execution. Hash parity is deploy evidence, not runtime evidence.
- Managed deploy and recreate requirements apply per the lifecycle inventory.
- Host/Codex construction vs Hermes runtime verification boundary: source PASS
  does not imply runtime PASS.
- Project Steward owns documentation continuity, not Skill source implementation.
- task-main must not manually fabricate deployment success. If a deployment
  receipt is missing or runtime evidence contradicts declared state, report
  the discrepancy.
- Runtime-generated files (logs, snapshots, receipts, backups) are not managed
  source.
- No generic `/aota-runtime` access; use bounded runtime tools.


---
*W0 provenance: migrated from legacy `aota-hermes-tools` (SHA c2e0c4d) to `aota_forge` canonical; `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`, `SKILL_IS_AUTHORITY=no`.*
