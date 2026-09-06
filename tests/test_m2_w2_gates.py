"""M2/W2 acceptance-gate structural tests (authority boundaries).

These are cheap source/shape freezes proving the W2 scope contract:

    TASK_HANDOFF_RUNTIME_CONTAMINATION=no
    CORE_HERMES_CONTAMINATION_ADDED=no
    HERMES_LEDGER_IS_AF_AUTHORITY=no (W2 never adopts async_delegations)
    HERMES_PROCESS_EXIT_ZERO_IS_AF_ACCEPTANCE=no (vocabulary containment)
    DELIVERY_ACK_IMPLEMENTED=no / CONCURRENCY_COORDINATOR_IMPLEMENTED=no
"""

from __future__ import annotations

import ast
import inspect
import re
from dataclasses import fields
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
HERMES_DIR = REPO_ROOT / "aota_forge" / "adapters" / "hermes"
CORE_DIR = REPO_ROOT / "aota_forge" / "core"


def _sources(directory: Path) -> list[Path]:
    return sorted(p for p in directory.rglob("*.py") if "__pycache__" not in str(p))


# ---------------------------------------------------------------------------
# Core neutrality: Hermes-specific durable runtime never enters Core
# ---------------------------------------------------------------------------


def test_core_never_imports_hermes_adapter_runtime() -> None:
    for source in _sources(CORE_DIR):
        tree = ast.parse(source.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom):
                assert not (node.module or "").startswith("aota_forge.adapters.hermes"), (
                    f"{source} imports Hermes adapter internals into Core"
                )
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    assert not alias.name.startswith("aota_forge.adapters.hermes"), (
                        f"{source} imports Hermes adapter internals into Core"
                    )


