"""AF #57 M1/W2 — Trusted Local-Governance Root & Local Plan Authority Adapter.

One coherent W2 proof surface for the frozen Governance 2.0 local path:

  W2-A  I57-B001 repair: the Plan authority mutation port exports a real
        ``ALLOWED_OPERATIONS`` (no phantom ``__all__`` name)
  W2-B  trusted project-scoped local-governance binding (operator base +
        canonical project identity; whole plans base never a root)
  W2-C  AuthorizedRoot extension: task-main read/search local-governance;
        workers never inherit it; agent write capability stays absent
  W2-D  Local Plan read pipeline: plan.md -> LocalPlanAuthorityReadAdapter ->
        PlanAuthoritySnapshot -> normalize_portable_plan(portable_plan_local)
        -> PortablePlanDocument (one Plan ontology, source-neutral authority ref)
  W2-E  LocalPlanAuthorityAdapter behind the existing PlanAuthorityMutationPort
        + existing durable journal / RecoveryExecutor: read-before-write,
        PREPARED/APPLYING, at-most-one attempt, verify-after-write, unknown
        outcome handling, fresh retry authorization, revision/digest
        preconditions, truthful plan_retirement known no-effect

PROVES (V1 behavior + V2 component integration over the real modules):

* model-supplied paths are rejected before any filesystem access;
* a sibling project scope is never reachable; the whole governance base is
  never granted;
* the accepted #55 sandbox validator is not weakened (project-main /
  active-worktree forgery still fails closed even with a governance binding);
* wrong Plan bindings, stale preconditions, symlinks and non-canonical paths
  fail closed with no filesystem effect;
* adapter success alone never becomes VERIFIED without the journal pipeline;
* the normalized local document is the same PortablePlanDocument and feeds
  existing projection consumers through a source-neutral authority reference.

DOES_NOT_PROVE:

* no Project Governance Store (W3), no project.yaml active_plan_id
  reconciliation (W3), no plan-aware worktree layout / Governance 1.x
  cutover (W4), no terminal/web UI;
* no production runtime activation beyond the trusted composition seam.

Deterministic, local-only; no network.
"""

from __future__ import annotations

import hashlib
import json
import subprocess
import uuid
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority import port as port_module
from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import (
    ABSENT_RAW_DOCUMENT_DIGEST,
    ADAPTER_SUCCESS_ALONE_MEANS_VERIFIED,
    DURABLE_JOURNAL_REUSED,
    LOCAL_GOVERNANCE_AGENT_WRITE_CAPABILITY,
    LOCAL_PLAN_RETIREMENT_IMPLEMENTED,
    PLAN_AUTHORITY_MUTATION_PORT_REUSED,
    RECOVERY_EXECUTOR_REUSED,
    SECOND_PLAN_MUTATION_PROTOCOL_CREATED,
    SECOND_PLAN_ONTOLOGY_CREATED,
    LocalGovernanceAdapterError,
    LocalGovernanceAuthorityError,
    LocalPlanAuthorityAdapter,
    LocalPlanAuthorityDestination,
    LocalPlanAuthorityReadAdapter,
    load_local_portable_plan,
)
from aota_forge.adapters.plan_authority.port import (
    ALLOWED_OPERATIONS,
    PlanAuthorityMutationPort,
    PortablePlanMutationRequest,
)
from aota_forge.composition.thin_task_main_host import compose_thin_task_main_host
from aota_forge.core.contracts.descriptor import (
    PLAN_INIT_OPERATION,
    PLAN_RETIREMENT_OPERATION,
)
from aota_forge.core.identity.ids import make_id
from aota_forge.core.identity.kinds import IdKind, SubjectKind
from aota_forge.core.identity.refs import ObjectRef, object_ref_subject
from aota_forge.core.journal.executor import ExecutorError, RecoveryExecutor
from aota_forge.core.journal.model import JournalRecord, JournalState
from aota_forge.core.journal.retry_handoff import RetryHandoffError, create_retry_journal
from aota_forge.core.journal.store import FileBackedDurableJournalStore
from aota_forge.core.plan.normalize import PlanNormalizationError, normalize_portable_plan
from aota_forge.core.plan.projection import project_milestone_views
from aota_forge.core.plan.read_model import (
    PORTABLE_PLAN_SOURCE_ISSUE_BODY,
    PORTABLE_PLAN_SOURCE_LOCAL,
    PortablePlanDocument,
    portable_plan_digest,
)
from aota_forge.composition.project_binding import derive_canonical_project_evidence
from aota_forge.work_plane import authorized_roots as ar
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox

OWNER = "wzjcccc-dotcom"
REPO = "aota_reader_mcp"
PROJECT_ID = "aota-reader"
PLAN_ID = "plan_af57_pilot"
SIBLING_PROJECT_ID = "unrelated-project"

MANIFEST = (
    "schema_version: 1\n"
    "project:\n"
    "  id: {project_id}\n  name: test project\n  kind: test\n  status: active\n"
    "summary: bounded test project\n"
    "capabilities: []\n"
    "paths:\n  source_root: .\n  source: []\n  docs: []\n  scripts: []\n"
    "  profiles: []\n  skills: []\n  tests: []\n"
    "commands:\n  validate: []\n  deploy: []\n  verify_deploy: []\n"
    "runtime:\n  deployment_type: manual\n  requires_human_checkpoint: false\n"
    "codegraph:\n  enabled: false\n  index_location: .codegraph/\n"
    "plan:\n  active_plan_id: null\n"
    "constraints: []\n"
)

LOCAL_PLAN_BODY = (
    "```text\n"
    "PLAN_STATUS=active\n"
    "CURRENT_MILESTONE=M1\n"
    "M1_STATUS=ready\n"
    "M1_USER_APPROVAL_SATISFIED=yes\n"
    "ENTRY_BASE=abcdef1234567890\n"
    "M1_DAG=W1 -> W2\n"
    "M1_WORK_ITEMS=W1, W2\n"
    "```\n"
    "# [PLAN] AF57 M1/W2 local governance fixture\n"
    "\n"
    "### M1/W1 — local authority fixture\n"
    "Local governance fixture objective.\n"
)


