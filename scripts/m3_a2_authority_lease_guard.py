#!/usr/bin/env python3
"""M3-A2 authority model and capability lease contract guard (Issue #9, lane M3-A2).

Design-only freeze guard.  Validates
deploy/evidence/issues/9/m3-a/a2-authority-lease.json at the exact M3-A base
(10b8018ecf618cd7ff4c2b73f962356af044669f) plus the M3-A2 lane contract:

- every required top-level section exists and is structurally valid
- exact base SHA and A0 commit/source references are recorded and verified
  against the checked-out A0 input map and guard
- all four OQ-M3A2 questions are resolved; A1 questions are NOT resolved
- ID taxonomy: six categories, machine fields, raw-ID deny-by-default policy
- principal model: five typed classes, trusted injection, no self-assertion
- authority evaluation: deterministic inputs, ALLOW/DENY/BLOCKED/
  NEEDS_APPROVAL, no semantic reasoning, adapter authority bounded
- capability lease contract: exact canonical field names, lifecycle, and
  bound/expansion/expiry/consumption/replay/delegation rules
- approval is a lease-issuance prerequisite, never an authority identity
- trusted-context separation, NF2 resolution, read-only diagnostic boundary
- error semantics: required entries, origin classification, registry unmodified
- six regression design proofs with NOT_YET_IMPLEMENTED dispositions
- all design-only machine flags frozen to their exact values
- static/source proof: no authority/lease/mutation implementation exists in
  aota_forge and no source file changed (git diff against the exact base)

The guard performs NO mutation.  Exit status: 0 on PASS, 1 on FAIL.

Usage:

    python3 scripts/m3_a2_authority_lease_guard.py
    python3 scripts/m3_a2_authority_lease_guard.py --json
    python3 scripts/m3_a2_authority_lease_guard.py --artifact /tmp/x.json
    python3 scripts/m3_a2_authority_lease_guard.py --repo-root /path/to/worktree
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

EXPECTED_BASE_SHA = "10b8018ecf618cd7ff4c2b73f962356af044669f"
A0_COMMON_BASE = "ccce2e02c40ec92fe1717744e801ddcb5b01095c"

A2_REL = Path("deploy/evidence/issues/9/m3-a/a2-authority-lease.json")
GUARD_REL = Path("scripts/m3_a2_authority_lease_guard.py")
A0_MAP_REL = Path("deploy/evidence/issues/9/m3-a/a0-input-map.json")
A0_GUARD_REL = Path("scripts/m3_a0_reconnaissance_guard.py")
MATRIX_REL = Path("deploy/evidence/issues/9/m2-successor-regression-matrix.json")

REQUIRED_TOP_LEVEL = [
    "schema_version",
    "project_id",
    "milestone",
    "phase",
    "lane",
    "governing_plan_issue",
    "canonical_source_repository",
    "base_sha",
    "source_inputs",
    "resolved_design_questions",
    "id_model",
    "principal_model",
    "principal_binding_rule",
    "authority_evaluation_model",
    "capability_lease_contract",
    "approval_relationship",
    "trusted_context_model",
    "trusted_context_authority_rule",
    "nf2_resolution_contract",
    "readonly_authority_boundary",
    "error_semantics",
    "regression_design_proofs",
    "deferred_questions",
    "implementation_boundary",
    "invariants",
]

REQUIRED_FLAGS = {
    "ID_IS_AUTHORITY": "no",
    "MODEL_INTERNAL_IDS_NORMAL_INPUT": "no",
    "RAW_INTERNAL_ID_MODEL_SURFACE": "denied_by_default",
    "MODEL_INTERNAL_ID_GUESSING": "denied",
    "CAPABILITY_LEASE_REQUIRED_FOR_BOUNDED_MUTATION": "yes",
    "UNTRUSTED_MODEL_CAN_SELF_ASSERT_PRIVILEGED_PRINCIPAL": "no",
    "AUTHORITY_ENGINE_PERFORMS_SEMANTIC_REASONING": "no",
    "LEASE_PRINCIPAL_BOUND": "yes",
    "LEASE_OPERATION_BOUND": "yes",
    "LEASE_OBJECT_BOUND": "yes",
    "LEASE_REVISION_BOUND": "yes",
    "LEASE_EXPIRY_ENFORCED": "yes",
    "LEASE_OBJECT_SCOPE_EXPANSION": "denied",
    "LEASE_OPERATION_SCOPE_EXPANSION": "denied",
    "LEASE_PRINCIPAL_SUBSTITUTION": "denied",
    "TRUSTED_RESOURCE_IMPLIES_MUTATION_AUTHORITY": "no",
    "TRUSTED_ADAPTER_IMPLIES_UNBOUNDED_AUTHORITY": "no",
    "SAFE_READONLY_LEASE_REQUIRED": "no",
    "READONLY_DIAGNOSTIC_PLANE_PRESERVED": "yes",
    "NF2_SOURCE_FIXED": "no",
    "AUTHORITY_IMPLEMENTATION_STARTED": "no",
    "LEASE_IMPLEMENTATION_STARTED": "no",
    "SOURCE_CODE_CHANGED": "no",
    "MUTATION_IMPLEMENTATION_STARTED": "no",
    "AUTHORITY_MODEL_BLOCKS_SAFE_READONLY_DIAGNOSIS": "no",
}

A2_QUESTION_IDS = {"OQ-M3A2-01", "OQ-M3A2-02", "OQ-M3A2-03", "OQ-M3A2-04"}
A1_QUESTION_IDS_DEFERRED = {
    "OQ-M3A1-01",
    "OQ-M3A1-02",
    "OQ-M3A1-04",
    "OQ-M3A1-05",
    "OQ-M3A1-06",
}

ID_CATEGORY_IDS = {
    "semantic_public_reference",
    "internal_durable_object_id",
    "execution_private_id",
    "correlation_id",
    "adapter_private_executor_id",
    "lease_id",
}
ID_ROW_FIELDS = {
    "category",
    "model_facing",
    "durable",
    "authority_bearing",
    "guessable_input_allowed",
    "adapter_private",
    "audit_visible",
    "operator_diagnostic_visible",
}

PRINCIPAL_CLASSES = {
    "human",
    "llm_agent_session",
    "executor_adapter",
    "system_mechanical",
    "operator_debug",
}
PRINCIPAL_ROW_FIELDS = {
    "class",
    "meaning",
    "identity_fields",
    "trust_evidence",
    "freshness_channel_binding",
    "notes",
}

DECISION_CLASSES = {"ALLOW", "DENY", "BLOCKED", "NEEDS_APPROVAL"}

LEASE_CANONICAL_FIELDS = {
    "lease_id",
    "principal",
    "operation",
    "object_ref",
    "expected_revision",
    "mutation_scope",
    "issued_at",
    "expires_at",
    "authority_basis",
    "approval_basis",
    "delegation_policy",
}

REQUIRED_ERROR_CODES = {
    "PRINCIPAL_UNTRUSTED",
    "AUTHORITY_DENIED",
    "LEASE_REQUIRED",
    "LEASE_EXPIRED",
    "LEASE_SCOPE_MISMATCH",
    "REVISION_MISMATCH",
}
EXISTING_ERROR_CODES = {
    "NEEDS_SEMANTIC_CHOICE",
    "HOST_RESOURCE_DENIED",
    "CONTEXT_NOT_SUPPORTED",
}

REGRESSION_PROOFS = {
    "BIND-1",
    "B014-F",
    "RC2-1",
    "RECOVERY-1",
    "WCTX-1",
    "ACTIVATE-R-current-binding",
}

# Bounded static scan: implementation surface must not exist in aota_forge.
# These patterns deliberately do NOT match the M2 placeholders
# (capability_lease: None, required_authority, approval_required) or the
# adapter package adapters/plan_authority (PlanAuthoritySnapshot, M2 read-only).
IMPL_CLASS_RE = re.compile(
    r"class\s+\w*(?:Authority|Lease|Mutation)\w*"
    r"(?:Engine|Broker|Manager|Store|Service|Evaluator|Issuer|Ledger|Vault)\w*\s*[:(]"
)
IMPL_FUNC_RE = re.compile(
    r"def\s+(?:evaluate_authority|authorize_mutation|issue_lease|consume_lease|"
    r"revoke_lease|renew_lease|acquire_lease|validate_lease|grant_lease|"
    r"apply_mutation|perform_mutation|commit_mutation|execute_mutation)\s*\("
)
IMPL_OP_RE = re.compile(r'name\s*=\s*"(?:authority|lease|mutation)\.[a-z0-9_.-]+"')
IMPL_IMPORT_RE = re.compile(
    r"(?:from|import)\s+aota_forge\.(?:core|adapters)\.(?:authority|lease|mutation)\b"
)

M2_PLACEHOLDER_MARKERS = {
    ("core/context.py", "capability_lease: None = None"),
    ("core/context.py", "principal: str"),
    ("core/contracts/descriptor.py", "required_authority: str | None = None"),
    ("core/contracts/descriptor.py", "approval_required: bool = False"),
    ("adapters/hermes/invoke.py", 'PRINCIPAL = "hermes"'),
    ("core/ingress.py", "operation is not read-only in M2"),
    ("core/contracts/errors.py", 'code = "NEEDS_SEMANTIC_CHOICE"'),
    ("core/contracts/errors.py", 'code = "HOST_RESOURCE_DENIED"'),
    ("core/contracts/errors.py", 'code = "CONTEXT_NOT_SUPPORTED"'),
}


def _read_json(path: Path) -> dict:
    if not path.is_file():
        raise FileNotFoundError(f"missing file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _check(ok: bool, message: str) -> tuple[bool, str]:
    return ok, message


def _git(repo_root: Path, args: list[str]) -> tuple[int, str]:
    proc = subprocess.run(
        ["git", "-C", str(repo_root), *args],
        capture_output=True,
        text=True,
        timeout=60,
    )
    return proc.returncode, proc.stdout.strip()


def run_checks(artifact: dict, repo_root: Path, a0_map: dict, matrix: dict) -> list[tuple[bool, str]]:
    results: list[tuple[bool, str]] = []

    # 1. Top-level shape.
    for key in REQUIRED_TOP_LEVEL:
        results.append(_check(key in artifact, f"artifact missing top-level field: {key}"))
    results.append(_check(artifact.get("schema_version") == 1, "schema_version must be 1"))
    results.append(_check(artifact.get("project_id") == "aota_forge", "project_id must be aota_forge"))
    results.append(_check(artifact.get("milestone") == "M3", "milestone must be M3"))
    results.append(_check(artifact.get("phase") == "M3-A", "phase must be M3-A"))
    results.append(_check(artifact.get("lane") == "M3-A2", "lane must be M3-A2"))
    results.append(_check(artifact.get("governing_plan_issue") == 9, "governing_plan_issue must be 9"))
    results.append(_check(
        artifact.get("canonical_source_repository") == "wzjcccc-dotcom/aota_forge",
        "canonical_source_repository must be wzjcccc-dotcom/aota_forge",
    ))

    # 2. Exact base SHA and A0 commit/source references.
    results.append(_check(
        artifact.get("base_sha") == EXPECTED_BASE_SHA,
        f"base_sha must be {EXPECTED_BASE_SHA} (got {artifact.get('base_sha')!r})",
    ))
    results.append(_check(
        artifact.get("a0_commit_sha") == EXPECTED_BASE_SHA,
        "a0_commit_sha must equal the exact A2 base (the A0 commit)",
    ))
    results.append(_check(
        artifact.get("m3_a_common_base") == A0_COMMON_BASE,
        f"m3_a_common_base must be the A0 recorded common base {A0_COMMON_BASE}",
    ))
    results.append(_check(a0_map.get("base_sha") == A0_COMMON_BASE, "A0 input map must record the M2 common base"))
    results.append(_check(
        a0_map.get("invariants", {}).get("frozen_plan_facts", {}).get("ID_IS_AUTHORITY") == "no",
        "A0 input map must still freeze ID_IS_AUTHORITY=no",
    ))
    a0_questions = {q.get("QUESTION_ID") for q in a0_map.get("open_design_questions", [])}
    results.append(_check(
        A2_QUESTION_IDS <= a0_questions,
        "A0 input map must carry the four OQ-M3A2 questions this lane resolves",
    ))
    sources = artifact.get("source_inputs", [])
    results.append(_check(isinstance(sources, list) and sources, "source_inputs must be a non-empty list"))
    source_text = " ".join(json.dumps(s, sort_keys=True) for s in sources if isinstance(s, dict))
    for marker in ("a0-input-map.json", "m3_a0_reconnaissance_guard.py", "m2-successor-regression-matrix.json"):
        results.append(_check(marker in source_text, f"source_inputs must reference {marker}"))

    # 3. Resolved design questions: four A2 resolved, no A1 falsely resolved.
    resolved = artifact.get("resolved_design_questions", [])
    resolved_ids = [q.get("QUESTION_ID") for q in resolved if isinstance(q, dict)]
    results.append(_check(
        set(resolved_ids) == A2_QUESTION_IDS,
        f"resolved_design_questions must be exactly {sorted(A2_QUESTION_IDS)} (got {sorted(set(resolved_ids))})",
    ))
    for q in resolved:
        qid = q.get("QUESTION_ID", "?")
        results.append(_check(
            q.get("resolution_status") == "resolved" and q.get("RESOLUTION"),
            f"resolved question {qid} must carry resolution_status=resolved and a RESOLUTION",
        ))
    results.append(_check(
        not any(str(qid).startswith("OQ-M3A1-") for qid in resolved_ids),
        "no M3-A1 question may be resolved by this artifact",
    ))

    # 4. Deferred questions: required A1 questions present and unanswered.
    deferred = artifact.get("deferred_questions", [])
    deferred_ids = [q.get("QUESTION_ID") for q in deferred if isinstance(q, dict)]
    results.append(_check(isinstance(deferred, list) and deferred, "deferred_questions must be a non-empty list"))
    results.append(_check(
        A1_QUESTION_IDS_DEFERRED <= set(deferred_ids),
        f"deferred_questions must cover {sorted(A1_QUESTION_IDS_DEFERRED)}",
    ))
    for q in deferred:
        qid = q.get("QUESTION_ID", "?")
        results.append(_check(
            q.get("not_answered_by_a2") is True and q.get("DEFERRED_TO"),
            f"deferred question {qid} must set not_answered_by_a2=true and DEFERRED_TO",
        ))
    results.append(_check(
        not any(qid in A2_QUESTION_IDS for qid in deferred_ids),
        "resolved A2 questions must not appear in deferred_questions",
    ))
    a1_boundary = artifact.get("a1_resolution_boundary", {})
    results.append(_check(
        set(a1_boundary.get("resolved_by_a2", [])) == A2_QUESTION_IDS,
        "a1_resolution_boundary.resolved_by_a2 must be exactly the four A2 questions",
    ))
    results.append(_check(
        a1_boundary.get("subject_identity_resolution") == "none",
        "a1_resolution_boundary.subject_identity_resolution must be 'none'",
    ))

    # 5. ID model: six categories, machine fields, raw-ID policies.
    id_model = artifact.get("id_model", {})
    categories = id_model.get("id_categories", [])
    category_ids = [row.get("category") for row in categories if isinstance(row, dict)]
    results.append(_check(
        set(category_ids) == ID_CATEGORY_IDS,
        f"id_model.id_categories must be exactly {sorted(ID_CATEGORY_IDS)}",
    ))
    for row in categories:
        cid = row.get("category", "?")
        missing = ID_ROW_FIELDS - set(row)
        results.append(_check(not missing, f"id category {cid!r} missing fields: {sorted(missing)}"))
        results.append(_check(row.get("authority_bearing") == "no", f"id category {cid!r} must be authority_bearing=no"))
    by_cat = {row.get("category"): row for row in categories}
    lease_row = by_cat.get("lease_id", {})
    internal_row = by_cat.get("internal_durable_object_id", {})
    corr_row = by_cat.get("correlation_id", {})
    semantic_row = by_cat.get("semantic_public_reference", {})
    results.append(_check(
        lease_row.get("model_facing") == "no" and lease_row.get("adapter_private") == "yes",
        "lease_id must be non-model-facing and adapter-private",
    ))
    results.append(_check(
        lease_row.get("guessable_input_allowed") == "no",
        "lease_id must be non-guessable",
    ))
    results.append(_check(
        internal_row.get("model_facing") == "no" and internal_row.get("guessable_input_allowed") == "no",
        "internal_durable_object_id must be non-model-facing and non-guessable",
    ))
    results.append(_check(
        corr_row.get("model_facing") == "bounded" and corr_row.get("authority_bearing") == "no",
        "correlation_id must be bounded diagnostic metadata with no authority",
    ))
    results.append(_check(
        semantic_row.get("model_facing") == "explicit_contract_only"
        and semantic_row.get("guessable_input_allowed") == "explicit_contract_only",
        "semantic public references may be model-facing/guessable only as explicit contract inputs",
    ))
    results.append(_check(
        isinstance(id_model.get("normal_model_input_allowlist"), list)
        and id_model["normal_model_input_allowlist"],
        "id_model.normal_model_input_allowlist must be a non-empty list",
    ))
    results.append(_check(
        id_model.get("raw_internal_id_rule") == "deny_by_default",
        "id_model.raw_internal_id_rule must be deny_by_default",
    ))
    for flag, expected in (
        ("ID_IS_AUTHORITY", "no"),
        ("MODEL_INTERNAL_IDS_NORMAL_INPUT", "no"),
        ("RAW_INTERNAL_ID_MODEL_SURFACE", "denied_by_default"),
        ("MODEL_INTERNAL_ID_GUESSING", "denied"),
    ):
        results.append(_check(
            id_model.get("policy_flags", {}).get(flag) == expected,
            f"id_model.policy_flags.{flag} must be {expected}",
        ))

    # 6. Principal model: five classes, trusted injection, no self-assertion.
    p_model = artifact.get("principal_model", {})
    p_classes = p_model.get("principal_classes", [])
    p_ids = [p.get("class") for p in p_classes if isinstance(p, dict)]
    results.append(_check(
        set(p_ids) == PRINCIPAL_CLASSES,
        f"principal_model.principal_classes must be exactly {sorted(PRINCIPAL_CLASSES)}",
    ))
    for p in p_classes:
        cid = p.get("class", "?")
        missing = PRINCIPAL_ROW_FIELDS - set(p)
        results.append(_check(not missing, f"principal class {cid!r} missing fields: {sorted(missing)}"))
    results.append(_check(
        bool(p_model.get("trusted_injection_boundary")),
        "principal_model.trusted_injection_boundary must be defined",
    ))
    results.append(_check(
        "cannot self-assert" in p_model.get("self_assertion_rule", ""),
        "principal_model.self_assertion_rule must deny self-assertion",
    ))
    results.append(_check(
        bool(p_model.get("no_authority_by_identity")),
        "principal_model.no_authority_by_identity must be defined",
    ))
    binding_rule = artifact.get("principal_binding_rule", {})
    for key in ("rule", "binding_boundary", "fail_closed"):
        results.append(_check(bool(binding_rule.get(key)), f"principal_binding_rule missing field: {key}"))

    # 7. Authority evaluation model.
    auth = artifact.get("authority_evaluation_model", {})
    inputs = " ".join(str(x) for x in auth.get("engine_inputs", []))
    for marker in (
        "principal",
        "contract_hash",
        "object reference",
        "expected revision",
        "mutation scope",
        "approval",
        "semantic decision evidence",
        "lifecycle predecessor",
        "lease validity",
    ):
        results.append(_check(marker in inputs, f"authority engine_inputs must include: {marker}"))
    results.append(_check(
        set(auth.get("decision_classes", {})) == DECISION_CLASSES,
        f"decision_classes must be exactly {sorted(DECISION_CLASSES)}",
    ))
    results.append(_check(
        "never performs semantic reasoning" in auth.get("engine_role", ""),
        "engine_role must state the engine never performs semantic reasoning",
    ))
    results.append(_check(
        auth.get("no_semantic_reasoning") == "AUTHORITY_ENGINE_PERFORMS_SEMANTIC_REASONING=no",
        "no_semantic_reasoning flag must be frozen",
    ))
    results.append(_check(
        bool(auth.get("adapter_boundary")),
        "authority_evaluation_model.adapter_boundary must be defined (adapters cannot invent authority)",
    ))

    # 8. Capability lease contract.
    lease = artifact.get("capability_lease_contract", {})
    results.append(_check(
        set(lease.get("canonical_fields", {})) == LEASE_CANONICAL_FIELDS,
        f"lease canonical_fields must be exactly {sorted(LEASE_CANONICAL_FIELDS)}",
    ))
    results.append(_check(
        lease.get("optional_fields") == ["approval_basis"],
        "lease optional_fields must be exactly ['approval_basis']",
    ))
    bound = lease.get("bound_properties", {})
    for flag in ("principal_bound", "operation_bound", "object_bound", "revision_bound"):
        results.append(_check(bound.get(flag) == "yes", f"lease bound_properties.{flag} must be yes"))
    results.append(_check(
        bound.get("ttl_policy") == "bounded_short_lived",
        "lease bound_properties.ttl_policy must be bounded_short_lived",
    ))
    results.append(_check(
        bound.get("non_semantic") == "yes" and bound.get("non_delegable_by_default") == "yes",
        "lease must be non_semantic and non_delegable_by_default",
    ))
    lifecycle = lease.get("lifecycle", {})
    for key in ("issuance", "validation", "expiry", "consumption", "replay", "revocation", "renewal", "expansion", "substitution", "delegation"):
        results.append(_check(bool(lifecycle.get(key)), f"lease lifecycle missing stage: {key}"))
    results.append(_check(
        "hard denial" in lifecycle.get("expiry", ""),
        "lease expiry must be a hard denial",
    ))
    results.append(_check(
        "denied" in lifecycle.get("replay", "") and "denied" in lifecycle.get("expansion", "")
        and "denied" in lifecycle.get("substitution", ""),
        "lease replay/expansion/substitution must be denied",
    ))
    results.append(_check(
        "new bounded lease" in lifecycle.get("delegation", ""),
        "lease delegation must require a new bounded lease",
    ))
    results.append(_check(
        bool(lease.get("semantic_boundary")) and bool(lease.get("lease_id_policy"))
        and bool(lease.get("not_implemented")),
        "lease contract must define semantic_boundary, lease_id_policy and not_implemented",
    ))

    # 9. Approval relationship.
    approval = artifact.get("approval_relationship", {})
    results.append(_check(isinstance(approval.get("rules"), list) and approval["rules"], "approval_relationship.rules must be non-empty"))
    approval_text = " ".join(approval.get("rules", [])) + " " + approval.get("definition", "")
    results.append(_check(
        "prerequisite at lease issuance" in approval_text or "prerequisite" in approval_text,
        "approval must be a prerequisite at lease issuance",
    ))
    results.append(_check(
        "never an authority identity" in approval.get("definition", ""),
        "approval must never be an authority identity",
    ))
    for flag, expected in (
        ("APPROVAL_IS_AUTHORITY_IDENTITY", "no"),
        ("APPROVAL_PREREQUISITE_AT_LEASE_ISSUANCE", "yes"),
    ):
        results.append(_check(
            approval.get("flags", {}).get(flag) == expected,
            f"approval_relationship.flags.{flag} must be {expected}",
        ))

    # 10. Trusted context / NF2 / read-only boundary.
    tctx = artifact.get("trusted_context_model", {})
    results.append(_check(bool(tctx.get("logical_reference")), "trusted_context_model.logical_reference missing"))
    results.append(_check(
        "non-model-facing" in tctx.get("resolved_path_boundary", ""),
        "raw filesystem paths must be non-model-facing",
    ))
    results.append(_check(bool(tctx.get("principal_binding")), "trusted_context_model.principal_binding missing"))
    axes = [a.get("axis") for a in tctx.get("axes", []) if isinstance(a, dict)]
    for axis in ("trusted provenance", "trusted identity", "trusted resource resolution", "authorization"):
        results.append(_check(axis in axes, f"trusted_context_model.axes must include: {axis}"))
    for flag in ("TRUSTED_RESOURCE_IMPLIES_MUTATION_AUTHORITY", "TRUSTED_ADAPTER_IMPLIES_UNBOUNDED_AUTHORITY"):
        results.append(_check(
            tctx.get("flags", {}).get(flag) == "no",
            f"trusted_context_model.flags.{flag} must be no",
        ))
    tctx_rule = artifact.get("trusted_context_authority_rule", {})
    results.append(_check(
        "TRUSTED_RESOURCE != AUTHORIZED_MUTATION" in tctx_rule.get("rule", "")
        and "TRUSTED_ADAPTER != UNBOUNDED_AUTHORITY" in tctx_rule.get("rule", ""),
        "trusted_context_authority_rule must separate trusted resource/adapter from mutation authority",
    ))
    for flag in ("TRUSTED_RESOURCE_IMPLIES_MUTATION_AUTHORITY", "TRUSTED_ADAPTER_IMPLIES_UNBOUNDED_AUTHORITY"):
        results.append(_check(
            tctx_rule.get("frozen_flags", {}).get(flag) == "no",
            f"trusted_context_authority_rule.frozen_flags.{flag} must be no",
        ))
    nf2 = artifact.get("nf2_resolution_contract", {})
    results.append(_check(bool(nf2.get("NF2_GAP")), "nf2_resolution_contract.NF2_GAP missing"))
    results.append(_check(
        isinstance(nf2.get("RESOLVED_DESIGN_RULES"), list) and nf2["RESOLVED_DESIGN_RULES"],
        "nf2_resolution_contract.RESOLVED_DESIGN_RULES must be non-empty",
    ))
    results.append(_check(nf2.get("NF2_SOURCE_FIXED") == "no", "NF2_SOURCE_FIXED must be no"))
    readonly = artifact.get("readonly_authority_boundary", {})
    for flag, expected in (
        ("SAFE_READONLY_LEASE_REQUIRED", "no"),
        ("READONLY_DIAGNOSTIC_PLANE_PRESERVED", "yes"),
        ("AUTHORITY_MODEL_BLOCKS_SAFE_READONLY_DIAGNOSIS", "no"),
    ):
        results.append(_check(readonly.get(flag) == expected, f"readonly_authority_boundary.{flag} must be {expected}"))
    results.append(_check(
        bool(readonly.get("safe_diagnosis_no_lease")) and bool(readonly.get("protected_resources")),
        "readonly_authority_boundary must define safe_diagnosis_no_lease and protected_resources",
    ))

    # 11. Error semantics.
    err = artifact.get("error_semantics", {})
    results.append(_check(err.get("registry_modified") == "no", "error_semantics.registry_modified must be no"))
    entries = err.get("entries", [])
    results.append(_check(isinstance(entries, list) and entries, "error_semantics.entries must be non-empty"))
    codes = set()
    for entry in entries:
        code = entry.get("code")
        origin = entry.get("origin")
        codes.add(code)
        results.append(_check(
            origin in ("new_design_concept", "existing_canonical_error"),
            f"error {code!r}: origin must be new_design_concept|existing_canonical_error",
        ))
        results.append(_check(bool(entry.get("meaning")), f"error {code!r} missing meaning"))
    results.append(_check(REQUIRED_ERROR_CODES <= codes, "all required lease/authority error codes must be present"))
    for code in EXISTING_ERROR_CODES:
        entry = next((e for e in entries if e.get("code") == code), {})
        results.append(_check(
            entry.get("origin") == "existing_canonical_error",
            f"existing code {code} must be classified existing_canonical_error",
        ))
    results.append(_check(
        all(code.startswith("LEASE_") or code in ("PRINCIPAL_UNTRUSTED", "AUTHORITY_DENIED", "REVISION_MISMATCH", "APPROVAL_MISMATCH") for code in codes - EXISTING_ERROR_CODES),
        "every new error code must be a lease/authority design concept",
    ))

    # 12. Regression design proofs.
    proofs = artifact.get("regression_design_proofs", [])
    proof_classes = [p.get("failure_class") for p in proofs if isinstance(p, dict)]
    results.append(_check(
        set(proof_classes) == REGRESSION_PROOFS,
        f"regression_design_proofs must cover exactly {sorted(REGRESSION_PROOFS)}",
    ))
    matrix_by_class = {e.get("failure_class"): e for e in matrix.get("failure_classes", [])}
    results.append(_check(len(matrix_by_class) == 16, "canonical matrix must carry 16 failure classes"))
    for p in proofs:
        pclass = p.get("failure_class", "?")
        for key in ("authority_concern", "frozen_design_invariant", "future_implementation_requirement"):
            results.append(_check(bool(p.get(key)), f"regression proof {pclass} missing field: {key}"))
        results.append(_check(
            p.get("a0_current_disposition") == "NOT_YET_IMPLEMENTED",
            f"regression proof {pclass} must keep a0_current_disposition=NOT_YET_IMPLEMENTED",
        ))
        results.append(_check(
            p.get("behavioral_regression_implemented") is False,
            f"regression proof {pclass} must not claim behavioral_regression_implemented",
        ))
        matrix_entry = matrix_by_class.get(pclass, {})
        results.append(_check(
            matrix_entry.get("expected_successor_disposition") == "NOT_YET_IMPLEMENTED",
            f"regression proof {pclass} must match matrix disposition NOT_YET_IMPLEMENTED",
        ))

    # 13. Design-only flags and constraints.
    flags = artifact.get("invariants", {}).get("frozen_authority_flags", {})
    for flag, expected in sorted(REQUIRED_FLAGS.items()):
        results.append(_check(
            flags.get(flag) == expected,
            f"frozen_authority_flags.{flag} must be {expected} (got {flags.get(flag)!r})",
        ))
    results.append(_check(
        set(flags) == set(REQUIRED_FLAGS),
        "frozen_authority_flags must not add or drop flags",
    ))
    constraints = artifact.get("invariants", {}).get("m3_a2_constraints", {})
    for flag, expected in (("AUTHORITATIVE_GRAPH_WRITES_ALLOWED", "no"), ("AUTHORITATIVE_STATE_MUTATION_ALLOWED", "no")):
        results.append(_check(constraints.get(flag) == expected, f"m3_a2_constraints.{flag} must be {expected}"))

    # 14. Implementation boundary.
    impl = artifact.get("implementation_boundary", {})
    for concept in ("authority engine", "capability lease runtime", "mutation runtime"):
        results.append(_check(
            concept in impl.get("implemented_nothing", []),
            f"implementation_boundary.implemented_nothing must include {concept}",
        ))
    results.append(_check(
        set(impl.get("changed_paths", [])) == {str(A2_REL), str(GUARD_REL)},
        f"implementation_boundary.changed_paths must be exactly {[str(A2_REL), str(GUARD_REL)]}",
    ))
    results.append(_check(
        impl.get("error_registry_modified") == "no"
        and impl.get("source_mutation_scope") == "design_artifacts_only",
        "implementation_boundary must declare error registry unmodified and design_artifacts_only",
    ))

    # 15. Report metrics consistency.
    expected_metrics = {
        "resolved_design_question_count": len(resolved),
        "deferred_question_count": len(deferred),
        "id_category_count": len(categories),
        "principal_class_count": len(p_classes),
        "decision_class_count": len(auth.get("decision_classes", {})),
        "lease_canonical_field_count": len(lease.get("canonical_fields", {})),
        "error_semantics_entry_count": len(entries),
        "regression_proof_count": len(proofs),
        "frozen_authority_flag_count": len(REQUIRED_FLAGS),
    }
    metrics = artifact.get("report_metrics", {})
    for metric, expected in expected_metrics.items():
        results.append(_check(
            metrics.get(metric) == expected,
            f"report_metrics.{metric} must be {expected} (got {metrics.get(metric)!r})",
        ))

    # 16. Static/source proof: only the two A2-owned paths changed vs the base.
    allowed_paths = {str(A2_REL), str(GUARD_REL)}
    rc, diff_out = _git(repo_root, ["diff", "--name-only", EXPECTED_BASE_SHA, "HEAD", "--"])
    results.append(_check(rc == 0, "git diff against the exact base failed"))
    rc2, status_out = _git(repo_root, ["status", "--porcelain"])
    results.append(_check(rc2 == 0, "git status --porcelain failed"))
    changed = set(diff_out.splitlines()) if diff_out else set()
    for line in status_out.splitlines():
        if not line.strip():
            continue
        path = line[3:].strip().strip('"')
        if "__pycache__" in path or path.endswith(".pyc"):
            continue
        changed.add(path)
    results.append(_check(
        changed <= allowed_paths and bool(changed),
        f"changed paths must be exactly the two A2-owned files (got {sorted(changed)})",
    ))

    # 17. Static scan: no authority/lease/mutation implementation in aota_forge.
    aota_pkg = repo_root / "aota_forge"
    impl_hits: list[str] = []
    if aota_pkg.is_dir():
        for py in sorted(aota_pkg.rglob("*.py")):
            text = py.read_text(encoding="utf-8")
            rel = str(py.relative_to(repo_root))
            for pattern, matcher in (
                ("class", IMPL_CLASS_RE),
                ("function", IMPL_FUNC_RE),
                ("import", IMPL_IMPORT_RE),
            ):
                for match in matcher.finditer(text):
                    impl_hits.append(f"{rel}:{pattern} {match.group(0)}")
        catalog_text = (aota_pkg / "core" / "catalog.py").read_text(encoding="utf-8")
        for match in IMPL_OP_RE.finditer(catalog_text):
            impl_hits.append(f"aota_forge/core/catalog.py:operation {match.group(0)}")
    else:
        results.append(_check(False, "aota_forge package not found under repo root"))
    results.append(_check(not impl_hits, f"authority/lease/mutation implementation surface found: {impl_hits}"))

    # 18. M2 placeholder seams unchanged (no silent source repair).
    for rel_marker, marker in M2_PLACEHOLDER_MARKERS:
        source_file = aota_pkg / rel_marker
        results.append(_check(
            source_file.is_file() and marker in source_file.read_text(encoding="utf-8"),
            f"M2 placeholder seam missing (would mean silent fix): {marker!r} in {rel_marker}",
        ))
    results.append(_check(
        (repo_root / A0_MAP_REL).is_file() and (repo_root / A0_GUARD_REL).is_file()
        and (repo_root / MATRIX_REL).is_file(),
        "A0 input map / A0 guard / M2 matrix must exist under the repo root",
    ))

    return results


def summarize(artifact: dict, results: list[tuple[bool, str]]) -> dict:
    passed = sum(1 for ok, _ in results if ok)
    metrics = artifact.get("report_metrics", {})
    return {
        "artifact": "m3-a2-authority-lease",
        "base_sha": artifact.get("base_sha"),
        "check_total": len(results),
        "check_passed": passed,
        "verdict": "PASS" if passed == len(results) else "FAIL",
        "resolved_design_question_count": metrics.get("resolved_design_question_count"),
        "deferred_question_count": metrics.get("deferred_question_count"),
        "id_category_count": metrics.get("id_category_count"),
        "principal_class_count": metrics.get("principal_class_count"),
        "decision_class_count": metrics.get("decision_class_count"),
        "lease_canonical_field_count": metrics.get("lease_canonical_field_count"),
        "error_semantics_entry_count": metrics.get("error_semantics_entry_count"),
        "regression_proof_count": metrics.get("regression_proof_count"),
        "frozen_authority_flag_count": metrics.get("frozen_authority_flag_count"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="M3-A2 authority/lease design freeze guard")
    parser.add_argument("--json", action="store_true", help="emit a machine JSON summary")
    parser.add_argument("--artifact", type=Path, help="path to the A2 artifact JSON")
    parser.add_argument("--repo-root", type=Path, help="repository root (default: script parent.parent)")
    args = parser.parse_args()

    repo_root = args.repo_root or Path(__file__).resolve().parent.parent
    repo_root = repo_root.resolve()
    artifact_path = args.artifact or repo_root / A2_REL
    artifact_path = artifact_path.resolve()

    try:
        artifact = _read_json(artifact_path)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: artifact unreadable/invalid: {exc}")
        return 1
    try:
        a0_map = _read_json(repo_root / A0_MAP_REL)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: A0 input map unreadable/invalid: {exc}")
        return 1
    try:
        matrix = _read_json(repo_root / MATRIX_REL)
    except (FileNotFoundError, json.JSONDecodeError) as exc:
        print(f"GUARD FAIL: canonical matrix unreadable/invalid: {exc}")
        return 1

    results = run_checks(artifact, repo_root, a0_map, matrix)
    summary = summarize(artifact, results)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, sort_keys=True))
        return 0 if summary["verdict"] == "PASS" else 1

    print(f"artifact: {artifact_path.relative_to(repo_root)}")
    print(f"base SHA recorded: {artifact.get('base_sha')}")
    print(f"checks: {summary['check_passed']}/{summary['check_total']} PASS")
    for ok, message in results:
        print(f"  {'PASS' if ok else 'FAIL'}  {message}")
    print(f"VERDICT: {summary['verdict']}")
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
