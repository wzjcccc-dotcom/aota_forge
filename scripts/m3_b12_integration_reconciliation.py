#!/usr/bin/env python3
"""Cross-lane proof for the integrated M3-B1/M3-B2 ingress foundation."""

from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

RESULTS: list[tuple[str, bool, str]] = []


def check(name: str, condition: bool, detail: str = "") -> bool:
    passed = bool(condition)
    RESULTS.append((name, passed, detail[:400]))
    print(f"{'PASS' if passed else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))
    return passed


def _bind_fixture(name: str, handler: Callable[[Any], object]) -> None:
    from aota_forge.core.contracts.descriptor import OperationContractDescriptor
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY

    DEFAULT_REGISTRY.bind(
        OperationContractDescriptor(
            name=name,
            description="M3-B1/B2 integration reconciliation fixture",
            read_write="read",
        ),
        handler=handler,
    )


def _canonical_failure(payload: object, operation: str, code: str) -> bool:
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    audit = payload.get("audit")
    return (
        payload.get("ok") is False
        and payload.get("operation") == operation
        and payload.get("data") == {}
        and isinstance(error, dict)
        and error.get("code") == code
        and isinstance(error.get("message"), str)
        and isinstance(error.get("retryable"), bool)
        and payload.get("status") == "error"
        and payload.get("result") == "error"
        and isinstance(audit, dict)
        and payload.get("errors")
        and isinstance(payload.get("correlation_id"), str)
    )


def _flow_is_reconciled() -> bool:
    source = (REPO_ROOT / "aota_forge" / "core" / "ingress.py").read_text(encoding="utf-8")
    markers = (
        "validated = validate_inputs(descriptor, params)",
        "context = _RESOLVER.resolve(",
        "audit[\"principal\"] = {",
        "payload = handler(context)",
        "return _normalize_handler_result(operation, payload, cid, audit)",
    )
    positions = [source.find(marker) for marker in markers]
    return all(position >= 0 for position in positions) and positions == sorted(positions)


def main() -> int:
    from aota_forge.adapters.host import resources as host_resources
    from aota_forge.core import bind_trusted_context, execute
    from aota_forge.core.resources.resolver import TrustedResourceConfig

    trusted = bind_trusted_context(
        principal_id="integration-operator",
        principal_type="operator_debug",
        provenance="runtime_attestation",
        channel="integration_runtime",
        freshness="integration-1",
    )

    malformed_operation = "m3.b12.n1.malformed"
    _bind_fixture(malformed_operation, lambda _ctx: None)
    malformed = execute(malformed_operation, trusted_context=trusted)
    malformed_principal = malformed.get("audit", {}).get("principal", {})
    check(
        "B12-N1_trusted_principal_survives_malformed_result",
        _canonical_failure(malformed, malformed_operation, "FORGE_ERROR")
        and malformed.get("audit", {}).get("handler") == "handler_result_invalid"
        and malformed_principal.get("id") == "integration-operator"
        and malformed_principal.get("trust") == "trusted",
        json.dumps(malformed.get("audit", {}), sort_keys=True),
    )

    valid_operation = "m3.b12.n2.untrusted"
    _bind_fixture(valid_operation, lambda _ctx: {"data": {"normal": "ok"}})
    self_asserted = execute(valid_operation, principal="integration-operator")
    self_asserted_principal = self_asserted.get("audit", {}).get("principal", {})
    check(
        "B12-N2_self_asserted_principal_not_trusted",
        self_asserted.get("ok") is True
        and self_asserted.get("data") == {"normal": "ok"}
        and self_asserted_principal.get("id") == "unbound"
        and self_asserted_principal.get("trust") == "untrusted_input_ignored",
        json.dumps(self_asserted_principal, sort_keys=True),
    )

    logical_operation = "m3.b12.n3.logical_malformed"
    _bind_fixture(logical_operation, lambda _ctx: {"data": []})
    with tempfile.TemporaryDirectory(prefix="m3-b12-resource-") as temp_dir:
        root = Path(temp_dir)
        (root / "logical.pid").write_text(str(os.getpid()), encoding="utf-8")
        projection = host_resources.resolve_host_resource(
            "runtime_pidfile",
            "logical",
            TrustedResourceConfig({"runtime_pidfile": root}),
        )
        logical_context = bind_trusted_context(
            principal=trusted.principal,
            provenance=trusted.provenance,
            channel=trusted.channel,
            freshness=trusted.freshness,
            metadata={"pidfile_id": "logical"},
        )
        logical_result = execute(logical_operation, trusted_context=logical_context)
        logical_text = json.dumps(logical_result, ensure_ascii=False, sort_keys=True)
        check(
            "B12-N3_logical_resource_malformed_result_has_no_path_leak",
            projection.get("resolved") is True
            and _canonical_failure(logical_result, logical_operation, "FORGE_ERROR")
            and str(root) not in logical_text
            and '"path"' not in logical_text
            and logical_result.get("audit", {}).get("principal", {}).get("id")
            == "integration-operator",
            logical_text,
        )

    exception_operation = "m3.b12.n4.unexpected"
    secret = "/private/integration-handler-secret"

    def unexpected(_ctx: Any) -> object:
        raise RuntimeError(f"handler detail: {secret}")

    _bind_fixture(exception_operation, unexpected)
    exception_result = execute(exception_operation, trusted_context=trusted)
    exception_text = json.dumps(exception_result, ensure_ascii=False, sort_keys=True)
    check(
        "B12-N4_unexpected_exception_bounded_with_trusted_context",
        _canonical_failure(exception_result, exception_operation, "FORGE_ERROR")
        and exception_result.get("audit", {}).get("handler") == "internal_error"
        and secret not in exception_text
        and exception_result.get("audit", {}).get("principal", {}).get("id")
        == "integration-operator",
        exception_text,
    )

    readonly = execute("operations.list")
    check(
        "B12-N5_readonly_operation_without_lease_succeeds",
        readonly.get("ok") is True
        and "authority" not in readonly
        and "capability_lease" not in readonly
        and bool(readonly.get("data", {}).get("operations")),
        json.dumps(readonly.get("audit", {}), sort_keys=True),
    )

    normal = execute("runtime.status", {"pid": os.getpid()})
    check(
        "B12-N6_valid_normal_operation_preserved",
        normal.get("ok") is True
        and normal.get("operation") == "runtime.status"
        and normal.get("status") == "ok"
        and normal.get("result") == "ok"
        and isinstance(normal.get("data"), dict),
        json.dumps(normal.get("data", {}), sort_keys=True),
    )

    check("INGRESS_FLOW_ORDER_RECONCILED", _flow_is_reconciled())

    passed = all(result[1] for result in RESULTS)
    print(f"\nB2_PRE_HANDLER_CONTEXT_PRESERVED={'yes' if passed else 'no'}")
    print(f"B1_POST_HANDLER_NORMALIZATION_PRESERVED={'yes' if passed else 'no'}")
    print(f"INGRESS_SHARED_HOT_FILE_RECONCILED={'yes' if passed else 'no'}")
    print(f"TRUSTED_CONTEXT_PRINCIPAL_SUPPORTED={'yes' if passed else 'no'}")
    print(f"PRINCIPAL_INCLUDED_IN_AUDIT={'yes' if passed else 'no'}")
    print(f"RAW_RESOLVED_PATH_MODEL_FACING={'no' if passed else 'yes'}")
    print(f"ALL_HANDLER_RESULTS_NORMALIZED={'yes' if passed else 'no'}")
    print(f"UNCAUGHT_HANDLER_RESULT_SHAPE_ERROR={'no' if passed else 'yes'}")
    print(f"HANDLER_INVOCATION_EXCEPTION_NORMALIZED={'yes' if passed else 'no'}")
    print(f"HANDLER_RESULT_SHAPE_EXCEPTION_NORMALIZED={'yes' if passed else 'no'}")
    print(f"CROSS_LANE_INTEGRATION_REGRESSION={'PASS' if passed else 'FAIL'}")
    print(f"B12_NEGATIVE_REVERSION_PROOF={'PASS' if passed else 'FAIL'}")
    return 0 if passed else 1


if __name__ == "__main__":
    raise SystemExit(main())
