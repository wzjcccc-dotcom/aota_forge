# Plan #004 M1 - Trusted OpenCode Session Bridge & Context Isolation

Status: frozen M1 contract, not a production bridge.

```text
PROJECT_ID=aota_forge
PLAN_ID=plan_004_trusted_opencode_session_bridge_context_isolation
MILESTONE=M1
PINNED_OPENCODE_VERSION=1.18.30
PINNED_OPENCODE_COMMIT=3104c1428ec91f809e5ab86631300de41eb6952e
UPSTREAM_MODIFICATION_REQUIRED=no
MCP_PUBLIC_TOOL_COUNT=1
MCP_PUBLIC_TOOL=aota.invoke
```

## Evidence Boundary

This document separates three evidence classes:

1. `SOURCE`: independently inspected pinned OpenCode and AF source.
2. `USER_POC`: the PoC values supplied in the Plan. They are not reproduced here and are not production security acceptance.
3. `CONTRACT_PROOF`: the bounded standard-library proof in `tests/test_plan004_m1_contract.py`. It proves the token contract model only; it does not prove a real OpenCode process, MCP child, or production routing path.

No M1 evidence authorizes M2 construction or M3 activation by itself.

## W1 - Invocation Identity Reconnaissance

### Findings

```text
TRUSTED_SESSION_SOURCE=OpenCode Plugin hook input.sessionID from tool.execute.before
TRUSTED_CALL_SOURCE=OpenCode Plugin hook input.callID, sourced from Tool.Context callID/options.toolCallId
ACTUAL_MCP_REQUEST_SHAPE=JSON-RPC tools/call with name, arguments, numeric client-local id, and optional _meta.progressToken
PLUGIN_TO_MCP_CORRELATION_SEAM=no native sessionID/callID carrier; use a trusted per-call carrier injected after the hook
SHARED_CHILD_LIFECYCLE=one MCP Client/child set per OpenCode InstanceState.directory; finalizer closes clients and descendants
UPSTREAM_MODIFICATION_REQUIRED=no; only the trusted Plugin and AF transport seam are in scope
POC_EVIDENCE_CLASSIFICATION=USER_POC for routing/isolation results; SOURCE for hook and transport boundaries
```

### Hook timing and identity

The pinned Plugin API defines `tool.execute.before` with:

```text
input: { tool, sessionID, callID }
output: { args }
```

The normal OpenCode tool wrapper constructs `Tool.Context` from the current
Session and AI SDK `options.toolCallId`, triggers the hook, then executes the
tool. For MCP tools, the same wrapper triggers the hook before calling the
converted MCP tool. Therefore:

- `sessionID` is a trusted OpenCode runtime value at the trusted Plugin seam,
  not an LLM argument.
- `callID` is an opaque OpenCode tool-call correlation value at that seam. It is
  useful only when MAC-bound to the invocation; it is not authentication,
  authority, or a JSON-RPC request id.
- The hook is before the MCP `execute(args, opts)` call. The hook output's
  `args` object is the only no-upstream-patch seam available in the inspected
  source for a trusted Plugin to attach an invocation carrier.
- Static source inspection does not yet prove that a deployed Plugin mutation
  survives every adapter/plugin ordering combination. M2 must prove that
  property through the real MCP child.

Source references:

- `packages/plugin/src/index.ts:266-269` defines the hook input/output.
- `packages/opencode/src/session/tools.ts:59-64, 102-125` builds Context and
  wraps ordinary tools with the hook.
- `packages/opencode/src/session/tools.ts:390-423` wraps each MCP tool with
  the same before/after hook around the MCP execution.
- `packages/opencode/src/mcp/catalog.ts:42-81` converts an MCP tool to
  `client.callTool({ name, arguments }, ..., options)`.

### Actual MCP request

OpenCode pins `@modelcontextprotocol/sdk` `1.29.0` in
`packages/opencode/package.json:83` and `bun.lock:597,1065,1831`.
The SDK request implementation adds the JSON-RPC envelope and, because
OpenCode always supplies `onprogress: () => {}` in the MCP catalog, adds the
progress token:

