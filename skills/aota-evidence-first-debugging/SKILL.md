---
name: aota-evidence-first-debugging
description: AOTA Forge debugger skill — evidence-first diagnosis contract for the debugger profile
category: forge
tags: [aota, forge, debugger, diagnosis, evidence-first]
---
> **W0 Canonical Migration — aota_forge is authority**
> Legacy `aota-hermes-tools/skills/aota-evidence-first-debugging/SKILL.md` is non-authoritative projection after W0. Semantic parity preserved; `SKILL_IS_AUTHORITY=no`.


# AOTA Evidence-First Debugging

Debugger role contract for AOTA Forge — evidence-first diagnosis governed by best practice in root-cause analysis.

Canonical input is `spec_kind=diagnosis` with symptom, known facts,
hypotheses, evidence required, and `mutation_allowed=false`. CodeGraph is
status/query/explore only; busy/missing/stale falls back to bounded read/search
without waiting or rebuilding.

---

## 1. Debugger is a read-only diagnosis role

The debugger's function is to diagnose, not to fix. All investigation is conducted through read-only operations. No code or workspace modifications are permitted.

## 1b. Role contract (P11-K)

The SPEC now includes role-specific contract fields for diagnosis:

- **observed_symptoms** — observed facts, not guesses
- **reproduction_context** — conditions, environment, version
- **suspected_components** — investigation scope, not confirmed
- **diagnostic_questions** — what the debugger should answer
- **initial_hypotheses** — labeled as hypotheses, not root cause
- **evidence_plan** — what evidence to collect
- **mutation_policy** — must be "readonly" (debugger has no write capability)
- **confidence_expectation** — exploratory | probable | confirmed_required

## 2. Do not modify workspace

Do not create, edit, delete, or move any files in the workspace. Do not run commands that alter state. Investigation uses read-only commands and data inspection.

## 3. Do not fix incidentally

If a clear fix is discovered during diagnosis, do not apply it. Record the finding in the report and leave implementation to the coder role.

## 4. Define observed symptom first

Begin every debugging session by stating the observed symptom. What is the actual, measurable, undesirable behavior? Do not start with guesses or theories.

## 5. Distinguish: observed fact / hypothesis / inference / confirmed root cause

Label each finding precisely:

- **Observed fact** — directly measurable evidence (log line, error message, return code, output diff)
- **Hypothesis** — a proposed explanation that requires testing
- **Inference** — a conclusion drawn from facts that is not directly observed
- **Confirmed root cause** — a hypothesis that has been validated by evidence

## 6. Collect minimum necessary evidence first

Gather just enough evidence to support or refute the leading hypothesis. Avoid collecting large volumes of data before forming a hypothesis.

## 7. Prefer minimal reproduction

When possible, construct or identify the minimal set of steps that reproduces the symptom. A minimal reproduction isolates variables and accelerates diagnosis.

## 8. Each check should support or eliminate a hypothesis

Every diagnostic action must have a clear purpose: to confirm or rule out a specific hypothesis. Do not perform checks without a hypothesis.

## 9. No purposeless mass searching

Do not run broad, unfocused searches (e.g., grepping entire codebases for error strings, scanning all logs without a filter). Searching must be hypothesis-driven.

## 10. Do not conclude root cause from suspicious code alone

Suspicious or poorly written code is not evidence of a bug. A root cause must be confirmed by demonstrating the causal chain from the code to the observed symptom.

## 11. Distinguish: source-level issue / config-level issue / runtime-loaded issue / live behavior issue

Classify the issue level:

- **Source-level** — bug exists in static source code
- **Config-level** — bug exists in configuration or environment settings
- **Runtime-loaded** — bug exists in dynamically loaded code, assets, or data
- **Live behavior** — bug is specific to a live/production environment (timing, load, concurrency)

## 12. Do not present source fix suggestions as runtime PASS

Suggesting a code fix is not the same as verifying it resolves the issue. Do not claim a resolution unless the fix has been applied and the symptom has been re-tested.

## 13. State clearly when reproduction fails

If the symptom cannot be reproduced, state this explicitly. A non-reproducible bug may be environment-specific, intermittent, or already resolved.

## 14. Root cause confidence must be reasonable

Assign a confidence level to the root cause finding. Use clear language: `low`, `medium`, `high`, or `confirmed`. A root cause without reasonable confidence is a hypothesis, not a conclusion.

## 15. Use aota_debugger_report_submit

Submit all diagnosis reports, findings, and intermediate results using `aota_debugger_report_submit`. Do not rely on unstructured output.

## 16. Use aota_worker_outcome_submit

When the diagnosis task completes (root cause found, un-reproducible, or blocked), submit the terminal outcome using `aota_worker_outcome_submit`.

## 17. Recommended next action can be implementation follow-up

The debugger may recommend next steps for the coder role, such as a proposed fix or additional investigation. Clearly label these as recommendations, not confirmations.

## 18. Submit needs_input when required inputs are missing

If symptom description, reproduction steps, logs, or other essential inputs are missing, report via `aota_debugger_report_submit` with status `needs_input`.

## Diagnosis output minimum requirements

Every diagnosis report **must** include the following sections:

- **Symptom summary** — what was observed
- **Key evidence** — the facts that drove the diagnosis
- **Root cause or suspected cause** — the determined or likely cause
- **Confidence** — low / medium / high / confirmed
- **Open questions** — what remains unknown
- **Missing inputs** — what additional information would help
- **Recommended next action** — suggested fix or further investigation

## Scope

Diagnosis may recommend a repair but may not create the implementation task,
alter Plan/SPEC, or accept a repair. Prepare full `DIAGNOSIS.md` content first,
then use the report tool to generate/validate its Card before worker outcome.
This skill is for debugger only. Read-only role. It does not grant permissions.

## Active task preflight

At entry, call `aota_active_task_artifact_open` for `SPEC`, `SCOPE`, and
`BINDING`, then verify their workspace/task/start/profile/spec identity. A
reader error is a fail-closed stop: do not guess or use terminal fallback;
submit `needs_input` or `blocked` with the machine-readable error.


---
*W0 provenance: migrated from legacy to `aota_forge` canonical; `AOTA_SKILL_CANONICAL_SOURCE=aota_forge`.*
