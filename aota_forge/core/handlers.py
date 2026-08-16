"""Canonical read-only operation handlers (M1).

Handlers are thin routes: resolve context, call Core domain, return bounded
payload.  They are registered into the Operation Contract Registry and are
invoked exclusively through the Unified Ingress.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from aota_forge.adapters.host import environment as host_env
from aota_forge.adapters.host import process as host_process
from aota_forge.adapters.host import runtime as host_runtime
from aota_forge.core.context import OperationContext
from aota_forge.core.contracts.errors import (
    ForgeError,
    ProjectBindingMissingError,
    RuntimeIdentityUnavailableError,
)
from aota_forge.core.contracts.operations import OperationContract, available_operations, register
from aota_forge.core.git.inspect import inspect_git
from aota_forge.core.project.resolver import (
    resolve_project_with_fingerprint,
    resolve_workspace,
)
from aota_forge.core.runtime.inspect import process_status, version_identity


def _registry_path(params: dict[str, Any]) -> Path:
    raw = params.get("registry_path")
    if not isinstance(raw, str) or not raw:
        raise ForgeError("PROJECT_REGISTRY_INVALID", "registry_path is required")
    return Path(raw)


def _handle_project_resolve(ctx: OperationContext) -> dict[str, Any]:
    workspace_id = ctx.params.get("workspace_id")
    project_id = ctx.params.get("project_id")
    if not isinstance(workspace_id, str) or not isinstance(project_id, str):
        raise ForgeError("PROJECT_NOT_FOUND", "workspace_id and project_id are required")
    result = resolve_project_with_fingerprint(workspace_id, _registry_path(ctx.params), project_id)
    return {"data": result}


def _handle_git_inspect(ctx: OperationContext) -> dict[str, Any]:
    workspace_id = ctx.params.get("workspace_id")
    project_id = ctx.params.get("project_id")
    if not isinstance(workspace_id, str) or not isinstance(project_id, str):
        raise ForgeError("PROJECT_NOT_FOUND", "workspace_id and project_id are required")
    resolved = resolve_project_with_fingerprint(workspace_id, _registry_path(ctx.params), project_id)
    project_root = Path(resolved["project_root"])
    result = inspect_git(project_root, boundary=project_root)
    return {"data": result}


def _handle_runtime_status(ctx: OperationContext) -> dict[str, Any]:
    pid = ctx.params.get("pid")
    if not isinstance(pid, int) or isinstance(pid, bool):
        raise ForgeError("RUNTIME_NOT_RUNNING", "pid must be an integer")
    return {"data": process_status(pid).to_dict()}


def _handle_host_status(ctx: OperationContext) -> dict[str, Any]:
    """Host mechanical read-only inspection with explicit diagnostics.

    Case A — caller explicitly requested project-bound semantics (both
    workspace_id and project_id supplied): deterministic resolution failure
    raises the root-cause error (PROJECT_BINDING_MISSING /
    PROJECT_REGISTRY_INVALID / PROJECT_NOT_FOUND / PROJECT_AMBIGUOUS) so the
    top-level operation never pretends full success.

    Case B — project augmentation not requested or only partially supplied:
    the independent Host diagnosis still completes, but every failed or
    unavailable optional check is explicitly visible through ``checks`` and
    ``warnings`` (never a silent errors=[]/warnings=[] success).
    """
    params = ctx.params
    warnings: list[str] = []
    data: dict[str, Any] = {"identity": host_env.safe_environment_identity()}
    checks: dict[str, dict[str, object]] = {
        key: {"requested": False, "state": "not_requested", "error_code": None, "message": None}
        for key in ("project", "runtime_identity", "process", "deployment_receipt")
    }

    workspace_id = params.get("workspace_id")
    project_id = params.get("project_id")
    registry_path = params.get("registry_path")
    workspace_supplied = isinstance(workspace_id, str) and bool(workspace_id)
    project_supplied = isinstance(project_id, str) and bool(project_id)
    registry_supplied = isinstance(registry_path, str) and bool(registry_path)
    project_requested = workspace_supplied and project_supplied

    if project_requested:
        checks["project"]["requested"] = True
        checks["runtime_identity"]["requested"] = True
        if not registry_supplied:
            checks["project"]["state"] = "failed"
            checks["project"]["error_code"] = "PROJECT_BINDING_MISSING"
            checks["project"]["message"] = "registry_path is required for project-bound binding"
            raise ProjectBindingMissingError(
                "registry_path is required for project-bound host status"
            )
        try:
            resolved = resolve_project_with_fingerprint(workspace_id, Path(registry_path), project_id)
        except ForgeError as exc:
            checks["project"]["state"] = "failed"
            checks["project"]["error_code"] = exc.code
            checks["project"]["message"] = exc.message
            raise
        checks["project"]["state"] = "available"
        data["project"] = dict(resolved)
        project_root = Path(resolved["project_root"])
        try:
            data["runtime_identity"] = host_runtime.host_runtime_identity(project_root)
            checks["runtime_identity"]["state"] = "available"
        except RuntimeIdentityUnavailableError as exc:
            data["runtime_identity"] = {
                "state": "not_available",
                "error_code": exc.code,
                "message": exc.message,
            }
            checks["runtime_identity"]["state"] = "not_available"
            checks["runtime_identity"]["error_code"] = exc.code
            warnings.append(f"runtime identity unavailable: {exc.code}")
    elif workspace_supplied or project_supplied:
        checks["project"]["requested"] = True
        checks["project"]["state"] = "failed"
        checks["project"]["error_code"] = "PROJECT_BINDING_MISSING"
        checks["project"]["message"] = "both workspace_id and project_id are required for project-bound binding"
        warnings.append("project-bound binding incomplete: both workspace_id and project_id are required")

    pidfile = params.get("pidfile")
    if isinstance(pidfile, str) and pidfile:
        checks["process"]["requested"] = True
        try:
            data["process"] = host_process.host_process_status(Path(pidfile))
            checks["process"]["state"] = "available"
        except ForgeError as exc:
            data["process"] = {"state": "failed", "error_code": exc.code, "message": exc.message}
            checks["process"]["state"] = "failed"
            checks["process"]["error_code"] = exc.code
            warnings.append(f"process check failed: {exc.code}")

    receipt = params.get("receipt")
    if isinstance(receipt, str) and receipt:
        checks["deployment_receipt"]["requested"] = True
        try:
            data["deployment_receipt"] = host_process.read_receipt(Path(receipt))
            checks["deployment_receipt"]["state"] = "available"
        except ForgeError as exc:
            data["deployment_receipt"] = {"state": "failed", "error_code": exc.code, "message": exc.message}
            checks["deployment_receipt"]["state"] = "failed"
            checks["deployment_receipt"]["error_code"] = exc.code
            warnings.append(f"deployment receipt check failed: {exc.code}")

    data["checks"] = checks
    payload: dict[str, Any] = {"data": data}
    if warnings:
        payload["warnings"] = warnings
    return payload


def _handle_operations_list(ctx: OperationContext) -> dict[str, Any]:
    return {"data": {"operations": available_operations()}}


def register_operations() -> None:
    register(OperationContract(
        name="project.resolve",
        description="resolve exactly one registered project deterministically (0/1/many fail-closed)",
        read_only=True,
        inputs={"workspace_id": "str", "project_id": "str", "registry_path": "str"},
        handler=_handle_project_resolve,
    ))
    register(OperationContract(
        name="git.inspect",
        description="read-only git state inspection within the resolved project boundary (full SHA)",
        read_only=True,
        inputs={"workspace_id": "str", "project_id": "str", "registry_path": "str"},
        handler=_handle_git_inspect,
    ))
    register(OperationContract(
        name="runtime.status",
        description="executor-neutral runtime process status by pid",
        read_only=True,
        inputs={"pid": "int"},
        handler=_handle_runtime_status,
    ))
    register(OperationContract(
        name="host.status",
        description="host mechanical read-only inspection; requires no Plan/SPEC/Profile Task",
        read_only=True,
        inputs={"pidfile": "str?", "receipt": "str?", "workspace_id": "str?", "project_id": "str?", "registry_path": "str?"},
        handler=_handle_host_status,
    ))
    register(OperationContract(
        name="operations.list",
        description="list canonical operations registered in the ingress contract registry",
        read_only=True,
        inputs={},
        handler=_handle_operations_list,
    ))


register_operations()