```json
{
  "jsonrpc": "2.0",
  "id": 17,
  "method": "tools/call",
  "params": {
    "name": "aota.invoke",
    "arguments": {
      "operation": "workspace.search",
      "arguments": {"query": "..."}
    },
    "_meta": {"progressToken": 17}
  }
}
```

The exact numeric value is illustrative. In SDK 1.29.0 it is the
`Protocol` instance's incrementing request counter, copied into
`_meta.progressToken` when `onprogress` is present. It is not a Session ID,
not the OpenCode Plugin `callID`, not a child-global identity, and not a
cryptographic proof. Creating a new OpenCode `Client`/child resets the
counter; a transport reconnect on the same `Protocol` instance must not be
assumed to reset it. The progress token is therefore usable for
timeout/progress routing only; it is not a trusted per-invocation identity
carrier.

The SDK request shape was checked against the exact pinned package source at:
`https://unpkg.com/@modelcontextprotocol/sdk@1.29.0/dist/esm/shared/protocol.js`
and the request schema at:
`https://unpkg.com/@modelcontextprotocol/sdk@1.29.0/dist/esm/types.js`.

The OpenCode MCP tool key exposed to the model is derived by
`McpCatalog.toolName(clientName, name)` (`packages/opencode/src/mcp/catalog.ts:117-119`),
so the configured `aota` server's model-visible key is `aota_aota_invoke`.
The AF public MCP surface remains `aota.invoke`; the OpenCode key is only a
transport/tool-wrapper selector and is not authority.

### Child lifecycle and state classification

OpenCode creates the MCP client with the current `InstanceState.directory` and
uses that directory for local child `cwd` and MCP roots
(`packages/opencode/src/mcp/index.ts:75-80, 340-369`). The MCP service stores
`clients`, definitions, status and instructions in one directory-scoped
`InstanceState` (`packages/opencode/src/mcp/index.ts:492-558`). The finalizer
closes clients and terminates discovered descendants
(`packages/opencode/src/mcp/index.ts:531-550`). A close marks the client
failed; a new directory instance or explicit reconnect creates a new child.

The shared child may safely own process/directory-global state:

- MCP client connection, child process handle, tool definitions and status.
- Immutable canonical operation descriptors and transport configuration.
- One bridge secret epoch and one atomic nonce-consumption mechanism for that
  child instance.

The shared child must never use process-global mutable state for:

- current Session, current call, current bound delegate or current Plan;
- OpenCode tool arguments, token, authorization roots or governance context;
- in-flight request identity or a latest/FIFO token queue.

Every such value is invocation-local or Session-local in the M2 router.

## W2 - Frozen Trusted Token and Correlation Contract

### Trust chain

```text
trusted OpenCode Plugin hook
    | real sessionID + real callID + exact logical args
    v
Trusted Bridge issuer
    | one-time HMAC credential in the same MCP tool-call carrier
    v
AOTA MCP verification ingress
    | MAC, epoch, time, request digest, identity and atomic nonce check
    v
Verified Invocation Identity
    | session_id, call_id, child_instance, tool, args_digest
    v
Session-scoped AF routing
```

The token proves an invocation identity. It does not grant Project, Plan,
root, Git, worker, or user-gate authority. Those remain resolved by existing
AF authority owners at the operation boundary.

### Token schema

The frozen M2 token format is:

```text
aota-bridge-v1.<base64url(canonical-json(payload))>.<base64url(mac)>
```

The payload fields are exactly:

```text
v              = 1
iss            = aota.trusted-opencode-plugin
aud            = aota.mcp.verify
bridge_id      = random child/bridge epoch identifier
session_id     = exact Plugin hook input.sessionID
call_id        = exact Plugin hook input.callID
tool           = aota_aota_invoke
args_sha256    = SHA-256(canonical logical {operation, arguments})
nonce          = 128-bit CSPRNG value, unique within the bridge epoch
iat            = issued-at Unix seconds
exp            = expiry Unix seconds
```

