# AOTA Forge

Canonical source repository for the executor-neutral AOTA Forge Core, Unified
Control Plane, canonical CLI, and executor adapters.

## Governance

The governing Portable Plan remains
[wzjcccc-dotcom/aota-hermes-tools#9](https://github.com/wzjcccc-dotcom/aota-hermes-tools/issues/9)
until a separately authorized Plan-authority migration is performed.

`aota-hermes-tools` is legacy Hermes/AOTA tooling and migration/regression
evidence only; it is not the source authority for the new Forge Core.

## Layout

- `aota_forge/` — Forge Core package (M1: read-only ingress + canonical CLI)
- `scripts/` — validation and fixture scripts
- `deploy/evidence/` — migration and milestone evidence

## Source migration

See `deploy/evidence/issues/9/m1-repository-migration.json` for the
deterministic migration provenance from `aota-hermes-tools`.
