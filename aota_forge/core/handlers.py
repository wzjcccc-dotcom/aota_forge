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
from aota_forge.core.contracts.errors import ForgeError
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
    data: dict[str, Any] = {
        "identity": host_env.safe_environment_identity(),
    }
    project_root = None
    registry = None
    try:
        registry = _registry_path(ctx.params)
    except ForgeError:
        registry = None
    if registry is not None:
        try:
            workspace_id = ctx.params.get("workspace_id")
            project_id = ctx.params.get("project_id")
            if isinstance(workspace_id, str) and isinstance(project_id, str):
                resolved = resolve_project_with_fingerprint(workspace_id, registry, project_id)
                project_root = Path(resolved["project_root"])
                data["runtime_identity"] = host_runtime.host_runtime_identity(project_root)
        except ForgeError:
            project_root = None
    pidfile = ctx.params.get("pidfile")
    if isinstance(pidfile, str) and pidfile:
        data["process"] = host_process.host_process_status(Path(pidfile))
    receipt = ctx.params.get("receipt")
    if isinstance(receipt, str) and receipt:
        data["deployment_receipt"] = host_process.read_receipt(Path(receipt))
    return {"data": data}


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
