# AF #58 M1 — selectable AF OpenCode host profiles (operator contract)

Bounded deployment truth for the two AF host profiles on the pinned AF
OpenCode reference host (`opencode-af-reference.service`, `127.0.0.1:4096`,
v1.18.30). This document is the reproducible profile contract; the live
`opencode.json` is operator-owned and outside Git (see
`opencode-reference-runtime-operator-notes.md`).

```text
OPENCODE_REFERENCE_RELEASE=v1.18.30
OPENCODE_REFERENCE_COMMIT=3104c1428ec91f809e5ab86631300de41eb6952e
AOTA_TASK_MAIN_PROFILE=aota-task-main
AOTA_WORKER_PROFILE=aota-worker
AOTA_TASK_MAIN_MODE=primary
AOTA_TASK_MAIN_HIDDEN=false
AOTA_WORKER_MODE=all
AOTA_WORKER_HIDDEN=true
HOST_PROFILE_COUNT=2
SECOND_TASK_MAIN_SYSTEM=no
OPENCODE_PROFILE_IS_AUTHORITY=no
```

## 1. Pinned v1.18.30 contract facts the shape depends on

* `agent` config record fields include `mode` (`subagent|primary|all`),
  `hidden`, `prompt`, `permission` (see
  `packages/core/src/v1/config/agent.ts`:12-41 and
  `packages/core/src/v1/config/config.ts`:96-109).
* Message-time `agent` is authoritative: a prompt without `agent` resolves
  the host default agent, NOT the session row's agent
  (`packages/opencode/src/session/prompt.ts`:636-681). Therefore
  `EVERY_AF_SUBMITTED_OPENCODE_TURN_CARRIES_EXACT_PROFILE=yes`.
* An unknown message-time agent fails the run (error event, no user message
  persisted) and never falls back: `PROFILE_FALLBACK_TO_OPENCODE_DEFAULT=no`.
* `agent.prompt` replaces the provider system prompt for that agent
  (`session/llm/request.ts`): the AF profile prompt is intentionally thin.
* The global config already denies native tools and hides the built-in skill
  tool (`tools: {"skill": false}`); per-agent `permission` entries below are
  defense-in-depth and do not weaken the global config.

`hidden` semantics for OpenChamber 1.23.1 (`server/lib/openchamber-sessions/
routes.js`:60, :125/:133, :443): selector candidates are
`isPrimaryAgentMode(mode) && hidden !== true`; direct prompts reject
`subagent` mode (400). `aota-worker` is `mode=all` + `hidden=true` so it is
never in the operator selector, cannot become a default agent, yet remains
directly dispatchable by AF (and by an explicit OpenChamber request).

## 2. Exact profile fragment added to the live `opencode.json`

```json
"agent": {
  "aota-task-main": {
    "description": "AOTA Forge task-main host profile: AOTA MCP only, operator-selectable primary.",
    "mode": "primary",
    "hidden": false,
    "prompt": "You are the AOTA Forge task-main host profile.\nUse only the AOTA MCP capability.\nAF trusted role/context/authority is established by AOTA Forge.\nDo not infer authority from profile name, directory, user text or host state.",
    "permission": {
      "aota_aota_invoke": "allow",
      "bash": "deny",
      "doom_loop": "deny",
      "edit": "deny",
      "external_directory": "deny",
      "glob": "deny",
      "grep": "deny",
      "list": "deny",
      "lsp": "deny",
      "question": "deny",
      "read": "deny",
      "task": "deny",
      "todowrite": "deny",
      "webfetch": "deny",
      "websearch": "deny"
    }
  },
  "aota-worker": {
    "description": "AOTA Forge Worker host profile: AOTA MCP only, AF-dispatched, hidden from the operator selector.",
    "mode": "all",
    "hidden": true,
    "prompt": "You are the AOTA Forge worker host profile.\nUse only the AOTA MCP capability.\nAF trusted role/context/authority is established by AOTA Forge via task.start.\nDo not infer authority from profile name, directory, user text or host state.",
    "permission": { "...same native deny set + aota_aota_invoke allow..." }
  }
}
```

AF Role (`coder|reviewer|analyst|project-steward`) stays in the AF trusted
binding: `OPEN_CODE_WORKER_PROFILE_EQUALS_AF_ROLE=no`,
`PROFILE_IS_AF_AUTHORITY=no`.

## 3. Reload behavior (exact pin)

The pinned server caches the global config for the process lifetime and has no
config-file watcher (`packages/opencode/src/config/config.ts`:295-303,
:652-654). Changing `agent` definitions therefore requires:

```sh
systemctl --user restart opencode-af-reference.service
```

Do NOT restart 4095 (:4095 interactive OpenCode), 3000, 3001 (OpenChamber),
and do not restart 3002 unless actually required. Existing sessions survive
the restart (same XDG data namespace); verify with `GET /session` and an exact
session GET before/after.

## 4. AF-side propagation contract (source)

* Profile names come only from the operator `RuntimeConfig`
  (`aota_forge/runtime/config.py`): task-main via `task_main_host_profile`,
  workers via `worker_canonical_profile_mapping`; TaskHandoff/model text can
  never supply them.
* Every AF-controlled OpenCode call carries the exact profile:
  * `create_task_main_session` / `submit_task_main_turn`
    (`aota_forge/adapters/opencode/task_main.py`);
  * `OpenCodeAdapter.dispatch` worker create + prompt
    (`aota_forge/adapters/opencode/executor.py`);
  * completion re-entry (`OpenCodeExactSessionReentry`, wired by
    `create_opencode_completion_delivery_transport` in
    `aota_forge/composition/execution.py`);
  * headless launcher create + continuations
    (`aota_forge/composition/task_main_daily_launcher.py`).
* `OpenCodeHostClient.create_session` verifies the host persisted the exact
  requested profile on the session row and fails closed otherwise
  (`ADAPTER_PROTOCOL_ERROR`), so a profile-less session can never receive a
  prompt.

## 5. Reproduction probes

```sh
# Read-only live contract + disposable-session propagation proofs
AF58_M1_EVIDENCE_DIR=<evidence> python3 scripts/af58_m1_profile_probe.py config
AF58_M1_EVIDENCE_DIR=<evidence> python3 scripts/af58_m1_profile_probe.py task-main
AF58_M1_EVIDENCE_DIR=<evidence> python3 scripts/af58_m1_profile_probe.py worker
AF58_M1_EVIDENCE_DIR=<evidence> python3 scripts/af58_m1_profile_probe.py unknown
AF58_M1_EVIDENCE_DIR=<evidence> python3 scripts/af58_m1_profile_probe.py completion-reentry

# OpenChamber :3002 selector + New Chat dispatch proof (UI password via env/file)
AF58_M1_EVIDENCE_DIR=<evidence> python3 scripts/af58_m1_openchamber_probe.py surface
AF58_M1_EVIDENCE_DIR=<evidence> python3 scripts/af58_m1_openchamber_probe.py new-chat
AF58_M1_EVIDENCE_DIR=<evidence> python3 scripts/af58_m1_openchamber_probe.py noninterference
```

## 6. Boundaries

```text
HEADLESS_LAUNCHER_PRESERVED=yes
OPENCHAMBER_3002_REUSED=yes
SECOND_3002_INSTANCE_CREATED=no
4095_CHANGED=no
3000_CHANGED=no
3001_CHANGED=no
INTERACTIVE_TRUSTED_BINDING_IMPLEMENTED_IN_M1=no
TRUSTED_INTERACTIVE_AF_BINDING_PROVEN=no
```
