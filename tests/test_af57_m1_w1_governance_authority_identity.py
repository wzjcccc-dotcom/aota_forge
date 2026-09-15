"""AF #57 M1/W1 — Governance Authority & Identity Contract Convergence (V1).

One coherent W1 proof surface for the frozen Governance 2.0 contracts:

  W1-A  one canonical new-Plan ID grammar (core/plan/validation.py)
  W1-B  explicit, isolated legacy Plan ID read compatibility
  W1-C  source-neutral Plan Authority binding (identity != authority source)
  W1-D  PortablePlanDocument remains the one normalized Plan semantic model
  W1-E  one Governance 2.0 Plan writer path (PlanAuthorityMutationPort);
        internal core plan_init does not become a second external writer
  W1-F  the three durable ownership domains keep their negative boundaries

PROVES (V1 behavior/type-level over the real modules):

* the canonical Plan ID grammar rejects legacy-only uppercase IDs and is the
  single grammar used by project manifest Plan binding and canonical helpers;
* LegacyPlanStateReader still reads already accepted legacy fixtures exactly
  as observed, with no silent normalization;
* a PlanAuthorityBinding keeps internal Plan identity distinct from the bound
  authority source, grants no authority, and fails closed without a bound
  source;
* PortablePlanDocument / PlanAuthoritySnapshot carry no GitHub Issue number,
  comment ID, filesystem path, or SQLite schema field;
* PlanAuthorityMutationPort remains the only external Plan mutation protocol
  and internal core plan_init reports `external_authority_write=False`;
* ExecutionStateStore / TaskMainCoordinatorState still own their existing
  domains and no speculative Project Governance Store schema exists.

DOES_NOT_PROVE:

* does not implement or prove W2 local-governance root / local Plan adapter;
* does not implement or prove the W3 Project Governance Store;
* does not implement or prove W4 multi-Plan runtime identity / worktree
  layout / Governance 1.x cutover;
* does not prove production runtime behavior.

This module adds tests only; it creates no new Plan authority, no second Plan
ontology, and no governance store.
"""

from __future__ import annotations

import ast
import dataclasses
import inspect
import json
import re
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
AF_ROOT = REPO_ROOT / "aota_forge"

sys.path.insert(0, str(Path(__file__).resolve().parent))

from aota_forge.adapters.plan_authority import PlanAuthoritySnapshot
from aota_forge.adapters.plan_authority.binding import (
    AUTHORITY_REF_IS_AUTHORITY,
    BOUND_AUTHORITY_SOURCE_REQUIRED_FOR_AUTHORITATIVE_READ,
    OBJECT_REF_IS_AUTHORITY,
    ONE_CURRENT_BOUND_PLAN_AUTHORITY_PER_PLAN,
    PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
    PLAN_AUTHORITY_SOURCE_KINDS,
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PLAN_ID_IS_AUTHORITY,
    PlanAuthorityBinding,
    PlanAuthorityBindingError,
    require_bound_authority,
)
from aota_forge.adapters.plan_authority.port import (
    PlanAuthorityMutationPort,
    PortablePlanMutationRequest,
)
from aota_forge.core.contracts.descriptor import (
    PLAN_INIT_OPERATION,
    PLAN_RETIREMENT_OPERATION,
)
from aota_forge.core.execution.durable_state import (
    DurableExecutionRecord,
    InMemoryExecutionStateStore,
)
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import object_ref_subject
from aota_forge.core.plan.normalize import normalize_portable_plan
from aota_forge.core.plan.read_model import (
    LEGACY_PLAN_ID_RE,
    LegacyPlanStateReader,
    PortablePlanDocument,
)
from aota_forge.core.plan.validation import PLAN_ID_RE, is_plan_id
from aota_forge.core.project.manifest import (
    ProjectManifestInvalidError,
    validate_project,
)
from aota_forge.core.transitions import PlanInitRequest, plan_init
from aota_forge.runtime.task_main.coordinator_state import TaskMainCoordinatorState
from test_m4_2_m4_4_integration import (
    _issue_authorization,
    _lifecycle_fixture,
    _plan_init_request,
)

