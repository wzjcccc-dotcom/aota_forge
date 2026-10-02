# m3-preparation-v6

Cumulative immutable successor package for Plan #004 M3, built from source commit `875a0b1f650d77b2ec9b6728401456977e10b730` (tree `36ba26cb7482401f9bf298e76d84303a5c8538c6`). It preserves the v2 Bridge group repair, v3 socket propagation, v4 project-bound Plan promotion, v5 project-only durability, and v5r1 exact runtime package closure.

The v6 source repair makes the shared MCP tool callback asynchronous, bounds synchronous operation offload to two workers, and serializes each verified Session using its existing Session slot. It adds no MCP tool, Session registry, trust rule, or authority semantic.

The runtime archive contains 451 exact-frontier file members, including `.aota/contracts/operations.yaml`. Candidate package digest is the SHA-256 of the exact `artifact-manifest.json` bytes; the manifest authenticates all package files except itself.

Production remains on `m3-preparation-v5r1`. This candidate has not been activated. The v5r1 activation approval does not extend to v6. Next gate: `EXPLICIT_CONCURRENCY_REPAIR_CONTROLLED_REACTIVATION_APPROVAL`, which remains unapproved.
