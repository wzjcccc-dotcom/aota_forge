"""OpenCode thin host adapter namespace (AF #56 M2).

Bounded adapter package: ``host_client`` (pinned legacy-root HTTP/SSE
contract), ``executor`` (existing ExecutorAdapter protocol), ``session_reentry``
(exact-session completion re-entry) and ``delivery`` (existing
CompletionDeliveryTransport protocol). No OpenCode TypeScript internals are
imported anywhere in this package; the boundary is HTTP/SSE to the pinned
OpenCode reference server.
"""
