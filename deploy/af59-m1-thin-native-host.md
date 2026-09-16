# AF #59 M1 — thin native host operator contract

Bounded deployment truth for the simplified AF reference host after #59 M1.

```text
OPENCODE_REFERENCE_RELEASE=v1.18.30
OPENCODE_REFERENCE_SERVICE=opencode-af-reference.service (127.0.0.1:4096)
OPENCHAMBER_AF_INTERACTIVE=100.123.10.71:3002 (openchamber-af-reference.service)
INTERACTIVE_INGRESS_SEAM=removed (#58 machinery deleted; not installed)
MCP_PUBLIC_TOOL_COUNT=1 (aota.invoke)
AOTA_MCP_ALWAYS_AVAILABLE=yes
MCP_AVAILABILITY_IS_NOT_AUTHORITY=yes
SESSION_BINDING_REQUIRED=no
FIRST_MESSAGE_PLAN_GRAMMAR_REQUIRED=no
ONE_CHAT_ONE_PLAN=no
CUSTOM_AF_HISTORY_PROJECTION=no
```

## 1. Final host shape

```text
OpenChamber :3002 (OPENCODE_HOST=http://127.0.0.1:4096, isolated data dir)
      ↓
OpenCode :4096 (pinned v1.18.30, isolated XDG)
      ├── native session lifecycle / history / sidebar / directory / providers
      ├── aota-task-main (primary, visible)
      ├── aota-worker (mode=all, hidden, AF-dispatched)
      ├── native tools denied; built-in skill disabled
      └── mcp.aota -> ONE AOTA MCP (always available)
              ↓
          aota.invoke
              ↓
          AOTA Forge Core
              ↓
      operation-time ref-scoped authority check
```

No AF reserve/bind interception, no directory rewrite, no AF history
projection, no preparation store. New New-Chat sessions are ordinary OpenCode
sessions in the workspace the operator selected.

## 2. task-main interactive startup prompt (operator-owned)

The `aota-task-main` agent `prompt` in the live `opencode.json` is the thin
interactive startup guidance. Required content (equivalent):

```text
你是 AOTA Forge task-main。
使用 AOTA MCP 處理 AF 能力。
你可以正常與使用者討論、分析、檢查、重新規劃 Plan；
不要因為進入 task-main profile 就自動開始施工。
需要執行實際工作時，使用相關 Plan / Handoff / Task ref。
AOTA Forge Control Plane 會在 operation boundary
重新驗證 authority、project、scope、approval 與 worktree。
工作拆解、review 時機、repair 策略與語意判斷由你負責。
```

Notes:

* No mandatory first call (`ROLE_BOOTSTRAP_FIRST_MECHANICAL_GATE=no`).
* `role.bootstrap` remains available as an optional useful operation.
* Plan reading: `github.issue.read` / `github.issue.comments.read` with the
  canonical `plan_ref` (`owner/repo#number`).
* `aota-worker` prompt is unchanged (AF-dispatched; authority via task.start).

## 3. AOTA MCP unbound mode (ordinary workspace)

The deployed wrapper (`scripts/opencode_aota_mcp_server.py`) starts the single
canonical MCP entry with `AOTA_GLOBAL_MCP=1` when the directory has no
`.aota/opencode/active_binding.json`:

* `aota.invoke` is connected immediately (health + `/mcp` show
  `aota_aota_invoke`).
* Reads/discussion work without any session/Plan binding
  (`github.issue.read`, `github.issue.comments.read`, `workspace.read/search`,
  `role.bootstrap`, `result.hydrate`).
* Play-by-ref side effects resolve authority at the operation boundary:
  * `plan_ref` -> live Plan read -> milestone approval (current server-side
    fact) -> canonical project binding -> thin task-main authority.
  * `task.start` / `handoff.write` -> `plan_ref` + `handoff_ref`; wrong
    project/worktree/task refs fail closed.
  * Worker-side effects keep the trusted binding envelope discipline
    (worker directory = actual authorized worktree; cwd is not authority).
* Environment required by the deployed command:
  `AOTA_FORGE_REPO_ROOT`, `AOTA_FORGE_REGISTRY`,
  `AOTA_FORGE_RUNTIME_CONFIG` (operator-owned).

## 4. Deploy / restart

```sh
# source -> reference runtime MCP copy + operator config updates
cp scripts/opencode_aota_mcp_server.py \
  /home/latios/.local/share/aota-forge/opencode-reference/runtime/mcp/
systemctl --user restart opencode-af-reference.service

# :3002 simplification (remove #58 env seam + uninstall the JS seam)
systemctl --user restart openchamber-af-reference.service
```

Do NOT restart/modify :4095, :3000, :3001.

## 5. Minimum live smoke

```sh
curl -s http://127.0.0.1:4096/global/health
# /mcp in OpenChamber must show aota connected with one tool aota_aota_invoke
# ordinary workspace chat: no Plan grammar, no 409 bind response
# unapproved Plan discussion works; attempt at construction is denied at the
# operation boundary (MILESTONE_APPROVAL_REQUIRED)
# restart only :3002; native OpenChamber sidebar shows and resumes the same
# OpenCode session id (no AF projection)
```

Old #58 sessions are left in place: `OLD_58_SESSIONS_MIGRATED=no`,
`OLD_58_SESSIONS_DELETED=no`.