CANONICAL_PLAN_ID_GRAMMAR = r"^plan_[a-z0-9]+(?:[_-][a-z0-9]+)*$"
LEGACY_PLAN_ID_GRAMMAR = r"^plan_[a-zA-Z0-9]+(?:[_-][a-zA-Z0-9]+)*$"

LEGACY_UPPERCASE_PLAN_ID = "plan_20260729T075202_ff38a6a0"
LEGACY_CORPUS_PLAN_ID = "plan_20260816T000000_corpus01"

CANONICAL_PLAN_ID_SAMPLES = (
    "plan_af57",
    "plan_af57_pilot",
    "plan_af57-pilot",
    "plan_af57_pilot-2",
    "plan_0",
    "plan_x9_y8-z7",
)
NON_CANONICAL_PLAN_ID_SAMPLES = (
    LEGACY_UPPERCASE_PLAN_ID,
    LEGACY_CORPUS_PLAN_ID,
    "plan_AF57",
    "plan_",
    "plan_-x",
    "plan_x-",
    "plan_x--y",
    "plan_x__y",
    "plan_ x",
    "57",
    "#57",
    "aota_forge",
    "plan.af57",
    "plan/af57",
    "plan_af57/../x",
    "",
    None,
    57,
)

# Frozen Governance 2.0 negative ownership boundary for the future store.
PROJECT_GOVERNANCE_STORE_IMPLEMENTED = "no"
PROJECT_GOVERNANCE_STORE_OWNS_EXECUTION_ATTEMPTS = "no"
PROJECT_GOVERNANCE_STORE_OWNS_WORKER_RESULTS = "no"
PROJECT_GOVERNANCE_STORE_OWNS_WORK_PROGRESSION = "no"
PROJECT_GOVERNANCE_STORE_OWNS_HUMAN_BRAKE = "no"
PROJECT_GOVERNANCE_STORE_OWNS_COMPLETION_QUEUE = "no"


def _source_files() -> list[Path]:
    return sorted(path for path in AF_ROOT.rglob("*.py") if "__pycache__" not in path.parts)


def _manifest_document(active_plan_id):
    return {
        "schema_version": 1,
        "project": {"id": "proj", "name": "proj", "kind": "test", "status": "active"},
        "summary": "bounded manifest",
        "capabilities": [],
        "paths": {
            "source_root": ".",
            "source": [],
            "docs": [],
            "scripts": [],
            "profiles": [],
            "skills": [],
            "tests": [],
        },
        "commands": {"validate": [], "deploy": [], "verify_deploy": []},
        "runtime": {"deployment_type": "manual", "requires_human_checkpoint": False},
        "codegraph": {"enabled": False, "index_location": ".codegraph/"},
        "plan": {"active_plan_id": active_plan_id},
        "constraints": [],
    }


def _manifest_accepts(active_plan_id) -> bool:
    try:
        validate_project(_manifest_document(active_plan_id))
        return True
    except ProjectManifestInvalidError:
        return False


def _write_legacy_plan(root: Path, plan_id: str) -> None:
    plan_dir = root / ".aota" / "forge" / "plans" / plan_id
    plan_dir.mkdir(parents=True)
    (plan_dir / "plan.json").write_text(
        json.dumps(
            {
                "plan_id": plan_id,
                "revision": 3,
                "status": "planned",
                "current_milestone": "M2",
                "milestones": {
                    "M1": {
                        "status": "completed",
                        "work_items": [{"work_item_id": "M1-A", "status": "completed"}],
                    }
                },
            }
        ),
        encoding="utf-8",
    )


def _plan_ref(name: str = "plan_af57_pilot"):
    return object_ref_subject(make_id(IdKind.SUBJECT, name, sub_kind=SubjectKind.PLAN))