The MAC is `HMAC-SHA-256(secret, "aota-bridge-v1." + payload_segment)`.
`args_sha256` uses the AF canonical JSON contract, not an incidental
language-default serializer: JSON-native values only, sorted object keys,
compact separators, raw UTF-8 (`ensure_ascii=false`), `allow_nan=false`, and
the same rejection of non-finite numbers and unsupported values as
`aota_forge/core/contracts/canonical.py:11-39`. The bridge issuer and AF
verifier use this one contract (or a frozen cross-language vector set); the
Plugin must not independently invent a second numeric/string encoding. The
token has a maximum bounded size of 4096 bytes, a fixed maximum lifetime of 10
seconds, and a maximum accepted clock skew of 2 seconds. `exp <= iat + 10` is
enforced.

The logical argument digest is computed before adding the carrier and after
removing the carrier at verification. It binds the token to the exact
`operation` and operation arguments seen by AF, not merely to a Session.

### Carrier and verification order

The existing public MCP tool stays unchanged. The trusted Plugin adds a
reserved transport field inside the existing `aota.invoke.arguments` object:

```json
{
  "operation": "workspace.search",
  "arguments": {
    "query": "needle",
    "__aota_trusted_invocation": {
      "schema": "aota-bridge-v1",
      "token": "aota-bridge-v1...."
    }
  }
}
```

This is transport metadata, not a logical operation input. The AF ingress must
extract and remove it before canonical operation validation. A missing,
duplicated, malformed or operation-visible carrier is a failure. A model may
emit a field with that name, but it cannot create a valid MAC; the trusted
Plugin overwrites the carrier for each call and the verifier never treats
model-supplied Session or token values as identity.

Verification is ordered as follows:

1. Require exactly one bounded carrier on the existing `aota.invoke` call.
2. Decode the bounded token and validate schema, issuer, audience, bridge
   epoch, field lengths and `iat/exp`.
3. Verify HMAC with constant-time comparison using the current secret epoch.
4. Remove the carrier, canonicalize the actual logical request and compare
   `args_sha256`.
5. Atomically consume `(bridge_id, nonce)` while checking the expected
   `session_id`, `call_id` and tool. There is no FIFO, latest-token, timing
   window, PID-only or arrival-order matching.
6. Return an ephemeral `VerifiedInvocationIdentity` for this one dispatch.
   Never store it as Project/Plan authority and never reuse it for another
   invocation.

The request is rejected before canonical AF dispatch if any step fails.

### Secret and replay rules

- The secret is generated from the OS CSPRNG for every bridge/child epoch.
- It is held only in the trusted bridge/verifier memory boundary. It is never
  placed in LLM-visible arguments, environment variables, command arguments,
  logs, Plan files, context projections or `governance.sqlite3`.
- Private local IPC between issuer and verifier is operator-owned and bounded
  to the same user/child instance (Unix socket or inherited protected handle,
  mode `0600` plus peer credential checking). M2 must choose and prove one
  concrete mechanism.
- Startup, child replacement, explicit secret exposure response and bridge
  epoch change rotate the secret and `bridge_id`. Old tokens fail MAC/epoch
  validation even if their nonce was never consumed.
- Nonce consumption is atomic within one bridge epoch. It is not persisted as
  a second Session database. Restart invalidates the entire old epoch, which
  is the restart replay boundary.
- A token is never reissued for a call. A failed verification never falls
  back to another token or another Session.

### Failure contract

| Failure | Required result | State effect |
| --- | --- | --- |
| Missing/duplicate carrier | fail closed | no AF dispatch |
| Invalid MAC or malformed token | fail closed | no identity |
| Expired/not-yet-valid token | fail closed | no identity |
| Replayed nonce | fail closed | no second dispatch |
| Call mismatch or argument digest mismatch | fail closed | no route change |
| Cross-session token substitution | fail closed | Session A cannot route as B |
| Secret exposure or verifier integrity failure | fail closed | invalidate epoch; reject pending calls |
| Bridge unavailable/IPC timeout | fail closed | no FIFO or unauthenticated fallback |
| MCP child restart | fail closed for old credentials | new epoch only; durable context may resume separately |
| Unknown or stale Session | fail closed | no new context or authority; a valid token without a durable record is only the explicit initial-bind state below |
| Missing/corrupt durable binding | fail closed | no in-memory promotion |

