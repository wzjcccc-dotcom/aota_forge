"""AF #57 M2/W4 — Governed Read Extensions: CrossProjectGrant & authorized-evidence.

V1 contract tests + bounded V2 composition over real accepted modules:

* durable, typed, bounded CrossProjectGrant owned by the existing Project
  Governance Store (no second grant database, no schema-version bump, no
  generic migration framework, no ORM);
* authority basis grounded in trusted Plan/user-gate facts (no arbitrary
  model string, no model self-grant, no model physical path, no write);
* trusted target Project resolution through the existing canonical
  registry/project binding; grant stores logical identity only;
* grant CAS/revoke, stale mutation fail-closed, same-process and real-process
  reopen durability, corrupt-grant fail-closed, lazy plan-bounded expiry;
* AuthorizedRootSet grant-specific foreign read/search (bounded scope and
  symlink/traversal protection) without weakening default sibling isolation;
* authorized-evidence as a trusted project-scoped read/search root plus a
  typed projection over existing evidence/result owners (no second Evidence
  Store, no duplicated bytes, projection/ref is never authority);
* no new public Agent Tool, no operation-surface change, no W3/M2 hot-file
  contract change.

DOES_NOT_PROVE: production cross-project dogfood, foreign source write,
evidence UX, Stewardship engine, migration/cutover (M3 owns operational
grant proof).
"""

from __future__ import annotations

import ast
import dataclasses
import json
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.composition.governed_read import (
    CROSS_PROJECT_WRITE_ALLOWED,
    DEFAULT_ROOT_SET_UNCHANGED_WITHOUT_GRANTS,
    GRANT_STORE_OWNER,
    SECOND_EVIDENCE_STORE_CREATED as COMPOSITION_SECOND_EVIDENCE_STORE,
    bind_authorized_evidence_root,
    create_bound_cross_project_grant,
    materialize_cross_project_grant_binding,
    materialize_live_cross_project_read_roots,
    resolve_cross_project_target_project,
    trusted_target_resolution,
)
from aota_forge.composition.project_governance import open_project_governance_store
from aota_forge.composition.project_binding import derive_canonical_project_evidence
from aota_forge.core.providers.tool import ToolRequest
from aota_forge.core.result_governance import GovernedReference, GovernedReferenceKind
from aota_forge.governance import cross_project_grant as grant_module
from aota_forge.governance import evidence_projection as projection_module
from aota_forge.governance.cross_project_grant import (
    AUTHORITY_BASIS_EXPLICIT_USER_APPROVAL,
    AUTHORITY_BASIS_PREAPPROVED_BY_PLAN,
    CROSS_PROJECT_GRANT_CAPABILITIES,
    END_CONDITION_PLAN_RETIRED,
    END_CONDITION_UNTIL_REVOKED,
    GRANT_STATE_ACTIVE,
    GRANT_STATE_REVOKED,
    CrossProjectGrant,
    CrossProjectGrantAlreadyExistsError,
    CrossProjectGrantAuthorityError,
    CrossProjectGrantCapabilityError,
    CrossProjectGrantInactiveError,
    CrossProjectGrantNotFoundError,
    CrossProjectGrantRecordError,
    CrossProjectGrantSelfGrantError,
    CrossProjectGrantStore,
    CrossProjectGrantTargetError,
    ExplicitUserApproval,
    PreapprovedByPlan,
    StaleCrossProjectGrantRevisionError,
    TargetProjectResolution,
    UserGateApproval,
    create_cross_project_grant,
    evaluate_cross_project_grant,
    require_live_cross_project_grant,
    validate_bounded_scope,
)
from aota_forge.governance.evidence_projection import (
    ACCESS_KIND_RECEIPT_READER,
    ACCESS_KIND_RESULT_HYDRATE,
    ACCESS_KIND_WORKSPACE_READ,
    ACCESS_KIND_WORKSPACE_SEARCH,
    ACCESS_KINDS,
    AUTHORIZED_EVIDENCE_ROOT_REF,
    AuthorizedEvidenceProjectionError,
    EVIDENCE_OWNERS,
    OWNER_KIND_DURABLE_PAYLOAD,
    OWNER_KIND_EVIDENCE_TREE,
    OWNER_KIND_RESULT_STORE,
    build_authorized_evidence_projection,
    project_governed_evidence_item,
    project_result_payload_item,
    project_workspace_evidence_item,
)
from aota_forge.governance.project_store import (
    PLAN_LIFECYCLE_ACTIVE,
    PLAN_LIFECYCLE_RETIRED,
    ProjectGovernanceCorruptStateError,
    ProjectPlanRecord,
)
from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore
from aota_forge.work_plane import authorized_roots as ar
from aota_forge.work_plane.workspace_tools import (
    WORKSPACE_READ_DESCRIPTOR,
    WORKSPACE_SEARCH_DESCRIPTOR,
    BoundedWorkspaceToolProvider,
    create_broad_workspace_read_authority,
)
from aota_forge.work_plane.worktree_sandbox import bind_worktree_sandbox

REPO_ROOT = Path(__file__).resolve().parents[1]
GOVERNANCE_DIR = REPO_ROOT / "aota_forge" / "governance"
W4_NEW_MODULES = (
    GOVERNANCE_DIR / "cross_project_grant.py",
    GOVERNANCE_DIR / "evidence_projection.py",
    REPO_ROOT / "aota_forge" / "composition" / "governed_read.py",
)

NATIVE = "aota_forge"
TARGET = "aota_reader_mcp"
SIBLING = "unrelated_sibling"
WORKTREE_ID = "wt-af57-m2w4"
PLAN_ID = "plan_af57_pilot"

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

_REOPEN_SCRIPT = """
import sys

from aota_forge.governance.sqlite_store import SQLiteProjectGovernanceStore

store = SQLiteProjectGovernanceStore(sys.argv[1])
grant = store.get_cross_project_grant(sys.argv[2])
assert grant is not None, "W4_REOPEN_GRANT_MISSING"
assert grant.requesting_project == "aota_forge", grant.requesting_project
assert grant.target_project == "aota_reader_mcp", grant.target_project
assert grant.root_kind == "project-main", grant.root_kind
assert sorted(grant.capabilities) == ["read", "search"], grant.capabilities
assert grant.authority.basis == "preapproved_by_plan", grant.authority.basis
assert grant.authority.anchor_plan_id == "plan_af57_pilot", grant.authority.anchor_plan_id
assert grant.state == "active", grant.state
assert grant.revision == 1, grant.revision
plan = store.get_plan("aota_forge", "plan_af57_pilot")
assert plan is not None and plan.revision == 1, plan
revoked = store.revoke_cross_project_grant(sys.argv[2], 1)
assert revoked.state == "revoked", revoked.state
assert revoked.revision == 2, revoked.revision
store.close()
print(f"W4_REOPEN_OK grant={revoked.grant_id} revision={revoked.revision}")
"""


def _project(root: Path, project_id: str) -> Path:
    (root / ".aota").mkdir(parents=True, exist_ok=True)
    (root / ".aota" / "project.yaml").write_text(
        MANIFEST.format(project_id=project_id), encoding="utf-8"
    )
    return root


