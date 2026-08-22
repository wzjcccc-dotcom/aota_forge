"""Focused M5-R-R2D capability contract and registry matching tests."""

from __future__ import annotations

from typing import Any

import pytest

from aota_forge.adapters.execution.reference import ReferenceFakeExecutorAdapter
from aota_forge.core.execution import (
    ALLOWED_EXECUTION_MODES,
    ALLOWED_ISOLATION_MODES,
    CANONICAL_ROLES,
    ExecutionPackage,
    ExecutorCapabilities,
    RoleMapping,
)
from aota_forge.core.execution.registry import ExecutorRegistry, ResolutionOutcome


def capability_data() -> dict[str, Any]:
    return {
        "executor_id": "r2d-executor",
        "adapter_kind": "in_process_test_double",
        "supported_execution_modes": ["sync"],
        "supports_streaming_events": True,
        "supports_task_cancellation": True,
        "supports_task_resume": True,
        "supports_structured_result": True,
        "supported_canonical_roles": list(CANONICAL_ROLES),
        "supported_isolation_modes": ["worktree"],
        "supports_working_directory": True,
        "supports_artifact_transport": True,
        "max_timeout_seconds": 3600,
        "concurrency_limit": 8,
    }


def make_capabilities(
    executor_id: str = "r2d-executor",
    modes: tuple[str, ...] = ("sync",),
    isolations: tuple[str, ...] = ("worktree",),
    supports_cancellation: bool = True,
) -> ExecutorCapabilities:
    return ExecutorCapabilities(
        executor_id=executor_id,
        adapter_kind="in_process_test_double",
        supported_execution_modes=modes,
        supports_streaming_events=True,
        supports_task_cancellation=supports_cancellation,
        supports_task_resume=True,
        supports_structured_result=True,
        supported_canonical_roles=("coder",),
        supported_isolation_modes=isolations,
        supports_working_directory=True,
        supports_artifact_transport=True,
        max_timeout_seconds=3600,
        concurrency_limit=8,
    )


def make_adapter(
    executor_id: str,
    modes: tuple[str, ...] = ("sync",),
    isolations: tuple[str, ...] = ("worktree",),
) -> ReferenceFakeExecutorAdapter:
    capabilities = make_capabilities(executor_id, modes, isolations)
    role_mapping = RoleMapping.create(executor_id, {"coder": f"{executor_id}_coder"})
    return ReferenceFakeExecutorAdapter(
        capabilities=capabilities,
        role_mapping=role_mapping,
    )


def make_package(requirements: dict[str, Any] | None = None) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id="r2d-task",
        project_id="aota_forge",
        canonical_role="coder",
        instruction="validate canonical capabilities",
        capability_requirements=requirements or {},
    )


@pytest.mark.parametrize("mode", ("magic", "hermes", "vm-ish", ""))
def test_noncanonical_execution_mode_rejected(mode: str) -> None:
    data = capability_data()
    data["supported_execution_modes"] = [mode]

    with pytest.raises(ValueError, match="supported_execution_modes"):
        ExecutorCapabilities.from_dict(data)


@pytest.mark.parametrize("mode", ("magic", "hermes", "vm-ish", ""))
def test_noncanonical_isolation_mode_rejected(mode: str) -> None:
    data = capability_data()
    data["supported_isolation_modes"] = [mode]

    with pytest.raises(ValueError, match="supported_isolation_modes"):
        ExecutorCapabilities.from_dict(data)


@pytest.mark.parametrize("mode", sorted(ALLOWED_EXECUTION_MODES))
def test_all_canonical_execution_modes_accepted(mode: str) -> None:
    caps = make_capabilities(modes=(mode,))

    assert caps.supported_execution_modes == (mode,)
    assert caps.to_dict()["supported_execution_modes"] == [mode]


@pytest.mark.parametrize("isolation", sorted(ALLOWED_ISOLATION_MODES))
def test_all_canonical_isolation_modes_accepted(isolation: str) -> None:
    caps = make_capabilities(isolations=(isolation,))

    assert caps.supported_isolation_modes == (isolation,)
    assert caps.to_dict()["supported_isolation_modes"] == [isolation]


def test_unknown_capability_requirement_rejected() -> None:
    caps = make_capabilities()
    package = make_package({"unknown_requirement": True})

    compatible, reasons = ExecutorRegistry.check_compatibility(caps, package)

    assert compatible is False
    assert any("Unknown capability requirement key" in reason for reason in reasons)


@pytest.mark.parametrize(
    ("requirements", "caps", "reason_fragment"),
    [
        (
            {"execution_mode": "async"},
            make_capabilities(modes=("sync",)),
            "Execution mode",
        ),
        (
            {"isolation_mode": "container"},
            make_capabilities(isolations=("worktree",)),
            "Isolation mode",
        ),
        (
            {"requires_cancellation": True},
            make_capabilities(supports_cancellation=False),
            "requires_cancellation",
        ),
    ],
)
def test_unsupported_known_requirement_rejected(
    requirements: dict[str, Any],
    caps: ExecutorCapabilities,
    reason_fragment: str,
) -> None:
    compatible, reasons = ExecutorRegistry.check_compatibility(
        caps, make_package(requirements)
    )

    assert compatible is False
    assert any(reason_fragment in reason for reason in reasons)


def test_supported_capability_requirements_accepted() -> None:
    caps = make_capabilities(modes=("async",), isolations=("container",))
    package = make_package(
        {
            "execution_mode": "async",
            "isolation_mode": "container",
            "timeout_seconds": 120,
            "requires_cancellation": True,
            "requires_resume": True,
            "requires_structured_result": True,
            "requires_streaming_events": True,
            "requires_working_directory": True,
            "requires_artifact_transport": True,
        }
    )

    compatible, reasons = ExecutorRegistry.check_compatibility(caps, package)

    assert compatible is True
    assert reasons == ()


def test_zero_match_fails_closed() -> None:
    registry = ExecutorRegistry()

    resolution = registry.resolve(make_package({"execution_mode": "batch"}))

    assert resolution.outcome == ResolutionOutcome.EXECUTOR_NOT_FOUND
    assert resolution.selected_executor_id is None
    assert resolution.candidate_executor_ids == ()


def test_one_match_is_deterministic() -> None:
    registry = ExecutorRegistry()
    registry.register(make_adapter("only", modes=("batch",), isolations=("none",)))

    resolution = registry.resolve(
        make_package({"execution_mode": "batch", "isolation_mode": "none"})
    )

    assert resolution.outcome == ResolutionOutcome.RESOLVED
    assert resolution.selected_executor_id == "only"
    assert resolution.candidate_executor_ids == ("only",)


def test_multiple_matches_require_semantic_choice() -> None:
    registry = ExecutorRegistry()
    registry.register(make_adapter("z-executor", modes=("batch",), isolations=("none",)))
    registry.register(make_adapter("a-executor", modes=("batch",), isolations=("none",)))

    resolution = registry.resolve(
        make_package({"execution_mode": "batch", "isolation_mode": "none"})
    )

    assert resolution.outcome == ResolutionOutcome.NEEDS_SEMANTIC_CHOICE
    assert resolution.selected_executor_id is None
    assert resolution.candidate_executor_ids == ("a-executor", "z-executor")