class TestCanonicalPlanIdGrammar:
    """W1-A: one canonical new-Plan ID grammar."""

    def test_canonical_source_defines_the_one_grammar(self):
        assert PLAN_ID_RE.pattern == CANONICAL_PLAN_ID_GRAMMAR
        assert LEGACY_PLAN_ID_RE.pattern == LEGACY_PLAN_ID_GRAMMAR

    def test_canonical_lowercase_underscore_hyphen_valid(self):
        for value in CANONICAL_PLAN_ID_SAMPLES:
            assert is_plan_id(value) is True, value

    def test_non_canonical_and_legacy_only_ids_invalid(self):
        for value in NON_CANONICAL_PLAN_ID_SAMPLES:
            assert is_plan_id(value) is False, value

    def test_legacy_uppercase_ids_rejected_by_canonical_grammar(self):
        assert is_plan_id(LEGACY_UPPERCASE_PLAN_ID) is False
        assert is_plan_id(LEGACY_CORPUS_PLAN_ID) is False
        # bounded legacy-read grammar is explicit and separate
        assert LEGACY_PLAN_ID_RE.fullmatch(LEGACY_UPPERCASE_PLAN_ID) is not None

    def test_project_manifest_plan_binding_uses_the_canonical_grammar(self):
        for value in CANONICAL_PLAN_ID_SAMPLES + NON_CANONICAL_PLAN_ID_SAMPLES:
            if value is None:
                continue  # null active_plan_id is separately valid
            assert _manifest_accepts(value) is is_plan_id(value), value

    def test_manifest_accepts_null_active_plan_id(self):
        assert _manifest_accepts(None) is True

    def test_manifest_rejects_legacy_uppercase_plan_id(self):
        with pytest.raises(ProjectManifestInvalidError):
            validate_project(_manifest_document(LEGACY_UPPERCASE_PLAN_ID))

    def test_one_canonical_grammar_definition_source_guard(self):
        allowed = {
            AF_ROOT / "core" / "plan" / "validation.py",
            AF_ROOT / "core" / "plan" / "read_model.py",
        }
        holders = {path for path in _source_files() if "plan_[" in path.read_text(encoding="utf-8")}
        assert holders == allowed


class TestLegacyPlanReadCompatibility:
    """W1-B: explicit, isolated legacy read compatibility with no normalization."""

    def test_legacy_reader_reads_accepted_legacy_fixture_exactly(self, tmp_path: Path):
        _write_legacy_plan(tmp_path, LEGACY_UPPERCASE_PLAN_ID)
        reader = LegacyPlanStateReader()

        snapshot = reader.load(tmp_path, LEGACY_UPPERCASE_PLAN_ID)
        assert snapshot is not None
        assert snapshot.source == "legacy_shadow"
        assert snapshot.plan_id == LEGACY_UPPERCASE_PLAN_ID
        assert snapshot.revision == 3
        assert snapshot.current_milestone == "M2"
        assert is_plan_id(snapshot.plan_id) is False

    def test_legacy_reader_discovers_legacy_plan_without_id(self, tmp_path: Path):
        _write_legacy_plan(tmp_path, LEGACY_CORPUS_PLAN_ID)
        snapshot = LegacyPlanStateReader().load(tmp_path)
        assert snapshot is not None
        assert snapshot.plan_id == LEGACY_CORPUS_PLAN_ID

    def test_legacy_reader_does_not_normalize_lookup_identity(self, tmp_path: Path):
        _write_legacy_plan(tmp_path, LEGACY_UPPERCASE_PLAN_ID)
        reader = LegacyPlanStateReader()
        assert reader.load(tmp_path, LEGACY_UPPERCASE_PLAN_ID) is not None
        # A silently lowercased identity would resolve; it must not.
        assert reader.load(tmp_path, LEGACY_UPPERCASE_PLAN_ID.lower()) is None

    def test_legacy_reader_still_reads_canonical_plan_id(self, tmp_path: Path):
        canonical_id = "plan_af57_legacy-read"
        _write_legacy_plan(tmp_path, canonical_id)
        snapshot = LegacyPlanStateReader().load(tmp_path, canonical_id)
        assert snapshot is not None
        assert snapshot.plan_id == canonical_id


