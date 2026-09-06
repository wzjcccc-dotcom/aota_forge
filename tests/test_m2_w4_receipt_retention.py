"""M2/W4 RV1 F01 repair: terminal receipt retention authority.

Plan #36 semantic identity under test:

    A Hermes terminal mechanical receipt must NOT be removable before the AF
    canonical terminal truth (CanonicalResult + WorkerResultCard with verified
    digest) is durable. Mechanical age is an additional retention criterion,
    never the sole criterion, and NEVER an authority.

Gates proven here:

    CAN_W2_TERMINAL_RECEIPT_BE_DELETED_BEFORE_AF_CANONICAL_PERSIST=no
    GC_ELIGIBILITY_AFTER_CANONICAL_PERSIST=yes
    NEW_DISPATCH_CAN_PRUNE_UNCANONICALIZED_RECEIPT=no
    ACTIVE_RUN_TIME_PRUNE=no  UNKNOWN_RUN_TIME_PRUNE=no  CORRUPT_RUN_TIME_PRUNE=no
    HERMES_LOCATOR_IMPORTS_EXECUTION_STATE_STORE=no
    T_RETENTION_WITHOUT_CANONICAL_PERSIST=PASS
    T_SAFE_POST_CANONICAL_GC=PASS
    T_CANONICAL_PERSIST_FAILURE_PRESERVES_RECEIPT=PASS

The W2 supervisor pad + real locator/host-client file protocol provide the
mechanical terminal evidence; the real W1 store + W3 coordinator + production
composition seam provide the canonical side.
"""

from __future__ import annotations

import ast
import time
from pathlib import Path

import pytest
from hermes_durable_support import DurableSupervisorPad

from aota_forge.adapters.hermes import locator
from aota_forge.adapters.hermes.executor import HermesAdapter
from aota_forge.adapters.hermes.host_client import HermesHostClient
from aota_forge.composition.execution import prune_reconciled_hermes_receipts
from aota_forge.core.execution.dispatcher import ExecutionDispatcher
from aota_forge.core.execution.durable_state import (
    DeliveryState,
    FileBackedExecutionStateStore,
)
from aota_forge.core.execution.package import ExecutionPackage
from aota_forge.core.execution.registry import ExecutorRegistry
from aota_forge.core.execution.state import CanonicalTaskState
from aota_forge.runtime.completion import DurableCompletionCoordinator

REPO_ROOT = Path(__file__).resolve().parents[1]


def _package(task_id: str) -> ExecutionPackage:
    return ExecutionPackage.create(
        canonical_task_id=task_id,
        project_id="aota_forge",
        canonical_role="coder",
        instruction="Return exactly: AOTA_FORGE_M2_W4_RETENTION",
        working_context={"cwd": str(Path.cwd())},
    )


class _World:
    """Dispatcher+store+coordinator over the real Hermes host client (padded)."""

    def __init__(self, tmp_path: Path) -> None:
        self.tmp_path = tmp_path
        self.pad = DurableSupervisorPad()
        self.client = HermesHostClient(
            str(tmp_path / "unused-launcher"),
            default_cwd=str(tmp_path),
            popen_factory=self.pad,
            validate_launcher=False,
            # retention_seconds=0.0: EVERY terminal receipt is immediately
            # "past retention". If any deletion still happens before
            # canonical persistence, the defect is proven.
            retention_seconds=0.0,
            timeout_seconds=30,
            runtime_root=tmp_path / "af-runtime",
        )
        self.adapter = HermesAdapter(host_client=self.client)
        registry = ExecutorRegistry()
        registry.register(self.adapter)
        self.store = FileBackedExecutionStateStore(tmp_path / "exec-store.json")
        self.dispatcher = ExecutionDispatcher(
            registry,
            state_store=self.store,
            admission_scope_resolver=lambda package: "hermes:coder",
        )
        self.coordinator = DurableCompletionCoordinator(
            dispatcher=self.dispatcher,
            store=self.store,
            transport=None,
        )

    def dispatch_terminal_worker(self, task_id: str) -> str:
        dispatch = self.dispatcher.dispatch(_package(task_id))
        handle = dispatch.adapter_handle
        self.pad.last.release(0)  # supervised worker finishes: terminal receipt
        deadline = time.monotonic() + 6.0
        while time.monotonic() < deadline:
            if self.client.query_status(handle)["status"] == "done":
                return handle
            time.sleep(0.005)
        raise AssertionError("padded supervisor did not publish a terminal receipt")

    def receipt_dir(self, handle: str) -> Path:
        run_id = locator.run_id_from_adapter_handle(handle)
        assert run_id is not None
        return Path(self.client.runtime_root) / "runs" / run_id


# ---------------------------------------------------------------------------
# RV1 blocking reproduction (T24 equivalent) — must no longer delete
# ---------------------------------------------------------------------------


