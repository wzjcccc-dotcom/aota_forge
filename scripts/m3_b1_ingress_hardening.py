#!/usr/bin/env python3
"""Focused M3-B1 proof for I9-B008 canonical ingress hardening.

The fixture handlers exercise malformed and valid internal result shapes through
the same canonical ingress used by the Core.  A temporary source copy also
reintroduces the unsafe ``payload.get`` assumption to prove the regression
validator would fail on the pre-fix seam.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any, Callable


REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT))

results: list[dict[str, object]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append({"check": name, "pass": bool(ok), "detail": detail[:400]})
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def _serializable(value: object) -> bool:
    try:
        json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    except Exception:
        return False
    return True


def _failure_envelope(payload: object, operation: str, code: str) -> bool:
    if not isinstance(payload, dict):
        return False
    error = payload.get("error")
    audit = payload.get("audit")
    if not isinstance(error, dict) or not isinstance(audit, dict):
        return False
    if (
        payload.get("ok") is not False
        or payload.get("operation") != operation
        or error.get("code") != code
        or not isinstance(error.get("message"), str)
        or not isinstance(error.get("retryable"), bool)
        or audit.get("operation") != operation
        or payload.get("status") != "error"
        or payload.get("result") != "error"
    ):
        return False
    return _serializable(payload) and "Traceback" not in json.dumps(payload, sort_keys=True)


def _bind_fixture(
    name: str,
    handler: Callable[[Any], object],
) -> None:
    from aota_forge.core.contracts.descriptor import OperationContractDescriptor
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY

    DEFAULT_REGISTRY.bind(
        OperationContractDescriptor(
            name=name,
            description="M3-B1 ingress-hardening fixture",
            read_write="read",
        ),
        handler=handler,
    )


def _run_shape_fixtures() -> bool:
    from aota_forge.core.contracts.errors import UnsupportedOperationError
    from aota_forge.core.ingress import execute

    class OpaqueResult:
        pass

    shapes: list[tuple[str, object]] = [
        ("none", None),
        ("list", []),
        ("tuple", ()),
        ("string", "malformed"),
        ("int", 7),
        ("bool", True),
        ("object", OpaqueResult()),
        (
            "mapping",
            {
                "data": {"fixture": "success"},
                "evidence": {"source": "m3-b1"},
                "warnings": ["fixture warning"],
                "status": "ok",
                "result": "ok",
                "next_action": "none",
                "semantic_choices": [{"choice": "stable"}],
            },
        ),
        ("mapping_bad_data", {"data": []}),
        ("mapping_bad_warnings", {"warnings": "malformed"}),
    ]
    for name, value in shapes:
        _bind_fixture(
            f"m3.b1.shape.{name}",
            lambda _ctx, value=value: value,
        )

    def canonical_failure(_ctx: Any) -> object:
        raise UnsupportedOperationError("fixture canonical failure")

    def unexpected_failure(_ctx: Any) -> object:
        raise RuntimeError("fixture-private-detail")

    _bind_fixture("m3.b1.raise.forge", canonical_failure)
    _bind_fixture("m3.b1.raise.unexpected", unexpected_failure)

    labels = {
        "none": "B1-N1",
        "list": "B1-N2",
        "string": "B1-N3",
        "object": "B1-N4",
        "mapping": "B1-N5",
    }
    all_passed = True
    for name, value in shapes:
        operation = f"m3.b1.shape.{name}"
        try:
            payload = execute(operation, correlation_id="m3-b1-fixture")
        except Exception as exc:
            check(labels.get(name, f"B1-{name}"), False, f"uncaught {type(exc).__name__}: {exc}")
            all_passed = False
            continue

        if name == "mapping":
            ok = (
                isinstance(payload, dict)
                and payload.get("ok") is True
                and payload.get("operation") == operation
                and payload.get("data") == {"fixture": "success"}
                and payload.get("evidence") == {"source": "m3-b1"}
                and payload.get("warnings") == ["fixture warning"]
                and payload.get("audit", {}).get("handler") == "success"
                and _serializable(payload)
            )
        else:
            ok = _failure_envelope(payload, operation, "FORGE_ERROR")
        check(labels.get(name, f"B1-{name}"), ok)
        all_passed = all_passed and ok

    for name, label, expected_code, expected_handler in (
        ("forge", "B1-N6", "UNSUPPORTED_OPERATION", "forge_error"),
        ("unexpected", "B1-N7", "FORGE_ERROR", "internal_error"),
    ):
        operation = f"m3.b1.raise.{name}"
        try:
            payload = execute(operation, correlation_id="m3-b1-fixture")
        except Exception as exc:
            check(label, False, f"uncaught {type(exc).__name__}: {exc}")
            all_passed = False
            continue
        ok = (
            _failure_envelope(payload, operation, expected_code)
            and payload.get("audit", {}).get("handler") == expected_handler
        )
        check(label, ok)
        all_passed = all_passed and ok

    return all_passed


def _negative_reversion_probe() -> tuple[bool, str]:
    ingress_path = REPO_ROOT / "aota_forge" / "core" / "ingress.py"
    source = ingress_path.read_text(encoding="utf-8")
    safe_call = "return _normalize_handler_result(operation, payload, cid, audit)"
    if source.count(safe_call) != 1:
        return False, "safe normalization call marker is not unique"

    with tempfile.TemporaryDirectory(prefix="m3-b1-reversion-") as temp_dir:
        temp_root = Path(temp_dir)
        package_copy = temp_root / "aota_forge"
        shutil.copytree(REPO_ROOT / "aota_forge", package_copy)
        unsafe_path = package_copy / "core" / "ingress.py"
        unsafe_source = unsafe_path.read_text(encoding="utf-8").replace(
            safe_call,
            'return payload.get("data", {})',
            1,
        )
        unsafe_path.write_text(unsafe_source, encoding="utf-8")

        probe = (
            "from aota_forge.core.contracts.descriptor import OperationContractDescriptor; "
            "from aota_forge.core.contracts.registry import DEFAULT_REGISTRY; "
            "from aota_forge.core.ingress import execute; "
            "DEFAULT_REGISTRY.bind(OperationContractDescriptor(name='m3.b1.reversion', "
            "description='reversion', read_write='read'), handler=lambda _ctx: None); "
            "payload = execute('m3.b1.reversion'); "
            "assert isinstance(payload, dict) and payload.get('ok') is False"
        )
        env = os.environ.copy()
        env["PYTHONDONTWRITEBYTECODE"] = "1"
        env["PYTHONPATH"] = str(temp_root)
        process = subprocess.run(
            [sys.executable, "-c", probe],
            cwd=temp_root,
            env=env,
            capture_output=True,
            text=True,
            timeout=30,
        )
    escaped = process.returncode != 0 and "AttributeError" in process.stderr
    return escaped, process.stderr.strip()[:240]


def main() -> int:
    focused_pass = _run_shape_fixtures()
    repro_pass, repro_detail = _negative_reversion_probe()
    check("REPRO_BEFORE_FIX", repro_pass, repro_detail)
    check("NEGATIVE_REVERSION_PROOF", repro_pass, repro_detail)

    print(f"I9_B008_SOURCE_FIXED={'yes' if focused_pass else 'no'}")
    print(f"ALL_HANDLER_RESULTS_NORMALIZED={'yes' if focused_pass else 'no'}")
    print(f"UNCAUGHT_HANDLER_RESULT_SHAPE_ERROR={'no' if focused_pass else 'yes'}")
    print(f"CANONICAL_ERROR_ENVELOPE_ALWAYS_USED={'yes' if focused_pass else 'no'}")
    print(f"FOCUSED_B1_REGRESSION={'PASS' if focused_pass else 'FAIL'}")
    print(f"NEGATIVE_REVERSION_PROOF={'PASS' if repro_pass else 'FAIL'}")
    return 0 if focused_pass and repro_pass and all(item["pass"] for item in results) else 1


if __name__ == "__main__":
    raise SystemExit(main())