class TestPlanAuthorityBinding:
    """W1-C: internal Plan identity is distinct from bound source authority."""

    def test_github_issue_binding_is_identity_plus_source(self):
        binding = PlanAuthorityBinding(
            plan_id="plan_af57_pilot",
            source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
            authority_ref="wzjcccc-dotcom/aota-hermes-tools#57",
            source_revision=7,
            source_digest="a" * 64,
        )
        assert binding.plan_id == "plan_af57_pilot"
        assert binding.source_kind == "github_issue"
        assert binding.authority_ref != binding.plan_id

    def test_local_governance_binding_supported_as_target_kind(self):
        binding = PlanAuthorityBinding(
            plan_id="plan_af57_pilot",
            source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
            authority_ref="aota_forge/plans",
        )
        assert binding.source_kind == "local_governance"
        assert binding.source_revision is None
        assert binding.source_digest is None

    def test_binding_grants_no_authority(self):
        binding = PlanAuthorityBinding(
            plan_id="plan_af57_pilot",
            source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
            authority_ref="wzjcccc-dotcom/aota-hermes-tools#57",
        )
        assert binding.is_authority() is False
        assert binding.authority() is False
        assert PLAN_ID_IS_AUTHORITY is False
        assert AUTHORITY_REF_IS_AUTHORITY is False
        assert OBJECT_REF_IS_AUTHORITY is False
        ref = _plan_ref()
        assert ref.authority() is False

    def test_binding_requires_canonical_internal_plan_identity(self):
        for bad in (LEGACY_UPPERCASE_PLAN_ID, "57", "#57", "aota_forge", ""):
            with pytest.raises(PlanAuthorityBindingError):
                PlanAuthorityBinding(
                    plan_id=bad,
                    source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
                    authority_ref="wzjcccc-dotcom/aota-hermes-tools#57",
                )

    def test_binding_rejects_unknown_source_kind_and_empty_ref(self):
        with pytest.raises(PlanAuthorityBindingError):
            PlanAuthorityBinding(
                plan_id="plan_af57_pilot",
                source_kind="sqlite_table",
                authority_ref="x",
            )
        with pytest.raises(PlanAuthorityBindingError):
            PlanAuthorityBinding(
                plan_id="plan_af57_pilot",
                source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
                authority_ref="   ",
            )

    def test_binding_rejects_malformed_observed_revision_and_digest(self):
        base = dict(
            plan_id="plan_af57_pilot",
            source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
            authority_ref="wzjcccc-dotcom/aota-hermes-tools#57",
        )
        with pytest.raises(PlanAuthorityBindingError):
            PlanAuthorityBinding(**base, source_revision=True)
        with pytest.raises(PlanAuthorityBindingError):
            PlanAuthorityBinding(**base, source_revision=-1)
        with pytest.raises(PlanAuthorityBindingError):
            PlanAuthorityBinding(**base, source_revision="")
        with pytest.raises(PlanAuthorityBindingError):
            PlanAuthorityBinding(**base, source_digest="not-a-digest")

    def test_one_current_bound_authority_per_plan_shape(self):
        field_names = {item.name for item in dataclasses.fields(PlanAuthorityBinding)}
        assert field_names == {
            "plan_id",
            "source_kind",
            "authority_ref",
            "source_revision",
            "source_digest",
        }
        assert ONE_CURRENT_BOUND_PLAN_AUTHORITY_PER_PLAN is True
        assert BOUND_AUTHORITY_SOURCE_REQUIRED_FOR_AUTHORITATIVE_READ is True

    def test_bound_source_required_for_authoritative_read(self):
        with pytest.raises(PlanAuthorityBindingError):
            require_bound_authority(None)
        binding = PlanAuthorityBinding(
            plan_id="plan_af57_pilot",
            source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
            authority_ref="wzjcccc-dotcom/aota-hermes-tools#57",
        )
        assert require_bound_authority(binding) is binding

    def test_source_kind_switch_keeps_internal_plan_identity(self):
        github = PlanAuthorityBinding(
            plan_id="plan_af57_pilot",
            source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
            authority_ref="wzjcccc-dotcom/aota-hermes-tools#57",
        )
        local = PlanAuthorityBinding(
            plan_id="plan_af57_pilot",
            source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
            authority_ref="aota_forge/plans",
        )
        assert github.plan_id == local.plan_id
        assert github.source_kind != local.source_kind
        assert PLAN_AUTHORITY_SOURCE_KINDS == {"github_issue", "local_governance"}

    def test_binding_round_trip_is_lossless(self):
        binding = PlanAuthorityBinding(
            plan_id="plan_af57_pilot",
            source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
            authority_ref="aota_forge/plans",
            source_revision="r7",
            source_digest="b" * 64,
        )
        assert PlanAuthorityBinding.from_dict(binding.to_dict()) == binding

    def test_binding_has_no_storage_format_ontology(self):
        field_names = {item.name for item in dataclasses.fields(PlanAuthorityBinding)}
        forbidden = ("issue_number", "comment_id", "github", "sqlite", "table", "path")
        assert not [name for name in field_names for token in forbidden if token in name]


