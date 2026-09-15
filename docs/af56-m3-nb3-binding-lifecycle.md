# AF #56 M3 - NB-3/NB-4 Binding Lifecycle (OpenCode Reference Host)

Documentation only, grounded in the AF #56 Plan body (sections 11, 21-23, 26), the M2 close development_notes (D16, D20, NB-3, NB-4), and the M3/W2 sources listed below. No new authority claims: the AF trusted runtime and the digest-verified binding envelope remain the only authority.

## 1. Instance directory = task/session-scoped mechanical namespace

- Plan section 11: "Each OpenCode Worker session must be bound to the AF-assigned trusted worktree. The model must not choose arbitrary directory authority." The binding runs through adapter mechanical directory binding, never model choice.
- <worktree_root>/.aota/opencode/instances/<instance_key> is a trusted task/session-scoped MECHANICAL NAMESPACE (aota_forge/adapters/opencode/task_main.py), never AF semantic authority.
- M2 empirical fact (NB-3/D20): the pinned host keeps one MCP child per directory instance for the instance lifetime.

## 2. The binding envelope carries the AF authorized worktree

- D16: the Worker session directory comes only from the existing governed Worker env resolver (grounded task.start metadata -> digest-verified pre-resolved envelope -> worktree_root).
- create_worker_envelope (aota_forge/runtime/trusted_runtime_binding.py) binds project, worktree, canonical task id, worktree_root, handoff and handoff digest; tamper fails closed on load.
- scripts/opencode_aota_mcp_server.py resolves, per instance, <instance_dir>/.aota/opencode/active_binding.json -> the envelope staged inside that instance's .aota, checks containment and worktree_root == pointer binding_root, then exports AOTA_PRE_RESOLVED_BINDING to the single production MCP entry.
- HOST_INSTANCE_DIRECTORY_EQUALS_AF_WORKTREE=no: the instance directory is not the AF authorized worktree; only the envelope worktree_root is.

## 3. NB-3: stale bindings are neutralized per task lifetime

- M2 NB-3 (M3 input) and D20: one MCP child per directory instance for the instance lifetime; same-worktree re-dispatch across task lifetimes keeps the old binding. M2 used one dedicated assigned worktree per Worker session (proven); same-worktree re-dispatch design belongs to M3.
- M3/W2 closure (option A): every task/session gets its own instance namespace; pointer + digest-verified envelope live inside <instance_dir>/.aota/..., so the MCP child can only resolve the binding staged for that exact instance; stale task A authority cannot authorize task B.
- Mis-staged/foreign/missing pointer material fails closed (MCP server does not start). STALE_BINDING_REUSED=no; OVERWRITE_POINTER_AND_HOPE=no; SECOND_MCP_SERVER=no.

## 4. Independent namespace per task lifetime

- task-main S0 -> task-main-<run>; Worker Task A -> worker-<task A>; Worker Task B on the same trusted worktree -> worker-<task B>.
- A new task never inherits a previous task's cached MCP child/binding; task-main authority is never visible to a Worker instance (and vice versa). No stale binding can authorize a new task.

## 5. NB-4: no synthetic project evidence in the reference runtime

- M2 NB-4 (M3 scope): the M2 reference MCP wrapper defaults the accepted synthetic project-evidence test seam for bounded test roots; real project onboarding evidence is M3 scope.
- M3 closure: the production/reference wrapper never provides the seam (SYNTHETIC_PROJECT_EVIDENCE_PROVIDED=False) and strips AOTA_ALLOW_SYNTHETIC_PROJECT_EVIDENCE from the child env; the seam stays bounded-test-only.
- Plan section 26 (Runtime configuration - bounded host selection): host/runtime selection stays explicit and bounded (aota_forge/runtime/config.py, composition/execution.py); section 23: prompt instructions are insufficient - isolation must be mechanically proven.

## Evidence index

Plan #56 body: sections 11, 21, 22, 23, 26. M2 close development_notes: D16, D20, NB-3, NB-4. Sources: aota_forge/adapters/opencode/task_main.py; aota_forge/composition/execution.py; aota_forge/runtime/trusted_runtime_binding.py; scripts/opencode_aota_mcp_server.py.