The bounded contract proof covers token forgery, expiry, replay, argument/call
mismatch, cross-session substitution, restart epoch invalidation and concurrent
one-time consumption. It does not claim the OpenCode hook mutation or a real
MCP child has been exercised.

## W3 - Session Routing and Durability Design

### Frozen routing fields

```text
ONE_SHARED_MCP_CHILD_ALLOWED=yes
SESSION_A_CONTEXT != SESSION_B_CONTEXT
SESSION_ID_VERIFIED_PER_INVOCATION=yes
SESSION_A_BIND_CANNOT_CHANGE_SESSION_B=yes
GLOBAL_BOUND_DELEGATE_SHARED_ACROSS_SESSIONS=no
EXISTING_DURABLE_BINDING_REUSED=yes
SECOND_SESSION_DATABASE=no
SECOND_CONTEXT_ENGINE=no
SECOND_PLAN_AUTHORITY=no
```

```text
ROUTING_OWNER=one SessionScopedContextRouter instance at the AF MCP child boundary
VERIFICATION_ENTRYPOINT=trusted aota.invoke transport ingress before Core dispatch
SESSION_CONTEXT_LOOKUP=VerifiedInvocationIdentity.session_id -> bound slot or verified-unbound slot -> exact durable lookup for bound operations -> fresh trusted recomposition
DELEGATE_ISOLATION_MODEL=one in-memory slot/delegate and one ExecutionDispatcher per verified Session; no process-global bound delegate or dispatcher
DURABLE_PROJECTION_REUSE=FileBackedTaskMainContextBindingProjection in the trusted Project .aota directory
CACHE_LIFECYCLE=bounded ref-counted in-flight slots; evict only when idle; recompose from durable evidence on reuse
GLOBAL_EXECUTION_DISPATCHER_SHARED_ACROSS_SESSIONS=no
INITIAL_VERIFIED_UNBOUND_STATE=inspect/bind only; no AF Context or authority until existing bind readback succeeds
HEADLESS_COMPATIBILITY=existing bound TrustedWorkerBinding path remains valid and bypasses headed bridge issuance
M2_MINIMAL_IMPLEMENTATION_SEAMS=carrier extraction/verification, SessionScopedContextRouter, per-Session recomposition and dispatcher injection, projection CAS, bridge epoch lifecycle, focused real-MCP proof
```

### Owner and delegate isolation

The current `_UnboundAotaAdapter` has one `_bound_delegate` attribute and is
safe only for one exact host Session. M2 must not make that attribute global
across headed Sessions. The new router owns the mutable mapping:

```text
router[verified_session_id] -> SessionSlot
SessionSlot:
  exact origin_task_main_session_ref
  state = verified_unbound | bound
  fresh TrustedWorkerBinding / AF delegate
  session-local ExecutionDispatcher and its durable execution store
  immutable project/Plan identity evidence
  authorized-root digest
  in_flight_count
  bridge_epoch
```

The router is the only owner allowed to create, install, reuse, evict or
invalidate a Session slot. Each invocation verifies the token first, looks up
only its exact Session key, then performs the existing live authority and
context checks. A/B interleaving therefore selects A/B by token identity, not
by arrival order. Concurrent slot creation uses one per-session lock and
publishes only after the existing bind/readback commit point succeeds.

The current AF dispatcher seam is a known M2 boundary, not an acceptable
shared-state design. `core/ingress.py:205-234` stores one mutable
`_BOUND_EXECUTION_DISPATCHER`; `thin_task_main_host.py:718-740, 977-979`
creates a Session-specific dispatcher and overwrites that global; and
`core_ingress/__init__.py:321-339, 1810-1833, 2030-2041` reads it for
`task.start`/`task.return`. M2 must carry the trusted dispatcher through the
Session slot and the `CanonicalDispatchBinding`/Core dispatch seam, so every
Session-sensitive lifecycle operation selects its slot dispatcher. The
headed router must never call `bind_execution_dispatcher` as a shared global.
The existing single-session headless path may retain a compatibility adapter,
but it must not be overwritten by another headed Session.

