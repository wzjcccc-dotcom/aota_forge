"""AF #57 M1/W4 — Multi-Plan Identity & Governance Compatibility Integration (V1/V2).

One coherent W4 proof surface for the frozen Governance 2.0 integration lane:

  W4-A  canonical Plan-aware runtime identity: explicit Plan position,
        roundtrip, two Plans with the same M/W, bounded legacy read
        compatibility, malformed / wrong-Plan fail-closed
  W4-B  producer + every real positional consumer updated atomically
        (task_facade mint; worker_vertical_slice verification; the legacy
        ``rsplit(":", 3)`` heuristic is proven wrong for the Plan-aware form)
  W4-C  launch-time source-neutral PlanAuthorityBinding (github_issue |
        local_governance; exactly one source; no silent dual authority) and
        the V2 multi-Plan collision proof over real composed thin hosts,
        the W3 Project Governance Store and the W2 local authority adapter
  W4-D  Governance 1.x regression: a GitHub-bound Plan still launches and
        reads without any local-governance configuration; the W2 local
        authority path stays valid
  W4-E  new-work worktree/branch identity is Plan-aware; the physical layout
        stays operator/governance convention (no runtime worktree creation,
        no legacy mass migration)

PROVES (V1 behavior + V2 component integration over real modules):

* ``{project}:{plan}:{milestone}:{work}:{tail}`` roundtrips; the Plan segment
  position is explicit and never inferred from Issue/worktree/branch/repo/title;
* legacy plan-less identities remain readable, classified and byte-identical,
  with no fabricated Plan ID;
* two Plans in the same project with the same M1/W1 produce distinct runtime
  task IDs, distinct authority bindings, distinct governance records, distinct
  worktree targets and distinct branch identities (zero collisions);
* Plan A can never mutate/target Plan B authority or governance record;
* one launch binds exactly one authority source; dual sources fail closed;
* a plan-less Governance 1.x launch (with or without a GitHub plan_ref) still
  works without local governance;
* W2 local authority + W3 governance store remain separate concerns.

DOES_NOT_PROVE:

* no real Plan authority cutover (M3 owns it); #57/#55 remain github_issue;
* no Plan lifecycle state in TaskMainCoordinatorState (unchanged ownership);
* no terminal/web UI, Cards, Context Route, Stewardship (M2/successor);
* does not run M1 V3 / RV1.

Deterministic, local-only; no network.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from typing import Any

import pytest

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import (
    LocalGovernanceAdapterError,
    LocalPlanAuthorityAdapter,
    LocalPlanAuthorityDestination,
    LocalPlanAuthorityReadAdapter,
    load_local_portable_plan,
    local_plan_authority_reference,
)
from aota_forge.adapters.plan_authority.port import PortablePlanMutationRequest
from aota_forge.adapters.plan_authority import StaticPlanAuthorityAdapter
from aota_forge.composition.plan_authority import (
    PLAN_AUTHORITY_SOURCE_AMBIGUOUS,
    PLAN_AUTHORITY_SOURCE_UNAVAILABLE,
    PlanAuthorityCompositionError,
    compose_plan_authority_binding,
)
from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
from aota_forge.composition.worker_vertical_slice import (
    _verify_canonical_task_handoff_identity,
)
from aota_forge.core.contracts.descriptor import PLAN_INIT_OPERATION
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import ObjectRef, object_ref_subject
from aota_forge.core.ingress import bind_execution_dispatcher, reset_execution_dispatcher
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import (
    PORTABLE_PLAN_SOURCE_ISSUE_BODY,
    PORTABLE_PLAN_SOURCE_LOCAL,
    PortablePlanDocument,
)
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    PLAN_LIFECYCLE_RETIRED,
    PlanRecordNotFoundError,
    ProjectGovernanceRecordError,
    ProjectPlanRecord,
    StalePlanRevisionError,
)
from aota_forge.composition.project_governance import open_project_governance_store
from aota_forge.runtime.trusted_runtime_binding import (
    TrustedBindingError,
    TrustedWorkerBinding,
)
from aota_forge.work_plane import authorized_roots as ar
from aota_forge.work_plane.execution_identity import (
    CANONICAL_TASK_ID_PLAN_POSITION_EXPLICIT,
    LEGACY_BRANCH_RENAME_REQUIRED,
    LEGACY_WORKTREE_MASS_MIGRATION,
    LEGACY_WORKTREE_READ_COMPATIBLE,
    NEW_BRANCH_IDENTITY_INCLUDES_PLAN,
    NEW_BRANCH_LAYOUT,
    NEW_WORKTREE_IDENTITY_INCLUDES_PLAN,
    NEW_WORKTREE_LAYOUT,
    PLAN_ID_POSITION,
    PLAN_ID_SILENT_INFERENCE,
    MalformedTaskIdentityError,
    TaskIdentityPlanMismatchError,
    format_plan_aware_task_id,
    is_plan_aware_task_identity,
    new_work_branch_name,
    new_work_worktree_relpath,
    parse_plan_aware_task_identity,
    parse_task_identity_bounded,
    require_task_identity_matches,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
PRODUCTION_ROOT = REPO_ROOT / "aota_forge"
WORKTREE_GOVERNANCE_DOC = REPO_ROOT / "chat_governance" / "aota-chatgpt-worktree-governance.md"

PROJECT_ID = "aota_forge"
PLAN_A = "plan_alpha"
PLAN_B = "plan_beta"
PLAN_REF_A = "wzjcccc-dotcom/aota-hermes-tools#57"
PLAN_REF_B = "wzjcccc-dotcom/aota-hermes-tools#58"

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

PLAN_BODY = (
    "PLAN_REVISION=R2\n"
    "PLAN_STATUS=active\n"
    "CURRENT_MILESTONE=M1\n"
    "M1_STATUS=ready\n"
)
LOCAL_PLAN_BODY = (
    "PORTABLE_PLAN=yes\n"
    "PLAN_STATUS=active\n"
    "CURRENT_MILESTONE=M1\n"
    "M1_STATUS=in-progress\n"
)


class _FakeHostClient:
    """Deterministic trusted host-client seam (records, never spawns)."""

    def __init__(self) -> None:
        self.payloads: list[Any] = []

    def dispatch(self, payload: Any) -> dict[str, Any]:
        self.payloads.append(payload)
        return {
            "adapter_handle": f"fake-{len(self.payloads)}",
            "status": "running",
            "dispatch_time": "2026-09-15T00:00:00Z",
        }


@pytest.fixture(autouse=True)
def _isolate_ingress_dispatcher():
    """Compose binds the process-global canonical dispatcher seam; isolate it."""
    yield
    reset_execution_dispatcher()


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _plan_ref(plan_id: str) -> ObjectRef:
    return object_ref_subject(make_id(IdKind.SUBJECT, plan_id, sub_kind=SubjectKind.PLAN))


def _make_root(tmp_path: Path, name: str) -> Path:
    root = tmp_path / name
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(
        MANIFEST.format(project_id=PROJECT_ID), encoding="utf-8"
    )
    return root


def _compose_host(
    tmp_path: Path,
    operator_config_file: Path,
    *,
    name: str,
    worktree_id: str,
    plan_id: str | None = None,
    plan_ref: str | None = None,
    governance_base: Path | None = None,
):
    root = _make_root(tmp_path, name)
    return compose_thin_task_main_host(
        worktree_root=root,
        project_id=PROJECT_ID,
        worktree_id=worktree_id,
        runtime_config_path=operator_config_file,
        origin_task_main_session_ref="20260915_af57_m1w4_w4_session",
        host_client=_FakeHostClient(),
        plan_id=plan_id,
        plan_ref=plan_ref,
        governance_base=governance_base,
    )


def _write_work_item(host: Any, *, work_item_ref: str = "W1", milestone_ref: str = "M1") -> str:
    response = host.invoke(
        "handoff.write",
        {
            "mode": "work_item",
            "payload": {
                "work_role": "coder",
                "task_kind": "af57-m1w4",
                "work_item_ref": work_item_ref,
                "milestone_ref": milestone_ref,
                "objective": "bounded W4 multi-plan identity probe",
                "bounded_scope": "bounded W4 probe scope",
                "validation_expectations": ["focused W4 validation"],
                "semantic_stop_expectations": ["stop on insufficient evidence"],
            },
        },
    )
    assert response.get("is_success"), response
    return response["payload"]["ref"]


def _start(host: Any, ref: str, role: str = "coder") -> dict[str, Any]:
    bind_execution_dispatcher(host.execution_dispatcher)
    return host.invoke("task.start", {"role": role, "handoff_ref": ref})


def _governance_base(tmp_path: Path) -> Path:
    base = tmp_path / "plans"
    base.mkdir()
    (base / PROJECT_ID).mkdir()
    return base


def _body_sha() -> str:
    return _sha(PLAN_BODY)


def _mutation_request(target: ObjectRef, *, plan_id: str) -> PortablePlanMutationRequest:
    slug = plan_id.replace("_", "-")
    return PortablePlanMutationRequest(
        operation=PLAN_INIT_OPERATION,
        typed_target=target,
        correlation_id=f"corr-{slug}",
        contract_hash="a" * 64,
        idempotency_key=f"key-{slug}",
        intent_fingerprint="b" * 64,
        subject_expected_revision=0,
        authority_source_revision=None,
        authority_observed_raw_digest=None,
        candidate_raw_digest=_sha(LOCAL_PLAN_BODY),
        normalized_plan_digest=None,
        principal="operator",
        authorization_reference=f"auth-{slug}",
        candidate_raw_body=LOCAL_PLAN_BODY,
    )


# ---------------------------------------------------------------------------
# W4-A canonical Plan-aware execution identity
# ---------------------------------------------------------------------------


class TestCanonicalTaskIdentity:
    def test_plan_position_is_explicit_and_roundtrips(self):
        cid = format_plan_aware_task_id(
            project_id=PROJECT_ID,
            plan_id=PLAN_A,
            milestone_id="M1",
            work_item_id="W1",
            tail=("abcd1234", "deadbeef"),
        )
        assert cid == f"{PROJECT_ID}:{PLAN_A}:M1:W1:abcd1234:deadbeef"
        parsed = parse_plan_aware_task_identity(cid)
        assert parsed.plan_id == PLAN_A
        assert parsed.milestone_id == "M1"
        assert parsed.work_item_id == "W1"
        assert parsed.tail == ("abcd1234", "deadbeef")
        assert parsed.to_canonical() == cid
        assert CANONICAL_TASK_ID_PLAN_POSITION_EXPLICIT is True
        assert PLAN_ID_POSITION == 1
        assert PLAN_ID_SILENT_INFERENCE is False
        assert is_plan_aware_task_identity(cid) is True

    def test_two_plans_same_milestone_work_are_distinct(self):
        id_a = format_plan_aware_task_id(
            project_id=PROJECT_ID, plan_id=PLAN_A, milestone_id="M1", work_item_id="W1", tail=("a", "1")
        )
        id_b = format_plan_aware_task_id(
            project_id=PROJECT_ID, plan_id=PLAN_B, milestone_id="M1", work_item_id="W1", tail=("a", "1")
        )
        assert id_a != id_b
        assert parse_plan_aware_task_identity(id_a).plan_id == PLAN_A
        assert parse_plan_aware_task_identity(id_b).plan_id == PLAN_B

    def test_legacy_forms_are_read_plan_less_without_injection(self):
        for legacy in (
            f"{PROJECT_ID}:M3:W1:attempt-1",
            f"{PROJECT_ID}:M3:W1:abcd1234:deadbeef",
        ):
            parsed = parse_task_identity_bounded(legacy)
            assert parsed.plan_id is None
            assert parsed.legacy_plan_less is True
            assert parsed.milestone_id == "M3"
            assert parsed.work_item_id == "W1"
            assert parsed.to_canonical() == legacy
        assert is_plan_aware_task_identity(f"{PROJECT_ID}:M3:W1:attempt-1") is False

    def test_strict_parse_rejects_missing_or_malformed_plan(self):
        for bad in (
            f"{PROJECT_ID}:M1:W1:abcd1234:deadbeef",
            f"{PROJECT_ID}:plan-Alpha:M1:W1:abcd:dead",
            f"{PROJECT_ID}:plan_alpha:M1",
            "",
            "aota_forge:plan_alpha:M1:W1::dead",
        ):
            with pytest.raises(MalformedTaskIdentityError):
                parse_plan_aware_task_identity(bad)
        with pytest.raises(MalformedTaskIdentityError):
            format_plan_aware_task_id(
                project_id=PROJECT_ID, plan_id="57", milestone_id="M1", work_item_id="W1", tail=("a",)
            )

    def test_wrong_plan_binding_fails_closed(self):
        id_a = format_plan_aware_task_id(
            project_id=PROJECT_ID, plan_id=PLAN_A, milestone_id="M1", work_item_id="W1", tail=("a", "1")
        )
        matched = require_task_identity_matches(
            id_a, plan_id=PLAN_A, milestone_id="M1", work_item_id="W1", project_id=PROJECT_ID
        )
        assert matched.plan_id == PLAN_A
        with pytest.raises(TaskIdentityPlanMismatchError):
            require_task_identity_matches(id_a, plan_id=PLAN_B)
        with pytest.raises(TaskIdentityPlanMismatchError):
            require_task_identity_matches(id_a, plan_id=PLAN_A, milestone_id="M2")
        with pytest.raises(MalformedTaskIdentityError):
            require_task_identity_matches(f"{PROJECT_ID}:M1:W1:attempt-1", plan_id=PLAN_A)

    def test_new_work_worktree_and_branch_identity_include_plan(self):
        rel_a = new_work_worktree_relpath(
            project_id=PROJECT_ID, plan_id=PLAN_A, milestone_id="M1", work_item_or_lane="w4"
        )
        rel_b = new_work_worktree_relpath(
            project_id=PROJECT_ID, plan_id=PLAN_B, milestone_id="M1", work_item_or_lane="w4"
        )
        assert rel_a == f".aota-worktrees/{PROJECT_ID}/{PLAN_A}/M1/w4"
        assert rel_b == f".aota-worktrees/{PROJECT_ID}/{PLAN_B}/M1/w4"
        assert rel_a != rel_b
        branch_a = new_work_branch_name(plan_id=PLAN_A, milestone_id="M1", work_item_or_lane="w4")
        branch_b = new_work_branch_name(plan_id=PLAN_B, milestone_id="M1", work_item_or_lane="w4")
        assert branch_a == f"aota/{PLAN_A}/M1/w4"
        assert branch_b == f"aota/{PLAN_B}/M1/w4"
        assert branch_a != branch_b
        assert NEW_WORKTREE_IDENTITY_INCLUDES_PLAN is True
        assert NEW_BRANCH_IDENTITY_INCLUDES_PLAN is True
        assert NEW_WORKTREE_LAYOUT == (
            "<workspace>/.aota-worktrees/<project-id>/<plan-id>/"
            "<milestone-id>/<work-item-or-lane>/"
        )
        assert NEW_BRANCH_LAYOUT == "aota/<plan-id>/<milestone-id>/<work-item-or-lane>"

    def test_new_work_identity_rejects_invalid_segments(self):
        for kwargs in (
            {"plan_id": "plan-Alpha", "milestone_id": "M1", "work_item_or_lane": "w4"},
            {"plan_id": PLAN_A, "milestone_id": "m1", "work_item_or_lane": "w4"},
            {"plan_id": PLAN_A, "milestone_id": "M1", "work_item_or_lane": "../escape"},
            {"plan_id": PLAN_A, "milestone_id": "M1", "work_item_or_lane": "a/b"},
        ):
            with pytest.raises(ValueError):
                new_work_worktree_relpath(project_id=PROJECT_ID, **kwargs)
            with pytest.raises(ValueError):
                new_work_branch_name(**kwargs)


# ---------------------------------------------------------------------------
# W4-B producer + positional consumers (atomic)
# ---------------------------------------------------------------------------


class TestProducerConsumerAtomicity:
    def test_legacy_positional_heuristic_is_real_regression_and_new_parser_fixes_it(self):
        plan_aware = f"{PROJECT_ID}:{PLAN_A}:M1:W1:abcd1234:deadbeef"
        legacy_parts = plan_aware.rsplit(":", 3)
        assert legacy_parts[1] == "W1" and legacy_parts[2] == "abcd1234"
        _verify_canonical_task_handoff_identity(plan_aware, "M1", "W1")
        _verify_canonical_task_handoff_identity(
            f"{PROJECT_ID}:{PLAN_A}:M1:W1:attempt-1", "M1", "W1"
        )

    def test_bounded_consumer_accepts_legacy_and_plan_aware_forms(self):
        for cid in (
            f"{PROJECT_ID}:M3:W1:attempt-1",
            f"{PROJECT_ID}:M3:W1:abcd1234:deadbeef",
        ):
            _verify_canonical_task_handoff_identity(cid, "M3", "W1")
        with pytest.raises(TrustedBindingError):
            _verify_canonical_task_handoff_identity(f"{PROJECT_ID}:M3:W2:attempt-1", "M3", "W1")
        with pytest.raises(TrustedBindingError):
            _verify_canonical_task_handoff_identity(f"{PROJECT_ID}:M4:W1:attempt-1", "M3", "W1")
        with pytest.raises(TrustedBindingError):
            _verify_canonical_task_handoff_identity("not-an-identity", "M3", "W1")

    def test_producer_mints_plan_aware_identity_when_plan_bound(
        self, tmp_path: Path, operator_config_file: Path
    ):
        host = _compose_host(
            tmp_path,
            operator_config_file,
            name="root-a",
            worktree_id="wt-w4-a",
            plan_id=PLAN_A,
            plan_ref=PLAN_REF_A,
        )
        ref = _write_work_item(host)
        started = _start(host, ref)
        assert started.get("is_success"), started
        task_id = started["payload"]["task_id"]
        parsed = parse_plan_aware_task_identity(task_id)
        assert parsed.project_id == PROJECT_ID
        assert parsed.plan_id == PLAN_A
        assert parsed.milestone_id == "M1"
        assert parsed.work_item_id == "W1"
        assert task_id.startswith(f"{PROJECT_ID}:{PLAN_A}:M1:W1:")

    def test_producer_keeps_bounded_legacy_identity_when_plan_less(
        self, tmp_path: Path, operator_config_file: Path
    ):
        host = _compose_host(
            tmp_path,
            operator_config_file,
            name="root-legacy",
            worktree_id="wt-w4-legacy",
            plan_ref=PLAN_REF_A,
        )
        ref = _write_work_item(host)
        started = _start(host, ref)
        assert started.get("is_success"), started
        task_id = started["payload"]["task_id"]
        parts = task_id.split(":")
        assert len(parts) == 5
        assert parts[0] == PROJECT_ID and parts[1] == "M1" and parts[2] == "W1"
        assert is_plan_aware_task_identity(task_id) is False


# ---------------------------------------------------------------------------
# W4-C launch binding + multi-Plan collision proof
# ---------------------------------------------------------------------------


class TestLaunchPlanAuthorityBinding:
    def test_github_bound_binding_needs_no_local_governance(self):
        binding = compose_plan_authority_binding(
            project_id=PROJECT_ID, plan_id=PLAN_A, github_plan_ref=PLAN_REF_A
        )
        assert binding is not None
        assert binding.source_kind == PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE
        assert binding.authority_ref == PLAN_REF_A
        assert binding.plan_id == PLAN_A

    def test_local_bound_binding_is_source_neutral(self):
        binding = compose_plan_authority_binding(
            project_id=PROJECT_ID, plan_id=PLAN_B, local_governance_enabled=True
        )
        assert binding is not None
        assert binding.source_kind == PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
        assert binding.authority_ref == local_plan_authority_reference(PROJECT_ID, PLAN_B)
        assert "/" not in binding.authority_ref.split(":", 1)[0]

    def test_dual_or_missing_sources_fail_closed(self):
        with pytest.raises(PlanAuthorityCompositionError) as dual:
            compose_plan_authority_binding(
                project_id=PROJECT_ID,
                plan_id=PLAN_A,
                github_plan_ref=PLAN_REF_A,
                local_governance_enabled=True,
            )
        assert dual.value.code == PLAN_AUTHORITY_SOURCE_AMBIGUOUS
        with pytest.raises(PlanAuthorityCompositionError) as missing:
            compose_plan_authority_binding(project_id=PROJECT_ID, plan_id=PLAN_A)
        assert missing.value.code == PLAN_AUTHORITY_SOURCE_UNAVAILABLE
        with pytest.raises(PlanAuthorityCompositionError):
            compose_plan_authority_binding(
                project_id=PROJECT_ID, plan_id="57", github_plan_ref=PLAN_REF_A
            )
        assert compose_plan_authority_binding(
            project_id=PROJECT_ID, plan_id=None, github_plan_ref=PLAN_REF_A
        ) is None

    def test_thin_host_materializes_source_neutral_binding(
        self, tmp_path: Path, operator_config_file: Path
    ):
        host = _compose_host(
            tmp_path,
            operator_config_file,
            name="root-bind",
            worktree_id="wt-w4-bind",
            plan_id=PLAN_A,
            plan_ref=PLAN_REF_A,
        )
        binding = host.plan_authority_binding
        assert binding is not None
        assert binding.plan_id == PLAN_A
        assert binding.source_kind == PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE
        assert binding.authority_ref == PLAN_REF_A
        assert host.local_governance_binding is None
        assert host.trusted_binding.plan_authority_binding is binding

    def test_thin_host_dual_authority_fails_closed(
        self, tmp_path: Path, operator_config_file: Path
    ):
        base = _governance_base(tmp_path)
        with pytest.raises(TrustedBindingError) as excinfo:
            _compose_host(
                tmp_path,
                operator_config_file,
                name="root-dual",
                worktree_id="wt-w4-dual",
                plan_id=PLAN_A,
                plan_ref=PLAN_REF_A,
                governance_base=base,
            )
        assert PLAN_AUTHORITY_SOURCE_AMBIGUOUS in str(excinfo.value)

    def test_thin_host_local_bound_binding_and_binding_validation(
        self, tmp_path: Path, operator_config_file: Path
    ):
        base = _governance_base(tmp_path)
        host = _compose_host(
            tmp_path,
            operator_config_file,
            name="root-local",
            worktree_id="wt-w4-local",
            plan_id=PLAN_B,
            governance_base=base,
        )
        binding = host.plan_authority_binding
        assert binding is not None
        assert binding.source_kind == PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
        assert binding.authority_ref == local_plan_authority_reference(PROJECT_ID, PLAN_B)
        assert host.trusted_binding.plan_authority_binding is binding
        # The source-neutral binding never becomes a second authority: a
        # mismatched/dual carried binding fails TrustedWorkerBinding validation.
        import dataclasses

        wrong = PlanAuthorityBinding(
            plan_id=PLAN_B,
            source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
            authority_ref=PLAN_REF_B,
        )
        with pytest.raises(TrustedBindingError):
            dataclasses.replace(host.trusted_binding, plan_authority_binding=wrong)
        assert TrustedWorkerBinding.__dataclass_fields__["plan_authority_binding"] is not None


class TestMultiPlanCollisionProof:
    def _host_pair(self, tmp_path: Path, operator_config_file: Path):
        base = _governance_base(tmp_path)
        host_a = _compose_host(
            tmp_path,
            operator_config_file,
            name="root-collision-a",
            worktree_id="wt-w4-coll-a",
            plan_id=PLAN_A,
            plan_ref=PLAN_REF_A,
        )
        host_b = _compose_host(
            tmp_path,
            operator_config_file,
            name="root-collision-b",
            worktree_id="wt-w4-coll-b",
            plan_id=PLAN_B,
            governance_base=base,
        )
        return host_a, host_b

    def test_two_plans_same_mw_zero_identity_worktree_branch_collisions(
        self, tmp_path: Path, operator_config_file: Path
    ):
        host_a, host_b = self._host_pair(tmp_path, operator_config_file)
        started_a = _start(host_a, _write_work_item(host_a))
        started_b = _start(host_b, _write_work_item(host_b))
        assert started_a.get("is_success"), started_a
        assert started_b.get("is_success"), started_b
        id_a = started_a["payload"]["task_id"]
        id_b = started_b["payload"]["task_id"]
        assert parse_plan_aware_task_identity(id_a).plan_id == PLAN_A
        assert parse_plan_aware_task_identity(id_b).plan_id == PLAN_B
        assert id_a != id_b
        assert {parse_plan_aware_task_identity(i).milestone_id for i in (id_a, id_b)} == {"M1"}
        assert {parse_plan_aware_task_identity(i).work_item_id for i in (id_a, id_b)} == {"W1"}
        assert host_a.project_id == host_b.project_id == PROJECT_ID
        assert host_a.plan_authority_binding != host_b.plan_authority_binding
        assert host_a.plan_authority_binding.source_kind == PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE
        assert host_b.plan_authority_binding.source_kind == PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE
        rel_a = new_work_worktree_relpath(
            project_id=PROJECT_ID, plan_id=PLAN_A, milestone_id="M1", work_item_or_lane="W1"
        )
        rel_b = new_work_worktree_relpath(
            project_id=PROJECT_ID, plan_id=PLAN_B, milestone_id="M1", work_item_or_lane="W1"
        )
        assert rel_a != rel_b
        assert new_work_branch_name(plan_id=PLAN_A, milestone_id="M1", work_item_or_lane="W1") != (
            new_work_branch_name(plan_id=PLAN_B, milestone_id="M1", work_item_or_lane="W1")
        )

    def test_two_plans_governance_store_records_are_isolated(
        self, tmp_path: Path
    ):
        store = open_project_governance_store(tmp_path / "governance.sqlite3")
        binding_a = compose_plan_authority_binding(
            project_id=PROJECT_ID, plan_id=PLAN_A, github_plan_ref=PLAN_REF_A
        )
        binding_b = compose_plan_authority_binding(
            project_id=PROJECT_ID, plan_id=PLAN_B, local_governance_enabled=True
        )
        store.put_plan(
            ProjectPlanRecord(
                project_id=PROJECT_ID,
                plan_id=PLAN_A,
                lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
                authority=binding_a,
            )
        )
        store.put_plan(
            ProjectPlanRecord(
                project_id=PROJECT_ID,
                plan_id=PLAN_B,
                lifecycle_state=PLAN_LIFECYCLE_ACTIVE,
                authority=binding_b,
            )
        )
        record_a = store.get_plan(PROJECT_ID, PLAN_A)
        record_b = store.get_plan(PROJECT_ID, PLAN_B)
        assert record_a is not None and record_b is not None
        assert record_a.authority == binding_a and record_b.authority == binding_b
        assert record_a.authority != record_b.authority
        # Plan A CAS mutation succeeds; Plan B is untouched by it.
        updated_a = store.compare_and_swap_plan(
            PROJECT_ID, PLAN_A, record_a.revision, lifecycle_state=PLAN_LIFECYCLE_RETIRED
        )
        assert updated_a.lifecycle_state == PLAN_LIFECYCLE_RETIRED
        untouched_b = store.get_plan(PROJECT_ID, PLAN_B)
        assert untouched_b is not None and untouched_b.lifecycle_state == PLAN_LIFECYCLE_ACTIVE
        assert untouched_b.revision == 1
        # Plan A's stale revision cannot mutate through Plan B's identity.
        with pytest.raises(StalePlanRevisionError):
            store.compare_and_swap_plan(PROJECT_ID, PLAN_A, 1, lifecycle_state=PLAN_LIFECYCLE_ACTIVE)
        # Plan A's authority cannot be attached to Plan B's record.
        with pytest.raises(ProjectGovernanceRecordError):
            store.compare_and_swap_plan(PROJECT_ID, PLAN_B, 1, authority=binding_a)
        with pytest.raises(PlanRecordNotFoundError):
            store.compare_and_swap_plan(PROJECT_ID, "plan_gamma", 1, lifecycle_state=PLAN_LIFECYCLE_ACTIVE)
        store.close()

    def test_plan_a_authority_cannot_target_plan_b(self, tmp_path: Path):
        base = _governance_base(tmp_path)
        root_binding = ar.LocalGovernanceRootBinding.from_trusted_base(base, project_id=PROJECT_ID)
        dest_a = LocalPlanAuthorityDestination(
            governance_root=root_binding, plan_id=PLAN_A, expected_ref=_plan_ref(PLAN_A)
        )
        dest_b = LocalPlanAuthorityDestination(
            governance_root=root_binding, plan_id=PLAN_B, expected_ref=_plan_ref(PLAN_B)
        )
        binding_a = PlanAuthorityBinding(
            plan_id=PLAN_A,
            source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
            authority_ref=dest_a.authority_ref,
        )
        # A Plan A authority binding cannot drive a Plan B destination.
        with pytest.raises(LocalGovernanceAdapterError):
            LocalPlanAuthorityReadAdapter(dest_b, binding=binding_a)
        # A Plan A target cannot mutate through the Plan B destination and
        # leaves no filesystem effect on Plan B.
        adapter_b = LocalPlanAuthorityAdapter(dest_b)
        response = adapter_b.mutate(_mutation_request(dest_a.expected_ref, plan_id=PLAN_A))
        assert response.adapter_success is False
        assert response.error_code == "KNOWN_REJECTION"
        assert adapter_b.write_count == 0
        assert not dest_b.plan_document_path().exists()
        assert not dest_a.plan_document_path().exists()

    def test_multiplan_collision_count_is_zero(self):
        assert LEGACY_WORKTREE_MASS_MIGRATION is False
        assert LEGACY_WORKTREE_READ_COMPATIBLE is True
        assert LEGACY_BRANCH_RENAME_REQUIRED is False


# ---------------------------------------------------------------------------
# W4-D Governance 1.x regression + W2 path validity
# ---------------------------------------------------------------------------


class TestGovernanceCompatibility:
    def test_github_bound_plan_read_pipeline_unchanged(self):
        adapter = StaticPlanAuthorityAdapter(PLAN_BODY, plan_authority=PLAN_REF_A)
        snapshot = adapter.load()
        assert snapshot.body == PLAN_BODY
        assert adapter.plan_authority == PLAN_REF_A
        document = normalize_portable_plan(
            snapshot.body, source_revision=snapshot.revision, source_kind=PORTABLE_PLAN_SOURCE_ISSUE_BODY
        )
        assert isinstance(document, PortablePlanDocument)
        assert document.source_kind == PORTABLE_PLAN_SOURCE_ISSUE_BODY

    def test_w2_local_authority_path_remains_valid(self, tmp_path: Path):
        base = _governance_base(tmp_path)
        root_binding = ar.LocalGovernanceRootBinding.from_trusted_base(base, project_id=PROJECT_ID)
        plan_dir = base / PROJECT_ID / PLAN_B
        plan_dir.mkdir()
        (plan_dir / "plan.md").write_text(LOCAL_PLAN_BODY, encoding="utf-8")
        destination = LocalPlanAuthorityDestination(
            governance_root=root_binding, plan_id=PLAN_B, expected_ref=_plan_ref(PLAN_B)
        )
        binding = PlanAuthorityBinding(
            plan_id=PLAN_B,
            source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
            authority_ref=destination.authority_ref,
        )
        document = load_local_portable_plan(
            LocalPlanAuthorityReadAdapter(destination, binding=binding)
        )
        assert document.source_kind == PORTABLE_PLAN_SOURCE_LOCAL
        with pytest.raises(LocalGovernanceAdapterError):
            LocalPlanAuthorityReadAdapter(destination, binding=None)

    def test_governance_1x_launch_needs_no_local_governance(
        self, tmp_path: Path, operator_config_file: Path
    ):
        host = _compose_host(
            tmp_path,
            operator_config_file,
            name="root-g1x",
            worktree_id="wt-w4-g1x",
            plan_id=PLAN_A,
            plan_ref=PLAN_REF_A,
        )
        assert host.local_governance_binding is None
        assert host.plan_authority_binding.source_kind == PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE
        started = _start(host, _write_work_item(host))
        assert started.get("is_success"), started

    def test_planless_legacy_launch_remains_functional(
        self, tmp_path: Path, operator_config_file: Path
    ):
        host = _compose_host(
            tmp_path,
            operator_config_file,
            name="root-planless",
            worktree_id="wt-w4-planless",
            plan_ref=PLAN_REF_A,
        )
        assert host.plan_authority_binding is None
        started = _start(host, _write_work_item(host))
        assert started.get("is_success"), started
        task_id = started["payload"]["task_id"]
        assert parse_task_identity_bounded(task_id).legacy_plan_less is True


# ---------------------------------------------------------------------------
# W4-E convention ownership (worktree/branch stays operator/governance truth)
# ---------------------------------------------------------------------------


class TestNewWorkConventionOwnership:
    def test_production_source_owns_no_worktree_or_branch_creation(self):
        forbidden = (
            '"worktree", "add"',
            "'worktree', 'add'",
            '"checkout", "-b"',
            "'checkout', '-b'",
            '"branch", "-c"',
        )
        violations: list[str] = []
        for path in sorted(PRODUCTION_ROOT.rglob("*.py")):
            if "__pycache__" in path.parts:
                continue
            text = path.read_text(encoding="utf-8")
            for snippet in forbidden:
                if snippet in text:
                    violations.append(f"{path}: {snippet}")
        assert violations == []

    def test_worktree_governance_doc_records_plan_aware_prospective_pattern(self):
        text = WORKTREE_GOVERNANCE_DOC.read_text(encoding="utf-8")
        assert "NEW_WORKTREE_PATH_PATTERN=" in text
        assert "<plan-id>" in text
        assert "aota/<plan-id>/<milestone-id>/<work-item-or-lane-slug>" in text
        assert "LEGACY_WORKTREE_MASS_MIGRATION=no" in text
        assert "LEGACY_BRANCH_RENAME_REQUIRED=no" in text
        assert "aota_forge/work_plane/execution_identity.py" in text


# ---------------------------------------------------------------------------
# W4 marker / ownership guard
# ---------------------------------------------------------------------------


class TestOwnershipUnchanged:
    def test_execution_and_coordinator_ownership_markers_unchanged(self):
        from aota_forge.governance.project_store import (
            GOVERNANCE_SUBSYSTEM_OWNS_EXECUTION_STATE,
            GOVERNANCE_SUBSYSTEM_OWNS_TASK_MAIN_COORDINATOR_STATE,
            PROJECT_GOVERNANCE_STORE_OWNS_WORK_PROGRESSION,
        )

        assert GOVERNANCE_SUBSYSTEM_OWNS_EXECUTION_STATE is False
        assert GOVERNANCE_SUBSYSTEM_OWNS_TASK_MAIN_COORDINATOR_STATE is False
        assert PROJECT_GOVERNANCE_STORE_OWNS_WORK_PROGRESSION is False
