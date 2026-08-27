# AOTA Forge

Canonical source repository for the executor-neutral AOTA Forge Core, Unified
Control Plane, canonical CLI, and executor adapters.

## Governance

Source authority is this repository, `aota_forge`.

Program architecture authority is
[wzjcccc-dotcom/aota-hermes-tools#16](https://github.com/wzjcccc-dotcom/aota-hermes-tools/issues/16),
the AOTA Generic Architecture Umbrella Program Plan.

Child Portable Plan authority is held by Program-authorized child Issues under
`#16`. The current S1 child authority is
[wzjcccc-dotcom/aota-hermes-tools#17](https://github.com/wzjcccc-dotcom/aota-hermes-tools/issues/17);
S2 and S3 will receive their own child Issues when materialized.

The legacy Forge implementation Plan is
[wzjcccc-dotcom/aota-hermes-tools#9](https://github.com/wzjcccc-dotcom/aota-hermes-tools/issues/9).
It is retained as implementation, migration, and regression evidence, not as
current Generic Architecture Program authority. The `aota-hermes-tools`
repository may host current governance Issues as well as legacy source/tooling
evidence; those roles remain distinct.

## Layout

- `aota_forge/` — Forge Core package (M1: read-only ingress + canonical CLI)
- `scripts/` — validation and fixture scripts
- `deploy/evidence/` — migration and milestone evidence

## Source migration

See `deploy/evidence/issues/9/m1-repository-migration.json` for the
deterministic migration provenance from `aota-hermes-tools`.