def _workspace(tmp_path: Path):
    ws = tmp_path / "workspace"
    native = _project(ws / NATIVE, NATIVE)
    target = _project(ws / TARGET, TARGET)
    sibling = _project(ws / SIBLING, SIBLING)
    worktree = ws / ".aota-worktrees" / WORKTREE_ID
    worktree.mkdir(parents=True)
    (native / "native.txt").write_text("native project needle", encoding="utf-8")
    (target / "top_secret.txt").write_text("target top secret needle", encoding="utf-8")
    (target / "docs").mkdir()
    (target / "docs" / "public.md").write_text("public docs needle", encoding="utf-8")
    (sibling / "sibling.txt").write_text("sibling secret needle", encoding="utf-8")
    return ws, native, target, sibling, worktree


def _sandbox(ws: Path, worktree: Path):
    evidence = derive_canonical_project_evidence(workspace_root=ws, project_id=NATIVE)
    return bind_worktree_sandbox(evidence, WORKTREE_ID, worktree)


def _store(tmp_path: Path, name: str = "governance.sqlite3") -> SQLiteProjectGovernanceStore:
    return SQLiteProjectGovernanceStore(tmp_path / name)


def _plan_binding(project_id: str = NATIVE, plan_id: str = PLAN_ID) -> PlanAuthorityBinding:
    return PlanAuthorityBinding(
        plan_id=plan_id,
        source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        authority_ref=f"{project_id}/plans",
    )


def _plan_record(
    project_id: str = NATIVE,
    plan_id: str = PLAN_ID,
    *,
    lifecycle_state: str = PLAN_LIFECYCLE_ACTIVE,
) -> ProjectPlanRecord:
    return ProjectPlanRecord(
        project_id=project_id,
        plan_id=plan_id,
        lifecycle_state=lifecycle_state,
        authority=_plan_binding(project_id, plan_id),
    )


def _target_binding(ws: Path, project_id: str = TARGET):
    return resolve_cross_project_target_project(project_id=project_id, workspace_root=ws)


def _accepting_store(tmp_path: Path, *, plan: ProjectPlanRecord | None = None):
    store = _store(tmp_path)
    store.put_plan(plan if plan is not None else _plan_record())
    return store


def _grant(
    store,
    target_binding,
    *,
    bounded_scope: str = "",
    end_condition: str = END_CONDITION_UNTIL_REVOKED,
    bound_plan_id: str = "",
    capabilities=CROSS_PROJECT_GRANT_CAPABILITIES,
):
    return create_bound_cross_project_grant(
        store=store,
        requesting_project=NATIVE,
        target_binding=target_binding,
        authority=PreapprovedByPlan(plan_id=PLAN_ID),
        bounded_scope=bounded_scope,
        end_condition=end_condition,
        bound_plan_id=bound_plan_id,
        capabilities=capabilities,
    )


def _task_main_provider(sandbox, roots, operation):
    authority = create_broad_workspace_read_authority(
        sandbox, operation, handoff=None, applicable_policies=(), authorized_roots=roots
    )
    return BoundedWorkspaceToolProvider(authority)


def _read(provider, path, root_ref=None, **extra):
    inputs = {"path": path, **extra}
    if root_ref is not None:
        inputs["root_ref"] = root_ref
    return provider.invoke(ToolRequest(operation=provider.authority.operation, inputs=inputs))


def _search(provider, query, root_ref=None, **extra):
    inputs = {"query": query, **extra}
    if root_ref is not None:
        inputs["root_ref"] = root_ref
    return provider.invoke(ToolRequest(operation=provider.authority.operation, inputs=inputs))


def _raw_connection(db_path: Path) -> sqlite3.Connection:
    return sqlite3.connect(str(db_path))


def _direct_imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"))
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.add(node.module)
    return names


# ---------------------------------------------------------------------------
# V1 — CrossProjectGrant record / authority / lifecycle / durability
# ---------------------------------------------------------------------------


