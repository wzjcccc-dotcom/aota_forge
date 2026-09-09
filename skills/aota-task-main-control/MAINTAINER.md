# Maintainer notes — aota-task-main-control (outside normal runtime)

- Full `next_action` vocabulary table (15 dispositions with Agent behaviors), internal coordinator state machine, receipt schemas, reconciliation algorithms, durable store layout are maintainer/operator knowledge.
- Worker startup prompt exact text (`worker_startup_prompt.md`, `WORKER_STARTUP_PROMPT_SOURCE=AF`) preserved in composition source; Skill runtime carries summary only.
- Progressive disclosure proof, `SkillSearchIndex`/`AllowedSkillUniverse` mechanics, descriptor parity derivations from `operations.yaml` preserved for audits.
- `MAINTAINER_CONTENT_IN_NORMAL_LLM_CONTEXT=no`.