def _sha(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def _plan_ref(plan_id: str = PLAN_ID) -> ObjectRef:
    return object_ref_subject(make_id(IdKind.SUBJECT, plan_id, sub_kind=SubjectKind.PLAN))


def _sandbox(tmp_path: Path, project_id: str = PROJECT_ID):
    project = tmp_path / "checkout"
    (project / ".aota").mkdir(parents=True)
    (project / ".aota" / "project.yaml").write_text(
        MANIFEST.format(project_id=project_id), encoding="utf-8"
    )
    worktree = tmp_path / "active-worktree"
    worktree.mkdir()
    evidence = derive_canonical_project_evidence(
        workspace_root=project, project_id=project_id
    )
    return bind_worktree_sandbox(evidence, "wt-w2", worktree)


def _governance_base(
    tmp_path: Path,
    project_id: str = PROJECT_ID,
    *,
    create_scope: bool = True,
    siblings: tuple[str, ...] = (SIBLING_PROJECT_ID,),
) -> Path:
    base = tmp_path / "plans"
    base.mkdir()
    if create_scope:
        (base / project_id).mkdir()
    for sibling in siblings:
        (base / sibling).mkdir(exist_ok=True)
    return base


def _binding(governance_base: Path, project_id: str = PROJECT_ID) -> ar.LocalGovernanceRootBinding:
    return ar.LocalGovernanceRootBinding.from_trusted_base(
        governance_base, project_id=project_id
    )


def _destination(binding: ar.LocalGovernanceRootBinding, plan_id: str = PLAN_ID) -> LocalPlanAuthorityDestination:
    return LocalPlanAuthorityDestination(
        governance_root=binding, plan_id=plan_id, expected_ref=_plan_ref(plan_id)
    )


def _plan_binding(destination: LocalPlanAuthorityDestination) -> PlanAuthorityBinding:
    return PlanAuthorityBinding(
        plan_id=destination.plan_id,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=destination.authority_ref,
    )


def _journal_record(
    destination: LocalPlanAuthorityDestination,
    *,
    operation: str = PLAN_INIT_OPERATION,
    observed_digest: str | None = ABSENT_RAW_DOCUMENT_DIGEST,
    authority_revision: str | None = None,
    candidate_body: str = LOCAL_PLAN_BODY,
) -> JournalRecord:
    normalized = portable_plan_digest(
        normalize_portable_plan(candidate_body, source_kind=PORTABLE_PLAN_SOURCE_LOCAL)
    )
    return JournalRecord(
        journal_id=f"journal-{uuid.uuid4().hex[:12]}",
        correlation_id=f"corr-{uuid.uuid4().hex[:12]}",
        attempt_id=f"attempt-{uuid.uuid4().hex[:8]}",
        operation=operation,
        typed_target=destination.expected_ref,
        principal="operator",
        contract_hash="a" * 64,
        idempotency_key=f"key-{uuid.uuid4().hex[:8]}",
        intent_fingerprint="b" * 64,
        subject_expected_revision=0,
        authority_source_revision=authority_revision,
        authority_observed_raw_digest=observed_digest,
        candidate_raw_digest=_sha(candidate_body),
        normalized_plan_digest=normalized,
        authorization_reference="auth-ref-w2",
        lease_reference="lease-ref-w2",
    )


def _request(
    record: JournalRecord,
    *,
    candidate_body: str = LOCAL_PLAN_BODY,
    target: ObjectRef | None = None,
) -> PortablePlanMutationRequest:
    return PortablePlanMutationRequest(
        operation=record.operation,
        typed_target=target or record.typed_target,
        correlation_id=record.correlation_id,
        contract_hash=record.contract_hash,
        idempotency_key=record.idempotency_key,
        intent_fingerprint=record.intent_fingerprint,
        subject_expected_revision=record.subject_expected_revision,
        authority_source_revision=record.authority_source_revision,
        authority_observed_raw_digest=record.authority_observed_raw_digest,
        candidate_raw_digest=record.candidate_raw_digest,
        normalized_plan_digest=record.normalized_plan_digest,
        principal=record.principal,
        authorization_reference=record.authorization_reference,
        lease_reference=record.lease_reference,
        attempt_reference=record.attempt_id,
        candidate_raw_body=candidate_body,
    )


class TestI57B001PortExportRepair:
    """W2-A: the first real port-surface consumer repairs the phantom export."""

    def test_every_declared_export_exists(self):
        missing = [name for name in port_module.__all__ if not hasattr(port_module, name)]
        assert missing == []

    def test_star_import_exposes_allowed_operations(self):
        namespace: dict[str, object] = {}
        exec("from aota_forge.adapters.plan_authority.port import *", namespace)
        assert "ALLOWED_OPERATIONS" in namespace
        assert namespace["ALLOWED_OPERATIONS"] == frozenset(
            {PLAN_INIT_OPERATION, PLAN_RETIREMENT_OPERATION}
        )

    def test_allowed_operations_is_enforced_by_request_validation(self):
        assert ALLOWED_OPERATIONS == frozenset({PLAN_INIT_OPERATION, PLAN_RETIREMENT_OPERATION})
        with pytest.raises(ValueError):
            PortablePlanMutationRequest(
                operation="plan_rewrite",
                typed_target=_plan_ref(),
                correlation_id="corr-1",
                contract_hash="0" * 64,
                idempotency_key="idem-1",
                intent_fingerprint="1" * 64,
                subject_expected_revision=0,
                authority_source_revision=None,
                authority_observed_raw_digest=None,
                candidate_raw_digest=None,
                normalized_plan_digest=None,
                principal="operator",
            )

    def test_repair_keeps_the_public_name_only(self):
        assert ALLOWED_OPERATIONS is port_module.ALLOWED_OPERATIONS
        assert PlanAuthorityMutationPort is port_module.PlanAuthorityMutationPort


class TestTrustedGovernanceBinding:
    """W2-B: project-scoped trusted governance binding (fail closed)."""

    def test_project_scoped_root_derived_from_base(self, tmp_path: Path):
        base = _governance_base(tmp_path)
        binding = _binding(base)
        assert binding.project_id == PROJECT_ID
        assert binding.root_path == str(base / PROJECT_ID)
        assert Path(binding.root_path).name == PROJECT_ID
        assert binding.root_path != str(base)
        assert Path(binding.root_path).is_relative_to(base)

    def test_sibling_project_scope_never_bound(self, tmp_path: Path):
        base = _governance_base(tmp_path)
        binding = _binding(base)
        assert binding.root_path != str(base / SIBLING_PROJECT_ID)
        sibling_marker = base / SIBLING_PROJECT_ID / "plan.md"
        sibling_marker.write_text("sibling secret", encoding="utf-8")
        assert sibling_marker.is_file()
        assert not (Path(binding.root_path) / "plan.md").exists()
        assert Path(binding.root_path) != sibling_marker.parent

    def test_binding_requires_existing_project_scope(self, tmp_path: Path):
        base = _governance_base(tmp_path, create_scope=False)
        with pytest.raises(ar.LocalGovernanceRootError):
            _binding(base)

    def test_binding_rejects_symlinked_project_scope(self, tmp_path: Path):
        base = _governance_base(tmp_path, create_scope=False)
        (base / "elsewhere").mkdir()
        (base / PROJECT_ID).symlink_to(base / "elsewhere")
        with pytest.raises(ar.LocalGovernanceRootError):
            _binding(base)

    def test_binding_rejects_symlinked_base(self, tmp_path: Path):
        real = _governance_base(tmp_path)
        alias = tmp_path / "plans-alias"
        alias.symlink_to(real)
        with pytest.raises(ar.LocalGovernanceRootError):
            _binding(alias)

    def test_binding_rejects_non_canonical_base(self, tmp_path: Path):
        base = _governance_base(tmp_path)
        non_canonical = base.parent / "plans" / ".." / "plans"
        with pytest.raises(ar.LocalGovernanceRootError):
            ar.LocalGovernanceRootBinding.from_trusted_base(
                str(non_canonical), project_id=PROJECT_ID
            )

    def test_binding_rejects_relative_and_missing_base(self, tmp_path: Path):
        with pytest.raises(ar.LocalGovernanceRootError):
            ar.LocalGovernanceRootBinding.from_trusted_base("plans", project_id=PROJECT_ID)
        with pytest.raises(ar.LocalGovernanceRootError):
            ar.LocalGovernanceRootBinding.from_trusted_base(
                str(tmp_path / "missing"), project_id=PROJECT_ID
            )

    @pytest.mark.parametrize(
        "bad_project_id",
        ("../escape", "a/b", "Aota", "aota.forge", "plan_x", "", "aota forge", None, 57),
    )
    def test_binding_rejects_traversal_and_noncanonical_project_ids(
        self, tmp_path: Path, bad_project_id
    ):
        base = _governance_base(tmp_path)
        with pytest.raises(ar.LocalGovernanceRootError):
            ar.LocalGovernanceRootBinding.from_trusted_base(
                base, project_id=bad_project_id
            )

    def test_whole_governance_base_is_never_the_bound_root(self, tmp_path: Path):
        base = _governance_base(tmp_path)
        binding = _binding(base)
        assert binding.root_path != str(base)
        assert Path(binding.root_path).parent == base


class TestAuthorizedRootExtension:
    """W2-C: local-governance root is task-main read/search only, project-scoped."""

    def test_task_main_default_roots_unchanged_without_binding(self, tmp_path: Path):
        sandbox = _sandbox(tmp_path)
        roots = ar.authorized_roots_for_task_main(sandbox)
        assert roots.names() == (ar.ROOT_REF_PROJECT_MAIN, ar.ROOT_REF_ACTIVE_WORKTREE)
        assert roots.governance_binding is None

    def test_task_main_with_binding_adds_read_search_governance_root(self, tmp_path: Path):
        sandbox = _sandbox(tmp_path)
        binding = _binding(_governance_base(tmp_path))
        roots = ar.authorized_roots_for_task_main(sandbox, governance_binding=binding)
        assert roots.names() == (
            ar.ROOT_REF_PROJECT_MAIN,
            ar.ROOT_REF_ACTIVE_WORKTREE,
            ar.ROOT_REF_LOCAL_GOVERNANCE,
        )
        governance = roots.get(ar.ROOT_REF_LOCAL_GOVERNANCE)
        assert governance is not None
        assert governance.capabilities == frozenset({ar.CAPABILITY_SEARCH, ar.CAPABILITY_READ})
        assert governance.has_capability(ar.CAPABILITY_WRITE) is False
        assert governance.root_path == binding.root_path
        assert governance.root_path != binding.governance_base
        assert governance.source == ar.SOURCE_TRUSTED_GOVERNANCE
        assert roots.governance_binding is binding

    def test_search_order_is_deterministic_and_default_compatible(self, tmp_path: Path):
        sandbox = _sandbox(tmp_path)
        binding = _binding(_governance_base(tmp_path))
        roots = ar.authorized_roots_for_task_main(sandbox, governance_binding=binding)
        assert [root.root_ref for root in roots.search_roots()] == [
            ar.ROOT_REF_ACTIVE_WORKTREE,
            ar.ROOT_REF_PROJECT_MAIN,
            ar.ROOT_REF_LOCAL_GOVERNANCE,
        ]

    def test_worker_never_inherits_local_governance(self, tmp_path: Path):
        sandbox = _sandbox(tmp_path)
        roots = ar.authorized_roots_for_worker(sandbox)
        assert roots.names() == (ar.ROOT_REF_ACTIVE_WORKTREE,)
        assert roots.get(ar.ROOT_REF_LOCAL_GOVERNANCE) is None

    def test_worker_set_cannot_grant_local_governance(self, tmp_path: Path):
        sandbox = _sandbox(tmp_path)
        binding = _binding(_governance_base(tmp_path))
        governance = ar.AuthorizedRoot(
            root_ref=ar.ROOT_REF_LOCAL_GOVERNANCE,
            root_kind=ar.ROOT_REF_LOCAL_GOVERNANCE,
            root_path=binding.root_path,
            capabilities=frozenset({ar.CAPABILITY_SEARCH, ar.CAPABILITY_READ}),
            project_id=sandbox.project_id,
            source=ar.SOURCE_TRUSTED_GOVERNANCE,
        )
        active = ar.authorized_roots_for_worker(sandbox).get(ar.ROOT_REF_ACTIVE_WORKTREE)
        with pytest.raises(ar.AuthorizedRootSetError):
            ar.AuthorizedRootSet(
                session_kind=ar.ROOT_SET_SESSION_WORKER,
                roots=(active, governance),
                governance_binding=binding,
            )

    def test_governance_root_without_trusted_binding_rejected(self, tmp_path: Path):
        sandbox = _sandbox(tmp_path)
        binding = _binding(_governance_base(tmp_path))
        governance = ar.AuthorizedRoot(
            root_ref=ar.ROOT_REF_LOCAL_GOVERNANCE,
            root_kind=ar.ROOT_REF_LOCAL_GOVERNANCE,
            root_path=binding.root_path,
            capabilities=frozenset({ar.CAPABILITY_READ}),
            project_id=sandbox.project_id,
            source=ar.SOURCE_TRUSTED_GOVERNANCE,
        )
        with pytest.raises(ar.AuthorizedRootSetError):
            ar.AuthorizedRootSet(
                session_kind=ar.ROOT_SET_SESSION_TASK_MAIN, roots=(governance,)
            )

    def test_governance_root_requires_trusted_governance_source(self, tmp_path: Path):
        sandbox = _sandbox(tmp_path)
        binding = _binding(_governance_base(tmp_path))
        with pytest.raises(ar.AuthorizedRootSetError):
            ar.AuthorizedRoot(
                root_ref=ar.ROOT_REF_LOCAL_GOVERNANCE,
                root_kind=ar.ROOT_REF_LOCAL_GOVERNANCE,
                root_path=binding.root_path,
                capabilities=frozenset({ar.CAPABILITY_READ}),
                project_id=sandbox.project_id,
            )

    def test_forged_governance_path_rejected_by_validator(self, tmp_path: Path):
        sandbox = _sandbox(tmp_path)
        binding = _binding(_governance_base(tmp_path))
        forged_path = tmp_path / "forged-governance"
        forged_path.mkdir()
        forged = ar.AuthorizedRoot(
            root_ref=ar.ROOT_REF_LOCAL_GOVERNANCE,
            root_kind=ar.ROOT_REF_LOCAL_GOVERNANCE,
            root_path=str(forged_path),
            capabilities=frozenset({ar.CAPABILITY_READ}),
            project_id=sandbox.project_id,
            source=ar.SOURCE_TRUSTED_GOVERNANCE,
        )
        with pytest.raises(ar.LocalGovernanceRootError):
            ar.validate_local_governance_root(
                forged, project_id=sandbox.project_id, binding=binding
            )

    def test_whole_base_path_rejected_by_validator(self, tmp_path: Path):
        sandbox = _sandbox(tmp_path)
        base = _governance_base(tmp_path)
        binding = _binding(base)
        forged = ar.AuthorizedRoot(
            root_ref=ar.ROOT_REF_LOCAL_GOVERNANCE,
            root_kind=ar.ROOT_REF_LOCAL_GOVERNANCE,
            root_path=str(base),
            capabilities=frozenset({ar.CAPABILITY_READ}),
            project_id=sandbox.project_id,
            source=ar.SOURCE_TRUSTED_GOVERNANCE,
        )
        with pytest.raises(ar.LocalGovernanceRootError):
            ar.validate_local_governance_root(
                forged, project_id=sandbox.project_id, binding=binding
            )

    def test_binding_project_mismatch_rejected_at_factory(self, tmp_path: Path):
        sandbox = _sandbox(tmp_path)
        foreign = ar.LocalGovernanceRootBinding.from_trusted_base(
            _governance_base(tmp_path), project_id=SIBLING_PROJECT_ID
        )
        with pytest.raises(ar.AuthorizedRootSetError):
            ar.authorized_roots_for_task_main(sandbox, governance_binding=foreign)

    def test_model_supplied_path_is_not_a_root_ref(self, tmp_path: Path):
        sandbox = _sandbox(tmp_path)
        binding = _binding(_governance_base(tmp_path))
        roots = ar.authorized_roots_for_task_main(sandbox, governance_binding=binding)
        with pytest.raises(ar.AuthorizedRootReferenceError):
            ar.resolve_authorized_root(roots, binding.root_path)
        with pytest.raises(ar.AuthorizedRootReferenceError):
            ar.resolve_authorized_root(roots, str(binding.governance_base))
        with pytest.raises(ar.AuthorizedRootUnknownError):
            ar.resolve_authorized_root(roots, SIBLING_PROJECT_ID)

    def test_agent_write_on_governance_root_denied(self, tmp_path: Path):
        sandbox = _sandbox(tmp_path)
        binding = _binding(_governance_base(tmp_path))
        roots = ar.authorized_roots_for_task_main(sandbox, governance_binding=binding)
        with pytest.raises(ar.AuthorizedRootCapabilityError):
            ar.resolve_authorized_root(
                roots, ar.ROOT_REF_LOCAL_GOVERNANCE, capability=ar.CAPABILITY_WRITE
            )
        assert roots.default_write_root().root_ref == ar.ROOT_REF_ACTIVE_WORKTREE
        assert LOCAL_GOVERNANCE_AGENT_WRITE_CAPABILITY is False
        assert ar.LOCAL_GOVERNANCE_AGENT_WRITE_CAPABILITY is False

    def test_sandbox_validator_is_not_weakened_by_governance_binding(self, tmp_path: Path):
        sandbox = _sandbox(tmp_path)
        binding = _binding(_governance_base(tmp_path))
        forged_project_main = ar.AuthorizedRoot(
            root_ref=ar.ROOT_REF_PROJECT_MAIN,
            root_kind=ar.ROOT_REF_PROJECT_MAIN,
            root_path=str(tmp_path / "unrelated-sibling"),
            capabilities=frozenset({ar.CAPABILITY_READ}),
            project_id=sandbox.project_id,
        )
        forged_worktree = ar.AuthorizedRoot(
            root_ref=ar.ROOT_REF_ACTIVE_WORKTREE,
            root_kind=ar.ROOT_REF_ACTIVE_WORKTREE,
            root_path=str(tmp_path / "unrelated-sibling"),
            capabilities=frozenset({ar.CAPABILITY_READ}),
            project_id=sandbox.project_id,
            worktree_id=sandbox.worktree_id,
        )
        for forged in (forged_project_main, forged_worktree):
            root_set = ar.AuthorizedRootSet(
                session_kind=ar.ROOT_SET_SESSION_TASK_MAIN,
                roots=(forged,),
                governance_binding=binding,
            )
            with pytest.raises(ar.AuthorizedRootSetError):
                ar.validate_root_set_against_sandbox(root_set, sandbox)

    def test_flags_and_projection_do_not_leak_physical_paths(self, tmp_path: Path):
        sandbox = _sandbox(tmp_path)
        binding = _binding(_governance_base(tmp_path))
        roots = ar.authorized_roots_for_task_main(sandbox, governance_binding=binding)
        public = roots.public_roots()
        assert all("root_path" not in item for item in public)
        projection = ar.build_trusted_project_context_projection(
            project_id=PROJECT_ID, worktree_id=sandbox.worktree_id, roots=roots
        )
        assert projection["roots"][ar.ROOT_REF_LOCAL_GOVERNANCE] == ["read", "search"]
        assert binding.root_path not in json.dumps(projection)
        assert binding.governance_base not in json.dumps(projection)
        assert ar.LOCAL_GOVERNANCE_ROOT_PROJECT_SCOPED is True
        assert ar.LOCAL_GOVERNANCE_MODEL_NOMINATED_PATH is False
        assert ar.UNRELATED_SIBLING_PROJECT_REACHABLE is False
        assert ar.MODEL_NOMINATES_ROOT_PATH is False


class TestLocalPlanReadPipeline:
    """W2-D: one Plan ontology, bounded local source kind, source-neutral ref."""

    def _adapter(self, tmp_path: Path, body: str = LOCAL_PLAN_BODY):
        base = _governance_base(tmp_path)
        binding = _binding(base)
        destination = _destination(binding)
        destination.plan_directory().mkdir(parents=True)
        destination.plan_document_path().write_text(body, encoding="utf-8")
        adapter = LocalPlanAuthorityReadAdapter(
            destination, binding=_plan_binding(destination)
        )
        return destination, adapter

    def test_snapshot_round_trips_raw_plan_document(self, tmp_path: Path):
        destination, adapter = self._adapter(tmp_path)
        snapshot = adapter.load()
        assert snapshot.body == LOCAL_PLAN_BODY
        assert snapshot.digest == _sha(LOCAL_PLAN_BODY)
        assert snapshot.revision == _sha(LOCAL_PLAN_BODY)
        assert adapter.plan_authority == f"local-governance:{PROJECT_ID}/{PLAN_ID}"
        assert str(destination.governance_root.root_path) not in adapter.plan_authority
        assert destination.governance_root.governance_base not in adapter.plan_authority

    def test_local_pipeline_normalizes_to_portable_plan_document(self, tmp_path: Path):
        _, adapter = self._adapter(tmp_path)
        document = load_local_portable_plan(adapter)
        assert isinstance(document, PortablePlanDocument)
        assert document.source_kind == PORTABLE_PLAN_SOURCE_LOCAL
        assert document.current_milestone == "M1"
        assert document.plan_status == "active"
        assert document.entry_base == "abcdef1234567890"
        assert document.milestone_work_items["M1"] == ("W1", "W2")
        assert document.source_digest

    def test_local_and_issue_sources_share_one_semantics(self, tmp_path: Path):
        _, adapter = self._adapter(tmp_path)
        local = load_local_portable_plan(adapter)
        issue = normalize_portable_plan(LOCAL_PLAN_BODY)
        assert issue.source_kind == PORTABLE_PLAN_SOURCE_ISSUE_BODY
        assert issue.current_milestone == local.current_milestone
        assert issue.plan_status == local.plan_status
        assert issue.milestone_work_items == local.milestone_work_items
        assert issue.milestone_dependencies == local.milestone_dependencies
        assert issue.milestone_approvals == local.milestone_approvals
        assert issue.entry_base == local.entry_base
        assert issue.work_source_slices == local.work_source_slices

    def test_unknown_source_kind_rejected(self):
        with pytest.raises(PlanNormalizationError) as excinfo:
            normalize_portable_plan(LOCAL_PLAN_BODY, source_kind="portable_plan_sqlite")
        assert excinfo.value.diagnostic_code == "MALFORMED_SOURCE_KIND"

    def test_projection_consumer_accepts_the_source_neutral_reference(self, tmp_path: Path):
        _, adapter = self._adapter(tmp_path)
        document = load_local_portable_plan(adapter)
        live, next_view = project_milestone_views(
            document, plan_authority=adapter.plan_authority
        )
        assert live.plan_authority == adapter.plan_authority
        assert live.milestone_id == "M1"
        assert live.milestone_user_approval_satisfied is True
        assert next_view is None

    def test_wrong_plan_binding_rejected(self, tmp_path: Path):
        base = _governance_base(tmp_path)
        binding = _binding(base)
        destination = _destination(binding)
        with pytest.raises(LocalGovernanceAdapterError):
            LocalPlanAuthorityReadAdapter(destination, binding=None)
        with pytest.raises(LocalGovernanceAdapterError):
            LocalPlanAuthorityReadAdapter(
                destination,
                binding=PlanAuthorityBinding(
                    plan_id=destination.plan_id,
                    source_kind=PLAN_AUTHORITY_SOURCE_GITHUB_ISSUE,
                    authority_ref="wzjcccc-dotcom/aota-hermes-tools#57",
                ),
            )
        with pytest.raises(LocalGovernanceAdapterError):
            LocalPlanAuthorityReadAdapter(
                destination,
                binding=PlanAuthorityBinding(
                    plan_id="plan_other",
                    source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
                    authority_ref=destination.authority_ref,
                ),
            )
        with pytest.raises(LocalGovernanceAdapterError):
            LocalPlanAuthorityReadAdapter(
                destination,
                binding=PlanAuthorityBinding(
                    plan_id=destination.plan_id,
                    source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
                    authority_ref="local-governance:somewhere/else",
                ),
            )

    def test_symlinked_or_oversized_document_rejected(self, tmp_path: Path):
        base = _governance_base(tmp_path)
        binding = _binding(base)
        destination = _destination(binding)
        outside = tmp_path / "outside-plan.md"
        outside.write_text("outside authority", encoding="utf-8")
        destination.plan_directory().mkdir(parents=True)
        destination.plan_document_path().symlink_to(outside)
        adapter = LocalPlanAuthorityReadAdapter(
            destination, binding=_plan_binding(destination)
        )
        with pytest.raises(LocalGovernanceAdapterError):
            adapter.load()

    def test_absent_document_is_not_an_authoritative_plan(self, tmp_path: Path):
        base = _governance_base(tmp_path)
        destination = _destination(_binding(base))
        adapter = LocalPlanAuthorityReadAdapter(
            destination, binding=_plan_binding(destination)
        )
        snapshot = adapter.load()
        assert snapshot.body == ""
        assert snapshot.digest == ABSENT_RAW_DOCUMENT_DIGEST
        with pytest.raises(PlanNormalizationError) as excinfo:
            load_local_portable_plan(adapter)
        assert excinfo.value.diagnostic_code == "EMPTY_PLAN_BODY"


class TestLocalPlanAuthorityMutation:
    """W2-E: existing port + journal + RecoveryExecutor; no second pipeline."""

    def _destination(self, tmp_path: Path) -> LocalPlanAuthorityDestination:
        return _destination(_binding(_governance_base(tmp_path)))

    def test_plan_init_materializes_verified_local_authority(self, tmp_path: Path):
        destination = self._destination(tmp_path)
        adapter = LocalPlanAuthorityAdapter(destination)
        assert isinstance(adapter, PlanAuthorityMutationPort)
        store = FileBackedDurableJournalStore(tmp_path / "journal")
        executor = RecoveryExecutor(store, adapter)
        record = _journal_record(destination)
        executor.create_prepared(record)
        final = executor.attempt_external_mutation(
            record.journal_id, _request(record, candidate_body=LOCAL_PLAN_BODY)
        )
        assert final is not None
        assert final.record.journal_state == JournalState.VERIFIED_RECOVERED
        assert adapter.mutate_count == 1
        assert adapter.write_count == 1
        assert destination.plan_document_path().read_text(encoding="utf-8") == LOCAL_PLAN_BODY
        reopened = FileBackedDurableJournalStore(tmp_path / "journal").get(record.journal_id)
        assert reopened is not None
        assert reopened.record.journal_state == JournalState.VERIFIED_RECOVERED
        document = load_local_portable_plan(
            LocalPlanAuthorityReadAdapter(destination, binding=_plan_binding(destination))
        )
        assert document.current_milestone == "M1"
        assert PLAN_AUTHORITY_MUTATION_PORT_REUSED is True
        assert SECOND_PLAN_MUTATION_PROTOCOL_CREATED is False
        assert SECOND_PLAN_ONTOLOGY_CREATED is False
        assert DURABLE_JOURNAL_REUSED is True
        assert RECOVERY_EXECUTOR_REUSED is True

    def test_replay_of_identical_candidate_is_idempotent(self, tmp_path: Path):
        destination = self._destination(tmp_path)
        adapter = LocalPlanAuthorityAdapter(destination)
        record = _journal_record(destination, observed_digest=None, authority_revision=None)
        request = _request(record)
        first = adapter.mutate(request)
        assert first.adapter_success is True
        assert adapter.write_count == 1
        second = adapter.mutate(request)
        assert second.adapter_success is True
        assert adapter.write_count == 1
        assert second.observed_raw_digest == first.observed_raw_digest

    def test_adapter_success_alone_is_not_verified(self, tmp_path: Path):
        destination = self._destination(tmp_path)
        adapter = LocalPlanAuthorityAdapter(destination)
        store = FileBackedDurableJournalStore(tmp_path / "journal")
        executor = RecoveryExecutor(store, adapter)
        record = _journal_record(destination)
        executor.create_prepared(record)
        direct = adapter.mutate(_request(record))
        assert direct.adapter_success is True
        assert destination.plan_document_path().is_file()
        still_prepared = store.get(record.journal_id)
        assert still_prepared is not None
        assert still_prepared.record.journal_state == JournalState.PREPARED
        recovered = executor.recover_one(record.journal_id)
        assert recovered is not None
        assert recovered.record.journal_state == JournalState.FAILED_NO_EFFECT
        assert recovered.record.journal_state != JournalState.VERIFIED
        assert ADAPTER_SUCCESS_ALONE_MEANS_VERIFIED is False

    def test_stale_precondition_fails_closed_with_no_effect(self, tmp_path: Path):
        destination = self._destination(tmp_path)
        destination.plan_directory().mkdir(parents=True)
        destination.plan_document_path().write_text("already existing authority", encoding="utf-8")
        adapter = LocalPlanAuthorityAdapter(destination)
        record = _journal_record(destination)
        response = adapter.mutate(_request(record))
        assert response.adapter_success is False
        assert response.error_code == "STALE_AUTHORITY"
        assert adapter.write_count == 0
        assert destination.plan_document_path().read_text(encoding="utf-8") == "already existing authority"
        store = FileBackedDurableJournalStore(tmp_path / "journal")
        executor = RecoveryExecutor(store, adapter)
        executor.create_prepared(record)
        final = executor.attempt_external_mutation(record.journal_id, _request(record))
        assert final is not None
        assert final.record.journal_state == JournalState.FAILED_NO_EFFECT

    def test_plan_init_never_amends_an_existing_document(self, tmp_path: Path):
        destination = self._destination(tmp_path)
        destination.plan_directory().mkdir(parents=True)
        destination.plan_document_path().write_text("existing authority body", encoding="utf-8")
        adapter = LocalPlanAuthorityAdapter(destination)
        record = _journal_record(
            destination,
            observed_digest=_sha("existing authority body"),
            authority_revision=_sha("existing authority body"),
        )
        response = adapter.mutate(_request(record))
        assert response.adapter_success is False
        assert response.error_code == "KNOWN_REJECTION"
        assert adapter.write_count == 0
        assert destination.plan_document_path().read_text(encoding="utf-8") == "existing authority body"

    def test_wrong_plan_binding_rejected_with_no_effect(self, tmp_path: Path):
        destination = self._destination(tmp_path)
        adapter = LocalPlanAuthorityAdapter(destination)
        record = _journal_record(destination)
        wrong_target = object_ref_subject(
            make_id(IdKind.SUBJECT, "plan_other", sub_kind=SubjectKind.PLAN)
        )
        response = adapter.mutate(_request(record, target=wrong_target))
        assert response.adapter_success is False
        assert response.error_code == "KNOWN_REJECTION"
        assert not destination.plan_document_path().exists()
        with pytest.raises(LocalGovernanceAuthorityError):
            adapter.read_raw_authority(wrong_target)
        with pytest.raises(LocalGovernanceAuthorityError):
            adapter.verify(wrong_target)

    def test_plan_retirement_is_a_truthful_known_no_effect(self, tmp_path: Path):
        destination = self._destination(tmp_path)
        adapter = LocalPlanAuthorityAdapter(destination)
        record = _journal_record(
            destination, operation=PLAN_RETIREMENT_OPERATION, observed_digest=None
        )
        response = adapter.mutate(_request(record))
        assert response.adapter_success is False
        assert response.error_code == "KNOWN_REJECTION"
        assert "not implemented" in (response.error_message or "")
        assert adapter.write_count == 0
        assert not destination.plan_document_path().exists()
        assert LOCAL_PLAN_RETIREMENT_IMPLEMENTED is False

    def test_at_most_one_mutation_attempt(self, tmp_path: Path):
        destination = self._destination(tmp_path)
        adapter = LocalPlanAuthorityAdapter(destination)
        store = FileBackedDurableJournalStore(tmp_path / "journal")
        executor = RecoveryExecutor(store, adapter)
        record = _journal_record(destination)
        executor.create_prepared(record)
        first = executor.attempt_external_mutation(record.journal_id, _request(record))
        assert first is not None
        assert first.record.journal_state == JournalState.VERIFIED_RECOVERED
        with pytest.raises(ExecutorError):
            executor.attempt_external_mutation(record.journal_id, _request(record))
        assert adapter.mutate_count == 1
        assert adapter.write_count == 1

    def test_unknown_outcome_is_reconciled_without_blind_retry(self, tmp_path: Path):
        destination = self._destination(tmp_path)

        class _CrashingAdapter(LocalPlanAuthorityAdapter):
            def mutate(self, request):  # noqa: D102 - deterministic crash injection
                self.mutate_count += 1
                raise RuntimeError("simulated crash before external effect")

        adapter = _CrashingAdapter(destination)
        store = FileBackedDurableJournalStore(tmp_path / "journal")
        executor = RecoveryExecutor(store, adapter)
        record = _journal_record(destination)
        executor.create_prepared(record)
        unknown = executor.attempt_external_mutation(record.journal_id, _request(record))
        assert unknown is not None
        assert unknown.record.journal_state == JournalState.OUTCOME_UNKNOWN
        recovered = executor.recover_one(record.journal_id)
        assert recovered is not None
        assert recovered.record.journal_state == JournalState.RETRYABLE_NO_EFFECT
        assert adapter.mutate_count == 1

    def test_fresh_retry_authorization_required_and_sufficient(self, tmp_path: Path):
        destination = self._destination(tmp_path)

        class _CrashingAdapter(LocalPlanAuthorityAdapter):
            def mutate(self, request):  # noqa: D102 - deterministic crash injection
                self.mutate_count += 1
                raise RuntimeError("simulated crash before external effect")

        crashing = _CrashingAdapter(destination)
        store = FileBackedDurableJournalStore(tmp_path / "journal")
        executor = RecoveryExecutor(store, crashing)
        first_record = _journal_record(destination)
        executor.create_prepared(first_record)
        unknown = executor.attempt_external_mutation(first_record.journal_id, _request(first_record))
        assert unknown is not None
        assert unknown.record.journal_state == JournalState.OUTCOME_UNKNOWN
        retryable = executor.recover_one(first_record.journal_id)
        assert retryable is not None
        assert retryable.record.journal_state == JournalState.RETRYABLE_NO_EFFECT
        with pytest.raises(RetryHandoffError):
            create_retry_journal(
                store,
                retryable,
                new_journal_id=f"journal-{uuid.uuid4().hex[:12]}",
                new_attempt_id=f"attempt-{uuid.uuid4().hex[:8]}",
                new_authorization_reference="auth-ref-w2-retry",
                new_lease_reference="lease-ref-w2-retry",
            )
        fresh = create_retry_journal(
            store,
            retryable,
            new_journal_id=f"journal-{uuid.uuid4().hex[:12]}",
            new_attempt_id=f"attempt-{uuid.uuid4().hex[:8]}",
            new_authorization_reference="auth-ref-w2-retry",
            new_lease_reference="lease-ref-w2-retry",
            new_authority_observed_raw_digest=ABSENT_RAW_DOCUMENT_DIGEST,
            has_fresh_authorization=True,
            has_fresh_subject_precondition=True,
            has_fresh_raw_authority_precondition=True,
            has_new_bounded_lease=True,
        )
        assert fresh.record.journal_state == JournalState.PREPARED
        assert fresh.record.correlation_id == first_record.correlation_id
        assert fresh.record.journal_id != first_record.journal_id
        assert fresh.record.lease_reference != first_record.lease_reference
        normal = LocalPlanAuthorityAdapter(destination)
        retry_executor = RecoveryExecutor(store, normal)
        verified = retry_executor.attempt_external_mutation(
            fresh.record.journal_id, _request(fresh.record)
        )
        assert verified is not None
        assert verified.record.journal_state == JournalState.VERIFIED_RECOVERED
        assert normal.write_count == 1
        assert crashing.write_count == 0


class _FakeHostClient:
    def dispatch(self, payload):  # pragma: no cover - trivial fake
        return {"adapter_handle": "fake-1", "status": "running", "dispatch_time": "t"}


def _checkout(parent: Path, folder: str, project_id: str, origin: str) -> Path:
    root = parent / folder
    (root / ".aota").mkdir(parents=True)
    (root / ".aota" / "project.yaml").write_text(
        MANIFEST.format(project_id=project_id), encoding="utf-8"
    )
    subprocess.run(["git", "init", "-q"], cwd=root, check=True)
    subprocess.run(["git", "remote", "add", "origin", origin], cwd=root, check=True)
    return root


def _runtime_config(tmp_path: Path) -> Path:
    exe = tmp_path / "hermes-stub"
    exe.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    exe.chmod(0o755)
    cfg = tmp_path / "runtime.json"
    cfg.write_text(
        json.dumps(
            {
                "executor": "hermes",
                "executable": str(exe),
                "concurrency": 1,
                "provider": "test-provider",
                "model": "test-model",
                "bindings": {
                    "analyst": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "coder": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "reviewer": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "project-steward": {"profile": "aota-worker", "toolsets": ["aota"]},
                    "task-main": {"profile": "aota-task-main"},
                },
            }
        ),
        encoding="utf-8",
    )
    return cfg


def _host_fixture(tmp_path: Path):
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    checkout = _checkout(
        workspace, "chatgpt-hermes-mcp-poc", PROJECT_ID, f"https://github.com/{OWNER}/{REPO}.git"
    )
    (checkout / "readme.md").write_text("canonical project needle", encoding="utf-8")
    registry = tmp_path / "workspaces.json"
    registry.write_text(json.dumps({"ws": {"candidates": [str(workspace)]}}), encoding="utf-8")
    worktree = tmp_path / "active-worktree"
    worktree.mkdir()
    (worktree / "change.txt").write_text("worktree needle", encoding="utf-8")
    return checkout, registry, worktree


def _compose_host(tmp_path: Path, worktree: Path, registry: Path, governance_base: Path | None):
    return compose_thin_task_main_host(
        worktree_root=worktree,
        project_id=PROJECT_ID,
        worktree_id="wt-af57-w2",
        runtime_config_path=_runtime_config(tmp_path),
        origin_task_main_session_ref="20260915_af57_m1w2_session",
        host_client=_FakeHostClient(),
        source_repository=f"{OWNER}/{REPO}",
        registry_path=registry,
        governance_base=governance_base,
    )


class TestThinHostLocalGovernanceWiring:
    """V2: task-main search/read through the real composed aota.invoke path."""

    def test_task_main_searches_and_reads_its_governance_scope(self, tmp_path: Path):
        _checkout, registry, worktree = _host_fixture(tmp_path)
        base = _governance_base(tmp_path)
        plan_dir = base / PROJECT_ID / PLAN_ID
        plan_dir.mkdir(parents=True)
        (plan_dir / "plan.md").write_text(LOCAL_PLAN_BODY, encoding="utf-8")
        host = _compose_host(tmp_path, worktree, registry, base)
        assert host.local_governance_binding is not None
        read = host.invoke(
            "workspace.read", {"path": f"{PLAN_ID}/plan.md", "root_ref": "local-governance"}
        )
        assert read.get("ok") is True, read
        assert read["payload"]["root_ref"] == "local-governance"
        assert "AF57 M1/W2 local governance fixture" in read["payload"]["content"]
        search = host.invoke("workspace.search", {"query": "AF57"})
        assert search.get("ok") is True, search
        tagged = {(item["root_ref"], item["path"]) for item in search["payload"]["results"]}
        assert ("local-governance", f"{PLAN_ID}/plan.md") in tagged
        write = host.invoke(
            "workspace.write",
            {"path": "x.txt", "content": "x", "mode": "create_only", "root_ref": "local-governance"},
        )
        assert write.get("ok") is False
        assert write["error"]["code"] in (
            "AUTHORIZED_ROOT_UNKNOWN",
            "AUTHORIZED_ROOT_CAPABILITY_DENIED",
            "INVALID_INPUT",
            "UNKNOWN_INPUT",
            "AUTHORITY_DENIED",
        )
        assert not (plan_dir / "x.txt").exists()

    def test_sibling_project_scope_denied_through_the_host(self, tmp_path: Path):
        _checkout, registry, worktree = _host_fixture(tmp_path)
        base = _governance_base(tmp_path)
        sibling = base / SIBLING_PROJECT_ID / PLAN_ID
        sibling.mkdir(parents=True)
        (sibling / "plan.md").write_text("sibling project secret", encoding="utf-8")
        host = _compose_host(tmp_path, worktree, registry, base)
        denied = host.invoke(
            "workspace.read", {"path": f"{PLAN_ID}/plan.md", "root_ref": "local-governance"}
        )
        assert denied.get("ok") is False
        assert denied["error"]["code"] == "NOT_FOUND"
        base_read = host.invoke(
            "workspace.read", {"path": f"{SIBLING_PROJECT_ID}/{PLAN_ID}/plan.md", "root_ref": "local-governance"}
        )
        assert base_read.get("ok") is False
        traversal = host.invoke(
            "workspace.read",
            {"path": f"../{SIBLING_PROJECT_ID}/{PLAN_ID}/plan.md", "root_ref": "local-governance"},
        )
        assert traversal.get("ok") is False
        assert traversal["error"]["code"] == "INVALID_PATH"

    def test_host_without_governance_base_keeps_accepted_roots(self, tmp_path: Path):
        _checkout, registry, worktree = _host_fixture(tmp_path)
        host = _compose_host(tmp_path, worktree, registry, None)
        assert host.local_governance_binding is None
        assert host.authorized_roots.names() == (
            ar.ROOT_REF_PROJECT_MAIN,
            ar.ROOT_REF_ACTIVE_WORKTREE,
        )
        denied = host.invoke(
            "workspace.read", {"path": "plan.md", "root_ref": "local-governance"}
        )
        assert denied.get("ok") is False
        assert denied["error"]["code"] == "AUTHORIZED_ROOT_UNKNOWN"