class TestGrantRecordValidation:
    def test_record_fields_are_logical_identities_only(self):
        fields = {item.name for item in dataclasses.fields(CrossProjectGrant)}
        assert fields == {
            "grant_id",
            "requesting_project",
            "target_project",
            "root_kind",
            "capabilities",
            "bounded_scope",
            "authority",
            "end_condition",
            "target_resolution",
            "bound_plan_id",
            "revision",
            "state",
        }
        for forbidden in ("root_path", "path", "host_path", "physical_path", "cwd"):
            assert forbidden not in fields

    def test_unknown_project_and_self_grant_rejected(self, tmp_path: Path):
        store = _accepting_store(tmp_path)
        target = TargetProjectResolution.from_evidence(
            trusted_target_resolution(_target_binding(_workspace(tmp_path)[0])),
            expected_project_id=TARGET,
        )
        with pytest.raises(CrossProjectGrantSelfGrantError):
            create_cross_project_grant(
                store=store,
                requesting_project=TARGET,
                target=target,
                authority=PreapprovedByPlan(plan_id=PLAN_ID),
            )
        with pytest.raises(CrossProjectGrantRecordError):
            create_cross_project_grant(
                store=store,
                requesting_project="AOTA",
                target=target,
                authority=PreapprovedByPlan(plan_id=PLAN_ID),
            )
        store.close()

    def test_write_and_unknown_capabilities_rejected(self, tmp_path: Path):
        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        store = _accepting_store(tmp_path)
        for capabilities in (
            frozenset({"read", "write"}),
            frozenset({"write"}),
            frozenset({"read", "admin"}),
            frozenset(),
        ):
            with pytest.raises(CrossProjectGrantCapabilityError):
                _grant(store, _target_binding(ws), capabilities=capabilities)
        record = _grant(store, _target_binding(ws))
        assert record.capabilities == frozenset({"read", "search"})
        assert grant_module.CROSS_PROJECT_WRITE_ALLOWED is False
        store.close()

    def test_unknown_root_kind_rejected(self, tmp_path: Path):
        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        store = _accepting_store(tmp_path)
        with pytest.raises(CrossProjectGrantRecordError):
            create_bound_cross_project_grant(
                store=store,
                requesting_project=NATIVE,
                target_binding=_target_binding(ws),
                authority=PreapprovedByPlan(plan_id=PLAN_ID),
                root_kind="active-worktree",
            )
        store.close()

    @pytest.mark.parametrize(
        "scope",
        ("/abs", "../up", "a/../b", "a//b", "a/./b", "a\\b", "a" * 600, "a/b/../../c", None, 57),
    )
    def test_bounded_scope_syntax_fails_closed(self, scope):
        with pytest.raises(CrossProjectGrantRecordError):
            validate_bounded_scope(scope)

    @pytest.mark.parametrize("scope", ("", "docs", "docs/plans", "a-b_c.1/x2"))
    def test_bounded_scope_accepts_bounded_relative_subtrees(self, scope):
        assert validate_bounded_scope(scope) == scope

    def test_authority_basis_must_be_typed_trusted_fact(self, tmp_path: Path):
        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        store = _accepting_store(tmp_path)
        target = TargetProjectResolution.from_evidence(
            trusted_target_resolution(_target_binding(ws)), expected_project_id=TARGET
        )
        with pytest.raises(CrossProjectGrantAuthorityError):
            create_cross_project_grant(
                store=store,
                requesting_project=NATIVE,
                target=target,
                authority="preapproved_by_plan",
            )
        with pytest.raises(CrossProjectGrantAuthorityError):
            create_cross_project_grant(
                store=store,
                requesting_project=NATIVE,
                target=target,
                authority=PreapprovedByPlan(plan_id="not-a-plan-id"),
            )
        store.close()

    def test_preapproved_authority_requires_active_plan_of_requesting_project(
        self, tmp_path: Path
    ):
        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        target = TargetProjectResolution.from_evidence(
            trusted_target_resolution(_target_binding(ws)), expected_project_id=TARGET
        )
        bare = _store(tmp_path, "bare.sqlite3")
        with pytest.raises(CrossProjectGrantAuthorityError):
            create_cross_project_grant(
                store=bare,
                requesting_project=NATIVE,
                target=target,
                authority=PreapprovedByPlan(plan_id=PLAN_ID),
            )
        bare.close()
        retired = _store(tmp_path, "retired.sqlite3")
        retired.put_plan(_plan_record(lifecycle_state=PLAN_LIFECYCLE_RETIRED))
        with pytest.raises(CrossProjectGrantAuthorityError):
            create_cross_project_grant(
                store=retired,
                requesting_project=NATIVE,
                target=target,
                authority=PreapprovedByPlan(plan_id=PLAN_ID),
            )
        retired.close()
        other = _store(tmp_path, "other.sqlite3")
        other.put_plan(_plan_record(project_id=SIBLING))
        with pytest.raises(CrossProjectGrantAuthorityError):
            create_cross_project_grant(
                store=other,
                requesting_project=NATIVE,
                target=target,
                authority=PreapprovedByPlan(plan_id=PLAN_ID),
            )
        other.close()

    def test_explicit_user_approval_requires_typed_gate_fact(self, tmp_path: Path):
        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        store = _accepting_store(tmp_path)
        target = TargetProjectResolution.from_evidence(
            trusted_target_resolution(_target_binding(ws)), expected_project_id=TARGET
        )
        with pytest.raises(CrossProjectGrantAuthorityError):
            create_cross_project_grant(
                store=store,
                requesting_project=NATIVE,
                target=target,
                authority=ExplicitUserApproval(
                    plan_id=PLAN_ID, approval="operator said yes"
                ),
            )
        with pytest.raises(CrossProjectGrantAuthorityError):
            UserGateApproval(approval_ref="gate-1", approval_digest="not-a-digest")
        with pytest.raises(CrossProjectGrantAuthorityError):
            UserGateApproval(approval_ref="", approval_digest="a" * 64)
        with pytest.raises(CrossProjectGrantAuthorityError):
            UserGateApproval(approval_ref="gate\n1", approval_digest="a" * 64)
        record = create_cross_project_grant(
            store=store,
            requesting_project=NATIVE,
            target=target,
            authority=ExplicitUserApproval(
                plan_id=PLAN_ID,
                approval=UserGateApproval(approval_ref="gate-57-approval", approval_digest="a" * 64),
            ),
        )
        assert record.authority.basis == AUTHORITY_BASIS_EXPLICIT_USER_APPROVAL
        assert record.authority.authority_ref == "gate-57-approval"
        assert len(record.authority.authority_digest) == 64
        # the operator gate digest is bound into the authority digest: the
        # same gate reference with a different gate digest is a different
        # grounded authority (and therefore a different deterministic grant).
        other_digest_authority = ExplicitUserApproval(
            plan_id=PLAN_ID,
            approval=UserGateApproval(approval_ref="gate-57-approval", approval_digest="b" * 64),
        )
        grounded_other = create_cross_project_grant(
            store=store,
            requesting_project=NATIVE,
            target=target,
            authority=other_digest_authority,
        )
        assert grounded_other.authority.authority_digest != record.authority.authority_digest
        assert grounded_other.grant_id != record.grant_id
        preapproved = _grant(store, _target_binding(ws))
        assert preapproved.authority.basis == AUTHORITY_BASIS_PREAPPROVED_BY_PLAN
        store.close()

    def test_target_resolution_requires_singular_resolved_evidence(self, tmp_path: Path):
        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        evidence = derive_canonical_project_evidence(workspace_root=ws, project_id=NATIVE)
        with pytest.raises(CrossProjectGrantTargetError):
            TargetProjectResolution.from_evidence(evidence, expected_project_id=TARGET)
        missing = derive_canonical_project_evidence(
            workspace_root=ws, project_id="does_not_exist"
        )
        with pytest.raises(CrossProjectGrantTargetError):
            TargetProjectResolution.from_evidence(missing, expected_project_id="does_not_exist")

    def test_unknown_target_project_fails_trusted_resolution(self, tmp_path: Path):
        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        from aota_forge.core.contracts.errors import ProjectNotFoundError

        with pytest.raises(ProjectNotFoundError):
            resolve_cross_project_target_project(
                project_id="does_not_exist", workspace_root=ws
            )

    def test_end_condition_and_bound_plan_validation(self, tmp_path: Path):
        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        store = _accepting_store(tmp_path)
        with pytest.raises(CrossProjectGrantRecordError):
            _grant(store, _target_binding(ws), end_condition="whenever")
        record = _grant(
            store,
            _target_binding(ws),
            end_condition=END_CONDITION_PLAN_RETIRED,
            bound_plan_id="",
        )
        assert record.end_condition == END_CONDITION_PLAN_RETIRED
        assert record.bound_plan_id == PLAN_ID
        with pytest.raises(CrossProjectGrantRecordError):
            _grant(
                store,
                _target_binding(ws),
                end_condition=END_CONDITION_PLAN_RETIRED,
                bound_plan_id="plan_missing",
            )
        assert record.bound_plan_id == PLAN_ID
        store.close()

    def test_dict_round_trip_is_lossless(self, tmp_path: Path):
        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        store = _accepting_store(tmp_path)
        record = _grant(store, _target_binding(ws), bounded_scope="docs")
        assert CrossProjectGrant.from_dict(record.to_dict()) == record
        with pytest.raises(CrossProjectGrantRecordError):
            CrossProjectGrant.from_dict({**record.to_dict(), "root_path": "/etc"})
        incomplete = {k: v for k, v in record.to_dict().items() if k != "capabilities"}
        with pytest.raises(CrossProjectGrantRecordError):
            CrossProjectGrant.from_dict(incomplete)
        store.close()

    def test_grant_id_is_trusted_minted_and_not_model_supplied(self, tmp_path: Path):
        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        store = _accepting_store(tmp_path)
        record = _grant(store, _target_binding(ws))
        assert record.grant_id.startswith("grant-")
        assert record.grant_id == record.grant_id.lower()
        with pytest.raises(CrossProjectGrantRecordError):
            CrossProjectGrant(
                grant_id="../../etc/passwd",
                requesting_project=NATIVE,
                target_project=TARGET,
                root_kind="project-main",
                capabilities=frozenset({"read"}),
                bounded_scope="",
                authority=record.authority,
                end_condition=END_CONDITION_UNTIL_REVOKED,
                target_resolution=record.target_resolution,
            )
        assert grant_module.MODEL_MINTS_GRANT is False
        store.close()


