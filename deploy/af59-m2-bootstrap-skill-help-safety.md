# AF #59 M2 — bootstrap / Skill routing / help / governance safety operator contract

Bounded deployment truth for the #59 M2 convergence (same Plan #59, same
host shape as M1). The #59 M1 operator contract stays authoritative for the
host topology (`deploy/af59-m1-thin-native-host.md`); this note records the
M2 deltas.

```text
ROLE_BOOTSTRAP_NORMAL_STARTUP=yes
ROLE_BOOTSTRAP_MECHANICAL_GATE=no
ROLE_BOOTSTRAP_IS_AUTHORITY=no
ROLE_BOOTSTRAP_BINDS_PLAN=no
ROLE_BOOTSTRAP_BINDS_SESSION=no
BASE_SKILL_EAGER_USABLE=yes
PROGRESSIVE_SKILL_ROUTING=yes
BOOTSTRAP_OPERATION_SET_EQUALS_OPERATIONS_LIST=yes
HELP_OPERATION=yes (read-only, derived from the canonical descriptors)
INVALID_PORTABLE_PLAN_BODY_WRITES_GITHUB=no
LIVE_MUTATION_PROBING_ALLOWED=no
```

## 1. task-main startup prompt (operator-owned, small)

The `aota-task-main` agent `prompt` in the live `opencode.json` and the
operator startup file (`~/.config/aota-forge/task-main-startup.md`) carry the
same small guidance. Required meaning (equivalent):

```text
你是 AF task-main。

正常 session 啟動時呼叫：
aota.invoke(operation="role.bootstrap", arguments={})

Bootstrap 提供你的 Role/Soul、可用的 base Skill 指引、progressive Skill refs 與
use_when、實際 AOTA operation surface，以及 runtime/context facts。
持續使用 base 指引工作。

只有在某個 progressive Skill 的 use_when 符合當下需求時，才開啟該 Skill。
不要把詳細 Skill 程序複製進 startup prompt。

Bootstrap 是 guidance / capability discovery，不是 authority、不是 Plan binding、
也不是 session binding。
```

Notes:

* No mandatory first call, no Plan activation protocol, no session activation
  protocol (`ROLE_BOOTSTRAP_MECHANICAL_GATE=no`). A safe read is never
  rejected because bootstrap was not first.
* The base Skill (routing table) and the progressive procedure Skills live in
  the AF checkout (`skills/`); `role.bootstrap`/`skill.open` deliver them.
* The detailed GitHub/Git governance mechanics live only in
  `aota-task-main-governance@1.0.0`.

## 2. Unbound task-main operation surface (one truth)

```text
role.bootstrap.AOTA_MCP.operations
  == unbound operations.list
  == canonical_task_main_operations()
  == the task-main role tool surface (visibility only; authority is server-side)
```

Exactly the ref-scoped operations + `role.bootstrap`, `skill.open`, `help`,
`result.hydrate`, `operations.list`, `host.status`, `runtime.status`. Legacy
`task_main.*` workflow operations are not on the thin task-main surface.

## 3. help

`help(operation="<canonical operation>")` is a read-only backup reader for
exact tool mechanics, derived from the canonical descriptors:

* exact lookup only (unknown -> typed `UNKNOWN_OPERATION`, no fuzzy/aliases);
* reports purpose, read/write kind, required/optional inputs, authority
  locator (`plan_ref`), mutation/CAS semantics, typed errors and the relevant
  progressive Skill ref;
* for `github.issue.update`: `body=WHOLE BODY REPLACEMENT`,
  `section_marker+section_content=BOUNDED_SECTION_UPSERT`,
  `state=open|closed`, `expected_updated_at=CAS_PRECONDITION`,
  `preferred_plan_state_update=section_marker+section_content`.

It is not a second schema authority and never a generic shell/help surface.

## 4. Governance mutation safety

* A whole-body replacement of a recognized Portable Plan Issue is
  pre-validated with the canonical `normalize_portable_plan` contract and a
  Plan-identity preservation check; an invalid/incomplete candidate fails
  `INVALID_INPUT` (`PLAN_BODY_INVALID`) BEFORE any GitHub mutation call.
* Bounded Plan-state changes prefer `section_marker` + `section_content`;
  closure uses `state=closed`.
* Never probe mutation schemas against live authoritative Plan data; read the
  Skill, then `help(operation=...)`, then proceed or stop.

## 5. Deploy / restart

```sh
# source -> reference runtime MCP copy (only if the wrapper changed)
cp scripts/opencode_aota_mcp_server.py \
  /home/latios/.local/share/aota-forge/opencode-reference/runtime/mcp/
systemctl --user restart opencode-af-reference.service

# operator startup prompt (used by the Hermes launcher path): materialize the
# seed PROMPT BODY only (the SEED_ONLY header is not part of the runtime prompt)
python3 - <<'PY'
from pathlib import Path
seed = Path("prompts/task-main-startup.default.md").read_text(encoding="utf-8")
body = seed.split("\n---\n", 1)[1].strip() + "\n"
Path.home().joinpath(".config/aota-forge/task-main-startup.md").write_text(body, encoding="utf-8")
PY
```

The live `opencode.json` `aota-task-main` agent prompt is updated in place
(operator-owned runtime config). Do NOT restart/modify :4095, :3000, :3001.
