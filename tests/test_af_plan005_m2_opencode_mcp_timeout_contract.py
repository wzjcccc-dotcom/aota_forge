"""AF Plan #005 M2 — OpenCode MCP timeout ordering and semantic return proof."""

from __future__ import annotations

import asyncio
import json
import os
import sys
from datetime import timedelta
from pathlib import Path
from typing import Any

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

from aota_forge.core.git.inspect import GIT_TIMEOUT
from aota_forge.core.project.resolver import (
    ProjectCandidateEvidence,
    ProjectResolutionEvidence,
)
from aota_forge.work_plane.github_tools import GH_READ_TIMEOUT, GH_WRITE_TIMEOUT
from aota_forge.work_plane.git_tools import GIT_MUTATION_TIMEOUT
from aota_forge.work_plane.restricted_shell import (
    MAX_TIMEOUT_SECONDS as RESTRICTED_SHELL_MAX_TIMEOUT_SECONDS,
)
from aota_forge.work_plane.test_execution import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS as TEST_RUN_MAX_TIMEOUT_SECONDS,
)
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox


REPO_ROOT = Path(__file__).resolve().parents[1]
NOTES_PATH = REPO_ROOT / "deploy" / "opencode-reference-runtime-operator-notes.md"
PROBE_SERVER = Path(__file__).with_name("mcp_timeout_probe_server.py")
BOUNDED_TRANSPORT_MARGIN_MS = 60_000
PRE_REPAIR_MCP_TIMEOUT_MS = 15_000
PROBE_TIME_SCALE = 0.001


def _operator_config() -> dict[str, Any]:
    notes = NOTES_PATH.read_text(encoding="utf-8")
    marker = "Deployment shape used by the reference host (operator-owned `opencode.json`):"
    section = notes.split(marker, 1)[1]
    snippet = section.split("```json", 1)[1].split("```", 1)[0]
    value = json.loads(snippet)
    assert isinstance(value, dict)
    return value


def _structured(result: Any) -> dict[str, Any]:
    value = getattr(result, "structured_content", None)
    if value is None:
        value = getattr(result, "structuredContent", None)
    assert isinstance(value, dict)
    return value


def _sandbox(root: Path):
    candidate = ProjectCandidateEvidence(
        workspace_id="ws_timeout_contract",
        workspace_root=str(root),
        project_id="proj_timeout_contract",
        project_root=str(root),
        manifest_path=".aota/project.yaml",
        name="proj_timeout_contract",
        kind="test-fixture",
        status="active",
        registry_fingerprint="a" * 64,
        candidate_fingerprint="b" * 64,
    )
    evidence = ProjectResolutionEvidence(
        status="RESOLVED",
        workspace_id="ws_timeout_contract",
        workspace_root=str(root),
        registry_fingerprint="a" * 64,
        listing_fingerprint="c" * 64,
        candidates=(candidate,),
    )
    return bind_worktree_sandbox(evidence, "wt_timeout_contract", root)


def _server_parameters(root: Path, mode: str) -> StdioServerParameters:
    env = dict(os.environ)
    env["AOTA_MCP_TIMEOUT_PROBE_ROOT"] = str(root)
    env["AOTA_MCP_TIMEOUT_PROBE_MODE"] = mode
    env["PYTHONPATH"] = os.pathsep.join(
        item for item in (str(REPO_ROOT), env.get("PYTHONPATH", "")) if item
    )
    return StdioServerParameters(
        command=sys.executable,
        args=[str(PROBE_SERVER)],
        env=env,
    )


def _probe_request_timeout_seconds() -> float:
    mcp_timeout_ms = _operator_config()["mcp"]["aota"]["timeout"]
    return (mcp_timeout_ms / 1000) * PROBE_TIME_SCALE


async def _call(session: ClientSession, operation: str, arguments: dict[str, Any], timeout: float):
    return await session.call_tool(
        "aota.invoke",
        {"operation": operation, "arguments": arguments},
        read_timeout_seconds=timedelta(seconds=timeout),
    )


def test_t1_operator_mcp_timeout_exceeds_test_run_bound_and_margin() -> None:
    config = _operator_config()
    timeout_ms = config["mcp"]["aota"]["timeout"]
    assert type(timeout_ms) is int
    assert timeout_ms > (
        TEST_RUN_MAX_TIMEOUT_SECONDS * 1000 + BOUNDED_TRANSPORT_MARGIN_MS
    )
    assert DEFAULT_TIMEOUT_SECONDS == 30
    assert TEST_RUN_MAX_TIMEOUT_SECONDS == 300
    assert RESTRICTED_SHELL_MAX_TIMEOUT_SECONDS == 30
    assert max(
        RESTRICTED_SHELL_MAX_TIMEOUT_SECONDS,
        GIT_TIMEOUT,
        GIT_MUTATION_TIMEOUT,
        GH_READ_TIMEOUT,
        GH_WRITE_TIMEOUT,
    ) == 30


