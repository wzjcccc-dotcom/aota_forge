"""AF #58 M2 — trusted interactive ingress contract (mechanical, non-authoritative).

This package is the bounded server-side seam that lets an OpenChamber
``:3002`` New Chat become an EXACT trusted AF task-main session before any
model prompt is dispatched.

Ownership boundaries (AF #58 M2):

* The operator-supplied Plan reference extracted from the first message is
  user intent ONLY (``USER_PLAN_REF_IS_INTENT=yes``); it never grants
  project, root, source, approval, role or write authority.
* A preparation record contains mechanical staging data only (opaque id,
  instance directory, requested Plan ref, timestamps). It is NEVER authority
  (``PREPARATION_TOKEN_IS_AUTHORITY=no``).
* Live Plan truth, trusted project/source/root resolution, the digest-bound
  task-main binding envelope and the per-instance routing pointer are all
  produced by the existing accepted AF composition (no second Plan parser,
  no second task-main bootstrap engine, no second MCP server).
"""

from __future__ import annotations

from aota_forge.interactive_ingress.contract import (
    DEFAULT_PREPARATION_TTL_SECONDS,
    INTERACTIVE_SCHEMA,
    PREPARATION_STATE_BOUND,
    PREPARATION_STATE_PREPARED,
    SESSION_METADATA_PREPARATION_KEY,
    SESSION_METADATA_SCHEMA_KEY,
    InteractiveIngressError,
    InteractivePreparation,
    derive_interactive_plan_id,
    extract_canonical_plan_refs,
    new_preparation_id,
    validate_trusted_plan_state,
)
from aota_forge.interactive_ingress.service import (
    MATERIALIZE_REUSES_EXISTING_TASK_MAIN_FORMAT,
    PREPARATION_TOKEN_IS_AUTHORITY,
    SECOND_BINDING_ONTOLOGY_CREATED,
    SECOND_PLAN_PARSER_CREATED,
    bind_interactive_session,
    read_bound_plan_ref,
    reserve_interactive_session,
    resolve_workspace_root,
    sweep_interactive_preparations,
    trusted_plan_state_from_document,
)

__all__ = [
    "DEFAULT_PREPARATION_TTL_SECONDS",
    "INTERACTIVE_SCHEMA",
    "MATERIALIZE_REUSES_EXISTING_TASK_MAIN_FORMAT",
    "PREPARATION_STATE_BOUND",
    "PREPARATION_STATE_PREPARED",
    "PREPARATION_TOKEN_IS_AUTHORITY",
    "SECOND_BINDING_ONTOLOGY_CREATED",
    "SECOND_PLAN_PARSER_CREATED",
    "SESSION_METADATA_PREPARATION_KEY",
    "SESSION_METADATA_SCHEMA_KEY",
    "InteractiveIngressError",
    "InteractivePreparation",
    "bind_interactive_session",
    "derive_interactive_plan_id",
    "extract_canonical_plan_refs",
    "new_preparation_id",
    "read_bound_plan_ref",
    "reserve_interactive_session",
    "resolve_workspace_root",
    "sweep_interactive_preparations",
    "trusted_plan_state_from_document",
    "validate_trusted_plan_state",
]