Other process-global state is limited to the OpenCode/MCP child connection,
static operation catalog, bridge epoch and immutable configuration. Bound
delegates, dispatchers, execution stores, governance projections,
AuthorizedRootSet, Plan binding, tool arguments and in-flight identity are
Session-local. No global `current_session`, latest token, FIFO queue or shared
mutable `_bound_delegate` is permitted.

### Existing durable context and resume

`DurableTaskMainContextBinding` is explicitly evidence, not authority
(`runtime/task_main/context_binding.py:58-65`). The existing file projection
stores typed records under the trusted Project `.aota` directory and performs
exact Session lookup with conflict denial
(`runtime/task_main/context_binding.py:295-303, 450-468`). It atomically persists
and reads back new bindings (`:470-507`) and CAS-refreshes resume evidence
(`:509-564`).

For each verified Session:

1. Create a `verified_unbound` slot without creating an AF Context when the
   only requested operation is the existing `context.inspect` or `context.bind`.
   This is the legitimate initial state for an exact OpenCode Session and is
   not an unknown/stale Session.
2. For `context.inspect`, report the existing unbound state without authority.
   For `context.bind`, resolve the trusted Project/Plan selectors through the
   existing resolver, persist the typed durable binding, read it back, and only
   then promote the slot to `bound`.
3. For every operation other than the allowed inspect/bind path, require one
   exact durable record for `origin_task_main_session_ref`.
4. Re-resolve the trusted Project Registry, Plan authority, source digest and
   AuthorizedRootSet; never deserialize a historical runtime object as truth.
5. Recompose a fresh thin runtime through
   `recompose_trusted_context_from_evidence` and attach its Session-local
   dispatcher/store to the slot.
6. Recheck project fingerprint, Plan identity, source evidence, Session and
   AuthorizedRootSet before publishing the bound slot.
7. Preserve the existing `context.inspect`, `context.bind` and context-switch
   admission rules. The pure context-switch guard remains a guard, not a new
   store or operation.

The distinction is fail-closed: a valid token with no record can only enter
the explicit initial bind state; a stale epoch, unknown child, malformed
identity, conflicting record or authority drift cannot create a new Context.

The existing projection lock is currently instance-local
(`runtime/task_main/context_binding.py:305-331`) while `persist()` performs a
read-modify-write (`:470-507`). M2 must add one path-keyed shared
inter-process lock or an equivalent file CAS around the entire read-modify-
write/readback sequence. Per-Session locks alone are insufficient when A and B
use different projection instances. The proof must concurrently create or
refresh A and B and verify both records survive. This reuses the same
`task-main-context-bindings.json`; it creates no second persistence layer.

The existing Plan #003 restart/resume tests are the regression reference:
`tests/test_plan003_m3_w2_r1_context_resume.py:68-143, 297-322`. They prove
same-session exact lookup, different-session non-rehydration, digest/project
drift denial and persistence rollback. M2 extends this with two real routed
Sessions; it does not create a second database or context engine.

### Cache and child restart

The router may retain a slot while calls are active. Idle slots can be evicted
under a bounded capacity; eviction removes only memory and never deletes the
durable projection. A later verified call recomposes from current authority.
No token, secret, or short-lived credential is persisted.

On MCP child restart, the new child receives a new `bridge_id` and secret.
All old tokens fail closed. A bound Session can resume only by a new trusted
invocation followed by existing durable binding recomposition. A valid new
Session may first enter the explicit verified-unbound inspect/bind state; a
stale or unknown Session never creates an empty Context as a fallback.

### Headless compatibility and F-D seam

The existing headless path remains:

```text
4095 UI / Codex
  -> Existing AF Launcher
  -> OpenCode :4096 API
  -> exact task-main Session
  -> existing trusted binding
  -> AOTA MCP
```

The DailyTaskMainLauncher continues to establish and validate the exact
Session identity (`composition/task_main_daily_launcher.py:1359-1412`). A
pre-bound `TrustedWorkerBinding` continues to use the existing single-entry
transport; the headed bridge is not inserted into or allowed to weaken that
path. `role.bootstrap`, `origin_session_ref`, `context.inspect`, `context.bind`
and `context.switch` remain existing operations and authority boundaries.

