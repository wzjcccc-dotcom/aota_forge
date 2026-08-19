#!/usr/bin/env python3
"""M2-A focused deterministic validation — Operation Contract Foundation.

Proves:

1. descriptor serializes successfully (valid canonical JSON)
2. JSON round-trip preserves semantics
3. handler is not serialized (descriptor is JSON-native, callable-free)
4. descriptor lookup requires no handler execution
5. same descriptors yield same contract_hash
6. different semantic descriptors yield different contract_hash
7. import/registration order does not alter descriptor hash
8. available operation order is deterministic
9. duplicate operation registration fails closed
10. missing operation lookup is deterministic
11. protocol_version exists and is explicit (not package version)
12. existing canonical M1 operation names remain representable
13. new canonical modules do not import the legacy control plane
14. contract_hash is stable across processes

Exit status: 0 on all PASS, 1 on any FAIL.
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

AOTA_FORGE_ROOT = Path(__file__).resolve().parent.parent / "aota_forge"
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AOTA_FORGE_ROOT.parent))

results: list[dict[str, object]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append({"check": name, "pass": bool(ok), "detail": detail[:400]})
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def contains_callable(value: object) -> bool:
    if callable(value):
        return True
    if isinstance(value, dict):
        return any(contains_callable(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(contains_callable(v) for v in value)
    return False


def run_python(source: str, args: list[str] | None = None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env["PYTHONDONTWRITEBYTECODE"] = "1"
    return subprocess.run(
        [sys.executable, "-c", source, *(args or [])],
        capture_output=True,
        text=True,
        cwd=str(WORKSPACE_ROOT),
        env=env,
        timeout=60,
    )


def main() -> int:
    from aota_forge.core.contracts.descriptor import (
        READ_ONLY,
        InputSpec,
        OperationContractDescriptor,
    )
    from aota_forge.core.contracts.registry import (
        DuplicateOperationRegistrationError,
        HandlerRegistry,
        UnknownOperationError,
    )
    from aota_forge.core.contracts.version import PROTOCOL_VERSION

    # 1-2. Serialization + round-trip.
    base = OperationContractDescriptor(
        name="demo.read",
        description="demonstrate canonical descriptor semantics",
        inputs=(InputSpec("registry_path", "str"), InputSpec("project_id", "str")),
        required_context=("workspace",),
        optional_context=("principal",),
        internal_ids_required=(),
        internal_ids_created=(),
        read_write=READ_ONLY,
        idempotency="read",
        errors=("PROJECT_NOT_FOUND", "PROJECT_AMBIGUOUS"),
    )
    try:
        canonical = base.to_canonical_json()
        parsed = json.loads(canonical)
        ok = isinstance(parsed, dict) and parsed == base.to_dict()
        check("descriptor_serializes_canonical_json", ok)
    except Exception as exc:  # noqa: BLE001
        check("descriptor_serializes_canonical_json", False, str(exc))

    rebuilt = OperationContractDescriptor.from_dict(base.to_dict())
    check(
        "json_round_trip_preserves_semantics",
        rebuilt.to_dict() == base.to_dict() and rebuilt.contract_hash() == base.contract_hash(),
    )

    required_semantics = {
        "name", "description", "inputs", "required_context", "optional_context",
        "internal_ids_required", "internal_ids_created", "read_write",
        "mutation_scope", "required_authority", "approval_required",
        "valid_predecessor_state", "valid_successor_state", "idempotency",
        "errors", "protocol_version",
    }
    check("descriptor_minimum_semantics_present", required_semantics <= set(base.to_dict()))

    # 3. Handler is not serialized; 4. lookup requires no handler execution.
    bound = HandlerRegistry()
    bound.bind(base, handler=lambda ctx: {"data": {}})
    serialized = json.dumps(base.to_dict(), ensure_ascii=False)
    check(
        "handler_not_serialized",
        "<lambda" not in serialized and "function" not in serialized,
    )
    check("descriptor_carries_no_callable", not contains_callable(base.to_dict()))
    check(
        "handler_bound_only_in_registry",
        callable(bound.handler("demo.read")) and bound.get("demo.read") is base,
    )
    no_handler = HandlerRegistry()
    no_handler.bind(OperationContractDescriptor(name="op.nohandler", description="no handler", read_write=READ_ONLY))
    check(
        "descriptor_lookup_without_handler",
        no_handler.get("op.nohandler") is not None and no_handler.handler("op.nohandler") is None,
    )

    # 5. Same descriptors yield same hash (declaration/input order insensitive).
    twin = OperationContractDescriptor(
        name="demo.read",
        description="demonstrate canonical descriptor semantics",
        inputs=(InputSpec("project_id", "str"), InputSpec("registry_path", "str")),
        required_context=("workspace",),
        optional_context=("principal",),
        internal_ids_required=(),
        internal_ids_created=(),
        read_write=READ_ONLY,
        idempotency="read",
        errors=("PROJECT_NOT_FOUND", "PROJECT_AMBIGUOUS"),
    )
    check("same_descriptors_same_hash", base.contract_hash() == twin.contract_hash())

    # 6. Different semantic descriptors yield different hash.
    mutations = [
        OperationContractDescriptor(
            name="demo.read",
            description="different description",
            inputs=(InputSpec("registry_path", "str"), InputSpec("project_id", "str")),
            read_write=READ_ONLY,
        ),
        OperationContractDescriptor(
            name="demo.read",
            description="demonstrate canonical descriptor semantics",
            inputs=(InputSpec("registry_path", "str"), InputSpec("project_id", "str")),
            read_write="write",
            required_context=(),
            optional_context=(),
            mutation_scope="subject",
            required_authority="subject_mutation",
            approval_required=False,
            decision_required=False,
            valid_predecessor_state="open",
            valid_successor_state="completed",
            subject_revision_precondition=True,
            external_authority_precondition=False,
            idempotency="same intent replays; changed intent conflicts",
            result_contract="canonical_mutation_result.v1",
            errors=("CONFLICT",),
            protocol_version=PROTOCOL_VERSION,
        ),
        OperationContractDescriptor(
            name="demo.other",
            description="demonstrate canonical descriptor semantics",
            inputs=(InputSpec("registry_path", "str"), InputSpec("project_id", "str")),
            read_write=READ_ONLY,
        ),
    ]
    check(
        "different_semantics_different_hash",
        len({base.contract_hash(), *(m.contract_hash() for m in mutations)}) == 4,
    )

    # 7. Registration order does not alter descriptor hash.
    order_a = HandlerRegistry()
    order_b = HandlerRegistry()
    alpha = OperationContractDescriptor(name="op.alpha", description="alpha", read_write=READ_ONLY)
    beta = OperationContractDescriptor(name="op.beta", description="beta", read_write=READ_ONLY)
    order_a.bind(beta)
    order_a.bind(alpha)
    order_b.bind(alpha)
    order_b.bind(beta)
    check(
        "registration_order_hash_stable",
        order_a.get("op.alpha").contract_hash() == order_b.get("op.alpha").contract_hash()
        and order_a.get("op.beta").contract_hash() == order_b.get("op.beta").contract_hash(),
    )

    # 8. Available order deterministic.
    check(
        "available_operation_order_deterministic",
        order_a.available() == order_b.available()
        and [item["operation"] for item in order_a.available()] == ["op.alpha", "op.beta"],
    )

    # 9. Duplicate registration fails closed.
    dup = HandlerRegistry()
    dup.bind(alpha)
    try:
        dup.bind(alpha)
        check("duplicate_registration_fails_closed", False, "no exception raised")
    except DuplicateOperationRegistrationError:
        check("duplicate_registration_fails_closed", True)

    # 10. Missing operation deterministic.
    missing = HandlerRegistry()
    check(
        "missing_operation_deterministic",
        missing.get("op.nope") is None and missing.handler("op.nope") is None,
    )
    try:
        missing.require("op.nope")
        check("missing_operation_require_raises", False, "no exception raised")
    except UnknownOperationError:
        check("missing_operation_require_raises", True)

    # 11. Protocol version explicit, not package version.
    import aota_forge
    check("protocol_version_exists", isinstance(PROTOCOL_VERSION, str) and bool(PROTOCOL_VERSION), PROTOCOL_VERSION)
    check(
        "protocol_version_not_package_version",
        PROTOCOL_VERSION != aota_forge.__version__,
        f"protocol={PROTOCOL_VERSION} package={aota_forge.__version__}",
    )
    check("descriptor_carries_protocol_version", base.protocol_version == PROTOCOL_VERSION)

    # 12. Existing canonical M1 operation names remain representable.
    import aota_forge.core  # noqa: F401  (registers M1 canonical operations via the legacy shim)
    from aota_forge.core.contracts.operations import available_operations, get_contract
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY

    m1_names = ("project.resolve", "git.inspect", "runtime.status", "host.status", "operations.list")
    registered = set(DEFAULT_REGISTRY.names())
    check(
        "m1_operation_names_representable",
        all(name in registered for name in m1_names)
        and all(get_contract(name) is not None for name in m1_names),
    )
    legacy_ops = available_operations()
    check(
        "m1_available_shape_preserved",
        [op["operation"] for op in legacy_ops] == sorted(m1_names)
        and all(set(op) == {"operation", "description", "read_only"} for op in legacy_ops)
        and all(op["read_only"] is True for op in legacy_ops),
    )
    descriptor = DEFAULT_REGISTRY.get("project.resolve")
    check("m1_legacy_descriptor_hash_stable", descriptor.contract_hash() == descriptor.contract_hash())
    check(
        "m1_legacy_descriptor_semantics",
        descriptor.protocol_version == PROTOCOL_VERSION
        and descriptor.read_write == READ_ONLY
        and descriptor.idempotency == "read",
    )

    solo = HandlerRegistry()
    solo_desc = OperationContractDescriptor(name="op.solo", description="solo", read_write=READ_ONLY)
    solo.bind(solo_desc)
    solo_hash = solo_desc.contract_hash()
    solo.bind(OperationContractDescriptor(name="op.extra", description="extra", read_write=READ_ONLY))
    check("registry_state_not_in_hash", solo.get("op.solo").contract_hash() == solo_hash)

    # 13. New canonical modules do not import the legacy control plane.
    for module_file in ("descriptor.py", "registry.py", "version.py", "operations.py"):
        text = (AOTA_FORGE_ROOT / "core" / "contracts" / module_file).read_text(encoding="utf-8")
        violations = [
            line for line in text.splitlines()
            if line.strip().startswith(("import ", "from "))
            and any(needle in line for needle in ("handlers", "ingress", "contracts.operations", "core.context"))
        ]
        check(f"new_core_imports_legacy_control_plane_no_{module_file}", not violations, "; ".join(violations))

    # Runtime isolation proof: fresh process, aota_forge.core pre-loaded as a
    # stub so its M1 __init__ (which imports handlers) never runs while the
    # new canonical modules import.
    guard = run_python(
        "import sys, types, aota_forge;"
        "stub = types.ModuleType('aota_forge.core');"
        "stub.__path__ = [aota_forge.__path__[0] + '/core'];"
        "sys.modules['aota_forge.core'] = stub;"
        "import aota_forge.core.contracts.descriptor;"
        "import aota_forge.core.contracts.registry;"
        "import aota_forge.core.contracts.version;"
        "leaked = [m for m in sys.modules if 'handlers' in m or 'ingress' in m or 'contracts.operations' in m];"
        "print('ok' if not leaked else repr(leaked));"
        "sys.exit(1 if leaked else 0)"
    )
    check("new_modules_runtime_isolated", guard.returncode == 0 and guard.stdout.strip() == "ok", guard.stderr.strip()[:200])

    # 14. Contract hash stable across processes (reversed input declaration order).
    sub_source = (
        "import sys; sys.path.insert(0, sys.argv[1]);"
        "from aota_forge.core.contracts.descriptor import OperationContractDescriptor as D, InputSpec as I;"
        "from aota_forge.core.contracts.version import PROTOCOL_VERSION;"
        "d = D(name='demo.read', description='demonstrate canonical descriptor semantics',"
        "inputs=(I('project_id','str'), I('registry_path','str')), required_context=('workspace',),"
        "optional_context=('principal',), read_write='read', idempotency='read',"
        "errors=('PROJECT_NOT_FOUND','PROJECT_AMBIGUOUS'), protocol_version=PROTOCOL_VERSION);"
        "print(d.contract_hash())"
    )
    sub_proc = run_python(sub_source, [str(WORKSPACE_ROOT)])
    check(
        "cross_process_hash_stable",
        sub_proc.returncode == 0 and sub_proc.stdout.strip() == base.contract_hash(),
        sub_proc.stderr.strip()[:200],
    )

    passed = all(item["pass"] for item in results)
    print(f"\nM2-A CONTRACT FOUNDATION: {sum(1 for r in results if r['pass'])}/{len(results)} PASS")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