def test_T_RETENTION_WITHOUT_CANONICAL_PERSIST(tmp_path: Path) -> None:
    """Terminal receipt + expired retention + new dispatch/maintenance => kept.

    Directly closes RV1 F01: without a durable AF terminal CanonicalResult and
    WorkerResultCard, the mechanical receipt remains the SOLE terminal truth
    and must survive every new dispatch and every maintenance pass.
    """
    world = _World(tmp_path)
    handle = world.dispatch_terminal_worker("task-w4-no-canonical")
    receipt_dir = world.receipt_dir(handle)
    assert receipt_dir.is_dir()
    # No AF canonical terminal truth exists yet.
    record = world.store.get("task-w4-no-canonical")
    assert record.terminal_result is None and record.worker_result_card is None

    # 1) new dispatch occurs (pre-repair this pruned aged terminal evidence)
    other = world.dispatch_terminal_worker("task-w4-dispatcher-side")
    assert receipt_dir.is_dir(), (
        "RV1 F01 regression: a new dispatch deleted uncanonicalized terminal evidence"
    )

    # 2) retention-conditioned maintenance occurs anyway: nothing is eligible
    assert prune_reconciled_hermes_receipts(
        coordinator=world.coordinator, host_client=world.client
    ) == 0
    assert receipt_dir.is_dir(), "T_RETENTION_WITHOUT_CANONICAL_PERSIST=PASS requires survival"

    # 3) old adapter_handle stays mechanically recoverable with terminal truth
    assert world.client.query_execution_state(handle) == "done"
    envelope = world.client.fetch_result(handle)
    assert envelope["status"] == "done"
    assert envelope["exit_code"] == 0
    assert other != handle
    world.pad.close()


def test_NEW_DISPATCH_CAN_PRUNE_UNCANONICALIZED_RECEIPT_no(tmp_path: Path) -> None:
    """Many fresh dispatches with a pre-existing aged terminal receipt change
    nothing on that run directory (no implicit maintenance side effects)."""
    world = _World(tmp_path)
    handle = world.dispatch_terminal_worker("task-w4-keep")
    receipt_dir = world.receipt_dir(handle)
    mtime = receipt_dir.stat().st_mtime_ns
    for idx in range(3):
        world.dispatch_terminal_worker(f"task-w4-fresh-{idx}")
    assert receipt_dir.is_dir()
    assert receipt_dir.stat().st_mtime_ns == mtime, "aged receipt directory was silently rewritten"
    world.pad.close()


def test_active_unknown_corrupt_runs_are_never_time_pruned(tmp_path: Path) -> None:
    """W2 safety preserved: active / receipt-less (UNKNOWN) / corrupt evidence
    is never removed by the terminal-receipt maintenance path, even when the
    trusted layer (wrongly) declares them eligible."""
    world = _World(tmp_path)
    active_pkg = world.dispatcher.dispatch(_package("task-w4-active"))
    active_dir = world.receipt_dir(active_pkg.adapter_handle)
    done_handle = world.dispatch_terminal_worker("task-w4-done")
    done_dir = world.receipt_dir(done_handle)
    corrupt_run = locator.prepare_run(Path(world.client.runtime_root), locator.new_run_id())
    corrupt_run.receipt.write_bytes(b"{not json")

    # Eligibility for everything, including evidence that is not safe.
    eligible = [active_pkg.adapter_handle, done_handle, corrupt_run.adapter_handle()]
    removed = world.client.prune_canonicalized_receipts(eligible_handles=eligible)
    assert removed == 1, "only the mechanically-valid terminal receipt is collectable"
    assert active_dir.is_dir(), "ACTIVE_RUN_TIME_PRUNE=no"
    assert corrupt_run.run_dir.is_dir(), "CORRUPT_RUN_TIME_PRUNE=no"
    assert not done_dir.is_dir()
    world.pad.close()


# ---------------------------------------------------------------------------
# Safe post-canonical GC
# ---------------------------------------------------------------------------


def test_T_SAFE_POST_CANONICAL_GC(tmp_path: Path) -> None:
    """Full ordering: terminal receipt -> recover_once -> CanonicalResult ->
    Result Governance -> WorkerResultCard (digest verified) -> only NOW GC.

    After cleanup the AF canonical result, CARD, and delivery state must all
    remain recoverable from the durable store (receipt removal is not truth
    loss)."""
    world = _World(tmp_path)
    handle = world.dispatch_terminal_worker("task-w4-safe-gc")
    receipt_dir = world.receipt_dir(handle)

    # GC eligibility does NOT exist before canonicalization.
    assert world.coordinator.canonicalized_terminal_handles() == []

    report = world.coordinator.recover_once()
    assert report.observations["task-w4-safe-gc"] == "terminal_result_persisted"

    record = world.store.get("task-w4-safe-gc")
    assert record.canonical_task_state.is_terminal
    assert record.terminal_result is not None
    assert record.worker_result_card is not None
    assert record.worker_result_card_digest

    # ONLY NOW the handle is exposed as canonically safe.
    assert world.coordinator.canonicalized_terminal_handles() == [handle]

    assert prune_reconciled_hermes_receipts(
        coordinator=world.coordinator, host_client=world.client
    ) == 1
    assert not receipt_dir.is_dir(), "Hermes receipt removed"

    # AF canonical result / CARD / delivery truth remain durable across a
    # fresh reopen of the store (receipt removal changed nothing canonical).
    reopened = FileBackedExecutionStateStore(tmp_path / "exec-store.json")
    durable = reopened.get("task-w4-safe-gc")
    assert durable is not None
    assert durable.terminal_result is not None
    assert durable.terminal_result.canonical_task_state == CanonicalTaskState.COMPLETED.value
    from aota_forge.core.execution.durable_state import card_digest_for

    assert card_digest_for(dict(durable.worker_result_card)) == durable.worker_result_card_digest
    assert durable.delivery_state == DeliveryState.PENDING, "delivery truth is receipt-independent"