F-D (`BY_REF_HYDRATION_SCOPE_MISMATCH`) is recorded only as a reauthorization
seam: verified Session identity must be available to the existing hydration
scope check before a by-ref is authorized. Multi-root by-ref hydration is not
implemented or repaired by this M1.

## W1-W3 Validation Evidence

| Work | Minimum proof | Result | Does not prove |
| --- | --- | --- | --- |
| W1 | pinned-source/static inspection, dependency lock inspection | PASS | a production Plugin deployment |
| W2 | `pytest -q tests/test_plan004_m1_contract.py` (V1 plus bounded concurrent contract proof) | PASS when run | real OpenCode/MCP transport correlation |
| W3 | source inventory, invariant review, Plan #003 resume regression references, bounded dispatcher/projection/initial-bind seam design | PASS | implementation correctness of the future router |
| RV1 | one integrated review of this contract and proof | PASS_WITH_FINDINGS | M2/M3 acceptance |

The test's concurrent section is classified as bounded `V2-CONTRACT`, not
production V2/V3. No production OpenCode 4096 configuration, resident bridge
service, full suite, or dogfood was run.

## RV1 Integrated Review

```text
TRUSTED_SESSION_SOURCE_CONFIRMED=yes
PER_INVOCATION_CORRELATION_PROVEN=bounded-contract-proof-pass; production-path-pending-M2
TOKEN_SECURITY_CONTRACT_FROZEN=yes
SECRET_ISOLATION_DESIGNED=yes
SESSION_ROUTING_OWNER_IDENTIFIED=yes
DURABLE_CONTEXT_REUSE_CONFIRMED=yes
NO_SECOND_AUTHORITY_DESIGNED=yes
M2_IMPLEMENTATION_SEAMS_IDENTIFIED=yes
```

Reviewer checks:

- No second Session DB, context engine, Plan authority, or public MCP tool.
- No LLM-minted Session ID or token.
- No global bound delegate or order-based token matching.
- No changes to Plans #001, #002 or #003.
- F-D is recorded only; no hydration repair was added.
- Headless launcher and existing context operations remain the compatibility
  path.

### Bounded RV1 Triage

The reviewer identified four blocking design gaps. They were repaired inside
the approved M1 contract scope; no production bridge or AF runtime source was
added:

```text
B-001=RESOLVED_IN_CONTRACT
      SessionSlot now owns the Session-local ExecutionDispatcher/store. The
      current core.ingress global dispatcher is explicitly an M2 refactor
      seam; headed routing may not overwrite it.
B-002=RESOLVED_IN_CONTRACT_AND_PROOF
      args_sha256 now names AF canonical_json as the single owner, including
      UTF-8, sorted keys, compact separators, allow_nan=false and rejection
      rules. The proof adds Unicode and non-finite-number coverage.
B-003=RESOLVED_IN_CONTRACT
      M2 must use a shared path-keyed inter-process lock or file CAS around
      projection read-modify-write/readback and prove concurrent A/B records.
B-004=RESOLVED_IN_CONTRACT
      verified_unbound is an explicit inspect/bind-only slot state. It is
      distinct from unknown/stale identity and cannot create AF Context or
      authority before existing bind persistence/readback succeeds.
```

No blocking M1 design finding remains. The real MCP carrier mutation,
Session-local dispatcher implementation, projection lock, and headed
verified-unbound flow remain required M2 implementation/proof work.

### Non-blocking findings

```text
NBF-001=The native MCP request has no trusted Session/call carrier. The M2
        Plugin mutation and AF extraction seam must be proven through a real
        OpenCode/MCP child before production correlation can be accepted.
NBF-002=The exact plugin ordering and argument-mutation preservation property
        is not established by static source alone; M2 must fail closed if the
        reserved carrier is lost or schema-stripped.
NBF-003=Bridge IPC, secret rotation and child-restart handling are designed
        here but not implemented or production-tested in M1.
```

These findings are non-blocking for the M1 contract/design deliverable but are
blocking conditions for M2 implementation acceptance and any M3 production
claim. M1 stops here; it does not start M2 or production activation.
