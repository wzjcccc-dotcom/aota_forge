# #004 M3/W1/P4 frozen bounded repair contract

Diagnosis reconciliation: R18 / CAS 21, source digest
`cd9dcf3db11a1e974736c8ad4a0a497418e45fad80b1b22fcf67774fbde86d10`.

- Transition: unbound -> prepare Project delegate and evidence -> persist and
  exact-session readback -> install delegate -> project_bound. Failure restores
  prior durable evidence and runtime; an initially unbound Session stays unbound.
- v1 remains exclusively Plan-bound. Its JSON and canonical digest payload are
  unchanged. No missing Plan field is interpreted as Project-only.
- v2 explicitly discriminates `PROJECT_BOUND` / `PLAN_BOUND` by `binding_kind`.
  PROJECT_BOUND excludes `plan_binding`; PLAN_BOUND requires the existing typed
  PlanAuthorityBinding and observed source digest. New Plan binds retain v1.
- Persisted Project/session/root digest evidence is not authority. Recovery
  re-resolves current trusted Project Registry, Project fingerprint/identity,
  AuthorizedRootSet and applicable LocalGovernanceRootBinding. No persisted path
  or capability is promoted to authority; no Plan is guessed or selected.
- The existing projection and exact `origin_task_main_session_ref` remain the
  only recovery carrier/key. Bridge epochs/tokens are short-lived and absent
  from records. Other Sessions remain independent.
- Same-Project Plan promotion CAS-replaces Project evidence only after fresh
  trusted Plan composition/readback. Failure restores Project-only evidence.
  Generic context switching retains the existing guard.
- Completion gate: focused regression + isolated OpenCode 1.18.30 Plugin ->
  Bridge -> shared MCP proof, two lifecycle restarts, unchanged exact Session,
  Project recovery before Plan promotion, then Plan recovery.
- Local source checkpoint and cumulative m3-preparation-v5 candidate only;
  readiness requires new explicit v5 controlled reactivation approval.