class TestPortablePlanDocumentReused:
    """W1-D: one normalized Plan semantic model; no second Plan ontology."""

    def test_normalized_document_is_portable_plan_document(self):
        body = (
            "```text\n"
            "PLAN_STATUS=active\n"
            "CURRENT_MILESTONE=M1\n"
            "M1_USER_APPROVAL_SATISFIED=yes\n"
            "ENTRY_BASE=abcdef1234567890\n"
            "M1_DAG=W1 -> W2\n"
            "M1_WORK_ITEMS=W1, W2\n"
            "```\n"
            "# [PLAN] W1 convergence fixture\n"
        )
        document = normalize_portable_plan(body)
        assert isinstance(document, PortablePlanDocument)
        assert document.current_milestone == "M1"
        assert document.source_kind == "portable_plan_issue_body"

    def test_snapshot_and_document_carry_no_storage_ontology(self):
        snapshot_fields = {item.name for item in dataclasses.fields(PlanAuthoritySnapshot)}
        assert snapshot_fields == {"body", "revision", "digest", "control_projections"}
        document_fields = {item.name for item in dataclasses.fields(PortablePlanDocument)}
        forbidden = ("issue_number", "comment_id", "github", "sqlite", "table", "filesystem_path")
        assert not [name for name in document_fields for token in forbidden if token in name]

    def test_no_second_plan_ontology_source_guard(self):
        offenders = []
        for path in _source_files():
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and re.fullmatch(r".*PlanDocument", node.name):
                    if node.name != "PortablePlanDocument":
                        offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.name}")
        assert offenders == []


class TestSingleGovernance2PlanWriterPath:
    """W1-E: PlanAuthorityMutationPort stays the one external writer path."""

    def test_mutation_port_remains_the_single_external_protocol(self):
        assert PlanAuthorityMutationPort.__abstractmethods__ == {
            "read_raw_authority",
            "mutate",
            "verify",
        }
        offenders = []
        for path in sorted((AF_ROOT / "adapters" / "plan_authority").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if not isinstance(node, ast.ClassDef):
                    continue
                bases = {ast.unparse(base) for base in node.bases}
                if "ABC" in bases and any(
                    isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)) and item.name == "mutate"
                    for item in node.body
                ):
                    offenders.append(node.name)
        assert offenders == ["PlanAuthorityMutationPort"]

    def test_only_plan_init_and_plan_retirement_operations_are_mutable(self):
        request = dict(
            typed_target=_plan_ref(),
            correlation_id="corr-1",
            contract_hash="0" * 64,
            idempotency_key="idem-1",
            intent_fingerprint="1" * 64,
            subject_expected_revision=0,
            authority_source_revision="7",
            authority_observed_raw_digest="a" * 64,
            candidate_raw_digest="b" * 64,
            normalized_plan_digest="c" * 64,
            principal="operator",
        )
        for operation in (PLAN_INIT_OPERATION, PLAN_RETIREMENT_OPERATION):
            assert PortablePlanMutationRequest(operation=operation, **request).operation == operation
        for forbidden in ("plan_write", "governance.write", "filesystem.write", "artifact.mutate"):
            with pytest.raises(ValueError):
                PortablePlanMutationRequest(operation=forbidden, **request)

    def test_plan_authority_package_exposes_no_generic_write_api(self):
        pattern = re.compile(r"(?i)(governance_write|write_governance|plan_write|write_plan|generic_write)")
        offenders = []
        for path in sorted((AF_ROOT / "adapters" / "plan_authority").glob("*.py")):
            tree = ast.parse(path.read_text(encoding="utf-8"))
            for node in ast.walk(tree):
                if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    if pattern.search(node.name):
                        offenders.append(f"{path.name}:{node.name}")
        assert offenders == []

    def test_internal_plan_init_has_no_external_authority_channel(self):
        assert list(inspect.signature(plan_init).parameters) == ["store", "req"]
        request_fields = {item.name for item in dataclasses.fields(PlanInitRequest)}
        assert not [name for name in request_fields if "port" in name or "writer" in name or "adapter" in name]

    def test_internal_plan_init_result_declares_no_external_authority_write(self):
        with _lifecycle_fixture("af57-w1-writer") as fixture:
            authorization = _issue_authorization(fixture)
            result = plan_init(
                fixture.store,
                _plan_init_request(
                    fixture,
                    intent=authorization.intent,
                    lease=authorization.lease,
                ),
            )
            state_after = fixture.store.read_subject(fixture.plan_ref).mechanical_state["state"]
        assert result.code == "PLAN_INIT_APPLIED"
        assert result.data["external_authority_write"] is False
        assert state_after == "initialized"


