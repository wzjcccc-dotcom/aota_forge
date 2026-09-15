"""AF #58 M2 — trusted interactive session ingress focused tests.

Covers the W1/W2 contracts of the interactive ingress without any live
server: mechanical preflight/no-authority, canonical Plan-ref intent,
exact session/directory/profile verification, same-session binding,
binding envelope reuse, PlanAuthorityBinding reuse, trusted project/source/
root resolution, one-session-one-Plan, profile-only/wrong-profile/
wrong-session/cross-session/rebind denial, stale preparation, role.bootstrap
first + trusted context, headless/interactive binding parity and interactive
parent -> existing Worker binding.
"""

from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from aota_forge.adapters.opencode.host_client import OpenCodeHostClient, OpenCodeHttpResponse
from aota_forge.adapters.opencode.task_main import read_binding_pointer
from aota_forge.core.ingress import bind_execution_dispatcher, reset_execution_dispatcher
from aota_forge.interactive_ingress import contract as ingress_contract
from aota_forge.interactive_ingress import service as ingress_service
from aota_forge.interactive_ingress.contract import (
    INTERACTIVE_SCHEMA,
    InteractiveIngressError,
    derive_interactive_plan_id,
    extract_canonical_plan_refs,
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
    sweep_interactive_preparations,
)
from aota_forge.runtime.trusted_runtime_binding import (
    TrustedBindingError,
    create_task_main_envelope,
    load_binding_from_envelope,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
WRAPPER_PATH = REPO_ROOT / "scripts" / "opencode_aota_mcp_server.py"

PROJECT_ID = "aota-reader"
SOURCE_REPOSITORY = "wzjcccc-dotcom/aota_reader_mcp"
PLAN_REF = "wzjcccc-dotcom/aota-hermes-tools#39"
PLAN_REF_OTHER = "wzjcccc-dotcom/aota-hermes-tools#57"
SESSION_ID = "ses_af58m2interactivesession01"

MANIFEST = (
    "schema_version: 1\nproject:\n"
    "  id: {project_id}\n  name: t\n  kind: test\n  status: active\n"
    "summary: test\ncapabilities: []\npaths:\n  source_root: .\n  source: []\n"
    "  docs: []\n  scripts: []\n  profiles: []\n  skills: []\n  tests: []\n"
    "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
    "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
    "codegraph:\n  enabled: false\n  index_location: .codegraph\n"
    "plan:\n  active_plan_id: null\nconstraints: []\n"
)

PLAN_BODY_TEMPLATE = (
    "PLAN_STATUS=active\n"
    "CURRENT_MILESTONE=M1\n"
    "CURRENT_STATUS=ready\n"
    "PROJECT_ID={project_id}\n"
    "SOURCE_REPOSITORY={source_repository}\n"
    "KNOWN_SOURCE_ROOT={root}\n"
    "M1_STATUS=ready\n"
    "M1_USER_APPROVAL_SATISFIED=no\n"
)


@pytest.fixture(autouse=True)
def _isolate_ingress_dispatcher():
    yield
    reset_execution_dispatcher()


class FakePreparedHost:
    """Records calls; never dispatches a prompt unless a test says so."""

    def __init__(self, row: dict[str, Any] | None = None, error: Exception | None = None) -> None:
        self.row = row
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def get_session(self, session_id: str, **_kwargs: Any) -> dict[str, Any]:
        self.calls.append(("get_session", session_id))
        if self.error is not None:
            raise self.error
        assert self.row is not None
        return dict(self.row)


def _make_checkout(tmp_path: Path, *, origin: str | None = None) -> Path:
    root = tmp_path / "source-checkout"
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(
        MANIFEST.format(project_id=PROJECT_ID), encoding="utf-8"
    )
    if not (root / ".git").exists():
        subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(
        ["git", "remote", "remove", "origin"],
        cwd=root,
        check=False,
        capture_output=True,
    )
    subprocess.run(
        ["git", "remote", "add", "origin", origin or f"https://github.com/{SOURCE_REPOSITORY}.git"],
        cwd=root,
        check=True,
    )
    return root


def _registry(tmp_path: Path, root: Path) -> Path:
    path = tmp_path / "workspace-registry.json"
    path.write_text(json.dumps({"ws-test": {"candidates": [str(root)]}}), encoding="utf-8")
    return path


def _runtime_config(tmp_path: Path, *, executor: str = "hermes") -> Path:
    executable = tmp_path / "host-stub"
    executable.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    executable.chmod(0o755)
    path = tmp_path / f"runtime-{executor}.json"

    def worker_binding() -> dict[str, Any]:
        binding: dict[str, Any] = {"profile": "aota-worker"}
        if executor == "hermes":
            binding["toolsets"] = ["aota"]
        return binding

    payload: dict[str, Any] = {
        "executor": executor,
        "executable": str(executable),
        "concurrency": 1,
        "provider": "test-provider",
        "model": "test-model",
        "bindings": {
            "coder": worker_binding(),
            "reviewer": worker_binding(),
            "analyst": worker_binding(),
            "project-steward": worker_binding(),
            "task-main": {"profile": "aota-task-main"},
        },
    }
    if executor == "opencode":
        payload["host_endpoint"] = "http://127.0.0.1:4096"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


class FakePlanSnapshot:
    def __init__(self, body: str, revision: str = "rev-1") -> None:
        self.body = body
        self.revision = revision
        self.digest = hashlib.sha256(body.encode("utf-8")).hexdigest()


def _plan_loader_for(
    root: Path,
    *,
    source_repository: str = SOURCE_REPOSITORY,
    project_id: str = PROJECT_ID,
    approval: str = "no",
):
    body = PLAN_BODY_TEMPLATE.format(
        project_id=project_id, source_repository=source_repository, root=root
    ).replace("M1_USER_APPROVAL_SATISFIED=no", f"M1_USER_APPROVAL_SATISFIED={approval}")
    snapshot = FakePlanSnapshot(body)

    def loader(_plan_ref: str) -> FakePlanSnapshot:
        return snapshot

    return loader


def _reserve(tmp_path: Path, *, ttl_seconds: float | None = None) -> dict[str, Any]:
    runtime = _runtime_config(tmp_path)
    checkout = _make_checkout(tmp_path)
    registry = _registry(tmp_path, checkout)
    kwargs: dict[str, Any] = {}
    if ttl_seconds is not None:
        kwargs["ttl_seconds"] = ttl_seconds
    return reserve_interactive_session(
        workspace_root=tmp_path / "interactive",
        runtime_config_path=runtime,
        registry_path=registry,
        **kwargs,
    )


def _prepared_row(reserved: dict[str, Any], session_id: str = SESSION_ID) -> dict[str, Any]:
    return {
        "id": session_id,
        "directory": reserved["instance_dir"],
        "agent": "aota-task-main",
        "parentID": None,
        "metadata": {
            "af_interactive_preparation": reserved["preparation_id"],
            "af_interactive_schema": INTERACTIVE_SCHEMA,
        },
    }


def _workspace_paths(tmp_path: Path) -> tuple[Path, Path, Path]:
    runtime = _runtime_config(tmp_path)
    checkout = _make_checkout(tmp_path)
    registry = _registry(tmp_path, checkout)
    return runtime, checkout, registry


def _bind(
    tmp_path: Path,
    reserved: dict[str, Any],
    *,
    row: dict[str, Any] | None = None,
    message: str = f"執行 {PLAN_REF}",
    profile: str = "aota-task-main",
    session_id: str = SESSION_ID,
    host: FakePreparedHost | None = None,
    loader: Any | None = None,
) -> dict[str, Any]:
    runtime, checkout, registry = _workspace_paths(tmp_path)
    return bind_interactive_session(
        session_id=session_id,
        message_text=message,
        profile=profile,
        workspace_root=tmp_path / "interactive",
        runtime_config_path=runtime,
        registry_path=registry,
        host_client=host or FakePreparedHost(row or _prepared_row(reserved, session_id)),
        plan_loader=loader or _plan_loader_for(checkout),
    )


# ---------------------------------------------------------------------------
# W1 — preflight contract, no authority, canonical Plan intent
# ---------------------------------------------------------------------------


class TestInteractivePreflight:
    def test_preparation_grants_no_authority(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        instance_dir = Path(reserved["instance_dir"])
        assert reserved["authority"] is False
        assert instance_dir.is_dir()
        assert not (instance_dir / ".aota" / "opencode" / "active_binding.json").exists()
        assert not (Path(reserved["worktree_root"]) / ".aota" / "task-main-thin-bootstrap.json").exists()
        record = json.loads(
            (Path(reserved["session_root"]) / "preparation.json").read_text(encoding="utf-8")
        )
        assert record["state"] == "prepared"
        assert "authority" not in record
        assert PREPARATION_TOKEN_IS_AUTHORITY is False
        assert ingress_contract.PREPARATION_STATE_PREPARED == "prepared"

    def test_instance_namespace_is_unique_per_preparation(self, tmp_path: Path) -> None:
        first = _reserve(tmp_path)
        second = _reserve(tmp_path)
        assert first["instance_dir"] != second["instance_dir"]
        assert first["instance_key"] != second["instance_key"]
        assert first["preparation_id"] != second["preparation_id"]
        assert first["session_root"] != second["session_root"]

    def test_canonical_plan_ref_extraction(self) -> None:
        assert extract_canonical_plan_refs(f"執行 {PLAN_REF}") == (PLAN_REF,)
        assert extract_canonical_plan_refs("幫我跑那個 Reader plan") == ()
        assert extract_canonical_plan_refs("#39") == ()
        assert extract_canonical_plan_refs("aota-hermes-tools#39") == ()
        assert extract_canonical_plan_refs(f"{PLAN_REF} please") == (PLAN_REF,)
        assert extract_canonical_plan_refs(f"{PLAN_REF} then {PLAN_REF_OTHER}") == (
            PLAN_REF,
            PLAN_REF_OTHER,
        )
        # duplicate of the same reference is still one intent
        assert extract_canonical_plan_refs(f"{PLAN_REF} / {PLAN_REF}") == (PLAN_REF,)

    def test_interactive_plan_id_is_deterministic_canonical(self) -> None:
        plan_id = derive_interactive_plan_id(PLAN_REF)
        assert plan_id == "plan_wzjcccc_dotcom_aota_hermes_tools_39"
        assert derive_interactive_plan_id(PLAN_REF) == plan_id
        with pytest.raises(InteractiveIngressError):
            derive_interactive_plan_id("not-a-plan-ref")

    def test_ambiguous_plan_intent_fails_closed(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        with pytest.raises(InteractiveIngressError) as excinfo:
            _bind(tmp_path, reserved, message=f"{PLAN_REF} and {PLAN_REF_OTHER}")
        assert excinfo.value.code == "PLAN_REF_AMBIGUOUS"

    def test_missing_plan_intent_fails_closed(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        with pytest.raises(InteractiveIngressError) as excinfo:
            _bind(tmp_path, reserved, message="幫我跑那個 Reader plan")
        assert excinfo.value.code == "PLAN_REF_MISSING"

    def test_failed_preflight_never_prompts(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        host = FakePreparedHost(_prepared_row(reserved))
        with pytest.raises(InteractiveIngressError):
            _bind(tmp_path, reserved, message="no plan here", host=host)
        assert host.calls == [("get_session", SESSION_ID)]


# ---------------------------------------------------------------------------
# W2 — exact S0 verification + trusted binding materialization
# ---------------------------------------------------------------------------


class TestExactSessionBinding:
    def test_same_session_bound_with_exact_identity(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        host = FakePreparedHost(_prepared_row(reserved))
        result = bind_interactive_session(
            session_id=SESSION_ID,
            message_text=f"執行 {PLAN_REF}",
            profile="aota-task-main",
            workspace_root=tmp_path / "interactive",
            runtime_config_path=runtime,
            registry_path=registry,
            host_client=host,
            plan_loader=_plan_loader_for(checkout),
        )
        assert result["ok"] is True
        assert result["session_id"] == SESSION_ID
        assert host.calls == [("get_session", SESSION_ID)]
        pointer = read_binding_pointer(reserved["instance_dir"])
        assert pointer["kind"] == "task-main"
        assert str(pointer["instance_key"]) == reserved["instance_key"]
        bootstrap = json.loads(Path(pointer["bootstrap_path"]).read_text(encoding="utf-8"))
        assert bootstrap["origin_task_main_session_ref"] == SESSION_ID
        assert bootstrap["project_id"] == PROJECT_ID
        assert bootstrap["source_repository"] == SOURCE_REPOSITORY
        assert bootstrap["plan_ref"] == PLAN_REF
        assert bootstrap["plan_id"] == derive_interactive_plan_id(PLAN_REF)
        assert bootstrap["registry_path"] == str(registry)
        assert read_bound_plan_ref(reserved["instance_dir"]) == PLAN_REF

    def test_unknown_session_fails_closed(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        with pytest.raises(InteractiveIngressError) as excinfo:
            bind_interactive_session(
                session_id=SESSION_ID,
                message_text=f"執行 {PLAN_REF}",
                profile="aota-task-main",
                workspace_root=tmp_path / "interactive",
                runtime_config_path=runtime,
                registry_path=registry,
                host_client=FakePreparedHost(error=RuntimeError("not found")),
                plan_loader=_plan_loader_for(checkout),
            )
        assert excinfo.value.code == "UNKNOWN_SESSION"

    def test_non_exact_session_row_fails_closed(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        row = _prepared_row(reserved)
        row["id"] = "ses_someothersession0000000000"
        with pytest.raises(InteractiveIngressError) as excinfo:
            bind_interactive_session(
                session_id=SESSION_ID,
                message_text=f"執行 {PLAN_REF}",
                profile="aota-task-main",
                workspace_root=tmp_path / "interactive",
                runtime_config_path=runtime,
                registry_path=registry,
                host_client=FakePreparedHost(row),
                plan_loader=_plan_loader_for(checkout),
            )
        assert excinfo.value.code == "UNKNOWN_SESSION"

    def test_wrong_directory_fails_closed(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        row = _prepared_row(reserved)
        other = tmp_path / "other-instance"
        other.mkdir()
        row["directory"] = str(other)
        with pytest.raises(InteractiveIngressError) as excinfo:
            bind_interactive_session(
                session_id=SESSION_ID,
                message_text=f"執行 {PLAN_REF}",
                profile="aota-task-main",
                workspace_root=tmp_path / "interactive",
                runtime_config_path=runtime,
                registry_path=registry,
                host_client=FakePreparedHost(row),
                plan_loader=_plan_loader_for(checkout),
            )
        assert excinfo.value.code == "SESSION_DIRECTORY_MISMATCH"

    def test_profile_only_agent_grants_nothing(self, tmp_path: Path) -> None:
        """A session with agent=aota-task-main but no preparation metadata."""
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        row = {
            "id": SESSION_ID,
            "directory": reserved["instance_dir"],
            "agent": "aota-task-main",
            "parentID": None,
            "metadata": None,
        }
        with pytest.raises(InteractiveIngressError) as excinfo:
            bind_interactive_session(
                session_id=SESSION_ID,
                message_text=f"執行 {PLAN_REF}",
                profile="aota-task-main",
                workspace_root=tmp_path / "interactive",
                runtime_config_path=runtime,
                registry_path=registry,
                host_client=FakePreparedHost(row),
                plan_loader=_plan_loader_for(checkout),
            )
        assert excinfo.value.code == "SESSION_NOT_AF_INTERACTIVE"

    def test_wrong_profile_bind_denied(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        with pytest.raises(InteractiveIngressError) as excinfo:
            bind_interactive_session(
                session_id=SESSION_ID,
                message_text=f"執行 {PLAN_REF}",
                profile="aota-worker",
                workspace_root=tmp_path / "interactive",
                runtime_config_path=runtime,
                registry_path=registry,
                host_client=FakePreparedHost(_prepared_row(reserved)),
                plan_loader=_plan_loader_for(checkout),
            )
        assert excinfo.value.code == "PROFILE_MISMATCH"

    def test_session_row_wrong_agent_denied(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        row = _prepared_row(reserved)
        row["agent"] = "build"
        with pytest.raises(InteractiveIngressError) as excinfo:
            bind_interactive_session(
                session_id=SESSION_ID,
                message_text=f"執行 {PLAN_REF}",
                profile="aota-task-main",
                workspace_root=tmp_path / "interactive",
                runtime_config_path=runtime,
                registry_path=registry,
                host_client=FakePreparedHost(row),
                plan_loader=_plan_loader_for(checkout),
            )
        assert excinfo.value.code == "PROFILE_MISMATCH"

    def test_child_session_denied(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        row = _prepared_row(reserved)
        row["parentID"] = "ses_parent00000000000000000000"
        with pytest.raises(InteractiveIngressError) as excinfo:
            bind_interactive_session(
                session_id=SESSION_ID,
                message_text=f"執行 {PLAN_REF}",
                profile="aota-task-main",
                workspace_root=tmp_path / "interactive",
                runtime_config_path=runtime,
                registry_path=registry,
                host_client=FakePreparedHost(row),
                plan_loader=_plan_loader_for(checkout),
            )
        assert excinfo.value.code == "SESSION_NOT_AF_INTERACTIVE"

    def test_stale_preparation_fails_closed(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path, ttl_seconds=1.0)
        record = json.loads(
            (Path(reserved["session_root"]) / "preparation.json").read_text(encoding="utf-8")
        )
        record["expires_at"] = 1.0
        (Path(reserved["session_root"]) / "preparation.json").write_text(
            json.dumps(record), encoding="utf-8"
        )
        runtime, checkout, registry = _workspace_paths(tmp_path)
        with pytest.raises(InteractiveIngressError) as excinfo:
            bind_interactive_session(
                session_id=SESSION_ID,
                message_text=f"執行 {PLAN_REF}",
                profile="aota-task-main",
                workspace_root=tmp_path / "interactive",
                runtime_config_path=runtime,
                registry_path=registry,
                host_client=FakePreparedHost(_prepared_row(reserved)),
                plan_loader=_plan_loader_for(checkout),
            )
        assert excinfo.value.code == "STALE_PREPARATION"


# ---------------------------------------------------------------------------
# W2 — existing binding format reuse + Plan authority reuse
# ---------------------------------------------------------------------------


class TestBindingFormatReuse:
    def test_envelope_is_the_existing_task_main_format(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        result = bind_interactive_session(
            session_id=SESSION_ID,
            message_text=f"執行 {PLAN_REF}",
            profile="aota-task-main",
            workspace_root=tmp_path / "interactive",
            runtime_config_path=runtime,
            registry_path=registry,
            host_client=FakePreparedHost(_prepared_row(reserved)),
            plan_loader=_plan_loader_for(checkout),
        )
        envelope_path = Path(result["envelope_path"])
        assert envelope_path.parent.name == "pre-resolved-bindings"
        assert envelope_path.name.startswith("task-main-")
        data = json.loads(envelope_path.read_text(encoding="utf-8"))
        assert data["kind"] == "task-main"
        assert data["version"] == 1
        assert data["payload"]["kind"] == "task-main"
        assert data["payload"]["provenance"]["ingress"] == "interactive"
        assert data["payload"]["provenance"]["session_id"] == SESSION_ID
        assert data["payload"]["provenance"]["preparation_id"] == reserved["preparation_id"]
        assert MATERIALIZE_REUSES_EXISTING_TASK_MAIN_FORMAT is True
        assert SECOND_BINDING_ONTOLOGY_CREATED is False
        assert SECOND_PLAN_PARSER_CREATED is False

    def test_staged_envelope_and_pointer_are_contained(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        result = bind_interactive_session(
            session_id=SESSION_ID,
            message_text=f"執行 {PLAN_REF}",
            profile="aota-task-main",
            workspace_root=tmp_path / "interactive",
            runtime_config_path=runtime,
            registry_path=registry,
            host_client=FakePreparedHost(_prepared_row(reserved)),
            plan_loader=_plan_loader_for(checkout),
        )
        instance_dir = Path(result["instance_dir"]).resolve()
        pointer = read_binding_pointer(instance_dir)
        envelope_path = Path(pointer["envelope_path"]).resolve()
        envelope_path.relative_to(instance_dir / ".aota")
        bootstrap_path = Path(pointer["bootstrap_path"]).resolve()
        bootstrap_path.relative_to(Path(reserved["worktree_root"]) / ".aota")
        assert pointer["binding_root"] == reserved["worktree_root"]

    def test_role_bootstrap_observes_trusted_context(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        result = bind_interactive_session(
            session_id=SESSION_ID,
            message_text=f"執行 {PLAN_REF}",
            profile="aota-task-main",
            workspace_root=tmp_path / "interactive",
            runtime_config_path=runtime,
            registry_path=registry,
            host_client=FakePreparedHost(_prepared_row(reserved)),
            plan_loader=_plan_loader_for(checkout),
        )
        binding = load_binding_from_envelope(result["envelope_path"])
        from aota_forge.work_plane.role_bootstrap import handle_role_bootstrap

        bootstrap = handle_role_bootstrap(binding, {})
        assert bootstrap["ROLE"] == "task-main"
        gate = bootstrap["APPROVAL_GATE"]
        assert gate["required"] is True
        assert gate["current_milestone"] == "M1"
        assert gate["MECHANICAL_GATE_ENFORCED"] is True
        plan_state = bootstrap["TRUSTED_PLAN_STATE"]
        assert plan_state["plan_ref"] == PLAN_REF
        assert plan_state["plan_id"] == derive_interactive_plan_id(PLAN_REF)
        assert plan_state["current_milestone"] == "M1"
        assert plan_state["milestone_user_approval_satisfied"] is False
        authority = bootstrap["PLAN_AUTHORITY"]
        assert authority["source_kind"] == "github_issue"
        assert authority["authority_ref"] == PLAN_REF
        assert authority["IS_AUTHORITY"] is False
        context = bootstrap["TRUSTED_PROJECT_CONTEXT"]
        assert context["project"]["project_id"] == PROJECT_ID
        assert context["project"]["source_repository"] == SOURCE_REPOSITORY
        assert context["plan"]["plan_ref"] == PLAN_REF
        assert set(context["roots"]) == {"project-main", "active-worktree"}
        assert bootstrap["CURRENT_EXECUTION_CONTEXT"]["plan_ref"] == PLAN_REF

    def test_headless_and_interactive_bindings_converge(self, tmp_path: Path) -> None:
        """Same binding format; interactive adds the bounded Plan projection."""
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        result = bind_interactive_session(
            session_id=SESSION_ID,
            message_text=f"執行 {PLAN_REF}",
            profile="aota-task-main",
            workspace_root=tmp_path / "interactive",
            runtime_config_path=runtime,
            registry_path=registry,
            host_client=FakePreparedHost(_prepared_row(reserved)),
            plan_loader=_plan_loader_for(checkout),
        )
        interactive_binding = load_binding_from_envelope(result["envelope_path"])

        # Headless production path: DailyTaskMainLauncher.prepare (thin) with
        # the exact same trusted inputs, materializing the same bootstrap.
        from aota_forge.composition.task_main_daily_launcher import DailyTaskMainLauncher

        headless_root = tmp_path / "headless-worktree"
        headless_root.mkdir()
        context = DailyTaskMainLauncher().prepare(
            worktree_root=headless_root,
            project_id=PROJECT_ID,
            worktree_id="headless-probe",
            runtime_config_path=runtime,
            origin_task_main_session_ref="ses_headless000000000000000000",
            plan_ref=PLAN_REF,
            source_repository=SOURCE_REPOSITORY,
            registry_path=registry,
        )
        from aota_forge.composition.task_main_runtime_selection import (
            build_thin_task_main_binding_from_envelope_bootstrap,
        )

        headless_content = json.loads(
            (headless_root / ".aota" / "task-main-thin-bootstrap.json").read_text(encoding="utf-8")
        )
        headless_binding = build_thin_task_main_binding_from_envelope_bootstrap(headless_content)

        def root_contract(binding: Any) -> Any:
            return sorted(
                (root.root_ref, tuple(root.capabilities), str(root.root_kind))
                for root in binding.effective_authorized_roots.roots
            )

        assert interactive_binding.task_main_runtime_path == headless_binding.task_main_runtime_path
        assert interactive_binding.project_id == headless_binding.project_id
        assert interactive_binding.source_repository == headless_binding.source_repository
        assert interactive_binding.sandbox.project_root == headless_binding.sandbox.project_root
        assert interactive_binding.plan_binding.plan_ref == headless_binding.plan_binding.plan_ref
        assert root_contract(interactive_binding) == root_contract(headless_binding)
        interactive_project_main = next(
            root
            for root in interactive_binding.effective_authorized_roots.roots
            if root.root_ref == "project-main"
        )
        headless_project_main = next(
            root
            for root in headless_binding.effective_authorized_roots.roots
            if root.root_ref == "project-main"
        )
        assert interactive_project_main.root_path == headless_project_main.root_path
        # Interactive-only bounded enrichment; headless keeps the accepted shape.
        assert interactive_binding.trusted_plan_state is not None
        assert headless_binding.trusted_plan_state is None
        assert interactive_binding.plan_authority_binding is not None
        assert headless_binding.plan_authority_binding is None

    def test_trusted_plan_state_validation_fails_closed(self) -> None:
        good = {
            "plan_ref": PLAN_REF,
            "plan_id": derive_interactive_plan_id(PLAN_REF),
            "source_kind": "github_issue",
            "current_milestone": "M1",
            "milestone_user_approval_satisfied": False,
        }
        assert validate_trusted_plan_state(good) == good
        for bad in (
            {**good, "unexpected": "x"},
            {**good, "source_digest": "not-a-digest"},
            {**good, "milestone_user_approval_satisfied": "no"},
            {"plan_ref": PLAN_REF},
        ):
            with pytest.raises(InteractiveIngressError):
                validate_trusted_plan_state(bad)


# ---------------------------------------------------------------------------
# W2 — trusted project/source/root resolution
# ---------------------------------------------------------------------------


class TestTrustedProjectResolution:
    def test_plan_source_repository_mismatch_denied(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime = _runtime_config(tmp_path)
        checkout = _make_checkout(tmp_path, origin="https://github.com/wzjcccc-dotcom/other_repo.git")
        registry = _registry(tmp_path, checkout)
        with pytest.raises(InteractiveIngressError) as excinfo:
            bind_interactive_session(
                session_id=SESSION_ID,
                message_text=f"執行 {PLAN_REF}",
                profile="aota-task-main",
                workspace_root=tmp_path / "interactive",
                runtime_config_path=runtime,
                registry_path=registry,
                host_client=FakePreparedHost(_prepared_row(reserved)),
                plan_loader=_plan_loader_for(checkout),
            )
        assert excinfo.value.code == "PROJECT_RESOLUTION_FAILED"

    def test_unknown_project_denied(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        with pytest.raises(InteractiveIngressError) as excinfo:
            bind_interactive_session(
                session_id=SESSION_ID,
                message_text=f"執行 {PLAN_REF}",
                profile="aota-task-main",
                workspace_root=tmp_path / "interactive",
                runtime_config_path=runtime,
                registry_path=registry,
                host_client=FakePreparedHost(_prepared_row(reserved)),
                plan_loader=_plan_loader_for(checkout, project_id="unknown-project"),
            )
        assert excinfo.value.code == "PROJECT_RESOLUTION_FAILED"

    def test_declared_root_mismatch_denied(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        with pytest.raises(InteractiveIngressError) as excinfo:
            bind_interactive_session(
                session_id=SESSION_ID,
                message_text=f"執行 {PLAN_REF}",
                profile="aota-task-main",
                workspace_root=tmp_path / "interactive",
                runtime_config_path=runtime,
                registry_path=registry,
                host_client=FakePreparedHost(_prepared_row(reserved)),
                plan_loader=_plan_loader_for(tmp_path / "other-declared-root"),
            )
        assert excinfo.value.code in {"PROJECT_RESOLUTION_FAILED", "PROJECT_ROOT_MISMATCH"}

    def test_missing_plan_project_identity_denied(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, _checkout, registry = _workspace_paths(tmp_path)
        body = "PLAN_STATUS=active\nCURRENT_MILESTONE=M1\nM1_STATUS=ready\n"
        with pytest.raises(InteractiveIngressError) as excinfo:
            bind_interactive_session(
                session_id=SESSION_ID,
                message_text=f"執行 {PLAN_REF}",
                profile="aota-task-main",
                workspace_root=tmp_path / "interactive",
                runtime_config_path=runtime,
                registry_path=registry,
                host_client=FakePreparedHost(_prepared_row(reserved)),
                plan_loader=lambda _ref: FakePlanSnapshot(body),
            )
        assert excinfo.value.code == "PLAN_PROJECT_IDENTITY_MISSING"


# ---------------------------------------------------------------------------
# W2 — one session one Plan / lifecycle
# ---------------------------------------------------------------------------


class TestOneSessionOnePlan:
    def test_same_plan_retry_is_idempotent(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        loader = _plan_loader_for(checkout)
        first = bind_interactive_session(
            session_id=SESSION_ID,
            message_text=f"執行 {PLAN_REF}",
            profile="aota-task-main",
            workspace_root=tmp_path / "interactive",
            runtime_config_path=runtime,
            registry_path=registry,
            host_client=FakePreparedHost(_prepared_row(reserved)),
            plan_loader=loader,
        )
        second = bind_interactive_session(
            session_id=SESSION_ID,
            message_text=f"again {PLAN_REF}",
            profile="aota-task-main",
            workspace_root=tmp_path / "interactive",
            runtime_config_path=runtime,
            registry_path=registry,
            host_client=FakePreparedHost(_prepared_row(reserved)),
            plan_loader=loader,
        )
        assert first["idempotent"] is False
        assert second["idempotent"] is True
        assert second["plan_ref"] == PLAN_REF

    def test_plain_message_on_bound_session_is_allowed(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        loader = _plan_loader_for(checkout)
        bind_interactive_session(
            session_id=SESSION_ID,
            message_text=f"執行 {PLAN_REF}",
            profile="aota-task-main",
            workspace_root=tmp_path / "interactive",
            runtime_config_path=runtime,
            registry_path=registry,
            host_client=FakePreparedHost(_prepared_row(reserved)),
            plan_loader=loader,
        )
        result = bind_interactive_session(
            session_id=SESSION_ID,
            message_text="keep going",
            profile="aota-task-main",
            workspace_root=tmp_path / "interactive",
            runtime_config_path=runtime,
            registry_path=registry,
            host_client=FakePreparedHost(_prepared_row(reserved)),
            plan_loader=loader,
        )
        assert result["idempotent"] is True

    def test_different_plan_rebind_denied(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        loader = _plan_loader_for(checkout)
        bind_interactive_session(
            session_id=SESSION_ID,
            message_text=f"執行 {PLAN_REF}",
            profile="aota-task-main",
            workspace_root=tmp_path / "interactive",
            runtime_config_path=runtime,
            registry_path=registry,
            host_client=FakePreparedHost(_prepared_row(reserved)),
            plan_loader=loader,
        )
        with pytest.raises(InteractiveIngressError) as excinfo:
            bind_interactive_session(
                session_id=SESSION_ID,
                message_text=f"also {PLAN_REF_OTHER}",
                profile="aota-task-main",
                workspace_root=tmp_path / "interactive",
                runtime_config_path=runtime,
                registry_path=registry,
                host_client=FakePreparedHost(_prepared_row(reserved)),
                plan_loader=loader,
            )
        assert excinfo.value.code == "SESSION_ALREADY_BOUND_DIFFERENT_PLAN"

    def test_cross_session_bind_denied(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        loader = _plan_loader_for(checkout)
        bind_interactive_session(
            session_id=SESSION_ID,
            message_text=f"執行 {PLAN_REF}",
            profile="aota-task-main",
            workspace_root=tmp_path / "interactive",
            runtime_config_path=runtime,
            registry_path=registry,
            host_client=FakePreparedHost(_prepared_row(reserved)),
            plan_loader=loader,
        )
        other_session = "ses_otheraf58m2session00000002"
        row = _prepared_row(reserved, session_id=other_session)
        with pytest.raises(InteractiveIngressError) as excinfo:
            bind_interactive_session(
                session_id=other_session,
                message_text=f"執行 {PLAN_REF}",
                profile="aota-task-main",
                workspace_root=tmp_path / "interactive",
                runtime_config_path=runtime,
                registry_path=registry,
                host_client=FakePreparedHost(row),
                plan_loader=loader,
            )
        assert excinfo.value.code == "CROSS_SESSION_BIND"


# ---------------------------------------------------------------------------
# W2 — wrapper fail-closed + cleanup
# ---------------------------------------------------------------------------


class TestUnboundInstanceFailClosed:
    def test_wrapper_denies_unbound_instance(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        env = dict(os.environ)
        env["AOTA_FORGE_REPO_ROOT"] = str(REPO_ROOT)
        proc = subprocess.run(
            [sys.executable, str(WRAPPER_PATH)],
            cwd=reserved["instance_dir"],
            env=env,
            capture_output=True,
            text=True,
            timeout=60,
        )
        assert proc.returncode != 0
        combined = f"{proc.stdout}\n{proc.stderr}"
        assert "pointer" in combined.lower() or "binding" in combined.lower()

    def test_sweep_removes_expired_unbound_preparation(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path, ttl_seconds=1.0)
        record_path = Path(reserved["session_root"]) / "preparation.json"
        record = json.loads(record_path.read_text(encoding="utf-8"))
        record["expires_at"] = 1.0
        record_path.write_text(json.dumps(record), encoding="utf-8")
        result = sweep_interactive_preparations(workspace_root=tmp_path / "interactive")
        assert reserved["preparation_id"] in result["removed"]
        assert not Path(reserved["session_root"]).exists()

    def test_sweep_keeps_active_preparation(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        result = sweep_interactive_preparations(workspace_root=tmp_path / "interactive")
        assert reserved["preparation_id"] not in result["removed"]
        assert Path(reserved["session_root"]).exists()


# ---------------------------------------------------------------------------
# W2 — interactive parent feeds the EXISTING Worker path (no second path)
# ---------------------------------------------------------------------------


class FakeWorkerHostClient:
    """Structurally compatible OpenCode host-client double (records only)."""

    def __init__(self) -> None:
        self.sessions: list[dict[str, Any]] = []
        self.prompts: list[dict[str, Any]] = []
        self._counter = 0

    def create_session(self, **kwargs: Any) -> dict[str, Any]:
        self._counter += 1
        session_id = f"ses_worker{self._counter:020d}"
        row = {
            "id": session_id,
            "directory": kwargs.get("directory"),
            "agent": kwargs.get("agent"),
            "parentID": kwargs.get("parent_id"),
            "metadata": kwargs.get("metadata"),
            "title": kwargs.get("title"),
            "model": kwargs.get("model"),
        }
        self.sessions.append({**kwargs, "session_id": session_id})
        return row

    def submit_prompt_async(self, session_id: str, **kwargs: Any) -> None:
        self.prompts.append({"session_id": session_id, **kwargs})


class TestInteractiveParentWorkerPath:
    def test_interactive_parent_dispatches_existing_worker_binding(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime = _runtime_config(tmp_path, executor="opencode")
        checkout = _make_checkout(tmp_path)
        registry = _registry(tmp_path, checkout)
        bound = bind_interactive_session(
            session_id=SESSION_ID,
            message_text=f"執行 {PLAN_REF}",
            profile="aota-task-main",
            workspace_root=tmp_path / "interactive",
            runtime_config_path=runtime,
            registry_path=registry,
            host_client=FakePreparedHost(_prepared_row(reserved)),
            plan_loader=_plan_loader_for(checkout, approval="yes"),
        )
        bootstrap = json.loads(Path(bound["bootstrap_path"]).read_text(encoding="utf-8"))
        assert bootstrap["trusted_plan_state"]["milestone_user_approval_satisfied"] is True

        from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host

        host_client = FakeWorkerHostClient()
        host = compose_thin_task_main_host(
            worktree_root=bootstrap["worktree_root"],
            project_id=bootstrap["project_id"],
            worktree_id=bootstrap["worktree_id"],
            runtime_config_path=runtime,
            origin_task_main_session_ref=SESSION_ID,
            plan_ref=bootstrap["plan_ref"],
            plan_id=bootstrap["plan_id"],
            source_repository=bootstrap["source_repository"],
            registry_path=registry,
            trusted_plan_state=bootstrap["trusted_plan_state"],
            host_client=host_client,
        )
        bind_execution_dispatcher(host.execution_dispatcher)

        write = host.invoke(
            "handoff.write",
            {
                "mode": "work_item",
                "payload": {
                    "work_role": "coder",
                    "task_kind": "af58-m2-worker-proof",
                    "work_item_ref": "W1",
                    "milestone_ref": "M1",
                    "objective": "bounded interactive parent worker binding proof",
                    "bounded_scope": "bounded M2 proof scope",
                    "validation_expectations": ["focused M2 validation"],
                    "semantic_stop_expectations": ["stop on insufficient evidence"],
                },
            },
        )
        assert write.get("is_success"), write
        started = host.invoke(
            "task.start",
            {"role": "coder", "handoff_ref": write["payload"]["ref"]},
        )
        assert started.get("is_success"), started

        assert len(host_client.sessions) == 1
        worker_session = host_client.sessions[0]
        assert worker_session["agent"] == "aota-worker"
        worker_instance = Path(worker_session["directory"])
        assert worker_instance.parent.name == "instances"
        assert worker_instance.name.startswith("worker-")
        worker_pointer = read_binding_pointer(worker_instance)
        assert worker_pointer["kind"] == "worker"
        worker_envelope = json.loads(
            Path(worker_pointer["envelope_path"]).read_text(encoding="utf-8")
        )
        assert worker_envelope["kind"] == "worker"
        assert len(host_client.prompts) == 1
        assert host_client.prompts[0]["agent"] == "aota-worker"
        assert host_client.prompts[0]["session_id"] == worker_session["session_id"]

        from aota_forge.work_plane.execution_identity import is_plan_aware_task_identity

        task_id = started["payload"]["task_id"]
        assert is_plan_aware_task_identity(task_id) is True
        assert f":{bootstrap['plan_id']}:" in task_id
        assert bind_execution_dispatcher is not None  # dispatcher seam reused, not recreated


# ---------------------------------------------------------------------------
# W3/W4 — mechanical approval gate for interactive bindings (I58-B001 repair)
# ---------------------------------------------------------------------------


class TestInteractiveApprovalGate:
    def test_task_start_denied_when_approval_unsatisfied(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime = _runtime_config(tmp_path, executor="opencode")
        checkout = _make_checkout(tmp_path)
        registry = _registry(tmp_path, checkout)
        bound = bind_interactive_session(
            session_id=SESSION_ID,
            message_text=f"執行 {PLAN_REF}",
            profile="aota-task-main",
            workspace_root=tmp_path / "interactive",
            runtime_config_path=runtime,
            registry_path=registry,
            host_client=FakePreparedHost(_prepared_row(reserved)),
            plan_loader=_plan_loader_for(checkout, approval="no"),
        )
        bootstrap = json.loads(Path(bound["bootstrap_path"]).read_text(encoding="utf-8"))

        from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host

        host_client = FakeWorkerHostClient()
        host = compose_thin_task_main_host(
            worktree_root=bootstrap["worktree_root"],
            project_id=bootstrap["project_id"],
            worktree_id=bootstrap["worktree_id"],
            runtime_config_path=runtime,
            origin_task_main_session_ref=SESSION_ID,
            plan_ref=bootstrap["plan_ref"],
            plan_id=bootstrap["plan_id"],
            source_repository=bootstrap["source_repository"],
            registry_path=registry,
            trusted_plan_state=bootstrap["trusted_plan_state"],
            host_client=host_client,
        )
        bind_execution_dispatcher(host.execution_dispatcher)

        denied = host.invoke(
            "task.start", {"role": "coder", "handoff_ref": "handoff:any"}
        )
        assert denied.get("is_success") is not True
        error = json.dumps(denied)
        assert "MILESTONE_APPROVAL_REQUIRED" in error
        assert host_client.sessions == []
        assert host_client.prompts == []

    def test_governance_mutation_denied_when_approval_unsatisfied(self, tmp_path: Path) -> None:
        reserved = _reserve(tmp_path)
        runtime, checkout, registry = _workspace_paths(tmp_path)
        bound = bind_interactive_session(
            session_id=SESSION_ID,
            message_text=f"執行 {PLAN_REF}",
            profile="aota-task-main",
            workspace_root=tmp_path / "interactive",
            runtime_config_path=runtime,
            registry_path=registry,
            host_client=FakePreparedHost(_prepared_row(reserved)),
            plan_loader=_plan_loader_for(checkout, approval="no"),
        )
        binding = load_binding_from_envelope(bound["envelope_path"])
        from aota_forge.mcp_transport import create_aota_invoke_dispatch

        invoke = create_aota_invoke_dispatch(binding)
        denied = invoke(
            "github.issue.comment.update",
            {"comment_id": "1", "body": "fabricated"},
        )
        assert "MILESTONE_APPROVAL_REQUIRED" in json.dumps(denied)
        denied_shell = invoke("restricted_shell.run", {"command": "true"})
        assert "MILESTONE_APPROVAL_REQUIRED" in json.dumps(denied_shell)

    def test_gate_absent_without_interactive_plan_state(self) -> None:
        from aota_forge.mcp_transport import _interactive_approval_gate_denial

        class _Bare:
            pass

        assert _interactive_approval_gate_denial(_Bare(), "task.start") is None

        class _Planless:
            trusted_plan_state = None

        assert _interactive_approval_gate_denial(_Planless(), "task.start") is None

        class _Approved:
            trusted_plan_state = {"milestone_user_approval_satisfied": True}

        assert _interactive_approval_gate_denial(_Approved(), "task.start") is None
