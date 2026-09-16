# AF #58 M3 — interactive task-main path: production readiness

Operator contract for `openchamber-af-reference.service` (:3002, backend :4096),
composing the accepted M1 host-profile contract and the accepted M2 interactive
ingress. No new subsystem is introduced.

## 1. Operator procedure (New Chat -> aota-task-main -> Plan ref -> gate)

1. New Chat on :3002 reserves a unique instance namespace
   (`AF_INTERACTIVE_WORKSPACE_ROOT`); the session row carries
   `af_interactive_preparation` + `af_interactive_schema`.
2. First Send must use profile `aota-task-main` and exactly one canonical
   `owner/repo#number` Plan reference; otherwise the request is refused (409)
   and nothing reaches the model (PLAN_REF_MISSING / PLAN_REF_AMBIGUOUS /
   PLAN_UNREADABLE / PROFILE_MISMATCH / STALE_PREPARATION).
3. On success the exact session owns the digest-bound task-main binding in its
   own instance namespace; the first tool call is `role.bootstrap`;
   BINDING_TAMPERED / MATERIALIZATION_FAILED fail closed.
4. One chat = one Plan; a same-Plan retry or plain continuation is idempotent,
   another Plan needs a New Chat (SESSION_ALREADY_BOUND_DIFFERENT_PLAN /
   CROSS_SESSION_BIND).
5. While the bound Plan's current milestone user approval is not satisfied,
   construction/mutation operations are refused pre-dispatch with
   `MILESTONE_APPROVAL_REQUIRED` (task.start, workspace.write,
   restricted_shell.run, git.*, github.issue.*); `role.bootstrap` exposes
   APPROVAL_GATE (`MECHANICAL_GATE_ENFORCED=yes`) and a stop instruction.
   Headless bindings carry no Plan state and are unchanged.

## 2. Dispatch / completion contract

* `task.start` (task-main only) resolves the Worker host profile exactly
  `aota-worker` from the operator RuntimeConfig
  (`worker_canonical_profile_mapping`) and carries it on the worker session
  create + every prompt; model / TaskHandoff text never supplies it; a host
  row without the exact profile fails closed (ADAPTER_PROTOCOL_ERROR, no
  prompt).
* `task.return` is Worker-only; a valid return writes one bounded durable
  receipt; process exit is never semantic success.
* Completion re-entry targets the exact origin session (no create-on-miss,
  SESSION_NOT_FOUND); HTTP 204 is accepted, not ACK (ACK_NOT_OBSERVED when no
  identity-bound reconciliation text appears).
* ACK eligibility requires a stored CARD-first reconciliation receipt; an ACK
  string alone grants nothing.

## 3. Services / configuration

* `opencode-af-reference.service` 127.0.0.1:4096 (pin v1.18.30);
  `openchamber-af-reference.service` 100.123.10.71:3002 (backend :4096).
* Env: `AF_INTERACTIVE_INGRESS_ENABLED=true`,
  `AF_INTERACTIVE_PROFILE=aota-task-main`,
  `AF_INTERACTIVE_WORKSPACE_ROOT=/home/latios/workspace/.aota/interactive`,
  `AF_INTERACTIVE_RUNTIME_CONFIG=/home/latios/.config/aota-forge/runtime-opencode-reference.json`,
  `AF_INTERACTIVE_REGISTRY=/home/latios/.config/aota-forge/workspace-registry.json`.
* Profiles `aota-task-main` (primary) / `aota-worker` (mode=all, hidden) are
  operator-owned in `opencode.json`; a prompt/profile change needs
  `systemctl --user restart opencode-af-reference.service` (do not restart
  :4095 / :3000 / :3001).

## 4. Verification checklist

```sh
python -m pytest -q tests/test_af58_m3_operator_workflow.py
python -m pytest -q tests/test_af58_m1_selectable_profiles.py tests/test_af58_m2_interactive_ingress.py
python3 scripts/af58_m2_interactive_probe.py
python3 scripts/af58_m2_headless_regression.py
```

Observed offline in this checkout: 5 passed; M1+M2 suites 56 passed;
syntax/import validity via pytest collection.

## 5. Boundaries

No second :3002; :4095 / :3000 / :3001 unchanged; no new authority store,
binding ontology or Plan parser; profiles, Plan refs and approval truth are
never model-supplied; task-main cannot cross the user approval gate; the
headless launcher is preserved.