class TestDurableStateOwnership:
    """W1-F: existing state owners keep their domains; no speculative store."""

    def test_execution_state_store_owns_execution_durability(self):
        store = InMemoryExecutionStateStore()
        record = DurableExecutionRecord(
            canonical_task_id="task-1",
            executor_id="executor-1",
            package_id="package-1",
            correlation_id="corr-1",
            dispatch_attempt_id="attempt-1",
            idempotency_key="idem-1",
            intent_fingerprint="fingerprint-1",
        )
        store.create(record)
        loaded = store.get("task-1")
        assert loaded is not None
        assert loaded.execution_phase is record.execution_phase
        assert loaded.delivery_state is record.delivery_state
        fields = {item.name for item in dataclasses.fields(DurableExecutionRecord)}
        assert {
            "dispatch_attempt_id",
            "execution_phase",
            "canonical_task_state",
            "terminal_result",
            "delivery_state",
        } <= fields
        assert not {"source_kind", "authority_ref", "human_brake", "work_items", "frontier_ref"} & fields

    def test_task_main_coordinator_state_owns_work_progression(self):
        state = TaskMainCoordinatorState(
            coordinator_id="coord-1",
            plan_authority="wzjcccc-dotcom/aota-hermes-tools#57",
            plan_digest="d" * 64,
            milestone_id="M1",
            entry_base="a" * 40,
            origin_task_main_session_ref="session-1",
            project_id="aota_forge",
            executor_id="executor-1",
            work_items=("W1", "W2"),
            wi_status={"W1": "PENDING", "W2": "PENDING"},
        )
        fields = {item.name for item in dataclasses.fields(TaskMainCoordinatorState)}
        assert {
            "plan_authority",
            "human_brake",
            "attempt_states",
            "work_projections",
            "frontier_ref",
            "open_blockers",
            "next_action",
        } <= fields
        assert not {"execution_phase", "terminal_result", "delivery_state", "canonical_task_state"} & fields
        successor = state.with_cas_updates({"next_action": "continue M1/W1"})
        assert TaskMainCoordinatorState.from_dict(successor.to_dict()) == successor

    def test_project_governance_store_negative_boundaries(self):
        assert PROJECT_GOVERNANCE_STORE_IMPLEMENTED == "no"
        assert PROJECT_GOVERNANCE_STORE_OWNS_EXECUTION_ATTEMPTS == "no"
        assert PROJECT_GOVERNANCE_STORE_OWNS_WORKER_RESULTS == "no"
        assert PROJECT_GOVERNANCE_STORE_OWNS_WORK_PROGRESSION == "no"
        assert PROJECT_GOVERNANCE_STORE_OWNS_HUMAN_BRAKE == "no"
        assert PROJECT_GOVERNANCE_STORE_OWNS_COMPLETION_QUEUE == "no"

    def test_no_speculative_project_governance_store_schema(self):
        """W1-AC12 guard; the real W3 store replaces this W1 WIP boundary."""
        offenders = []
        for path in _source_files():
            text = path.read_text(encoding="utf-8")
            if "CREATE TABLE" in text:
                offenders.append(str(path.relative_to(REPO_ROOT)))
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.ClassDef) and node.name.startswith("ProjectGovernance"):
                    offenders.append(f"{path.relative_to(REPO_ROOT)}:{node.name}")
        assert offenders == []