# ---------------------------------------------------------------------------
# Crash / failure during canonicalization
# ---------------------------------------------------------------------------


def test_T_CANONICAL_PERSIST_FAILURE_PRESERVES_RECEIPT(tmp_path: Path) -> None:
    """A CAS/persist failure before durable terminal+CARD success publishes NO
    GC eligibility and preserves the receipt. A later clean recovery then
    unlocks safe cleanup."""
    world = _World(tmp_path)
    handle = world.dispatch_terminal_worker("task-w4-crashe")
    receipt_dir = world.receipt_dir(handle)

    world.store.inject_fail_next_persist()
    world.store.inject_fail_next_cas()
    world.coordinator.recover_once()  # canonical persistence fails closed

    record = world.store.get("task-w4-crashe")
    assert not (
        record.canonical_task_state.is_terminal and record.terminal_result and record.worker_result_card
    ), "failed canonicalization must not leave partial terminal truth"
    assert world.coordinator.canonicalized_terminal_handles() == []
    assert prune_reconciled_hermes_receipts(
        coordinator=world.coordinator, host_client=world.client
    ) == 0
    assert receipt_dir.is_dir(), "T_CANONICAL_PERSIST_FAILURE_PRESERVES_RECEIPT=PASS"

    # Retry the bounded recovery without injection: canonical truth lands,
    # and only then does safe GC become available.
    report = world.coordinator.recover_once()
    assert report.observations["task-w4-crashe"] == "terminal_result_persisted"
    assert world.coordinator.canonicalized_terminal_handles() == [handle]
    assert prune_reconciled_hermes_receipts(
        coordinator=world.coordinator, host_client=world.client
    ) == 1
    assert not receipt_dir.is_dir()
    world.pad.close()


# ---------------------------------------------------------------------------
# Ownership boundary guards (static, frontier-independent)
# ---------------------------------------------------------------------------


def _module_import_names(relative: str) -> set[str]:
    tree = ast.parse((REPO_ROOT / relative).read_text(encoding="utf-8"), filename=relative)
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and not node.level:
            names.add(node.module)
    return names


@pytest.mark.parametrize(
    "relative",
    [
        "aota_forge/adapters/hermes/locator.py",
        "aota_forge/adapters/hermes/host_client.py",
        "aota_forge/adapters/hermes/launcher.py",
    ],
)
def test_HERMES_MECHANICAL_LAYER_IMPORTS_NO_AF_CANONICAL_STORE(relative: str) -> None:
    """HERMES_LOCATOR_IMPORTS_EXECUTION_STATE_STORE=no.

    The Hermes mechanical evidence layer must never import the AF canonical
    execution store (or any core module) and decide semantic truth itself:
    cleanup authority stays executor-neutral on the AF side and mechanical on
    the Hermes side, joined only by the explicit composition maintenance seam.
    """
    imports = _module_import_names(relative)
    forbidden = ("aota_forge.core", "aota_forge.runtime", "aota_forge.work_plane")
    offenders = sorted(
        name
        for name in imports
        if any(name == root or name.startswith(root + ".") for root in forbidden)
    )
    assert not offenders, f"{relative} imports AF canonical/semantic layers: {offenders}"


def test_completion_runtime_keeps_zero_hermes_imports() -> None:
    """The AF coordinator side of the repair must stay executor-neutral too."""
    imports = _module_import_names("aota_forge/runtime/completion.py")
    assert not [name for name in imports if "hermes" in name.lower()], (
        f"runtime/completion.py must remain Hermes-free: {imports}"
    )


def test_dispatch_source_contains_no_implicit_prune() -> None:
    """The dispatch path may not regain implicit retention pruning: only the
    explicit canonical-safe maintenance API may delete receipts."""
    source = (REPO_ROOT / "aota_forge/adapters/hermes/host_client.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "dispatch":
            calls = [
                f"{n.func.value.id}.{n.func.attr}"
                for n in ast.walk(node)
                if isinstance(n, ast.Call)
                and isinstance(n.func, ast.Attribute)
                and isinstance(n.func.value, ast.Name)
            ]
            assert not any("prune" in c for c in calls), (
                f"dispatch() regained implicit receipt pruning: {calls}"
            )
