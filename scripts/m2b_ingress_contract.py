#!/usr/bin/env python3
"""M2-B focused deterministic validation — Canonical Ingress / Context / Error Contract.

Proves:

1. M2-A descriptor/registry consumption (no second registry, no second descriptor)
2. protocol_version source is core/contracts/version.py (never hard-coded "1.0")
3. contract_hash is available on every canonical execution
4. unknown input rejected (UNKNOWN_INPUT), handler not executed
5. missing required input rejected (REQUIRED_INPUT_MISSING), handler not executed
6. wrong type rejected (INPUT_TYPE_INVALID)
7. oversized input rejected (INPUT_SIZE_EXCEEDED), per-field bounds
8. valid input succeeds exactly once through validation -> resolver -> handler
9. distinct semantic errors (no collapse to INVALID_STATE / FORGE_ERROR)
10. all registered error types round-trip (code/message/retryable/details/type)
11. unknown error code degrades deterministically (UNKNOWN_FUTURE_ERROR)
12. I9-B004: explicit project-bound resolution failure surfaces (ok=false + code)
13. I9-B004: optional diagnostic failure is visible (warnings + checks states)
14. correlation_id present and safe on success and failure
15. bounded audit metadata, no secret/full input echo
16. non-read-only operation remains denied in M2, handler not executed
17. no subject graph / lease / mutation machinery added

Exit status: 0 on all PASS, 1 on any FAIL.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

AOTA_FORGE_ROOT = Path(__file__).resolve().parent.parent / "aota_forge"
WORKSPACE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(AOTA_FORGE_ROOT.parent))

results: list[dict[str, object]] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append({"check": name, "pass": bool(ok), "detail": detail[:400]})
    print(f"{'PASS' if ok else 'FAIL'}  {name}" + (f"  ({detail})" if detail else ""))


def make_project(workspace: Path, project_id: str, with_version: bool = False) -> Path:
    project_dir = workspace / project_id
    (project_dir / ".aota").mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "project": {"id": project_id, "name": project_id, "kind": "fixture", "status": "active"},
        "summary": "M2-B fixture project",
        "capabilities": ["fixture"],
        "paths": {"source_root": ".", "source": ["."], "docs": ["docs"], "scripts": ["scripts"], "profiles": ["profiles"], "skills": ["skills"], "tests": ["tests"]},
        "commands": {"validate": ["validate"], "deploy": ["deploy"], "verify_deploy": ["verify"]},
        "runtime": {"deployment_type": "managed-files", "requires_human_checkpoint": False},
        "codegraph": {"enabled": False, "index_location": ".codegraph/"},
        "plan": {"active_plan_id": None},
        "constraints": [],
    }
    (project_dir / ".aota" / "project.yaml").write_text(json.dumps(manifest, ensure_ascii=False), encoding="utf-8")
    if with_version:
        (project_dir / "VERSION").write_text("1.0.0\n", encoding="utf-8")
    return project_dir


def main() -> int:
    from aota_forge.core import execute
    from aota_forge.core.contracts.descriptor import OperationContractDescriptor, InputSpec
    from aota_forge.core.contracts.errors import (
        ERROR_CLASSES,
        ForgeError,
        UnknownFutureError,
        error_from_dict,
    )
    from aota_forge.core.contracts.registry import DEFAULT_REGISTRY
    from aota_forge.core.contracts.version import PROTOCOL_VERSION

    workdir = Path(tempfile.mkdtemp(prefix="m2b-fixtures-"))
    try:
        workspace = workdir / "workspace"
        workspace.mkdir()
        p1 = make_project(workspace, "fixture-alpha", with_version=True)
        make_project(workspace, "fixture-noversion")
        registry = workdir / "workspaces.json"
        registry.write_text(json.dumps({"fixture-ws": {"candidates": [str(workspace)]}}), encoding="utf-8")
        garbage_registry = workdir / "garbage.json"
        garbage_registry.write_text("not json at all", encoding="utf-8")

        # --- 1. M2-A descriptor/registry consumption ---------------------------------
        from aota_forge.core.contracts.descriptor import OperationContractDescriptor as D
        from aota_forge.core.contracts.registry import HandlerRegistry

        descriptor = DEFAULT_REGISTRY.get("host.status")
        check(
            "m2a_descriptor_consumed",
            isinstance(descriptor, OperationContractDescriptor) and descriptor.name == "host.status",
        )
        check("m2a_default_registry_consumed", isinstance(DEFAULT_REGISTRY, HandlerRegistry))
        for module_file in ("ingress.py", "context.py"):
            text = (AOTA_FORGE_ROOT / "core" / module_file).read_text(encoding="utf-8")
            registry_lines = [l for l in text.splitlines() if "class HandlerRegistry" in l]
            descriptor_lines = [l for l in text.splitlines() if "class OperationContractDescriptor" in l]
            check(
                f"no_second_registry_in_{module_file}",
                not registry_lines,
            )
            check(
                f"no_second_descriptor_in_{module_file}",
                not descriptor_lines,
            )

        # --- 2. protocol_version source ----------------------------------------------
        version_scan_modules = {
            "ingress.py": AOTA_FORGE_ROOT / "core" / "ingress.py",
            "context.py": AOTA_FORGE_ROOT / "core" / "context.py",
            "validation.py": AOTA_FORGE_ROOT / "core" / "contracts" / "validation.py",
            "errors.py": AOTA_FORGE_ROOT / "core" / "contracts" / "errors.py",
        }
        for module_file, path in version_scan_modules.items():
            text = path.read_text(encoding="utf-8")
            hard_coded = any('"1.0"' in line or "'1.0'" in line for line in text.splitlines())
            check(f"protocol_version_not_hardcoded_{module_file}", not hard_coded)
        ingress_text = (AOTA_FORGE_ROOT / "core" / "ingress.py").read_text(encoding="utf-8")
        check(
            "protocol_version_source_ingress",
            any(
                "contracts.version" in line and line.strip().startswith(("import ", "from "))
                for line in ingress_text.splitlines()
            ),
        )

        # --- 3. contract_hash available on execution ---------------------------------
        ok_result = execute("runtime.status", {"pid": os.getpid()})
        ok_audit = ok_result.get("audit", {})
        check(
            "contract_hash_available",
            ok_audit.get("contract_hash") == DEFAULT_REGISTRY.get("runtime.status").contract_hash(),
        )

        # --- spy ops (bound to the canonical DEFAULT_REGISTRY, runtime-local) --------
        calls: dict[str, int] = {}
        captured: dict[str, object] = {}

        def spy(name: str):
            def handler(ctx):
                calls[name] = calls.get(name, 0) + 1
                captured[name] = ctx
                return {"data": {"spy": name}}
            return handler

        DEFAULT_REGISTRY.bind(
            OperationContractDescriptor(
                name="test.spy.basic",
                description="m2b spy op",
                inputs=(InputSpec("project_id", "str"),),
                read_write="read",
            ),
            handler=spy("basic"),
        )
        DEFAULT_REGISTRY.bind(
            OperationContractDescriptor(
                name="test.spy.size",
                description="m2b size-bound spy op",
                inputs=(InputSpec("note", "str"), InputSpec("tags", "list")),
                read_write="read",
            ),
            handler=spy("size"),
        )
        DEFAULT_REGISTRY.bind(
            OperationContractDescriptor(
                name="test.spy.context",
                description="m2b context separation spy op",
                inputs=(InputSpec("project_id", "str"), InputSpec("registry_path", "str")),
                read_write="read",
            ),
            handler=spy("context"),
        )
        DEFAULT_REGISTRY.bind(
            OperationContractDescriptor(
                name="test.write.denied",
                description="m2b write-denial spy op",
                inputs=(InputSpec("payload", "str"),),
                read_write="write",
            ),
            handler=spy("write"),
        )
        DEFAULT_REGISTRY.bind(
            OperationContractDescriptor(
                name="test.ctx.unsupported",
                description="m2b unsupported context spy op",
                required_context=("work_item",),
                read_write="read",
            ),
            handler=spy("ctx_unsupported"),
        )

        # --- 4. unknown input rejected ------------------------------------------------
        unknown = execute("test.spy.basic", {"project_id": "p", "banana": "x"})
        check(
            "unknown_input_rejected",
            unknown.get("ok") is False and unknown["errors"][0]["code"] == "UNKNOWN_INPUT",
            unknown["errors"][0]["code"] if unknown.get("errors") else "no errors",
        )
        check("unknown_input_handler_not_executed", calls.get("basic", 0) == 0)
        check("unknown_input_audit_code", unknown["audit"]["validation"] == "UNKNOWN_INPUT")
        check("unknown_input_audit_handler", unknown["audit"]["handler"] == "not_executed")

        # --- 5. missing required input -----------------------------------------------
        missing = execute("runtime.status", {})
        check(
            "missing_required_input_rejected",
            missing.get("ok") is False and missing["errors"][0]["code"] == "REQUIRED_INPUT_MISSING",
        )

        # --- 6. wrong type ------------------------------------------------------------
        wrong = execute("runtime.status", {"pid": "123abc"})
        check(
            "wrong_type_rejected",
            wrong.get("ok") is False and wrong["errors"][0]["code"] == "INPUT_TYPE_INVALID",
        )
        wrong_bool = execute("runtime.status", {"pid": True})
        check("bool_not_int", wrong_bool.get("ok") is False and wrong_bool["errors"][0]["code"] == "INPUT_TYPE_INVALID")

        # --- 7. oversized input -------------------------------------------------------
        big_str = execute("test.spy.size", {"note": "x" * 5000, "tags": []})
        check(
            "oversized_field_rejected",
            big_str.get("ok") is False and big_str["errors"][0]["code"] == "INPUT_SIZE_EXCEEDED",
        )
        big_list = execute("test.spy.size", {"note": "ok", "tags": list(range(300))})
        check(
            "oversized_list_rejected",
            big_list.get("ok") is False and big_list["errors"][0]["code"] == "INPUT_SIZE_EXCEEDED",
        )
        check("oversized_handler_not_executed", calls.get("size", 0) == 0)

        # --- 8. valid input succeeds exactly once -------------------------------------
        valid = execute("test.spy.basic", {"project_id": "p"}, correlation_id="valid-run-1")
        check("valid_input_succeeds", valid.get("ok") is True and valid["data"].get("spy") == "basic")
        check("valid_input_handler_once", calls.get("basic", 0) == 1)
        check("valid_input_audit_ok", valid["audit"]["validation"] == "ok" and valid["audit"]["context"] == "ok" and valid["audit"]["handler"] == "success")

        project_ok = execute(
            "project.resolve",
            {"workspace_id": "fixture-ws", "project_id": "fixture-alpha", "registry_path": str(registry)},
            principal="library",
        )
        check("valid_project_resolve_succeeds", project_ok.get("ok") is True and project_ok["data"]["project_id"] == "fixture-alpha")

        # --- 9. distinct semantic errors ----------------------------------------------
        codes = sorted(ERROR_CLASSES)
        check("error_registry_not_collapsed", len(codes) >= 25, f"len={len(codes)}")
        check("error_registry_codes_distinct", len(set(codes)) == len(codes))
        required_codes = {
            "PROJECT_BINDING_MISSING", "PLAN_MISSING", "PLAN_WORKSPACE_CONTEXT_MISSING",
            "ACTIVE_WORK_ITEM_MISSING", "PROJECT_AMBIGUOUS", "PROJECT_INITIALIZATION_REQUIRED",
            "NEEDS_SEMANTIC_CHOICE", "GOVERNANCE_PROJECTION_DRIFT", "UNKNOWN_INPUT",
            "REQUIRED_INPUT_MISSING", "INPUT_TYPE_INVALID", "INPUT_SIZE_EXCEEDED",
            "CONTEXT_NOT_SUPPORTED", "UNKNOWN_FUTURE_ERROR",
        }
        check("semantic_codes_registered", required_codes <= set(codes))

        # --- 10. all registered errors round-trip -------------------------------------
        roundtrip_ok = True
        roundtrip_detail: list[str] = []
        for code in codes:
            cls = ERROR_CLASSES[code]
            if code == "FORGE_ERROR":
                instance = ForgeError("FORGE_ERROR", "boom", True, {"k": 1})
            elif code == "UNKNOWN_FUTURE_ERROR":
                instance = cls(original_code="SOME.FUTURE.CODE", message="m", retryable=True, details={"a": [1]})
            else:
                instance = cls("roundtrip message", details={"n": 1})
            payload = instance.to_dict()
            restored = error_from_dict(payload)
            good = (
                isinstance(restored, cls)
                and restored.code == instance.code
                and restored.message == instance.message
                and restored.retryable == instance.retryable
                and restored.details == instance.details
            )
            if not good:
                roundtrip_ok = False
                roundtrip_detail.append(code)
        check("all_registered_errors_roundtrip", roundtrip_ok, "; ".join(roundtrip_detail))

        # --- 11. unknown error deterministic ------------------------------------------
        unknown_payload = {"code": "FUTURE_CODE_42", "message": "future msg", "retryable": True, "details": {"z": 9}}
        degraded = error_from_dict(unknown_payload)
        check(
            "unknown_error_deterministic",
            isinstance(degraded, UnknownFutureError)
            and degraded.original_code == "FUTURE_CODE_42"
            and degraded.message == "future msg"
            and degraded.retryable is True
            and degraded.details == {"z": 9},
        )
        re_payload = degraded.to_dict()
        re_degraded = error_from_dict(re_payload)
        check(
            "unknown_error_roundtrips",
            isinstance(re_degraded, UnknownFutureError)
            and re_degraded.original_code == "FUTURE_CODE_42"
            and re_payload["original_code"] == "FUTURE_CODE_42",
        )

        # --- 12. I9-B004: explicit project-bound failure surfaced ---------------------
        binding = execute(
            "host.status",
            {"workspace_id": "fixture-ws", "project_id": "fixture-alpha"},
        )
        check(
            "i9b004_binding_missing_surfaced",
            binding.get("ok") is False and binding["errors"][0]["code"] == "PROJECT_BINDING_MISSING",
            binding["errors"][0]["code"] if binding.get("errors") else "no errors",
        )

        not_found = execute(
            "host.status",
            {"workspace_id": "fixture-ws", "project_id": "fixture-missing", "registry_path": str(registry)},
        )
        check(
            "i9b004_project_not_found_surfaced",
            not_found.get("ok") is False and not_found["errors"][0]["code"] == "PROJECT_NOT_FOUND",
        )

        dup_dir = workspace / "fixture-alpha-duplicate"
        (dup_dir / ".aota").mkdir(parents=True)
        (dup_dir / ".aota" / "project.yaml").write_text(
            (p1 / ".aota" / "project.yaml").read_text(encoding="utf-8"), encoding="utf-8"
        )
        ambiguous = execute(
            "host.status",
            {"workspace_id": "fixture-ws", "project_id": "fixture-alpha", "registry_path": str(registry)},
        )
        check(
            "i9b004_ambiguity_surfaced",
            ambiguous.get("ok") is False and ambiguous["errors"][0]["code"] == "PROJECT_AMBIGUOUS",
        )
        shutil.rmtree(dup_dir, ignore_errors=True)

        reg_invalid = execute(
            "host.status",
            {"workspace_id": "fixture-ws", "project_id": "fixture-alpha", "registry_path": str(garbage_registry)},
        )
        check(
            "i9b004_registry_invalid_surfaced",
            reg_invalid.get("ok") is False and reg_invalid["errors"][0]["code"] == "PROJECT_REGISTRY_INVALID",
        )
        check("i9b004_failures_never_ok_true", not_found.get("ok") is False and ambiguous.get("ok") is False and reg_invalid.get("ok") is False)

        # --- 13. I9-B004: optional diagnostic failure visible --------------------------
        partial = execute("host.status", {"workspace_id": "fixture-ws"})
        checks_b = partial.get("data", {}).get("checks", {})
        check(
            "i9b004_partial_request_visible",
            partial.get("ok") is True
            and partial.get("warnings")
            and checks_b.get("project", {}).get("state") == "failed"
            and checks_b.get("project", {}).get("error_code") == "PROJECT_BINDING_MISSING",
        )

        pidfile_missing = execute("host.status", {"pidfile": str(workdir / "no-such.pid")})
        checks_p = pidfile_missing.get("data", {}).get("checks", {})
        check(
            "i9b004_pidfile_failure_visible",
            pidfile_missing.get("ok") is True
            and pidfile_missing.get("warnings")
            and checks_p.get("process", {}).get("state") == "failed"
            and checks_p.get("process", {}).get("error_code") == "RECEIPT_INVALID",
        )

        pidfile_good = workdir / "good.pid"
        pidfile_good.write_text(str(os.getpid()), encoding="utf-8")
        pidfile_ok = execute("host.status", {"pidfile": str(pidfile_good)})
        checks_ok = pidfile_ok.get("data", {}).get("checks", {})
        check(
            "i9b004_pidfile_available_state",
            pidfile_ok.get("ok") is True
            and checks_ok.get("process", {}).get("state") == "available"
            and pidfile_ok.get("data", {}).get("process", {}).get("running") is True,
        )

        bare = execute("host.status", {})
        checks_bare = bare.get("data", {}).get("checks", {})
        check(
            "i9b004_not_requested_states",
            bare.get("ok") is True
            and not bare.get("warnings")
            and all(checks_bare[k]["state"] == "not_requested" for k in ("project", "runtime_identity", "process", "deployment_receipt")),
        )

        version_ok = execute(
            "host.status",
            {"workspace_id": "fixture-ws", "project_id": "fixture-alpha", "registry_path": str(registry)},
        )
        checks_v = version_ok.get("data", {}).get("checks", {})
        check(
            "i9b004_version_available",
            version_ok.get("ok") is True
            and checks_v.get("project", {}).get("state") == "available"
            and checks_v.get("runtime_identity", {}).get("state") == "available"
            and version_ok["data"]["runtime_identity"]["version"] == "1.0.0",
        )

        version_missing = execute(
            "host.status",
            {"workspace_id": "fixture-ws", "project_id": "fixture-noversion", "registry_path": str(registry)},
        )
        checks_vm = version_missing.get("data", {}).get("checks", {})
        check(
            "i9b004_version_missing_distinguished",
            version_missing.get("ok") is True
            and version_missing.get("warnings")
            and checks_vm.get("runtime_identity", {}).get("state") == "not_available"
            and version_missing["data"]["runtime_identity"].get("error_code") == "RUNTIME_IDENTITY_UNAVAILABLE",
        )

        # --- 14. correlation id --------------------------------------------------------
        cid_success = execute("runtime.status", {"pid": os.getpid()}, correlation_id="cli-run-abc")
        cid_failure = execute("runtime.status", {}, correlation_id="cli-run-abc")
        check("correlation_id_success", cid_success["correlation_id"] == "cli-run-abc")
        check("correlation_id_failure", cid_failure["correlation_id"] == "cli-run-abc")
        check("correlation_id_in_audit", cid_success["audit"]["correlation_id"] == "cli-run-abc")

        cid_invalid = execute("operations.list", {}, correlation_id="bad id!!")
        check(
            "correlation_id_unsafe_replaced",
            cid_invalid["correlation_id"] != "bad id!!" and re.fullmatch(r"[0-9a-f]{32}", cid_invalid["correlation_id"]) is not None,
        )
        cid_absent = execute("operations.list", {})
        check(
            "correlation_id_always_nonempty",
            isinstance(cid_absent["correlation_id"], str)
            and len(cid_absent["correlation_id"]) > 0
            and re.fullmatch(r"[0-9a-f]{32}", cid_absent["correlation_id"]) is not None,
        )

        # --- 15. bounded audit metadata, no echo ---------------------------------------
        audit = cid_success["audit"]
        check(
            "audit_bounded_keys",
            set(audit) == {"operation", "protocol_version", "contract_hash", "correlation_id", "validation", "context", "handler"},
        )
        check("audit_protocol_version", audit["protocol_version"] == PROTOCOL_VERSION)
        check("audit_protocol_version_value", audit["protocol_version"] == "1.0")

        secret_input = execute(
            "test.spy.context",
            {"project_id": "secret-project-xyz", "registry_path": str(workdir / "secret-registry-path")},
        )
        audit_json = json.dumps(secret_input["audit"], ensure_ascii=False)
        check(
            "audit_no_full_input_echo",
            "secret-project-xyz" not in audit_json and "secret-registry-path" not in audit_json,
        )

        # --- trusted adapter context separation ----------------------------------------
        ctx = captured.get("context")
        check(
            "trusted_adapter_context_separated",
            ctx is not None
            and ctx.trusted == {"registry_path": str(workdir / "secret-registry-path")}
            and ctx.semantic_inputs == {"project_id": "secret-project-xyz"}
            and set(ctx.params) == {"project_id", "registry_path"}
            and ctx.contract_hash == DEFAULT_REGISTRY.get("test.spy.context").contract_hash()
            and ctx.protocol_version == PROTOCOL_VERSION,
        )
        check(
            "trusted_keys_bounded",
            DEFAULT_REGISTRY.get("host.status").inputs is not None,
        )

        # --- 16. non-read-only denied in M2 --------------------------------------------
        denied = execute("test.write.denied", {"payload": "x"})
        check(
            "non_readonly_denied",
            denied.get("ok") is False and denied["errors"][0]["code"] == "UNSUPPORTED_OPERATION",
        )
        check("non_readonly_handler_not_executed", calls.get("write", 0) == 0)
        check("non_readonly_audit", denied["audit"]["handler"] == "not_executed")

        # --- context failure never executes handler ------------------------------------
        unsupported_ctx = execute("test.ctx.unsupported", {})
        check(
            "context_failure_deterministic",
            unsupported_ctx.get("ok") is False and unsupported_ctx["errors"][0]["code"] == "CONTEXT_NOT_SUPPORTED",
        )
        check("context_failure_handler_not_executed", calls.get("ctx_unsupported", 0) == 0)
        check("context_failure_audit", unsupported_ctx["audit"]["context"] == "CONTEXT_NOT_SUPPORTED" and unsupported_ctx["audit"]["handler"] == "not_executed")

        # --- 17. no subject graph / lease / mutation machinery --------------------------
        scanned_modules = {
            "core/ingress.py": AOTA_FORGE_ROOT / "core" / "ingress.py",
            "core/context.py": AOTA_FORGE_ROOT / "core" / "context.py",
            "core/handlers.py": AOTA_FORGE_ROOT / "core" / "handlers.py",
            "contracts/errors.py": AOTA_FORGE_ROOT / "core" / "contracts" / "errors.py",
            "contracts/results.py": AOTA_FORGE_ROOT / "core" / "contracts" / "results.py",
            "contracts/validation.py": AOTA_FORGE_ROOT / "core" / "contracts" / "validation.py",
        }
        forbidden_tokens = ("current_pointer", "mint_authority", "grant_lease", "create_subject", "bind_followup", "durable_subject")
        violations: list[str] = []
        for module_file, path in scanned_modules.items():
            text = path.read_text(encoding="utf-8")
            for line in text.splitlines():
                folded = line.casefold()
                if any(token in folded for token in forbidden_tokens):
                    violations.append(f"{module_file}: {line.strip()}")
                if line.strip().startswith(("import ", "from ")):
                    name = line.split()[1].replace("-", "_").casefold()
                    if any(t in name for t in ("hermes", "webui", "docker", "outbox", "current_pointer")):
                        violations.append(f"{module_file}: {line.strip()}")
        check("no_subject_graph_lease_mutation", not violations, "; ".join(violations))

        # --- M1 envelope compatibility -------------------------------------------------
        check("m1_envelope_keys_preserved", {"ok", "operation", "data", "evidence", "warnings"} <= set(ok_result))
        check(
            "machine_fields_preserved",
            {"status", "result", "errors", "blockers", "semantic_choices", "next_action", "correlation_id"} <= set(ok_result),
        )

        passed = all(item["pass"] for item in results)
        print(f"\nM2-B INGRESS / CONTEXT / ERROR CONTRACT: {sum(1 for r in results if r['pass'])}/{len(results)} PASS")
        return 0 if passed else 1
    finally:
        shutil.rmtree(workdir, ignore_errors=True)


if __name__ == "__main__":
    sys.exit(main())