class TestGrantLifecycleDurability:
    def test_create_is_revision_one_and_duplicate_content_fails(self, tmp_path: Path):
        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        store = _accepting_store(tmp_path)
        binding = _target_binding(ws)
        created = _grant(store, binding)
        assert created.revision == 1
        assert created.state == GRANT_STATE_ACTIVE
        with pytest.raises(CrossProjectGrantAlreadyExistsError):
            _grant(store, binding)
        listed = store.list_cross_project_grants(NATIVE)
        assert [grant.grant_id for grant in listed] == [created.grant_id]
        assert store.list_cross_project_grants(SIBLING) == ()
        store.close()

    def test_revoke_cas_and_stale_mutation_fail_closed(self, tmp_path: Path):
        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        store = _accepting_store(tmp_path)
        created = _grant(store, _target_binding(ws))
        revoked = store.revoke_cross_project_grant(created.grant_id, 1)
        assert revoked.state == GRANT_STATE_REVOKED
        assert revoked.revision == 2
        assert store.list_cross_project_grants(NATIVE, active_only=True) == ()
        with pytest.raises(StaleCrossProjectGrantRevisionError):
            store.revoke_cross_project_grant(created.grant_id, 1)
        with pytest.raises(CrossProjectGrantRecordError):
            store.revoke_cross_project_grant(created.grant_id, 2)
        preserved = store.get_cross_project_grant(created.grant_id)
        assert preserved.state == GRANT_STATE_REVOKED
        assert preserved.revision == 2
        with pytest.raises(CrossProjectGrantNotFoundError):
            store.revoke_cross_project_grant("grant-00000000000000000000", 1)
        assert grant_module.STALE_GRANT_MUTATION_FAILS_CLOSED is True
        store.close()

    def test_lazy_end_condition_plan_retired_and_until_revoked(self, tmp_path: Path):
        ws, _native, _target, _sibling, worktree = _workspace(tmp_path)
        store = _accepting_store(tmp_path)
        binding = _target_binding(ws)
        plan_bound = _grant(
            store, binding, end_condition=END_CONDITION_PLAN_RETIRED, bound_plan_id=PLAN_ID
        )
        assert evaluate_cross_project_grant(plan_bound, store=store) == (True, GRANT_STATE_ACTIVE)
        store.compare_and_swap_plan(NATIVE, PLAN_ID, 1, lifecycle_state=PLAN_LIFECYCLE_RETIRED)
        assert evaluate_cross_project_grant(plan_bound, store=store) == (False, END_CONDITION_PLAN_RETIRED)
        with pytest.raises(CrossProjectGrantInactiveError):
            require_live_cross_project_grant(plan_bound, store=store)
        sandbox = _sandbox(ws, worktree)
        with pytest.raises(CrossProjectGrantInactiveError):
            materialize_live_cross_project_read_roots(
                sandbox=sandbox,
                roots=ar.authorized_roots_for_task_main(sandbox),
                store=store,
                target_bindings={TARGET: binding},
            )
        store.close()

    def test_grant_write_failure_rolls_back(self, tmp_path: Path):
        from aota_forge.governance.project_store import ProjectGovernancePersistenceError

        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        store = _accepting_store(tmp_path)
        store.inject_fail_next_write()
        with pytest.raises(ProjectGovernancePersistenceError):
            _grant(store, _target_binding(ws))
        assert store.list_cross_project_grants(NATIVE) == ()
        store.close()

    def test_same_process_reopen_recovers_grant_and_plan(self, tmp_path: Path):
        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        db = tmp_path / "governance.sqlite3"
        store = SQLiteProjectGovernanceStore(db)
        store.put_plan(_plan_record())
        created = _grant(store, _target_binding(ws), bounded_scope="docs")
        store.close()

        reopened = SQLiteProjectGovernanceStore(db)
        recovered = reopened.get_cross_project_grant(created.grant_id)
        assert recovered == created
        assert reopened.get_plan(NATIVE, PLAN_ID).revision == 1
        with pytest.raises(StaleCrossProjectGrantRevisionError):
            reopened.revoke_cross_project_grant(created.grant_id, 99)
        reopened.close()

    def test_real_process_reopen_and_cas_remains_valid(self, tmp_path: Path):
        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        db = tmp_path / "governance.sqlite3"
        store = SQLiteProjectGovernanceStore(db)
        store.put_plan(_plan_record())
        created = _grant(store, _target_binding(ws))
        store.close()

        completed = subprocess.run(
            [sys.executable, "-c", _REOPEN_SCRIPT, str(db), created.grant_id],
            cwd=str(REPO_ROOT),
            capture_output=True,
            text=True,
            timeout=120,
        )
        assert completed.returncode == 0, completed.stderr
        assert "W4_REOPEN_OK" in completed.stdout

        reopened = SQLiteProjectGovernanceStore(db)
        grant = reopened.get_cross_project_grant(created.grant_id)
        assert grant.state == GRANT_STATE_REVOKED
        assert grant.revision == 2
        assert reopened.get_plan(NATIVE, PLAN_ID) is not None
        with pytest.raises(StaleCrossProjectGrantRevisionError):
            reopened.revoke_cross_project_grant(created.grant_id, 1)
        reopened.close()

    def test_v1_plan_data_survives_additive_grant_extension(self, tmp_path: Path):
        db = tmp_path / "governance.sqlite3"
        store = SQLiteProjectGovernanceStore(db)
        store.put_plan(_plan_record())
        store.close()
        connection = _raw_connection(db)
        try:
            tables = {
                row[0]
                for row in connection.execute(
                    "SELECT name FROM sqlite_master WHERE type = 'table'"
                )
            }
        finally:
            connection.close()
        assert tables == {"project_plan_governance"}
        assert grant_module.SECOND_GRANT_DATABASE_CREATED is False

        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        reopened = SQLiteProjectGovernanceStore(db)
        created = _grant(reopened, _target_binding(ws))
        reopened.close()

        final = SQLiteProjectGovernanceStore(db)
        assert final.get_plan(NATIVE, PLAN_ID).revision == 1
        assert final.get_cross_project_grant(created.grant_id) == created
        final.close()

    @pytest.mark.parametrize(
        ("column", "value"),
        (
            ("capabilities", "not-json"),
            ("authority_basis", "model_says_so"),
            ("authority_ref", ""),
            ("state", "paused"),
            ("requesting_project", "AOTA"),
            ("end_condition", "whenever"),
            ("bounded_scope", "../escape"),
        ),
    )
    def test_corrupt_grant_row_fails_closed_on_reopen(self, tmp_path: Path, column, value):
        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        db = tmp_path / "governance.sqlite3"
        store = SQLiteProjectGovernanceStore(db)
        store.put_plan(_plan_record())
        created = _grant(store, _target_binding(ws))
        store.close()
        connection = _raw_connection(db)
        try:
            connection.execute(
                f"UPDATE cross_project_grants SET {column} = ?", (value,)
            )
            connection.commit()
        finally:
            connection.close()
        with pytest.raises(ProjectGovernanceCorruptStateError) as excinfo:
            SQLiteProjectGovernanceStore(db)
        assert "corrupt" in str(excinfo.value).lower()
        assert created.grant_id

    def test_closed_store_grant_operations_fail_closed(self, tmp_path: Path):
        from aota_forge.governance.project_store import ProjectGovernanceStoreClosedError

        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        store = _accepting_store(tmp_path)
        created = _grant(store, _target_binding(ws))
        store.close()
        with pytest.raises(ProjectGovernanceStoreClosedError):
            store.get_cross_project_grant(created.grant_id)
        with pytest.raises(ProjectGovernanceStoreClosedError):
            store.list_cross_project_grants(NATIVE)
        with pytest.raises(ProjectGovernanceStoreClosedError):
            store.revoke_cross_project_grant(created.grant_id, 1)

    def test_port_backed_store_satisfies_grant_protocol(self, tmp_path: Path):
        store = open_project_governance_store(tmp_path / "governance.sqlite3")
        assert isinstance(store, CrossProjectGrantStore)
        assert isinstance(store, SQLiteProjectGovernanceStore)
        store.close()


# ---------------------------------------------------------------------------
# V2 — AuthorizedRootSet grant-specific foreign reads
# ---------------------------------------------------------------------------


