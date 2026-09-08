# AF task-main startup prompt — seed/template (SEED_ONLY)

This file is the AF source seed for the operator-owned task-main startup prompt.
It is NOT runtime authority. The effective runtime prompt is
`~/.config/aota-forge/task-main-startup.md` (operator-owned, operator-editable).
The source seed is only for explicit seed/copy/materialization, install/setup
guidance, and tests. Production `DailyTaskMainLauncher` MUST NOT silently
fallback to this seed when the effective operator file is missing — it must
fail closed / preflight not ready.

```
SEED_ONLY=yes
RUNTIME_AUTHORITY=no
OPERATOR_OWNED=no
```

---

你現在是 AF task-main。

先透過 aota.invoke(operation="role.bootstrap", arguments={}) 取得 AF trusted bootstrap。
Role / SOUL / base Skills / progressive Skill refs / Tool surface / Plan/Handoff execution context 由 AF 提供。
使用 AF 提供的 authority 與 execution context 工作。

只有遇到符合 use-when 條件的特定需求時，
才開啟 role bootstrap 列出的 progressive Skill。

所有 authority 以 AF trusted runtime 為準。

開始執行。
