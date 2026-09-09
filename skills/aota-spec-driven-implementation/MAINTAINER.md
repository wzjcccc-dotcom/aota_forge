# Maintainer notes — aota-spec-driven-implementation (outside normal runtime)

- W0 provenance: legacy coder profile (`spec_kind=implementation`, `aota_project_file_read/write/patch`, `aota_project_command_run`, `aota_coder_report_submit`, `aota_worker_outcome_submit`, `aota_active_task_artifact_open` with SPEC/SCOPE/BINDING identity) preserved for archaeology.
- Role contract history (`required_changes`, `behavioral_invariants`, `change_budget`, `forbidden_operations`, `checkpoint_conditions`, `compatibility_requirements`), P11-K/N/L enforcement stages, mutation audit boundary (`AUDIT_GAP`, `audit_event_id`), scope telemetry, plugin-tool-development routing are maintainer knowledge.
- Forbidden command catalog (`rm -rf`, `git reset --hard`, etc.), validation levels (Implemented/Statically checked/Smoke/Runtime/Live) history preserved here.
- `MAINTAINER_CONTENT_IN_NORMAL_LLM_CONTEXT=no`.