class TestAuthorizedRootGrantReads:
    def test_default_root_sets_unchanged_without_grants(self, tmp_path: Path):
        ws, _native, _target, _sibling, worktree = _workspace(tmp_path)
        sandbox = _sandbox(ws, worktree)
        roots = ar.authorized_roots_for_task_main(sandbox)
        assert roots.names() == ("project-main", "active-worktree")
        assert roots.cross_project_bindings == ()
        assert ar.authorized_roots_for_worker(sandbox).names() == ("active-worktree",)
        store = _accepting_store(tmp_path)
        binding = _target_binding(ws)
        unchanged = materialize_live_cross_project_read_roots(
            sandbox=sandbox, roots=roots, store=store, target_bindings={TARGET: binding}
        )
        assert unchanged is roots
        assert unchanged.names() == ("project-main", "active-worktree")
        assert DEFAULT_ROOT_SET_UNCHANGED_WITHOUT_GRANTS is True
        store.close()

    def test_without_grant_foreign_root_and_sibling_are_denied(self, tmp_path: Path):
        ws, _native, _target, _sibling, worktree = _workspace(tmp_path)
        sandbox = _sandbox(ws, worktree)
        roots = ar.authorized_roots_for_task_main(sandbox)
        provider = _task_main_provider(sandbox, roots, WORKSPACE_READ_DESCRIPTOR)
        denied = _read(provider, "top_secret.txt", root_ref="grant-00000000000000000000")
        assert denied.ok is False
        assert denied.error["code"] == "AUTHORIZED_ROOT_UNKNOWN"
        traversal = _read(provider, "../unrelated_sibling/sibling.txt")
        assert traversal.ok is False
        assert traversal.error["code"] == "INVALID_PATH"
        path_as_root = _read(provider, "top_secret.txt", root_ref=str(_target))
        assert path_as_root.ok is False
        assert path_as_root.error["code"] == "INVALID_ROOT_REF"

    def _materialized(self, tmp_path, *, bounded_scope: str = ""):
        ws, native, target, sibling, worktree = _workspace(tmp_path)
        sandbox = _sandbox(ws, worktree)
        store = _accepting_store(tmp_path)
        binding = _target_binding(ws)
        grant = _grant(store, binding, bounded_scope=bounded_scope)
        roots = materialize_live_cross_project_read_roots(
            sandbox=sandbox,
            roots=ar.authorized_roots_for_task_main(sandbox),
            store=store,
            target_bindings={TARGET: binding},
        )
        return ws, native, target, sibling, sandbox, store, grant, roots

    def test_with_exact_grant_foreign_read_and_search_succeed(self, tmp_path: Path):
        _ws, _native, _target, _sibling, sandbox, store, grant, roots = self._materialized(tmp_path)
        assert roots.names() == ("project-main", "active-worktree", grant.grant_id)
        granted = roots.get(grant.grant_id)
        assert granted.root_kind == "project-main"
        assert granted.project_id == TARGET
        assert granted.source == ar.SOURCE_EXPLICIT_GRANT
        assert granted.capabilities == frozenset({"read", "search"})
        assert granted.has_capability("write") is False

        reader = _task_main_provider(sandbox, roots, WORKSPACE_READ_DESCRIPTOR)
        read = _read(reader, "top_secret.txt", root_ref=grant.grant_id)
        assert read.ok is True
        assert read.payload["root_ref"] == grant.grant_id
        assert "target top secret needle" in read.payload["content"]

        searcher = _task_main_provider(sandbox, roots, WORKSPACE_SEARCH_DESCRIPTOR)
        hit = _search(searcher, "target top secret needle", root_ref=grant.grant_id)
        assert hit.ok is True
        assert hit.payload["root_refs"] == [grant.grant_id]
        assert {item["root_ref"] for item in hit.payload["results"]} == {grant.grant_id}

        # default search covers the granted (search-capable) root set and every
        # result carries its provenance root_ref; explicit root_ref selects one.
        default_hit = _search(searcher, "target top secret needle")
        assert {item["root_ref"] for item in default_hit.payload["results"]} == {grant.grant_id}
        store.close()

    def test_bounded_scope_is_mechanically_enforced(self, tmp_path: Path):
        _ws, _native, _target, _sibling, sandbox, store, grant, roots = self._materialized(
            tmp_path, bounded_scope="docs"
        )
        granted = roots.get(grant.grant_id)
        assert granted.root_path.endswith("/docs")
        reader = _task_main_provider(sandbox, roots, WORKSPACE_READ_DESCRIPTOR)
        inside = _read(reader, "public.md", root_ref=grant.grant_id)
        assert inside.ok is True
        assert "public docs needle" in inside.payload["content"]
        outside = _read(reader, "top_secret.txt", root_ref=grant.grant_id)
        assert outside.ok is False
        assert outside.error["code"] in ("NOT_FOUND", "INVALID_PATH")
        traversal = _read(reader, "../top_secret.txt", root_ref=grant.grant_id)
        assert traversal.ok is False
        assert traversal.error["code"] == "INVALID_PATH"
        store.close()

    def test_symlink_escape_is_denied(self, tmp_path: Path):
        ws, _native, target, _sibling, worktree = _workspace(tmp_path)
        outside = tmp_path / "outside-secret"
        outside.mkdir()
        (outside / "secret.txt").write_text("outside needle", encoding="utf-8")
        (target / "escape").symlink_to(outside)
        sandbox = _sandbox(ws, worktree)
        store = _accepting_store(tmp_path)
        binding = _target_binding(ws)
        grant = _grant(store, binding)
        roots = materialize_live_cross_project_read_roots(
            sandbox=sandbox,
            roots=ar.authorized_roots_for_task_main(sandbox),
            store=store,
            target_bindings={TARGET: binding},
        )
        reader = _task_main_provider(sandbox, roots, WORKSPACE_READ_DESCRIPTOR)
        denied = _read(reader, "escape/secret.txt", root_ref=grant.grant_id)
        assert denied.ok is False
        assert denied.error["code"] in ("INVALID_PATH", "SYMLINK_ESCAPE", "NOT_FOUND")
        store.close()

    def test_grant_write_capability_is_denied(self, tmp_path: Path):
        _ws, _native, _target, _sibling, sandbox, store, grant, roots = self._materialized(tmp_path)
        with pytest.raises(ar.AuthorizedRootCapabilityError):
            ar.resolve_authorized_root(roots, grant.grant_id, capability="write")
        assert roots.write_roots() == (roots.get("active-worktree"),)
        assert CROSS_PROJECT_WRITE_ALLOWED is False
        store.close()

    def test_revoked_grant_no_longer_materializes(self, tmp_path: Path):
        ws, _native, _target, _sibling, worktree = _workspace(tmp_path)
        sandbox = _sandbox(ws, worktree)
        store = _accepting_store(tmp_path)
        binding = _target_binding(ws)
        grant = _grant(store, binding)
        store.revoke_cross_project_grant(grant.grant_id, 1)
        roots = materialize_live_cross_project_read_roots(
            sandbox=sandbox,
            roots=ar.authorized_roots_for_task_main(sandbox),
            store=store,
            target_bindings={TARGET: binding},
        )
        assert roots.names() == ("project-main", "active-worktree")
        store.close()

    def test_forged_bindings_fail_closed(self, tmp_path: Path):
        ws, _native, target, sibling, worktree = _workspace(tmp_path)
        sandbox = _sandbox(ws, worktree)
        store = _accepting_store(tmp_path)
        binding = _target_binding(ws)
        grant = _grant(store, binding)
        materialized = materialize_cross_project_grant_binding(
            grant=grant, target_binding=binding
        )
        assert materialized.root_path == str(target)
        unrelated_binding = _target_binding(ws, SIBLING)
        with pytest.raises(ar.CrossProjectGrantRootError):
            materialize_cross_project_grant_binding(
                grant=grant, target_binding=unrelated_binding
            )
        # a foreign root whose path/identity disagrees with its binding is
        # rejected by the grant-specific validation seam.
        disagreeing_root = ar.AuthorizedRoot(
            root_ref=grant.grant_id,
            root_kind="project-main",
            root_path=str(sibling),
            capabilities=frozenset({"read", "search"}),
            project_id=TARGET,
            source=ar.SOURCE_EXPLICIT_GRANT,
            grant_id=grant.grant_id,
        )
        with pytest.raises(ar.CrossProjectGrantRootError):
            ar.validate_cross_project_grant_root(
                disagreeing_root, native_project_id=NATIVE, binding=materialized
            )
        wrong_project_root = ar.AuthorizedRoot(
            root_ref=grant.grant_id,
            root_kind="project-main",
            root_path=materialized.root_path,
            capabilities=frozenset({"read", "search"}),
            project_id=SIBLING,
            source=ar.SOURCE_EXPLICIT_GRANT,
            grant_id=grant.grant_id,
        )
        with pytest.raises(ar.CrossProjectGrantRootError):
            ar.validate_cross_project_grant_root(
                wrong_project_root, native_project_id=NATIVE, binding=materialized
            )
        wrong_requesting = dataclasses.replace(materialized, requesting_project=SIBLING)
        with pytest.raises(ar.CrossProjectGrantRootError):
            ar.validate_cross_project_grant_root(
                disagreeing_root, native_project_id=NATIVE, binding=wrong_requesting
            )
        # a binding without its matching grant root and a duplicate root are
        # rejected by the root set itself.
        with pytest.raises(ar.AuthorizedRootSetError):
            ar.AuthorizedRootSet(
                session_kind=ar.ROOT_SET_SESSION_TASK_MAIN,
                roots=ar.authorized_roots_for_task_main(sandbox).roots,
                cross_project_bindings=(materialized,),
            )
        # workers and non-task-main sessions never receive a foreign root.
        worker_roots = ar.authorized_roots_for_worker(sandbox)
        with pytest.raises(ar.CrossProjectGrantRootError):
            ar.authorize_cross_project_read_root(sandbox, worker_roots, materialized)
        single_roots = ar.authorized_roots_single_root(sandbox)
        with pytest.raises(ar.CrossProjectGrantRootError):
            ar.authorize_cross_project_read_root(sandbox, single_roots, materialized)
        store.close()

    def test_grant_root_never_adds_evidence_or_write(self, tmp_path: Path):
        _ws, _native, _target, _sibling, _sandbox_, store, grant, roots = self._materialized(
            tmp_path
        )
        assert roots.get(AUTHORIZED_EVIDENCE_ROOT_REF) is None
        assert roots.evidence_binding is None
        assert roots.write_roots() == (roots.get("active-worktree"),)
        assert projection_module.PROJECT_READ_GRANT_IMPLIES_EVIDENCE_GRANT is False
        assert grant_module.PROJECT_READ_GRANT_IMPLIES_EVIDENCE_GRANT is False
        store.close()


