# Plan 004 M3 Project-bound Plan Promotion Repair Candidate

```text
PREPARATION_VERSION=m3-preparation-v4
CANDIDATE_KIND=bounded_overlay
BASE_CANDIDATE=m3-preparation-v3
PROJECT_ID=aota_forge
PLAN_ID=plan_004_trusted_opencode_session_bridge_context_isolation
MILESTONE=M3
BASE_FRONTIER=aa40c5f37ec549276dd302f1a882e404a7ce993a
M2_ACCEPTED_FRONTIER=052fefb297b412052d7c94ffb8f4ab6062259c33
SCOPE=PROJECT_BOUND_TO_SAME_PROJECT_PLAN_PROMOTION
SOURCE_REPAIR_FRONTIER=aa40c5f37ec549276dd302f1a882e404a7ce993a
PRODUCTION_INSTALLATION=no
PRODUCTION_CONFIGURATION_CHANGE=no
PRODUCTION_SERVICE_RESTART=no
PRODUCTION_REACTIVATION_PERFORMED=no
REMOTE_PUSH=no
```

This candidate preserves the v2 `216/GROUP` repair and v3 headed-host
`AOTA_BRIDGE_SOCKET` propagation. The source frontier adds only the narrow
same-Project Plan promotion seam: a trusted Project-bound Session may resolve,
validate, persist and activate one Plan belonging to that Project. Generic
rebind and context switch behavior remain fail-closed.

The isolated real-path proof used OpenCode `1.18.30`, the candidate Plugin,
the trusted bridge and the AOTA MCP child. It is evidence for this candidate
only and does not install a unit, switch the 4096 configuration, restart a
production service, provision a secret, or change the accepted M2 source.
