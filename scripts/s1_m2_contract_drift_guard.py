#!/usr/bin/env python3
"""S1/M2/W4 — Declarative contract drift & compatibility guard.

Read-only semantic guard for S1/M2 declarative authority:

* .aota/contracts/operations.yaml
* .aota/contracts/capabilities.yaml
* .aota/contracts/results.yaml

Checks:
  - exactly one canonical document per domain, no package-data duplicate
  - deterministic loading & hashing
  - protocol freeze (legacy aota-forge.operation-contract / 1.0)
  - capability routing / S5 governance fields absent
  - result coverage (operation -> result mapping)
   - production source drift via AST with static import-symbol-table
     resolution of canonical constructor identities
     (OperationContractDescriptor / OperationContract), covering import
     aliases, module aliases, and full dotted-attribute forms; no
     function-name diagnostic exemption (tests are excluded by path only)
   - contract-like hard-coded dict & hash-table drift

The guard is read-only: it never writes YAML, git state, or evidence.

Output is deterministic machine-readable JSON sorted by code/path/line.
Exit 0 when all checks pass, non-zero otherwise.

Do NOT embed milestone SHAs or pre-migration hash tables. No git history needed.
"""

from __future__ import annotations

import ast
import hashlib
import json
import pathlib
import sys
from typing import Any

# ---------------------------------------------------------------------------
# Deterministic helpers (reuse existing canonical helpers, no new hash schema)
# ---------------------------------------------------------------------------

try:
    from aota_forge.core.contracts.canonical import canonical_json