# ---------------------------------------------------------------------------
# V2 — authorized-evidence
# ---------------------------------------------------------------------------


class TestAuthorizedEvidence:
    def _evidence_base(self, tmp_path: Path):
        base = tmp_path / "evidence-base"
        own = base / NATIVE
        own.mkdir(parents=True)
        (own / "issue-57").mkdir()
        (own / "issue-57" / "w4-note.txt").write_text(
            "governed evidence needle", encoding="utf-8"
        )
        other = base / TARGET
        other.mkdir()
        (other / "foreign.txt").write_text("foreign evidence needle", encoding="utf-8")
        (base / "root-level.txt").write_text("root level needle", encoding="utf-8")
        return base

    def _roots_with_evidence(self, ws, worktree, base):
        sandbox = _sandbox(ws, worktree)
        binding = bind_authorized_evidence_root(evidence_base=base, project_id=NATIVE)
        roots = ar.authorized_roots_for_task_main(sandbox, evidence_binding=binding)
        return sandbox, binding, roots

    def test_binding_is_project_scoped_and_never_the_whole_base(self, tmp_path: Path):
        ws, _native, _target, _sibling, worktree = _workspace(tmp_path)
        base = self._evidence_base(tmp_path)
        binding = bind_authorized_evidence_root(evidence_base=base, project_id=NATIVE)
        assert binding.root_path == str(base / NATIVE)
        assert binding.root_path != str(base)
        with pytest.raises(ar.AuthorizedEvidenceRootError):
            bind_authorized_evidence_root(evidence_base=base, project_id=SIBLING)
        with pytest.raises(ar.AuthorizedEvidenceRootError):
            bind_authorized_evidence_root(evidence_base="relative/path", project_id=NATIVE)
        with pytest.raises(ar.AuthorizedEvidenceRootError):
            bind_authorized_evidence_root(evidence_base=tmp_path / "missing", project_id=NATIVE)
        assert ar.AUTHORIZED_EVIDENCE_MODEL_NOMINATED_PATH is False

    def test_task_main_reads_own_evidence_and_sibling_is_denied(self, tmp_path: Path):
        ws, _native, _target, _sibling, worktree = _workspace(tmp_path)
        base = self._evidence_base(tmp_path)
        sandbox, binding, roots = self._roots_with_evidence(ws, worktree, base)
        assert roots.names() == ("project-main", "active-worktree", AUTHORIZED_EVIDENCE_ROOT_REF)
        evidence_root = roots.get(AUTHORIZED_EVIDENCE_ROOT_REF)
        assert evidence_root.source == ar.SOURCE_TRUSTED_EVIDENCE
        assert evidence_root.capabilities == frozenset({"read", "search"})
        assert evidence_root.root_path == binding.root_path
        assert roots.evidence_binding is binding

        reader = _task_main_provider(sandbox, roots, WORKSPACE_READ_DESCRIPTOR)
        own = _read(reader, "issue-57/w4-note.txt", root_ref=AUTHORIZED_EVIDENCE_ROOT_REF)
        assert own.ok is True
        assert "governed evidence needle" in own.payload["content"]
        foreign = _read(reader, "../aota_reader_mcp/foreign.txt", root_ref=AUTHORIZED_EVIDENCE_ROOT_REF)
        assert foreign.ok is False
        assert foreign.error["code"] == "INVALID_PATH"
        root_level = _read(reader, "../root-level.txt", root_ref=AUTHORIZED_EVIDENCE_ROOT_REF)
        assert root_level.ok is False
        assert root_level.error["code"] == "INVALID_PATH"
        absolute = _read(reader, str(base / TARGET / "foreign.txt"), root_ref=AUTHORIZED_EVIDENCE_ROOT_REF)
        assert absolute.ok is False
        assert absolute.error["code"] == "INVALID_PATH"

        searcher = _task_main_provider(sandbox, roots, WORKSPACE_SEARCH_DESCRIPTOR)
        hit = _search(searcher, "governed evidence needle", root_ref=AUTHORIZED_EVIDENCE_ROOT_REF)
        assert hit.ok is True
        assert {item["root_ref"] for item in hit.payload["results"]} == {AUTHORIZED_EVIDENCE_ROOT_REF}
        miss = _search(searcher, "foreign evidence needle")
        assert miss.ok is True
        assert all("foreign evidence" not in item["snippet"] for item in miss.payload["results"])

    def test_worker_default_has_no_evidence_root_and_write_is_denied(self, tmp_path: Path):
        ws, _native, _target, _sibling, worktree = _workspace(tmp_path)
        base = self._evidence_base(tmp_path)
        sandbox = _sandbox(ws, worktree)
        worker_roots = ar.authorized_roots_for_worker(sandbox)
        assert worker_roots.get(AUTHORIZED_EVIDENCE_ROOT_REF) is None
        binding = bind_authorized_evidence_root(evidence_base=base, project_id=NATIVE)
        with pytest.raises(ar.AuthorizedRootSetError):
            active = worker_roots.get("active-worktree")
            evidence_root = ar.AuthorizedRoot(
                root_ref=AUTHORIZED_EVIDENCE_ROOT_REF,
                root_kind=AUTHORIZED_EVIDENCE_ROOT_REF,
                root_path=binding.root_path,
                capabilities=frozenset({"read"}),
                project_id=NATIVE,
                source=ar.SOURCE_TRUSTED_EVIDENCE,
            )
            ar.AuthorizedRootSet(
                session_kind=ar.ROOT_SET_SESSION_WORKER,
                roots=(active, evidence_root),
                evidence_binding=binding,
            )
        task_main = ar.authorized_roots_for_task_main(sandbox, evidence_binding=binding)
        with pytest.raises(ar.AuthorizedRootCapabilityError):
            ar.resolve_authorized_root(
                task_main, AUTHORIZED_EVIDENCE_ROOT_REF, capability="write"
            )
        assert projection_module.AUTHORIZED_EVIDENCE_WRITE_ACCESS is False
        assert projection_module.WORKER_DEFAULT_AUTHORIZED_EVIDENCE_GRANT is False

    def test_evidence_root_requires_trusted_binding(self, tmp_path: Path):
        ws, _native, _target, _sibling, worktree = _workspace(tmp_path)
        sandbox = _sandbox(ws, worktree)
        base = self._evidence_base(tmp_path)
        forged_path = tmp_path / "forged-evidence"
        forged_path.mkdir()
        forged = ar.AuthorizedRoot(
            root_ref=AUTHORIZED_EVIDENCE_ROOT_REF,
            root_kind=AUTHORIZED_EVIDENCE_ROOT_REF,
            root_path=str(forged_path),
            capabilities=frozenset({"read"}),
            project_id=NATIVE,
            source=ar.SOURCE_TRUSTED_EVIDENCE,
        )
        binding = bind_authorized_evidence_root(evidence_base=base, project_id=NATIVE)
        with pytest.raises(ar.AuthorizedEvidenceRootError):
            ar.validate_authorized_evidence_root(
                forged, project_id=NATIVE, binding=binding
            )
        active = ar.authorized_roots_for_task_main(sandbox).get("active-worktree")
        with pytest.raises(ar.AuthorizedRootSetError):
            ar.AuthorizedRootSet(
                session_kind=ar.ROOT_SET_SESSION_TASK_MAIN,
                roots=(active, forged),
            )

    def test_projection_items_route_to_existing_owners_without_new_storage(self, tmp_path: Path):
        base = self._evidence_base(tmp_path)
        before = {path for path in base.rglob("*")}
        workspace_item = project_workspace_evidence_item(
            project_id=NATIVE, relative_ref="issue-57/w4-note.txt", digest="a" * 64
        )
        assert workspace_item.owner_kind == OWNER_KIND_EVIDENCE_TREE
        assert workspace_item.access_kind == ACCESS_KIND_WORKSPACE_READ
        assert workspace_item.access_mechanism.startswith("workspace.read")
        assert workspace_item.is_authority is False

        result_item = project_result_payload_item(
            result_ref="tool_output:abc", project_id=NATIVE, digest="b" * 64
        )
        assert result_item.owner_kind == OWNER_KIND_DURABLE_PAYLOAD
        assert result_item.access_kind == ACCESS_KIND_RESULT_HYDRATE
        assert result_item.access_mechanism.startswith("result.hydrate")
        assert result_item.is_authority is False

        receipt_item = project_governed_evidence_item(
            GovernedReference(kind=GovernedReferenceKind.EVIDENCE, ref="receipt-1", digest="c" * 64),
            project_id=NATIVE,
            owner_kind=OWNER_KIND_RESULT_STORE,
            access_kind=ACCESS_KIND_RECEIPT_READER,
        )
        assert receipt_item.access_kind == ACCESS_KIND_RECEIPT_READER

        search_item = project_workspace_evidence_item(
            project_id=NATIVE,
            relative_ref="issue-57/w4-note.txt",
            access_kind=ACCESS_KIND_WORKSPACE_SEARCH,
        )
        projection = build_authorized_evidence_projection(
            project_id=NATIVE,
            items=(workspace_item, result_item, receipt_item, search_item),
            complete=True,
        )
        assert projection.is_authority is False
        assert projection.by_owner_kind()[OWNER_KIND_EVIDENCE_TREE]
        assert json.loads(projection.canonical_json())["second_evidence_store_created"] is False
        after = {path for path in base.rglob("*")}
        assert before == after
        assert projection_module.SECOND_EVIDENCE_STORE_CREATED is False
        assert projection_module.EVIDENCE_BYTES_DUPLICATED is False
        assert projection_module.EVIDENCE_PROJECTION_IS_AUTHORITY is False
        assert projection_module.EVIDENCE_REF_IS_AUTHORITY is False
        assert projection_module.RESULT_REFS_USE_ORIGINAL_HYDRATION_AUTHORITY is True
        assert COMPOSITION_SECOND_EVIDENCE_STORE is False

    def test_projection_rejects_traversal_and_unknown_shapes(self):
        with pytest.raises(AuthorizedEvidenceProjectionError):
            project_workspace_evidence_item(project_id=NATIVE, relative_ref="../etc/passwd")
        with pytest.raises(AuthorizedEvidenceProjectionError):
            project_workspace_evidence_item(project_id=NATIVE, relative_ref="/abs/path")
        with pytest.raises(AuthorizedEvidenceProjectionError):
            project_workspace_evidence_item(project_id="AOTA", relative_ref="x")
        with pytest.raises(AuthorizedEvidenceProjectionError):
            project_workspace_evidence_item(
                project_id=NATIVE, relative_ref="x", access_kind="workspace_write"
            )
        with pytest.raises(AuthorizedEvidenceProjectionError):
            project_governed_evidence_item(
                GovernedReference(kind=GovernedReferenceKind.ARTIFACT, ref="artifact-1"),
                project_id=NATIVE,
                owner_kind=OWNER_KIND_RESULT_STORE,
                access_kind=ACCESS_KIND_RESULT_HYDRATE,
            )
        with pytest.raises(AuthorizedEvidenceProjectionError):
            build_authorized_evidence_projection(project_id=NATIVE, items=("x",))

    def test_evidence_owner_catalog_is_complete(self):
        kinds = {owner.owner_kind for owner in EVIDENCE_OWNERS}
        assert kinds == {
            OWNER_KIND_EVIDENCE_TREE,
            OWNER_KIND_DURABLE_PAYLOAD,
            OWNER_KIND_RESULT_STORE,
            projection_module.OWNER_KIND_MATERIALIZATION_RECEIPT,
            projection_module.OWNER_KIND_VALIDATION_RECEIPT,
        }
        for owner in EVIDENCE_OWNERS:
            assert owner.owner and owner.access_path and owner.projection_ref
            assert owner.write_owner
        assert ACCESS_KINDS == {
            ACCESS_KIND_WORKSPACE_READ,
            ACCESS_KIND_WORKSPACE_SEARCH,
            ACCESS_KIND_RESULT_HYDRATE,
            ACCESS_KIND_RECEIPT_READER,
        }
        assert not projection_module.WRITE_ACCESS_KINDS & ACCESS_KINDS