def test_pre_repair_outer_deadline_expires_before_legal_test_run() -> None:
    async def run() -> str:
        import tempfile

        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "tests").mkdir()
            (root / "tests" / "test_probe.py").write_text(
                "def test_probe(): assert True\n", encoding="utf-8"
            )
            (root / "after.txt").write_text("still-connected", encoding="utf-8")
            params = _server_parameters(root, "long-legal-run")
            async with stdio_client(params) as (read, write):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    try:
                        await _call(
                            session,
                            "test.run",
                            {"runner": "pytest", "targets": ["tests/test_probe.py"]},
                            PRE_REPAIR_MCP_TIMEOUT_MS / 1000 * PROBE_TIME_SCALE,
                        )
                    except Exception as exc:
                        assert type(exc).__name__ == "McpError"
                    else:
                        raise AssertionError(
                            "pre-repair 15-second MCP boundary unexpectedly outlived the 20-second legal call"
                        )
                    await asyncio.sleep(0.03)
                    result = await _call(
                        session,
                        "workspace.read",
                        {"path": "after.txt"},
                        _probe_request_timeout_seconds(),
                    )
                    assert _structured(result)["payload"]["content"] == "still-connected"
                    return "McpError"

    assert asyncio.run(run()) == "McpError"


def test_t2_long_legal_test_run_exceeds_old_boundary_and_keeps_mcp_usable(
    tmp_path: Path,
) -> None:
    async def run() -> None:
        root = tmp_path / "long-run-worktree"
        (root / "tests").mkdir(parents=True)
        (root / "tests" / "test_probe.py").write_text(
            "def test_probe(): assert True\n", encoding="utf-8"
        )
        (root / "after.txt").write_text("mcp-channel-ready", encoding="utf-8")
        params = _server_parameters(root, "long-legal-run")
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await _call(
                    session,
                    "test.run",
                    {"runner": "pytest", "targets": ["tests/test_probe.py"]},
                    _probe_request_timeout_seconds(),
                )
                returned = _structured(result)
                assert returned["ok"] is True
                assert returned["payload"]["timeout"] == DEFAULT_TIMEOUT_SECONDS
                assert "SIMULATED_LEGAL_TEST_DURATION_SECONDS=20" in returned["payload"]["stdout"]
                assert _probe_request_timeout_seconds() > 20 * PROBE_TIME_SCALE
                read_result = await _call(
                    session,
                    "workspace.read",
                    {"path": "after.txt"},
                    _probe_request_timeout_seconds(),
                )
                assert _structured(read_result)["payload"]["content"] == "mcp-channel-ready"

    asyncio.run(run())


def test_t3_inner_test_timeout_is_authoritative_and_mcp_stays_usable(
    tmp_path: Path,
) -> None:
    async def run() -> dict[str, Any]:
        root = tmp_path / "inner-timeout-worktree"
        (root / "tests").mkdir(parents=True)
        (root / "tests" / "test_probe.py").write_text(
            "def test_probe(): assert True\n", encoding="utf-8"
        )
        (root / "after.txt").write_text("mcp-channel-ready", encoding="utf-8")
        params = _server_parameters(root, "inner-timeout")
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                result = await _call(
                    session,
                    "test.run",
                    {
                        "runner": "pytest",
                        "targets": ["tests/test_probe.py"],
                        "timeout": 1,
                    },
                    _probe_request_timeout_seconds(),
                )
                return_result = _structured(result)
                assert return_result["ok"] is False
                assert return_result["error"]["code"] == "TEST_TIMEOUT"
                read_result = await _call(
                    session,
                    "workspace.read",
                    {"path": "after.txt"},
                    _probe_request_timeout_seconds(),
                )
                assert _structured(read_result)["payload"]["content"] == "mcp-channel-ready"
                return return_result

    asyncio.run(run())


def test_t4_worker_can_write_blocked_result_and_task_return_after_validation_timeout(
    tmp_path: Path,
) -> None:
    async def run() -> dict[str, Any]:
        root = tmp_path / "semantic-return-worktree"
        (root / "tests").mkdir(parents=True)
        (root / "tests" / "test_probe.py").write_text(
            "def test_probe(): assert True\n", encoding="utf-8"
        )
        params = _server_parameters(root, "inner-timeout")
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                validation = await _call(
                    session,
                    "test.run",
                    {
                        "runner": "pytest",
                        "targets": ["tests/test_probe.py"],
                        "timeout": 1,
                    },
                    _probe_request_timeout_seconds(),
                )
                assert _structured(validation)["error"]["code"] == "TEST_TIMEOUT"
                result_handoff = await _call(
                    session,
                    "handoff.write",
                    {
                        "mode": "result",
                        "payload": {
                            "summary": "validation timed out within its bound",
                            "work_done": "test.run returned TEST_TIMEOUT; a governed blocked result was written",
                            "validation": "bounded inner timeout",
                        },
                    },
                    _probe_request_timeout_seconds(),
                )
                handoff_body = _structured(result_handoff)
                assert handoff_body["ok"] is True
                returned = await _call(
                    session,
                    "task.return",
                    {
                        "status": "blocked",
                        "result_ref": handoff_body["payload"]["ref"],
                    },
                    _probe_request_timeout_seconds(),
                )
                return _structured(returned)

    completion = asyncio.run(run())
    assert completion["ok"] is True
    assert completion["payload"]["status"] == "blocked"
    assert completion["payload"]["semantic_return_receipt_digest"]
    from aota_forge.work_plane.task_return_receipt import read_task_return_receipt

    receipt = read_task_return_receipt(
        _sandbox(tmp_path / "semantic-return-worktree"), "timeout-contract-probe-task"
    )
    assert receipt is not None
    assert receipt.status == "blocked"
