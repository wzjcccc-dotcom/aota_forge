# Maintainer notes — aota-result-hydration (outside normal runtime)

- Durable store layout (`worktree_root/.aota/durable_payloads/<digest>.bin`, atomic persist, retention = project/worktree lifecycle, no time GC, `clear_durable_payloads`), whole-object vs selective (4096 evidence/artifact, 64KiB tool_output), `HYDRATION_REPLAYS_SIDE_EFFECT=no` preserved.
- Progressive disclosure proof and descriptor parity derivations preserved for audits.
- `MAINTAINER_CONTENT_IN_NORMAL_LLM_CONTEXT=no`.