# ---------------------------------------------------------------------------
# Architecture / ownership guards
# ---------------------------------------------------------------------------


class TestArchitectureGuards:
    def test_w4_flags_are_frozen(self):
        assert grant_module.CROSS_PROJECT_GRANT_IMPLEMENTED is True
        assert grant_module.GRANT_IS_AUTHORITY_RECORD is True
        assert grant_module.MODEL_MINTS_GRANT is False
        assert grant_module.MODEL_CAN_SELF_GRANT_CROSS_PROJECT_ACCESS is False
        assert grant_module.MODEL_PHYSICAL_PATH_AUTHORITY is False
        assert grant_module.CROSS_PROJECT_DEFAULT_DENIED is True
        assert grant_module.CROSS_PROJECT_GRANT_DEFAULT == "read_only"
        assert grant_module.SECOND_GRANT_DATABASE_CREATED is False
        assert GRANT_STORE_OWNER == "project_governance_store"
        assert grant_module.GRANT_STORE_OWNER == GRANT_STORE_OWNER
        assert ar.AUTHORIZED_EVIDENCE_ROOT_SUPPORTED is True
        assert ar.AUTHORIZED_EVIDENCE_ROOT_PROJECT_SCOPED is True
        assert ar.AUTHORIZED_EVIDENCE_ROOT_REQUIRES_TRUSTED_BINDING is True
        assert ar.AUTHORIZED_EVIDENCE_AGENT_WRITE_CAPABILITY is False
        assert ar.CROSS_PROJECT_GRANT_ROOT_SUPPORTED is True
        assert ar.CROSS_PROJECT_GRANT_ROOT_REQUIRES_EXPLICIT_GRANT is True
        assert ar.CROSS_PROJECT_GRANT_DEFAULT_DENIED is True
        assert ar.CROSS_PROJECT_GRANT_WRITE_CAPABILITY is False
        assert ar.DEFAULT_SIBLING_ISOLATION_WEAKENED is False
        assert ar.MODEL_CAN_MINT_GRANT_ROOT is False

    def test_no_public_operation_surface_change(self):
        from aota_forge import mcp_transport

        assert len(mcp_transport.LOGICAL_OPERATIONS) == 29
        for forbidden in ("grant.create", "grant.open", "evidence.open", "cross_project.search"):
            assert forbidden not in mcp_transport.LOGICAL_OPERATIONS
        assert grant_module.GRANT_IS_PUBLIC_AGENT_OPERATION is False

    def test_no_migration_framework_orm_or_grant_engine(self):
        banned_import_prefixes = (
            "alembic",
            "sqlalchemy",
            "peewee",
            "django",
        )
        banned_symbols = ("MigrationFramework", "GrantEngine", "PolicyEngine", "CapabilityLanguage")
        for path in W4_NEW_MODULES:
            imports = _direct_imports(path)
            for name in imports:
                assert not name.startswith(banned_import_prefixes), (path, name)
            source = path.read_text(encoding="utf-8")
            for symbol in banned_symbols:
                assert symbol not in source, (path, symbol)

    def test_grants_are_not_fake_plan_rows_and_plan_core_unchanged(self, tmp_path: Path):
        ws, _native, _target, _sibling, _worktree = _workspace(tmp_path)
        db = tmp_path / "governance.sqlite3"
        store = SQLiteProjectGovernanceStore(db)
        store.put_plan(_plan_record())
        grant = _grant(store, _target_binding(ws))
        store.close()
        connection = _raw_connection(db)
        try:
            plan_rows = connection.execute(
                "SELECT COUNT(*) FROM project_plan_governance"
            ).fetchone()[0]
            grant_rows = connection.execute(
                "SELECT COUNT(*) FROM cross_project_grants"
            ).fetchone()[0]
            plan_columns = {
                row[1] for row in connection.execute("PRAGMA table_info(project_plan_governance)")
            }
            plan_identifiers = {
                row[0]
                for row in connection.execute("SELECT plan_id FROM project_plan_governance")
            }
        finally:
            connection.close()
        assert plan_rows == 1
        assert grant_rows == 1
        assert plan_columns == {
            "project_id",
            "plan_id",
            "lifecycle_state",
            "source_kind",
            "authority_ref",
            "source_revision",
            "source_digest",
            "revision",
        }
        assert grant.grant_id not in plan_identifiers
        assert plan_identifiers == {PLAN_ID}

    def test_store_scope_and_version_contract_unchanged(self, tmp_path: Path):
        from aota_forge.governance import project_store

        assert project_store.PROJECT_GOVERNANCE_SCHEMA_VERSION == 1
        assert project_store.PROJECT_GOVERNANCE_STORE_SCOPE == "project_and_cross_plan_governance_only"
        assert project_store.PROJECT_GOVERNANCE_STORE_OWNS_EXECUTION_ATTEMPTS is False
        assert project_store.PROJECT_GOVERNANCE_STORE_OWNS_WORKER_RESULTS is False
        assert project_store.PROJECT_GOVERNANCE_STORE_OWNS_WORK_PROGRESSION is False
        assert project_store.GOVERNANCE_SUBSYSTEM_OWNS_EXECUTION_STATE is False
        assert project_store.GOVERNANCE_SUBSYSTEM_OWNS_TASK_MAIN_COORDINATOR_STATE is False

    def test_grant_module_does_not_import_work_plane_or_adapters(self):
        for path in (GOVERNANCE_DIR / "cross_project_grant.py", GOVERNANCE_DIR / "evidence_projection.py"):
            imports = _direct_imports(path)
            for name in imports:
                assert not name.startswith("aota_forge.work_plane"), (path, name)
                assert not name.startswith("aota_forge.composition"), (path, name)
                assert not name.startswith("aota_forge.adapters"), (path, name)

    def test_evidence_projection_does_not_touch_storage_or_filesystem(self):
        source = (GOVERNANCE_DIR / "evidence_projection.py").read_text(encoding="utf-8")
        for token in ("sqlite3", "connect(", "open(", "write_bytes", "mkdir", "shutil"):
            assert token not in source, token


class TestW3HotFilesUntouched:
    def test_w3_hot_files_have_no_w4_imports(self):
        w2_w3_hot_files = (
            REPO_ROOT / "aota_forge" / "work_plane" / "steward_dispatch.py",
            REPO_ROOT / "aota_forge" / "work_plane" / "steward_finalizer.py",
            REPO_ROOT / "aota_forge" / "work_plane" / "milestone_closure.py",
            REPO_ROOT / "aota_forge" / "work_plane" / "context_route.py",
            REPO_ROOT / "aota_forge" / "work_plane" / "working_set.py",
        )
        for path in w2_w3_hot_files:
            if not path.exists():
                continue
            imports = _direct_imports(path)
            for name in imports:
                assert "cross_project_grant" not in name, (path, name)
                assert "evidence_projection" not in name, (path, name)