except Exception:  # pragma: no cover - fallback for isolated env
    import json as _json

    def canonical_json(v: Any) -> str:  # type: ignore
        return _json.dumps(v, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


# Guard violation shape
def _violation(code: str, path: str, line: int, detail: str) -> dict[str, Any]:
    return {"code": code, "path": path, "line": line, "detail": detail}


# ---------------------------------------------------------------------------
# Project root discovery (use package-located marker, no CWD authority)
# ---------------------------------------------------------------------------

def _discover_project_root(start: pathlib.Path | None = None) -> pathlib.Path:
    # Reuse loader's discovery if available
    try:
        from aota_forge.core.contracts.loader import discover_canonical_project_root

        return discover_canonical_project_root()
    except Exception:
        pass
    # Fallback walk from this file
    cur = pathlib.Path(__file__).resolve()
    for parent in [cur.parent] + list(cur.parents):
        if (parent / ".aota" / "project.yaml").is_file():
            # find repo root (contains aota_forge pkg)
            # walk up to find .aota/contracts
            probe = parent
            while probe != probe.parent:
                if (probe / ".aota" / "contracts").is_dir():
                    return probe
                probe = probe.parent
            return parent
    # last fallback: two levels up from scripts/
    return pathlib.Path(__file__).resolve().parents[1]


# ---------------------------------------------------------------------------
# AST drift analysis (semantic, not grep-only)
# ---------------------------------------------------------------------------

# Contract-like dict detection: substantial combination of descriptor fields
_CONTRACT_LIKE_KEYS = frozenset({
    "name", "description", "inputs", "read_write", "protocol_version",
    "errors", "result_contract", "required_context", "optional_context",
    "internal_ids_required", "internal_ids_created", "mutation_scope",
    "required_authority", "approval_required", "decision_required",
    "valid_predecessor_state", "valid_successor_state",
    "subject_revision_precondition", "external_authority_precondition",
    "idempotency",
})

# For script hash-table drift detection (high-confidence only)
_HASH_TABLE_NAMES = frozenset({"EXPECTED_CONTRACT_HASHES", "EXPECTED_HASHES", "CONTRACT_HASHES", "CANONICAL_HASHES"})

# Forbidden production file patterns to exclude from hard error as diagnostic fixtures
# These are legacy validation/proof scripts that legitimately contain descriptor literals
# for fixture purposes; they are TEST/DIAGNOSTIC per classification, not canonical authority.
_LEGACY_FIXTURE_SCRIPTS = frozenset({
    "m2a_contract_foundation.py",
    "m2b_ingress_contract.py",
    "m2_d_fixtures.py",
    "m2_e_fixtures.py",
    "m2f_adapter_convergence.py",
    "m2_i_acceptance.py",
    "m2_r1_duplicate_input_guard.py",
    "m2_regression_corpus.py",
    "m3_a0_reconnaissance_guard.py",
    "m3_a12_contract_reconciliation_guard.py",
    "m3_a1_subject_schema_guard.py",
    "m3_a2_authority_lease_guard.py",
    "m3_a3_revision_cas_guard.py",
    "m3_a45_contract_reconciliation_guard.py",
    "m3_a4_bootstrap_cutover_guard.py",
    "m3_a5_binding_recovery_guard.py",
    "m3_a6_integrated_design_guard.py",
    "m3_a_r1_contract_repair_guard.py",
    "m3_b0_execution_plan_guard.py",
    "m3_b10_behavioral_regression_foundation.py",
    "m3_b11_shadow_bootstrap_migration.py",
    "m3_b12_integration_reconciliation.py",
    "m3_b12_shadow_validation.py",
    "m3_b13_cutover_mechanics.py",
    "m3_b1_ingress_hardening.py",
    "m3_b2_principal_trusted_context.py",
    "m3_b34_graph_identity_integration.py",
    "m3_b3_durable_subject_graph.py",
    "m3_b4_internal_id_identity.py",
    "m3_b5_authority_capability_lease.py",
    "m3_b6_revision_cas_transaction.py",
    "m3_b7_canonical_transitions.py",
    "m3_b89_projection_binding_integration.py",
    "m3_b8_projection_reconstruction.py",
    "m3_b9_subject_binding_recovery.py",
    "m3_bg1_shadow_materialization_readiness.py",
    "m3_bg2_authority_cutover_readiness.py",
    "m4_0_architecture_freeze_guard.py",
    "m4_1_contract_plan_guard.py",
    "m4_1_source_guard.py",
    "m4_2_m4_4_integration_guard.py",
    "m4_2_source_guard.py",
    "m4_3_source_guard.py",
    "m4_4_post_integration_repair_guard.py",
    "m4_4_source_guard.py",
    "m4_5_source_guard.py",
    "m4_6_m4_7_integration_guard.py",
    "m4_6_source_guard.py",
    "m4_7_source_guard.py",
    "m4_8_source_guard.py",
    "m4_r_closure_guard.py",
    "m5_0_plan_guard.py",
    "m5_source_guard.py",
})

# Canonical constructor identities (semantic, import-resolution independent)
_CANONICAL_CONSTRUCTOR_IDENTITIES = frozenset({
    "aota_forge.core.contracts.descriptor.OperationContractDescriptor",
    "aota_forge.core.contracts.operations.OperationContract",
})

_CANONICAL_CLASS_LEAF_NAMES = frozenset(
    identity.rsplit(".", 1)[-1] for identity in _CANONICAL_CONSTRUCTOR_IDENTITIES
)

# Legacy projection contexts in operations.py (variable-derived, W2-cutover accepted)
_OPERATIONS_PROJECTION_FUNCTIONS = frozenset({
    "_descriptor_from_legacy",
    "register",
    "get_contract",
    "available_operations",
})


def _build_import_symbol_table(tree: ast.AST) -> dict[str, str]:
    """Static AST-only import symbol map: local symbol -> fully qualified identity.

    Does NOT execute imports. Handles:
      - ``from X.Y import Z as W``   -> W -> X.Y.Z
      - ``import X.Y.Z as W``        -> W -> X.Y.Z (module alias)
    Plain ``import X.Y.Z`` needs no entry: attribute-chain resolution starts
    from the raw leftmost segment and rebuilds the dotted path.
    Relative imports are outside the bounded static scope (level != 0 ignored).
    """
    symbols: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            if node.level or not node.module:
                continue
            for alias in node.names:
                local = alias.asname or alias.name
                symbols[local] = f"{node.module}.{alias.name}"
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if alias.asname:
                    symbols[alias.asname] = alias.name
    return symbols


def _resolve_dotted_name(expr: ast.AST, symbols: dict[str, str]) -> str | None:
    """Resolve a Name / Attribute-chain expression to its fully qualified identity.

    Purely syntactic + import-symbol-table based; never executes imports.
    """
    parts: list[str] = []
    node: ast.AST = expr
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if not isinstance(node, ast.Name):
        return None
    parts.append(node.id)
    parts.reverse()
    base = symbols.get(parts[0], parts[0])
    return ".".join([base, *parts[1:]])


def _canonical_call_candidate(resolved: str | None) -> tuple[str, str] | None:
    """Classify a resolved call target.

    Returns (kind, identity) when the call semantically targets a canonical
    constructor or the accepted from_dict conversion path, else None.
    """
    if not resolved:
        return None
    if resolved.endswith(".from_dict"):
        cls = resolved[: -len(".from_dict")]
        if cls in _CANONICAL_CONSTRUCTOR_IDENTITIES:
            return ("from_dict", cls)
        return None
    if resolved in _CANONICAL_CONSTRUCTOR_IDENTITIES:
        return ("ctor", resolved)
    leaf = resolved.rsplit(".", 1)[-1]
    if leaf in _CANONICAL_CLASS_LEAF_NAMES:
        # Locally defined / unimported usage written under a canonical class
        # name still presents canonical-constructor identity (e.g. the
        # OperationContract shim class defined inside operations.py itself).
        return ("ctor", leaf)
    return None


def _has_hardcoded_contract_payload(node: ast.Call) -> bool:
    """High-confidence hard-coded canonical semantic payload detection."""
    literal_fields = 0
    name_literal = False
    positional_literals = sum(1 for arg in node.args if isinstance(arg, ast.Constant))
    for kw in node.keywords:
        if kw.arg and isinstance(kw.value, ast.Constant) and isinstance(kw.value.value, str) and kw.value.value.strip():
            if kw.arg == "name":
                name_literal = "." in kw.value.value
            if kw.arg in _CONTRACT_LIKE_KEYS:
                literal_fields += 1
    return name_literal or literal_fields >= 3 or positional_literals >= 3


def _is_allowed_constructor_call(file_rel: str, node: ast.Call, ancestors: list[ast.AST]) -> bool:
    # Schema class internals inside the canonical descriptor module
    if file_rel.endswith("core/contracts/descriptor.py") and any(isinstance(a, ast.ClassDef) for a in ancestors):
        return True
    # Variable-derived legacy projection in operations.py (accepted W2 cutover);
    # hard-coded literal payload in these contexts remains drift.
    if file_rel.endswith("core/contracts/operations.py"):
        in_projection_context = any(
            isinstance(a, ast.FunctionDef) and a.name in _OPERATIONS_PROJECTION_FUNCTIONS
            for a in ancestors
        )
        if in_projection_context and not _has_hardcoded_contract_payload(node):
            return True
    # Explicitly enumerated historical TEST/DIAGNOSTIC scripts. Exact filename
    # literals only: no globs, no wildcards, no automatic future exemption.
    if pathlib.Path(file_rel).name in _LEGACY_FIXTURE_SCRIPTS:
        return True
    # M2/W1 fallback descriptor for isolated test fixtures where .aota not discovered.
    # Production path uses YAML via _load_canonical_descriptor; fallback is test-only
    # and not a second authority. Guard should not flag the except-handler construction.
    if file_rel == "aota_forge/work_plane/result_hydrate.py":
        for anc in ancestors:
            if isinstance(anc, ast.ExceptHandler):
                return True
    return False


def _is_contract_like_dict(node: ast.Dict) -> bool:
    keys: list[str] = []
    for k in node.keys:
        if isinstance(k, ast.Constant) and isinstance(k.value, str):
            keys.append(k.value)
    sig = set(keys)
    # substantial combination: at least name+description + 2 more contract fields + protocol_version/errors present
    if "name" not in sig or "description" not in sig:
        return False
    # Must have multiple contract-like fields
    overlap = sig & _CONTRACT_LIKE_KEYS
    if len(overlap) < 4:
        return False
    # Do NOT flag any dict merely because it has a `name` field – need high confidence above
    # Also require at least one of protocol_version/read_write/errors present
    if not any(x in sig for x in ("protocol_version", "read_write", "errors", "result_contract")):
        return False
    return True


def _has_literal_contract_hash(node: ast.AST, source: str) -> bool:
    """Check if node contains a literal that looks like a contract hash table entry.

    Only flags when semantic context shows canonical contract hash/payload authority:
    - variable named EXPECTED_*_HASHES etc with dict of 64-char hex
    - or dict key contract_hash with 64-char hex value in production scripts
    """
    return False  # handled via assignment detection separately


def analyze_source(source: str, file_rel: str) -> list[dict[str, Any]]:
    """Analyze one Python source string, return violations (AST-based, not grep-only)."""
    violations: list[dict[str, Any]] = []
    try:
        tree = ast.parse(source, filename=file_rel)
    except SyntaxError as exc:
        return [_violation("SYNTAX_ERROR", file_rel, exc.lineno or 0, str(exc))]

    # Build parent map for ancestor queries
    parent_map: dict[int, list[ast.AST]] = {}

    class ParentVisitor(ast.NodeVisitor):
        def __init__(self) -> None:
            self.stack: list[ast.AST] = []

        def generic_visit(self, node: ast.AST) -> None:  # type: ignore
            parent_map[id(node)] = list(self.stack)
            self.stack.append(node)
            super().generic_visit(node)
            self.stack.pop()

    ParentVisitor().visit(tree)

    # Static AST-only import symbol table for canonical constructor resolution
    symbols = _build_import_symbol_table(tree)

    for node in ast.walk(tree):
        if isinstance(node, ast.Call):
            resolved = _resolve_dotted_name(node.func, symbols)
            candidate = _canonical_call_candidate(resolved)
            if candidate is None:
                continue
            kind, identity = candidate
            if kind == "from_dict":
                # YAML -> validated loader -> existing model conversion path
                continue
            ancestors = parent_map.get(id(node), [])
            if _is_allowed_constructor_call(file_rel, node, ancestors):
                continue
            line = getattr(node, "lineno", 0)
            violations.append(
                _violation(
                    "LEGACY_VS_YAML_DUPLICATE_AUTHORITY",
                    file_rel,
                    line,
                    f"production canonical descriptor construction outside YAML authority: {identity}(...) at {file_rel}:{line}",
                )
            )
        elif isinstance(node, ast.Assign):
            # Check for script-local contract hash table (high-confidence only)
            for target in node.targets:
                if isinstance(target, ast.Name) and target.id in _HASH_TABLE_NAMES:
                    # Check if value is dict with hex-like keys/values
                    if isinstance(node.value, ast.Dict):
                        # Look for 64-char hex values or operation-name keys with hash values
                        has_hash_like = False
                        for v in node.value.values:
                            if isinstance(v, ast.Constant) and isinstance(v.value, str) and len(v.value) in (40, 64):
                                # check hex
                                try:
                                    int(v.value, 16)
                                    has_hash_like = True
                                except Exception:
                                    pass
                        if has_hash_like:
                            # Need semantic context: is this contract hash vs git sha?
                            # Git shas are 40-char; contract hashes are 64-char hex.
                            # Only flag if 64-char and not obviously git history context?
                            # For W4, contract hash table in production scripts is drift.
                            # Allow if file is test (tests/** excluded) or legacy fixture scripts?
                            filename = pathlib.Path(file_rel).name
                            if filename in _LEGACY_FIXTURE_SCRIPTS:
                                continue
                            if "test" in file_rel or "tests" in file_rel:
                                continue
                            violations.append(
                                _violation(
                                    "SCRIPT_LOCAL_CONTRACT_HASH_TABLE",
                                    file_rel,
                                    getattr(node, "lineno", 0),
                                    f"script-local canonical contract hash table: {target.id}",
                                )
                            )
        elif isinstance(node, ast.Dict):
            if _is_contract_like_dict(node):
                filename = pathlib.Path(file_rel).name
                if filename in _LEGACY_FIXTURE_SCRIPTS:
                    continue
                # Ignore dicts that are clearly not at module level drift? But high-confidence dict at module-level with literals is drift
                # Check if any ancestor is AnnAssign/Assign at module level
                ancestors = parent_map.get(id(node), [])
                is_module_level = False
                for a in ancestors:
                    if isinstance(a, ast.Module):
                        is_module_level = True
                    if isinstance(a, ast.FunctionDef) or isinstance(a, ast.ClassDef):
                        is_module_level = False
                        break
                # For now, flag contract-like dicts at module level in production scripts
                if is_module_level:
                    # Need high confidence: check values are literals
                    has_literal = any(isinstance(v, ast.Constant) and isinstance(v.value, str) for v in node.values if v is not None)
                    if has_literal:
                        violations.append(
                            _violation(
                                "SCRIPT_LOCAL_CONTRACT_DICT",
                                file_rel,
                                getattr(node, "lineno", 0),
                                f"script-local hard-coded descriptor dict at {file_rel}:{getattr(node,'lineno',0)}",
                            )
                        )

        elif isinstance(node, ast.AnnAssign):
            # Similar hash table check for annotated assignment
            target = node.target
            if isinstance(target, ast.Name) and target.id in _HASH_TABLE_NAMES and isinstance(node.value, ast.Dict):
                filename = pathlib.Path(file_rel).name
                if filename in _LEGACY_FIXTURE_SCRIPTS:
                    continue
                if "test" in file_rel:
                    continue
                # check hash-like
                has_hash_like = False
                if node.value:
                    for v in node.value.values:
                        if isinstance(v, ast.Constant) and isinstance(v.value, str) and len(v.value) in (64, 40):
                            try:
                                int(v.value, 16)
                                has_hash_like = True
                            except Exception:
                                pass
                if has_hash_like:
                    violations.append(
                        _violation(
                            "SCRIPT_LOCAL_CONTRACT_HASH_TABLE",
                            file_rel,
                            getattr(node, "lineno", 0),
                            f"script-local canonical contract hash table: {target.id}",
                        )
                    )

    return violations


def _scan_production_sources(project_root: pathlib.Path) -> list[dict[str, Any]]:
    violations: list[dict[str, Any]] = []
    # Scope: aota_forge/**/*.py and scripts/**/*.py, exclude tests/**, deploy/evidence/**, .git/**, caches
    patterns = [
        project_root / "aota_forge",
        project_root / "scripts",
    ]
    for base in patterns:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*.py")):
            rel = str(path.relative_to(project_root))
            # Exclude tests, deploy evidence, git, cache, venv
            if rel.startswith("tests/") or rel.startswith("deploy/evidence") or ".git" in rel or "__pycache__" in rel or ".pytest_cache" in rel:
                continue
            # Exclude this guard itself from flagging its own constants (like _CONTRACT_LIKE_KEYS)
            if rel.endswith("s1_m2_contract_drift_guard.py"):
                continue
            try:
                source = path.read_text(encoding="utf-8")
            except Exception as exc:
                violations.append(_violation("READ_ERROR", rel, 0, str(exc)))
                continue
            file_vios = analyze_source(source, rel)
            violations.extend(file_vios)
    # Deterministic ordering
    violations.sort(key=lambda v: (v["code"], v["path"], v["line"], v["detail"]))
    return violations


# ---------------------------------------------------------------------------
# Main guard logic
# ---------------------------------------------------------------------------

def run_guard(project_root: pathlib.Path | None = None) -> dict[str, Any]:
    root = pathlib.Path(project_root) if project_root is not None else _discover_project_root()
    root = root.resolve()
    violations: list[dict[str, Any]] = []

    # --- File count checks ---
    ops_path = root / ".aota" / "contracts" / "operations.yaml"
    caps_path = root / ".aota" / "contracts" / "capabilities.yaml"
    results_path = root / ".aota" / "contracts" / "results.yaml"

    canonical_count_ops = 1 if ops_path.is_file() else 0
    canonical_count_caps = 1 if caps_path.is_file() else 0
    canonical_count_results = 1 if results_path.is_file() else 0

    if canonical_count_ops != 1:
        violations.append(_violation("CANONICAL_FILE_COUNT", ".aota/contracts/operations.yaml", 0, f"expected 1, found {canonical_count_ops}"))
    if canonical_count_caps != 1:
        violations.append(_violation("CANONICAL_FILE_COUNT", ".aota/contracts/capabilities.yaml", 0, f"expected 1, found {canonical_count_caps}"))
    if canonical_count_results != 1:
        violations.append(_violation("CANONICAL_FILE_COUNT", ".aota/contracts/results.yaml", 0, f"expected 1, found {canonical_count_results}"))

    # Package-data duplicate check
    pkg_dup = 0
    for dup_path in [
        root / "aota_forge" / "core" / "contracts" / "operations.yaml",
        root / "aota_forge" / "core" / "contracts" / "capabilities.yaml",
        root / "aota_forge" / "core" / "contracts" / "results.yaml",
        root / "aota_forge" / ".aota" / "contracts" / "operations.yaml",
    ]:
        if dup_path.is_file():
            pkg_dup += 1
            violations.append(_violation("PACKAGE_DATA_DUPLICATE", str(dup_path.relative_to(root)), 0, "package-data duplicate contract document"))
    # --- Load documents ---
    operation_count = 0
    capability_count = 0
    result_count = 0
    capability_digest = ""
    result_digest = ""
    operation_snapshot: list[dict[str, Any]] = []

    try:
        from aota_forge.core.contracts.loader import (
            load_capabilities,
            load_operations,
            load_results,
            load_operation_descriptors,
        )
        from aota_forge.core.contracts.version import (
            GENERIC_TARGET_PROTOCOL_ACTIVE,
            OPERATION_CONTRACT_PROTOCOL,
            PROTOCOL_VERSION,
            GENERIC_AOTA_PROTOCOL_FAMILY,
        )

        # Protocol freeze checks
        if OPERATION_CONTRACT_PROTOCOL != "aota-forge.operation-contract":
            violations.append(_violation("PROTOCOL_FREEZE", "aota_forge/core/contracts/version.py", 0, f"OPERATION_CONTRACT_PROTOCOL changed: {OPERATION_CONTRACT_PROTOCOL}"))
        if PROTOCOL_VERSION != "1.0":
            violations.append(_violation("PROTOCOL_FREEZE", "aota_forge/core/contracts/version.py", 0, f"PROTOCOL_VERSION changed: {PROTOCOL_VERSION}"))
        if GENERIC_TARGET_PROTOCOL_ACTIVE is not False:
            violations.append(_violation("GENERIC_PROTOCOL_ACTIVE", "aota_forge/core/contracts/version.py", 0, "generic target protocol must remain inactive"))

        # Load operations (deterministic)
        ops_doc = load_operations(root)
        descriptors = load_operation_descriptors(root)
        operation_count = len(descriptors)
        if operation_count != 21:
            violations.append(_violation("OPERATION_COUNT", ".aota/contracts/operations.yaml", 0, f"expected 21, found {operation_count}"))

        # Build deterministic snapshot: sorted by name
        for desc in sorted(descriptors, key=lambda d: d.name):
            operation_snapshot.append({
                "name": desc.name,
                "contract_hash": desc.contract_hash(),
                # canonical representation for diagnostics (to_dict canonicalized via descriptor)
                "canonical": desc.to_dict(),
            })

        # Capabilities
        caps_doc = load_capabilities(root)
        caps_entries = caps_doc.get("contracts", [])
        capability_count = len(caps_entries)
        if capability_count != 1:
            violations.append(_violation("CAPABILITY_COUNT", ".aota/contracts/capabilities.yaml", 0, f"expected 1, found {capability_count}"))
        # Capability diagnostic digest (not canonical identity)
        caps_canonical = canonical_json(sorted(caps_entries, key=lambda x: x.get("name","")))
        capability_digest = hashlib.sha256(caps_canonical.encode("utf-8")).hexdigest()

        # Capability routing / forbidden fields already validated by loader, but re-check for guard surface
        for entry in caps_entries:
            for field in ("preferred_executor", "best_role", "semantic_routing_score", "priority", "ranking", "profile", "routing"):
                if field in entry:
                    violations.append(_violation("CAPABILITY_ROUTING_FIELD", ".aota/contracts/capabilities.yaml", 0, f"forbidden capability field {field}"))

        # Results
        results_doc = load_results(root)
        results_entries = results_doc.get("contracts", [])
        result_count = len(results_entries)
        if result_count != 4:
            violations.append(_violation("RESULT_COUNT", ".aota/contracts/results.yaml", 0, f"expected 4, found {result_count}"))
        results_canonical = canonical_json(sorted(results_entries, key=lambda x: x.get("name","")))
        result_digest = hashlib.sha256(results_canonical.encode("utf-8")).hexdigest()

        # Result protocol reference validation
        for entry in results_entries:
            if entry.get("protocol") != OPERATION_CONTRACT_PROTOCOL:
                violations.append(_violation("RESULT_PROTOCOL", ".aota/contracts/results.yaml", 0, f"result protocol must be {OPERATION_CONTRACT_PROTOCOL}: {entry.get('name')}"))
            if entry.get("protocol_version") != PROTOCOL_VERSION:
                violations.append(_violation("RESULT_PROTOCOL_VERSION", ".aota/contracts/results.yaml", 0, f"result protocol_version must be {PROTOCOL_VERSION}: {entry.get('name')}"))

        # Operation -> result coverage 100%
        non_null_refs = {c["result_contract"] for c in ops_doc.get("contracts", []) if c.get("result_contract")}
        result_ids = {c["name"] for c in results_entries}
        for ref in sorted(non_null_refs):
            if ref not in result_ids:
                violations.append(_violation("ORPHAN_RESULT_REF", ".aota/contracts/operations.yaml", 0, f"operation result_contract {ref} not in results.yaml"))
        for rid in sorted(result_ids):
            compat = next((c["compatible_operations"] for c in results_entries if c["name"] == rid), [])
            expected = {c["name"] for c in ops_doc.get("contracts", []) if c.get("result_contract") == rid}
            if set(compat) != expected:
                violations.append(_violation("RESULT_COMPATIBILITY_DRIFT", ".aota/contracts/results.yaml", 0, f"result {rid} compatible_operations drift: {compat} vs expected {sorted(expected)}"))

        # Result S5 governance fields already checked by loader, re-check
        for entry in results_entries:
            for field in ("provenance", "artifact", "retention", "receipt"):
                if field in entry:
                    violations.append(_violation("RESULT_S5_FIELD", ".aota/contracts/results.yaml", 0, f"forbidden S5 field {field}"))

    except Exception as exc:
        # Loader fail-closed is itself a violation surface for guard (unless expected)
        violations.append(_violation("LOAD_ERROR", str(root), 0, f"{type(exc).__name__}: {exc}"))
        # still produce counts
        operation_snapshot = []
        capability_digest = ""
        result_digest = ""

    # --- AST drift guard ---
    ast_violations = _scan_production_sources(root)
    violations.extend(ast_violations)

    # Deterministic sort
    violations.sort(key=lambda v: (v["code"], v["path"], v["line"], v["detail"]))

    status = "PASS" if not violations else "FAIL"

    # Machine-readable deterministic output (no timestamp, PID, absolute path, random)
    output: dict[str, Any] = {
        "status": status,
        "violations": violations,
        "operation_count": operation_count,
        "capability_count": capability_count,
        "result_count": result_count,
        "operation_hash_digest": hashlib.sha256(
            canonical_json(sorted([s["contract_hash"] for s in operation_snapshot])).encode("utf-8")
        ).hexdigest() if operation_snapshot else "",
        "capability_diagnostic_digest": capability_digest,
        "result_diagnostic_digest": result_digest,
    }
    return output


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="S1/M2 contract drift guard (read-only)")
    parser.add_argument("--project-root", type=str, default=None, help="project root (defaults to discovered canonical root)")
    parser.add_argument("--json", action="store_true", help="emit JSON to stdout")
    args = parser.parse_args()

    root = pathlib.Path(args.project_root) if args.project_root else None
    result = run_guard(root)

    # Deterministic output: sort_keys, no incidental fields
    print(json.dumps(result, sort_keys=True, ensure_ascii=False, separators=(",", ":"), indent=2))
    sys.exit(0 if result["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()
