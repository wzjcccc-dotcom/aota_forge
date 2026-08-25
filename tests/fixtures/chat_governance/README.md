# Chat Governance Fixtures (P3 W3 + P3 Guard Hardening)

- static/: authority-map and regression fixtures
- materialization/: body+comment snapshots
Inline fixtures are exercised via scripts/test_chat_governance_guards.py.
This directory holds a manifest and example snapshots.

## P3 Guard Hardening coverage (deterministic regression)

- prefixed Milestone operational state (`M<n>_STATUS`, `M<n>_ACCEPTANCE`, `M<n>_FINAL_ACCEPTANCE`, `M<n>_COMPLETION_ACCEPTANCE`, `M<n>_CLOSURE_MATERIALIZED`)
- Milestone known-good / integration result (`M<n>_KNOWN_GOOD_*`, `M<n>_INTEGRATION_COMMIT/TREE`, `M<n>_KNOWN_GOOD_CHECKPOINT_CONFIRMED`)
- Work Item operational result (`M<n>_<A|Wn>_*` with operational suffix; excludes normative `M3_E_REVISION_CAS`-style criteria)
- Milestone DAG status decoration (`M<n> = ... [completed|planned|in-progress]`)
- current Workstream identity (`WORKSTREAM`, `WORKSTREAM_NAME`, `WORKSTREAM_ID`, `PARENT_MILESTONE` vs `LEGACY_PARENT_*` allowed)
- normative PASS false-positive protection (`MEMORY_ARCHIVE=PASS`, `CONFLICT_DIAGNOSTIC_READ=PASS`, `LIVE_MEMORY_RUNTIME_VERIFICATION=PASS`, `KNOWN_GOOD_GIT_CHECKPOINT=PASS`, `FINAL_INDEPENDENT_CLOSURE_REVIEW=PASS`, `LEGACY_PARENT_WORKSTREAM`, `planned behavior`, `completed=false`, retired `[completed]` notation)

See `fixture_manifest.json` for counts (positive/negative/false-positive).
