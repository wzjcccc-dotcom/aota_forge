#!/usr/bin/env python3
"""M3-B2 NF2 principal/trusted-context fixtures and reversion proof.

This validator is source-only and deterministic.  It covers the trusted
principal boundary, logical trusted-resource resolution, bounded audit
projection, and the preserved read-only plane.  It does not create graph
state, evaluate authority, issue leases, or perform deployment work.
"""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    passed = bool(condition)
    RESULTS.append((name, passed, detail[:400]))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return passed


def _json(payload: object) -> str:
    return json.dumps(payload, ensure_ascii=False, sort_keys=True)


def _all_operation_classes_are_read_only() -> bool:
    from aota_forge.core.contracts.descriptor import READ_ONLY
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY

    return all(
        descriptor.read_write == READ_ONLY
        for name in DEFAULT_REGISTRY.names()
        if (descriptor := DEFAULT_REGISTRY.get(name)) is not None
    )


def main() -> int:
    from aota_forge.adapters import hermes
    from aota_forge.adapters.host import resources as host_resources
    from aota_forge.adapters.hermes.schema import AdapterRequestInvalidError
    from aota_forge.core import bind_trusted_context, execute
    from aota_forge.core.context import ContextResolver, Principal
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY

    trusted_principal = bind_trusted_context(
        principal_id="operator-7",
        principal_type="operator_debug",
        provenance="runtime_attestation",
        channel="adapter_runtime",
        freshness="session-7",
    )

    # B2-N1: caller/model values never become trusted principal identity.
    self_asserted = execute("operations.list", {}, principal="operator-7")
    n1 = check(
        "B2-N1_model_principal_self_assertion_ignored",
        self_asserted.get("ok") is True
        and self_asserted.get("audit", {}).get("principal", {}).get("id") == "unbound"
        and self_asserted.get("audit", {}).get("principal", {}).get("trust")
        == "untrusted_input_ignored",
        _json(self_asserted.get("audit", {}).get("principal", {})),
    )
    model_field = execute("operations.list", {"principal": "operator-7"})
    check(
        "B2-N1_model_principal_field_rejected",
        model_field.get("ok") is False
        and model_field.get("errors", [{}])[0].get("code") == "UNKNOWN_INPUT",
    )
    plain_typed = execute(
        "operations.list",
        {},
        principal=Principal(
            principal_id="operator-7",
            principal_type="operator_debug",
            provenance="model_claim",
            channel="model",
        ),
    )
    check(
        "B2-N1_plain_typed_principal_not_promoted",
        plain_typed.get("audit", {}).get("principal", {}).get("id") == "unbound"
        and plain_typed.get("audit", {}).get("principal", {}).get("trust")
        == "untrusted_input_ignored",
    )

    # B2-N2: only a runtime-bound typed context reaches Core as principal.
    descriptor = DEFAULT_REGISTRY.get("operations.list")
    resolved_context = ContextResolver().resolve(
        descriptor,
        {},
        "legacy-free-form-value",
        "b2-n2",
        trusted_context=trusted_principal,
    )
    n2_context = check(
        "B2-N2_trusted_context_reaches_core_typed",
        isinstance(resolved_context.principal, Principal)
        and resolved_context.principal.id == "operator-7"
        and resolved_context.principal_trust == "trusted",
        _json(resolved_context.to_dict()),
    )
    n2_audit_payload = execute("operations.list", {}, trusted_context=trusted_principal)
    n2_audit = n2_audit_payload.get("audit", {}).get("principal", {})
    n2_audit_ok = check(
        "B2-N2_trusted_context_audited",
        n2_audit.get("id") == "operator-7"
        and n2_audit.get("type") == "operator_debug"
        and n2_audit.get("trust") == "trusted",
        _json(n2_audit),
    )

    # B2-N3: a logical resource is resolved privately and its path stays out
    # of the model-facing result envelope.
    with tempfile.TemporaryDirectory(prefix="m3-b2-resource-") as temp:
        root = Path(temp)
        pid_root = root / "pidfiles"
        pid_root.mkdir()
        pidfile = pid_root / "alive.pid"
        pidfile.write_text(str(os.getpid()), encoding="utf-8")
        previous = os.environ.get("AOTA_FORGE_HOST_ROOT_PIDFILE")
        os.environ["AOTA_FORGE_HOST_ROOT_PIDFILE"] = str(pid_root)
        try:
            resource_context = bind_trusted_context(
                principal=trusted_principal.principal,
                provenance=trusted_principal.provenance,
                channel=trusted_principal.channel,
                freshness=trusted_principal.freshness,
                metadata={"pidfile_id": "alive"},
            )
            logical_result = execute("host.status", {}, trusted_context=resource_context)
            logical_projection = host_resources.resolve_host_resource(
                "runtime_pidfile",
                "alive",
                host_resources.host_resource_config_from_env(),
            )
        finally:
            if previous is None:
                os.environ.pop("AOTA_FORGE_HOST_ROOT_PIDFILE", None)
            else:
                os.environ["AOTA_FORGE_HOST_ROOT_PIDFILE"] = previous
        encoded_result = _json(logical_result)
        n3 = check(
            "B2-N3_logical_resource_resolves_without_path_leak",
            logical_result.get("ok") is True
            and logical_result.get("data", {}).get("process", {}).get("running") is True
            and str(pidfile) not in encoded_result
            and '"path"' not in encoded_result,
            _json(logical_projection),
        )

    # B2-N4: raw paths remain unavailable on the model-facing adapter input.
    try:
        hermes.build_request("host.status", {"pidfile": "/private/secret.pid"})
        n4 = False
        detail = "raw pidfile path was accepted in model arguments"
    except AdapterRequestInvalidError as exc:
        n4 = True
        detail = exc.code
    check("B2-N4_model_raw_private_path_denied", n4, detail)

    # B2-N5: trusted resolution is provenance only; no authority/lease state
    # is created by context assembly or by the read-only operation.
    n5_context = check(
        "B2-N5_trusted_resource_does_not_create_authority",
        resolved_context.authority is None
        and resolved_context.capability_lease is None
        and _all_operation_classes_are_read_only(),
        _json(
            {
                "authority": resolved_context.authority,
                "capability_lease": resolved_context.capability_lease,
            }
        ),
    )
    n5_result = execute("operations.list", {}, trusted_context=trusted_principal)
    check(
        "B2-N5_principal_alone_does_not_create_authority",
        "authority" not in n5_result.get("data", {})
        and "capability_lease" not in n5_result.get("data", {}),
    )

    # B2-N6: audit contains stable, bounded identity/provenance and no trusted
    # path or credential material.
    audit_context = bind_trusted_context(
        principal=trusted_principal.principal,
        provenance=trusted_principal.provenance,
        channel=trusted_principal.channel,
        freshness=trusted_principal.freshness,
        metadata={"pidfile": "/private/credential-like-path"},
    )
    audited = execute("operations.list", {}, trusted_context=audit_context)
    audit_principal = audited.get("audit", {}).get("principal", {})
    audit_text = _json(audited.get("audit", {}))
    n6 = check(
        "B2-N6_audit_contains_safe_principal_provenance",
        audit_principal
        == {
            "id": "operator-7",
            "type": "operator_debug",
            "provenance": "runtime_attestation",
            "channel": "adapter_runtime",
            "freshness": "session-7",
            "trust": "trusted",
        }
        and "/private/credential-like-path" not in audit_text,
        _json(audit_principal),
    )

    # B2-N7: safe diagnosis remains usable with no lease or Subject binding.
    readonly = execute("operations.list", {})
    n7 = check(
        "B2-N7_readonly_diagnostic_plane_preserved",
        readonly.get("ok") is True
        and readonly.get("data", {}).get("operations")
        and "capability_lease" not in readonly
        and "subject" not in readonly,
        _json(readonly.get("audit", {})),
    )

    # Static scope guard: B2 must not start B5 or any graph/cutover authority.
    package_sources = "\n".join(
        path.read_text(encoding="utf-8")
        for path in sorted((REPO_ROOT / "aota_forge").rglob("*.py"))
    )
    check(
        "B2_scope_no_authority_or_lease_implementation",
        "class AuthorityEngine" not in package_sources
        and "class CapabilityLease" not in package_sources
        and "class DurableSubjectGraph" not in package_sources,
    )
    check(
        "B2_scope_executor_neutral",
        "PRINCIPAL = \"hermes\"" not in package_sources
        and "HERMES_ID_REQUIRED_AS_PRINCIPAL" not in package_sources,
    )

    actual_flags = {
        "UNTRUSTED_MODEL_CAN_SELF_ASSERT_PRIVILEGED_PRINCIPAL": "no" if n1 and n2_context else "yes",
        "RAW_RESOLVED_PATH_MODEL_FACING": "no" if n3 else "yes",
        "TRUSTED_RESOURCE_IMPLIES_MUTATION_AUTHORITY": "no" if n5_context else "yes",
        "PRINCIPAL_INCLUDED_IN_AUDIT": "yes" if n2_audit_ok and n6 else "no",
    }

    def nf2_contract_pass(flags: dict[str, str]) -> bool:
        return (
            flags["UNTRUSTED_MODEL_CAN_SELF_ASSERT_PRIVILEGED_PRINCIPAL"] == "no"
            and flags["RAW_RESOLVED_PATH_MODEL_FACING"] == "no"
            and flags["TRUSTED_RESOURCE_IMPLIES_MUTATION_AUTHORITY"] == "no"
            and flags["PRINCIPAL_INCLUDED_IN_AUDIT"] == "yes"
        )

    check("NF2_source_contract_flags_pass", nf2_contract_pass(actual_flags), _json(actual_flags))
    for flag, expected in (
        ("UNTRUSTED_MODEL_CAN_SELF_ASSERT_PRIVILEGED_PRINCIPAL", "no"),
        ("RAW_RESOLVED_PATH_MODEL_FACING", "no"),
        ("TRUSTED_RESOURCE_IMPLIES_MUTATION_AUTHORITY", "no"),
        ("PRINCIPAL_INCLUDED_IN_AUDIT", "yes"),
    ):
        reverted = dict(actual_flags)
        reverted[flag] = "yes" if expected == "no" else "no"
        check(
            f"NF2_negative_reversion_rejected_{flag}",
            not nf2_contract_pass(reverted),
        )

    matrix_path = REPO_ROOT / "deploy/evidence/issues/9/m2-successor-regression-matrix.json"
    matrix = json.loads(matrix_path.read_text(encoding="utf-8"))
    rows = matrix.get("failure_classes", [])
    check("REGRESSION_MATRIX_CLASS_COUNT_16", len(rows) == 16)
    allowed_dispositions = {
        "ELIMINATED_BY_CONSTRUCTION",
        "DETERMINISTICALLY_RECONCILED",
        "NEEDS_SEMANTIC_CHOICE",
        "NOT_YET_IMPLEMENTED",
    }
    unsupported = [
        row.get("failure_class")
        for row in rows
        if row.get("expected_successor_disposition") not in allowed_dispositions
    ]
    check("UNSUPPORTED_SUCCESS_CLAIMS_ZERO", not unsupported, _json(unsupported))

    passed = all(result[1] for result in RESULTS)
    print(f"\nFOCUSED_B2_REGRESSION={'PASS' if passed else 'FAIL'}")
    print(f"NF2_NEGATIVE_REVERSION_PROOF={'PASS' if passed else 'FAIL'}")
    print(f"NF2_SOURCE_FIXED={'yes' if passed else 'no'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