def test_hermes_durable_modules_stay_executor_private() -> None:
    # locator/launcher/session_reentry import NO canonical execution state
    # types — mechanical evidence only.
    for name in ("locator.py", "launcher.py", "session_reentry.py"):
        tree = ast.parse((HERMES_DIR / name).read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and (node.module or "").startswith("aota_forge"):
                assert node.module.startswith("aota_forge.adapters.hermes"), (
                    f"{name} reaches outside the Hermes adapter package: {node.module}"
                )


# ---------------------------------------------------------------------------
# TaskHandoff / ExecutionPackage purity (W2 identity never leaks upward)
# ---------------------------------------------------------------------------


def test_task_handoff_and_package_have_no_runtime_identity_fields() -> None:
    from aota_forge.core.execution.package import ExecutionPackage
    from aota_forge.work_plane.handoff import FORBIDDEN_MECHANICAL_FIELDS, TaskHandoff

    banned = {"session_id", "run_id", "adapter_handle", "locator", "delegation_id", "profile", "provider", "model", "pid", "exit_code"}
    for cls in (TaskHandoff, ExecutionPackage):
        names = {field.name.lower() for field in fields(cls)}
        assert names.isdisjoint(banned), f"{cls.__name__} gained runtime contamination: {names & banned}"
    # W2's mechanical identities are already listed as forbidden handoff
    # mechanics; TaskHandoff must not gain them as first-class fields.
    assert "adapter_handle" in FORBIDDEN_MECHANICAL_FIELDS
    handoff_names = {field.name for field in fields(TaskHandoff)}
    assert not (handoff_names & {"session_id", "hermes_locator", "delegation_id"})


def test_host_envelope_carries_no_session_identity() -> None:
    """The public host protocol envelopes may not smuggle the Hermes session
    id out; recovery uses the dedicated executor-private evidence seam."""
    from aota_forge.adapters.hermes import host_client

    source = inspect.getsource(host_client.HermesHostClient.query_status)
    source += inspect.getsource(host_client.HermesHostClient.fetch_result)
    assert "session" not in source.lower()
    evidence = inspect.getsource(host_client.HermesHostClient.execution_evidence)
    assert "session_id" in evidence


# ---------------------------------------------------------------------------
# Hermes ledger boundary: inspected, never adopted
# ---------------------------------------------------------------------------


def test_w2_never_touches_the_hermes_async_delegations_ledger() -> None:
    for name in ("locator.py", "launcher.py", "session_reentry.py", "host_client.py"):
        text = (HERMES_DIR / name).read_text()
        assert not re.search(r"(FROM|INTO|UPDATE|DELETE FROM)\s+async_delegations", text, re.IGNORECASE), (
            "HERMES_LEDGER_IS_AF_AUTHORITY=no: W2 must not build on the gateway ledger"
        )
        assert "async_delegations" not in text.replace(
            "Hermes' own ``async_delegations`` /", ""
        ).replace("``async_delegations``", ""), "docstring boundary statements aside, no ledger coupling"
    # the only Hermes-DB surfaces W2 reads are exact session identity and the
    # bounded turn-lease table (mechanical busy evidence, TTL 300s semantics).
    reentry = (HERMES_DIR / "session_reentry.py").read_text()
    tables = set(re.findall(r"FROM (\w+)", reentry))
    assert tables <= {"sessions", "session_turn_leases"}, tables
    assert reentry.count("mode=ro") >= 1, "the session store is consulted read-only"


def test_durable_records_are_mechanical_not_semantic() -> None:
    from aota_forge.adapters.hermes import locator

    assert locator._RECEIPT_STATUSES == frozenset({"done", "failed", "timeout", "cancelled"})
    forbidden_semantic = {"completed", "accepted", "acknowledged", "delivered", "reconciled"}
    assert not (set(locator._RECEIPT_STATUSES) & forbidden_semantic)
    # M1 one-shot boundary unchanged: the locator drives the direct -z path.
    from aota_forge.adapters.hermes import host_client

    dispatch_source = inspect.getsource(host_client.HermesHostClient._hermes_argv)
    assert '"-z"' in dispatch_source
    assert "--usage-file" in dispatch_source


# ---------------------------------------------------------------------------
# no delivery/ACK/concurrency machinery (W3 owns those)
# ---------------------------------------------------------------------------


def test_w2_modules_expose_no_delivery_ack_or_concurrency_state() -> None:
    from aota_forge.adapters.hermes import locator, session_reentry
    from aota_forge.adapters.hermes.host_client import HermesHostClient

    public_names = {
        name.lower()
        for name in dir(HermesHostClient)
        if not name.startswith("__")
    } | set(getattr(session_reentry, "__all__", [])) | set(getattr(locator, "__all__", []))
    banned_patterns = (
        "ack",
        "pending_delivery",
        "delivery_claim",
        "attempt",
        "retry_schedule",
        "admission",
        "semaphore",
        "queue",
    )
    offenders = [name for name in public_names if any(pattern in name for pattern in banned_patterns)]
    assert offenders == [], f"W3 authority vocabulary present in W2 surface: {offenders}"
    # session reentry results are a single mechanical outcome, not a state machine
    assert {
        result
        for result in (
            session_reentry.OUTCOME_COMPLETED,
            session_reentry.OUTCOME_RETRYABLE,
            session_reentry.OUTCOME_NOT_FOUND,
            session_reentry.OUTCOME_FAILED,
            session_reentry.OUTCOME_UNKNOWN,
        )
    } == {"completed", "retryable", "not_found", "failed", "unknown"}


@pytest.mark.parametrize(
    ("source_file", "forbidden"),
    [
        ("host_client.py", r"def\s+(claim|acknowledge|schedule)"),
        ("locator.py", r"def\s+(claim|acknowledge|schedule)"),
        ("session_reentry.py", r"def\s+(claim|acknowledge|schedule)"),
    ],
)
def test_no_delivery_state_machine_symbols(source_file: str, forbidden: str) -> None:
    assert re.search(forbidden, (HERMES_DIR / source_file).read_text()) is None


def test_error_vocabulary_is_bounded() -> None:
    from aota_forge.adapters.hermes import session_reentry

    codes = {
        session_reentry.ERROR_EXACT_SESSION_NOT_FOUND,
        session_reentry.ERROR_REENTRY_RETRYABLE,
        session_reentry.ERROR_REENTRY_FAILED,
        session_reentry.ERROR_EXECUTION_UNKNOWN,
    }
    assert codes == {
        "HERMES_EXACT_SESSION_NOT_FOUND",
        "HERMES_REENTRY_RETRYABLE",
        "HERMES_REENTRY_FAILED",
        "HERMES_EXECUTION_UNKNOWN",
    }
    from aota_forge.adapters.hermes import host_client

    client_source = inspect.getsource(host_client)
    assert '"HERMES_LOCATOR_CORRUPT"' in client_source or "HERMES_LOCATOR_CORRUPT" in (HERMES_DIR / "locator.py").read_text()
    assert '"HERMES_EXECUTION_UNKNOWN"' in client_source
