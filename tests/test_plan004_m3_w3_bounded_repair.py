"""Plan #004 M3/W3 bounded trusted-ingress and acceptance regressions."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

import pytest

from aota_forge.composition.task_main_runtime_selection import (
    materialize_thin_task_main_bootstrap,
)
from aota_forge import core_ingress
from aota_forge.mcp_transport import create_shared_mcp_server
from aota_forge.runtime.trusted_runtime_binding import (
    create_task_main_envelope,
    load_binding_from_envelope,
    verify_envelope,
)
from aota_forge.session_bridge import (
    BRIDGE_CARRIER_KEY,
    BRIDGE_CARRIER_SCHEMA,
    BRIDGE_SOCKET_ENV,
    BridgeVerificationError,
    InvocationBridge,
)
from test_af55_m2_w3_trusted_context_projection import (
    OWNER,
    REPO,
    PROJECT_ID,
    _runtime_config,
    _workspace_fixture,
)
from test_plan004_m2_bridge import _bridge_process


def _prebound_task_main_binding(tmp_path: Path):
    _checkout, registry, worktree = _workspace_fixture(tmp_path)
    bootstrap_path = materialize_thin_task_main_bootstrap(
        worktree_root=worktree,
        project_id=PROJECT_ID,
        worktree_id="wt-plan004-prebound",
        runtime_config_path=_runtime_config(tmp_path),
        origin_task_main_session_ref="ses-plan004-prebound-origin",
        source_repository=f"{OWNER}/{REPO}",
        registry_path=registry,
        plan_ref=f"{OWNER}/aota-hermes-tools#55",
    )
    envelope_path = create_task_main_envelope(
        worktree_root=worktree,
        bootstrap_path=bootstrap_path,
    )
    envelope_kind, payload = verify_envelope(envelope_path)
    assert envelope_kind == "task-main"
    assert payload["worktree_root"] == str(worktree.resolve())
    return load_binding_from_envelope(envelope_path)


def _call_raw(server, operation: str, arguments: dict):
    async def run():
        tool = next(
            item for item in server._tool_manager.list_tools() if item.name == "aota.invoke"
        )
        return await tool.fn(operation=operation, arguments=arguments)

    return asyncio.run(run())


def _call(server, operation: str, arguments: dict):
    result = _call_raw(server, operation, arguments)
    if hasattr(result, "structuredContent") and result.structuredContent is not None:
        return result.structuredContent
    if hasattr(result, "structured_content") and result.structured_content is not None:
        return result.structured_content
    if isinstance(result, dict) and isinstance(result.get("structuredContent"), dict):
        return result["structuredContent"]
    return result


def test_prebound_task_main_bridge_carrier_is_removed_before_descriptor_validation(
    tmp_path: Path, monkeypatch
) -> None:
    from aota_forge.core.ingress import reset_execution_dispatcher

    reset_execution_dispatcher()
    binding = _prebound_task_main_binding(tmp_path)
    descriptor_inputs: list[dict] = []
    original_validate = core_ingress.validate_operation_input

    def capture_validated_arguments(descriptor, arguments):
        if descriptor.name == "role.bootstrap":
            descriptor_inputs.append(dict(arguments))
        return original_validate(descriptor, arguments)

    monkeypatch.setattr(
        core_ingress, "validate_operation_input", capture_validated_arguments
    )

    with _bridge_process(tmp_path) as bridge:
        monkeypatch.setenv(BRIDGE_SOCKET_ENV, bridge.socket_path)
        server = create_shared_mcp_server(binding)
        token = bridge.issue(
            "ses-plan004-prebound-call",
            "call-plan004-prebound-bootstrap",
            "role.bootstrap",
            {},
        )
        result = _call(
            server,
            "role.bootstrap",
            {
                BRIDGE_CARRIER_KEY: {
                    "schema": BRIDGE_CARRIER_SCHEMA,
                    "token": token,
                }
            },
        )

    assert result.get("ok") is True, result.get("error")
    assert descriptor_inputs == [{}]
    assert binding.handoff.work_role.value == "task-main"
    assert binding.project_id == PROJECT_ID
    assert binding.plan_binding.plan_ref == f"{OWNER}/aota-hermes-tools#55"
    reset_execution_dispatcher()


def test_headed_unbound_bridge_ingress_verifies_and_strips_before_validation(
    tmp_path: Path, monkeypatch
) -> None:
    from aota_forge.core.ingress import get_execution_dispatcher, reset_execution_dispatcher
    from aota_forge.mcp_transport import SessionScopedContextRouter, UnboundHostContext

    reset_execution_dispatcher()
    validated: list[dict] = []
    original_validate = core_ingress.validate_operation_input

    def capture_validated_arguments(descriptor, arguments):
        if descriptor.name == "task_main.context":
            validated.append(dict(arguments))
        return original_validate(descriptor, arguments)

    monkeypatch.setattr(
        core_ingress, "validate_operation_input", capture_validated_arguments
    )
    session_ref = "ses-plan004-headed-regression"
    logical_arguments = {"mode": "inspect"}

    with _bridge_process(tmp_path) as bridge:
        router = SessionScopedContextRouter(
            UnboundHostContext(
                repo_root=str(Path(__file__).resolve().parents[1]),
                bridge_socket_path=bridge.socket_path,
                bind_global_dispatcher=False,
            )
        )
        inspect_token = bridge.issue(
            session_ref,
            "call-plan004-headed-inspect",
            "task_main.context",
            logical_arguments,
        )
        inspected = router.invoke(
            "task_main.context",
            {
                **logical_arguments,
                BRIDGE_CARRIER_KEY: {
                    "schema": BRIDGE_CARRIER_SCHEMA,
                    "token": inspect_token,
                },
            },
        )
        bootstrap_token = bridge.issue(
            session_ref,
            "call-plan004-headed-bootstrap",
            "role.bootstrap",
            {},
        )
        bootstrap = router.invoke(
            "role.bootstrap",
            {
                BRIDGE_CARRIER_KEY: {
                    "schema": BRIDGE_CARRIER_SCHEMA,
                    "token": bootstrap_token,
                }
            },
        )
        assert inspected["ok"] is True, inspected.get("error")
        assert bootstrap["ok"] is True, bootstrap.get("error")
        assert session_ref in router._slots
        assert validated and all(arguments == logical_arguments for arguments in validated)
        assert get_execution_dispatcher() is None
        router.close()

    reset_execution_dispatcher()


def test_bridge_rejects_wrong_session_and_wrong_call_id() -> None:
    authority = InvocationBridge(
        secret=b"plan004-bounded-repair-test-secret",
        bridge_id="bounded-repair-test-epoch",
        clock=lambda: 1_000.0,
    )
    operation = "task_main.context"
    arguments = {"mode": "inspect"}
    token = authority.issue("ses-security-source", "call-security-source", operation, arguments)

    with pytest.raises(BridgeVerificationError, match="session differs"):
        authority.verify(
            token,
            operation,
            arguments,
            expected_session_id="ses-security-other",
        )
    with pytest.raises(BridgeVerificationError, match="call differs"):
        authority.verify(
            token,
            operation,
            arguments,
            expected_call_id="call-security-other",
        )


def test_bridge_carrier_replay_and_argument_substitution_fail_closed() -> None:
    authority = InvocationBridge(
        secret=b"plan004-bounded-repair-test-secret",
        bridge_id="bounded-repair-test-epoch",
        clock=lambda: 1_000.0,
    )
    operation = "workspace.search"
    arguments = {"query": "bounded carrier test"}
    token = authority.issue("ses-security-replay", "call-security-replay", operation, arguments)
    identity = authority.verify(token, operation, arguments)
    assert identity.session_id == "ses-security-replay"
    assert identity.call_id == "call-security-replay"
    with pytest.raises(BridgeVerificationError, match="already consumed"):
        authority.verify(token, operation, arguments)

    substitution = authority.issue(
        "ses-security-substitution",
        "call-security-substitution",
        operation,
        arguments,
    )
    with pytest.raises(BridgeVerificationError, match="arguments differ"):
        authority.verify(substitution, operation, {"query": "substituted"})


def test_prebound_envelope_is_authority_and_bridge_identity_is_correlation_only(
    tmp_path: Path, monkeypatch
) -> None:
    from aota_forge.core.ingress import reset_execution_dispatcher
    from aota_forge.mcp_transport import MCP_PUBLIC_TOOLS

    reset_execution_dispatcher()
    binding = _prebound_task_main_binding(tmp_path)
    authority_before = (
        binding.project_id,
        binding.worktree_id,
        binding.canonical_task_id,
        binding.handoff.work_role.value,
        binding.plan_binding.plan_ref,
    )

    with _bridge_process(tmp_path) as bridge:
        monkeypatch.setenv(BRIDGE_SOCKET_ENV, bridge.socket_path)
        server = create_shared_mcp_server(binding)
        assert [tool.name for tool in server._tool_manager.list_tools()] == ["aota.invoke"]
        assert MCP_PUBLIC_TOOLS == ("aota.invoke",)
        args = {}
        token = bridge.issue(
            "ses-plan004-correlation-only",
            "call-plan004-correlation-only",
            "role.bootstrap",
            args,
        )
        result = _call(
            server,
            "role.bootstrap",
            {
                BRIDGE_CARRIER_KEY: {
                    "schema": BRIDGE_CARRIER_SCHEMA,
                    "token": token,
                }
            },
        )

    authority_after = (
        binding.project_id,
        binding.worktree_id,
        binding.canonical_task_id,
        binding.handoff.work_role.value,
        binding.plan_binding.plan_ref,
    )
    assert result["ok"] is True, result.get("error")
    assert authority_after == authority_before
    assert authority_after == (
        PROJECT_ID,
        "wt-plan004-prebound",
        binding.canonical_task_id,
        "task-main",
        f"{OWNER}/aota-hermes-tools#55",
    )
    reset_execution_dispatcher()


def _valid_by_ref_role_bootstrap_result() -> dict:
    output_ref = {
        "ref": "tool-output://bounded-test-result",
        "digest": "b" * 64,
        "project_id": "aota-reader",
        "worktree_id": "wt-plan004-prebound",
        "byte_length": 128,
    }
    return {
        "ok": True,
        "operation": "role.bootstrap",
        "outcome": "success",
        "output_mode": "by_ref",
        "digest": "a" * 64,
        "byte_length": 128,
        "output_ref": output_ref,
        "hydration": {
            "operation": "result.hydrate",
            "arguments": dict(output_ref),
        },
    }


def test_headless_runner_rejects_transport_completion_with_logical_ok_false() -> None:
    from scripts.af58_m2_headless_regression import (
        _aota_invoke_result_is_valid,
        _aota_invoke_result_succeeded,
    )

    failure = {
        "ok": False,
        "operation": "role.bootstrap",
        "outcome": "failure",
        "output_mode": "inline",
        "digest": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
        "byte_length": 0,
        "error_code": "UNKNOWN_INPUT",
    }
    assert _aota_invoke_result_is_valid("role.bootstrap", failure) is True
    assert _aota_invoke_result_succeeded("completed", "role.bootstrap", failure) is False


def test_headless_runner_accepts_only_valid_logical_ok_true_aota_result() -> None:
    from scripts.af58_m2_headless_regression import (
        _aota_invoke_result_is_valid,
        _aota_invoke_result_succeeded,
        _logical_aota_result,
    )

    success = _valid_by_ref_role_bootstrap_result()
    assert _aota_invoke_result_is_valid("role.bootstrap", success) is True
    assert _aota_invoke_result_succeeded("completed", "role.bootstrap", success) is True
    assert _aota_invoke_result_succeeded("failed", "role.bootstrap", success) is False
    assert _logical_aota_result({"output": json.dumps(success)}) == success
    assert _aota_invoke_result_succeeded(
        "completed", "role.bootstrap", {**success, "output_ref": None}
    ) is False
    assert _aota_invoke_result_succeeded(
        "completed", "role.bootstrap", {**success, "output_mode": []}
    ) is False


def test_headless_runner_resolves_real_prebound_by_ref_result_from_tool_output(
    tmp_path: Path, monkeypatch
) -> None:
    from scripts.af58_m2_headless_regression import (
        _aota_invoke_result_succeeded,
        _logical_aota_result,
    )

    binding = _prebound_task_main_binding(tmp_path)
    with _bridge_process(tmp_path) as bridge:
        monkeypatch.setenv(BRIDGE_SOCKET_ENV, bridge.socket_path)
        server = create_shared_mcp_server(binding)
        token = bridge.issue(
            "ses-plan004-runner-byref",
            "call-plan004-runner-byref",
            "role.bootstrap",
            {},
        )
        raw_result = _call_raw(
            server,
            "role.bootstrap",
            {
                BRIDGE_CARRIER_KEY: {
                    "schema": BRIDGE_CARRIER_SCHEMA,
                    "token": token,
                }
            },
        )

    structured = getattr(raw_result, "structuredContent", None)
    if structured is None:
        structured = raw_result.structured_content
    assert structured["ok"] is True
    assert structured["output_mode"] == "by_ref"
    logical_result = _logical_aota_result(
        {"status": "completed", "output": raw_result.content[0].text}
    )
    assert _aota_invoke_result_succeeded(
        "completed", "role.bootstrap", logical_result
    ) is True


def test_public_bootstrap_shape_and_architecture_remain_unchanged() -> None:
    from aota_forge import mcp_transport

    descriptor = core_ingress.resolve_descriptor("role.bootstrap")
    assert core_ingress.validate_operation_input(descriptor, {}) == {}
    assert mcp_transport.MCP_PUBLIC_TOOL_COUNT == 1
    assert mcp_transport.MCP_PUBLIC_TOOLS == ("aota.invoke",)
    assert mcp_transport.NEW_SESSION_DB is False
    assert mcp_transport.SECOND_SESSION_DATABASE is False
    assert mcp_transport.SECOND_CONTEXT_ENGINE is False
    assert mcp_transport.NEW_AUTHORITY_ENGINE is False
    assert mcp_transport.NEW_MCP_TOOL is False
    assert mcp_transport.MODEL_SUPPLIES_SESSION_ID is False
    assert mcp_transport.SESSION_ID_IS_AUTHORITY is False
    assert mcp_transport.GLOBAL_BOUND_DELEGATE_SHARED_ACROSS_SESSIONS is False
    assert not hasattr(mcp_transport, "GLOBAL_CURRENT_SESSION")
