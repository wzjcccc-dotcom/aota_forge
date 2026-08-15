"""Strict project manifest parsing and validation (M1-B).

EXTRACT of deterministic safe mechanics from legacy ``_project_common.py``:
strict YAML, duplicate-key rejection, bounded size, safe relative paths,
schema validation, path-escape protection.

Legacy semantic coupling (task-main decision_id, Profile Task, SPEC, active
Plan, current work item, current_* pointers) is NOT preserved here.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

from aota_forge.core.contracts.errors import ProjectManifestInvalidError

SCHEMA_VERSION = 1
MAX_MANIFEST_BYTES = 256 * 1024
MAX_SUMMARY = 4000
PROJECT_ID_RE = re.compile(r"^[a-z0-9_]+(?:[-_][a-z0-9_]+)*$")
STATUS_VALUES = {"active", "maintenance", "planned", "archived"}

TOP_LEVEL = {"schema_version", "project", "summary", "capabilities", "paths", "commands", "runtime", "codegraph", "plan", "constraints"}
PROJECT_FIELDS = {"id", "name", "kind", "status"}
PATH_FIELDS = {"source_root", "source", "docs", "scripts", "profiles", "skills", "tests"}
COMMAND_FIELDS = {"validate", "deploy", "verify_deploy"}


class _StrictLoader(yaml.SafeLoader):
    pass


def _strict_mapping(loader: _StrictLoader, node: yaml.MappingNode, deep: bool = False) -> dict[str, Any]:
    mapping: dict[str, Any] = {}
    for key_node, value_node in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if not isinstance(key, str):
            raise ProjectManifestInvalidError("manifest_invalid", "mapping keys must be strings")
        if key in mapping:
            raise ProjectManifestInvalidError("manifest_invalid", f"duplicate key: {key}")
        mapping[key] = loader.construct_object(value_node, deep=deep)
    return mapping


_StrictLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _strict_mapping)


def load_yaml(path: Path) -> dict[str, Any]:
    try:
        if path.is_symlink() or not path.is_file() or path.stat().st_size > MAX_MANIFEST_BYTES:
            raise ProjectManifestInvalidError("manifest_invalid", "manifest is unsafe or too large")
        data = yaml.load(path.read_text(encoding="utf-8"), Loader=_StrictLoader)
    except ProjectManifestInvalidError:
        raise
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ProjectManifestInvalidError("manifest_invalid", type(exc).__name__) from exc
    if not isinstance(data, dict):
        raise ProjectManifestInvalidError("manifest_invalid", "manifest must be an object")
    return data


def _mapping(value: Any, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ProjectManifestInvalidError("manifest_invalid", f"{name} must be an object")
    return value


def _fields(value: dict[str, Any], allowed: set[str], name: str) -> None:
    unknown = sorted(set(value) - allowed)
    if unknown:
        raise ProjectManifestInvalidError("manifest_invalid", f"unknown {name} field: {unknown[0]}")


def _required(value: dict[str, Any], names: set[str], name: str) -> None:
    missing = sorted(names - set(value))
    if missing:
        raise ProjectManifestInvalidError("manifest_invalid", f"missing {name} field: {missing[0]}")


def _string(value: Any, name: str, maximum: int = 512) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise ProjectManifestInvalidError("manifest_invalid", f"{name} must be a bounded string")
    return value


def _relative(value: Any, name: str) -> str:
    value = _string(value, name, 512)
    path = Path(value)
    if path.is_absolute() or ".." in path.parts or "\\" in value or "\x00" in value:
        raise ProjectManifestInvalidError("path_escape", f"{name} must be a safe relative path")
    return path.as_posix()


def _list(value: Any, name: str, item_kind: type = str, maximum: int = 64) -> list[Any]:
    if not isinstance(value, list) or len(value) > maximum or not all(isinstance(item, item_kind) for item in value):
        raise ProjectManifestInvalidError("manifest_invalid", f"{name} must be a bounded list")
    return value


def validate_project(data: dict[str, Any], root: Path | None = None) -> dict[str, Any]:
    _fields(data, TOP_LEVEL, "top-level")
    _required(data, TOP_LEVEL, "manifest")
    if data["schema_version"] != SCHEMA_VERSION:
        raise ProjectManifestInvalidError("manifest_invalid", "schema_version must be 1")
    project = _mapping(data["project"], "project")
    _fields(project, PROJECT_FIELDS, "project")
    _required(project, PROJECT_FIELDS, "project")
    project_id = _string(project["id"], "project.id", 96)
    if not PROJECT_ID_RE.fullmatch(project_id):
        raise ProjectManifestInvalidError("manifest_invalid", "project.id must be lowercase with - or _ separators")
    _string(project["name"], "project.name", 200)
    kind = _string(project["kind"], "project.kind", 96)
    if not PROJECT_ID_RE.fullmatch(kind):
        raise ProjectManifestInvalidError("manifest_invalid", "project.kind must be lowercase with - or _ separators")
    if project["status"] not in STATUS_VALUES:
        raise ProjectManifestInvalidError("manifest_invalid", "project.status is invalid")
    _string(data["summary"], "summary", MAX_SUMMARY)
    caps = _list(data["capabilities"], "capabilities", str, 32)
    if any(not PROJECT_ID_RE.fullmatch(item) or len(item) > 96 for item in caps):
        raise ProjectManifestInvalidError("manifest_invalid", "capabilities must be lowercase with - or _ separators")
    paths = _mapping(data["paths"], "paths")
    _fields(paths, PATH_FIELDS, "paths")
    _required(paths, PATH_FIELDS, "paths")
    for key, value in paths.items():
        if key == "source_root":
            _relative(value, f"paths.{key}")
        else:
            for item in _list(value, f"paths.{key}", str, 64):
                _relative(item, f"paths.{key}")
    commands = _mapping(data["commands"], "commands")
    _fields(commands, COMMAND_FIELDS, "commands")
    _required(commands, COMMAND_FIELDS, "commands")
    for key, value in commands.items():
        for item in _list(value, f"commands.{key}", str, 16):
            _relative(item, f"commands.{key}")
    runtime = _mapping(data["runtime"], "runtime")
    _fields(runtime, {"deployment_type", "requires_human_checkpoint"}, "runtime")
    _required(runtime, {"deployment_type", "requires_human_checkpoint"}, "runtime")
    _string(runtime["deployment_type"], "runtime.deployment_type", 96)
    if not isinstance(runtime["requires_human_checkpoint"], bool):
        raise ProjectManifestInvalidError("manifest_invalid", "runtime.requires_human_checkpoint must be boolean")
    codegraph = _mapping(data["codegraph"], "codegraph")
    _fields(codegraph, {"enabled", "index_location"}, "codegraph")
    _required(codegraph, {"enabled", "index_location"}, "codegraph")
    if not isinstance(codegraph["enabled"], bool):
        raise ProjectManifestInvalidError("manifest_invalid", "codegraph.enabled must be boolean")
    _relative(codegraph["index_location"], "codegraph.index_location")
    plan = _mapping(data["plan"], "plan")
    _fields(plan, {"active_plan_id"}, "plan")
    _required(plan, {"active_plan_id"}, "plan")
    if plan["active_plan_id"] is not None and (not isinstance(plan["active_plan_id"], str) or not re.fullmatch(r"plan_[a-z0-9]+(?:-[a-z0-9]+)*", plan["active_plan_id"])):
        raise ProjectManifestInvalidError("manifest_invalid", "plan.active_plan_id is invalid")
    _list(data["constraints"], "constraints", str, 32)
    if root is not None:
        _validate_paths_exist(data, root)
    return data


def _validate_paths_exist(data: dict[str, Any], root: Path) -> None:
    for key, value in data["paths"].items():
        values = [value] if key == "source_root" else value
        for item in values:
            candidate = root / item
            try:
                resolved = candidate.resolve(strict=False)
                resolved.relative_to(root.resolve())
            except ValueError as exc:
                raise ProjectManifestInvalidError("path_escape", f"paths.{key} escapes project root") from exc


def load_project(manifest: Path) -> tuple[Path, dict[str, Any]]:
    if manifest.parent.is_symlink() or manifest.parent.parent.is_symlink():
        raise ProjectManifestInvalidError("path_escape", "project root or .aota directory is a symlink")
    root = manifest.parent.parent
    data = validate_project(load_yaml(manifest), root)
    return root, data
