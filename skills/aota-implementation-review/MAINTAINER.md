# Maintainer notes — aota-implementation-review (outside normal runtime)

- W0 provenance: migrated from legacy `aota-hermes-tools`; `spec_kind=review` payload fields (`artifacts_under_review`, `review_dimensions`, `acceptance_mapping_required`, `inconclusive_conditions`, `independence_requirements`), SPEC revision binding, stale review handling preserved for audits.
- Legacy tool names (`aota_reviewer_report_submit`, `aota_worker_outcome_submit`, `aota_active_task_artifact_open`, CodeGraph status/query/explore fallback, Denied Audit Verification `audit-ledger.jsonl`) are pre-AF seams; current runtime uses `aota.invoke` + `WorkerResultCard` + `CommonResultEnvelope`.
- Test implementation notes, parity proofs, migration history excluded from normal runtime.
- `MAINTAINER_CONTENT_IN_NORMAL_LLM_CONTEXT=no`.
