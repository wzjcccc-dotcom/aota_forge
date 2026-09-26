"""Bounded review-only adoption of an already authorized Git frontier.

This is a transitional runtime seam for a Milestone whose historical Work Item
execution is outside the current coordinator.  It deliberately records only
the existing coordinator's review/frontier working fields.  It never invents
Work Item receipts, Work Item status, or execution history.

The caller is trusted runtime composition, not an agent-facing operation.  The
authorization facts and the current Git observation must agree exactly before
the existing integrated-review dispatch path may run.
"""

from __future__ import annotations

import base64
import hashlib
import json
import re
import zlib
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from aota_forge.adapters.plan_authority import PlanAuthorityReadAdapter
from aota_forge.adapters.plan_authority.binding import (
    PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
    PlanAuthorityBinding,
)
from aota_forge.adapters.plan_authority.local_governance import (
    local_plan_authority_reference,
)
from aota_forge.core.contracts.canonical import canonical_json, canonicalize
from aota_forge.core.execution.durable_state import ExecutionStateStore
from aota_forge.core.git.inspect import inspect_git
from aota_forge.runtime.task_main.coordinator import (
    MilestonePlanView,
    PlanDriftError,
    TaskMainCoordinatorError,
    _check_live_binding,
)
from aota_forge.runtime.task_main.coordinator_state import (
    CoordinatorStatus,
    TaskMainCoordinatorState,
    WorkItemCoordinatorStatus,
)
from aota_forge.runtime.task_main.coordinator_store import (
    StaleCoordinatorRevisionError,
    TaskMainCoordinatorStore,
)
from aota_forge.work_plane.progression import (
    ProgressionDisposition,
)
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.milestone_review import ReviewCycle, parse_review_cycle
from aota_forge.work_plane.roles import AgentWorkRole
from aota_forge.work_plane.steward_finalizer import GitPort, SubprocessGitPort
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary


REVIEW_ONLY_ADOPTION_ENABLED = True
REVIEW_ONLY_ADOPTION_STATUS = "REVIEW_ONLY_ADOPTION_READY"
REVIEW_ONLY_ADOPTION_STATE = "accepted_frontier_review_only"
REVIEW_ONLY_FRONTIER_STATUS = "REVIEW_ONLY_CANDIDATE"
REVIEW_ONLY_GATE_STATUS = "REVIEW_ONLY_ADOPTION_READY"

NO_FAKE_WORK_COMPLETION_RECEIPTS = True
NO_FAKE_WORK_COORDINATOR_STATE = True
NO_WORK_REDISPATCH_DURING_ADOPTION = True
NO_NEW_REVIEW_ADOPTION_PROTOCOL = True
NO_PUBLIC_REVIEW_ADOPTION_OPERATION = True

_SHA1_RE = re.compile(r"^[0-9a-f]{40}$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REVIEWER_TASK_ID_RE = re.compile(
    r":(?P<review_cycle>RV1|RV2):attempt-(?P<attempt>[0-9]+)$"
)
_MAX_REF_LENGTH = 512
DEFAULT_REVIEWER_ATTEMPT = 1
_SUPPORTED_REVIEWER_ATTEMPTS = frozenset({1, 2})
_AUTHORIZATION_PROVENANCE_LEGACY_VERSION = 1
_AUTHORIZATION_PROVENANCE_VERSION = 2
_TRUSTED_GIT_MAX_OBSERVED_PATHS = 4096
_RUNTIME_DIGEST_FILE_RE = re.compile(r"^[0-9a-f]{64}\.(?:bin|json)$")
_HANDOFF_FILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}\.json$")
_WORKER_BINDING_FILE_RE = re.compile(
    r"^(?:worker-[A-Za-z0-9._-]{1,64}-[0-9a-f]{8}|task-main-[0-9a-f]{8}-[0-9a-f]{8})\.json$"
)
_OBSERVATION_EVIDENCE_FILE_RE = re.compile(
    r"^observation-evidence(?:-[A-Za-z0-9._-]{1,64})?\.jsonl$"
)
_RUNTIME_ARTIFACT_EXACT_PATHS = frozenset(
    {
        ".aota/governance.sqlite3",
        ".aota/governance.sqlite3-wal",
        ".aota/governance.sqlite3-shm",
        ".aota/governance-journal.json",
        ".aota/coordinator.json",
        ".aota/execution.json",
        ".aota/task-main-bootstrap.json",
        ".aota/task-main-thin-bootstrap.json",
    }
)
_RUNTIME_ARTIFACT_DIRECTORY_PREFIXES = (
    ".aota/handoffs/",
    ".aota/durable_payloads/",
    ".aota/pre-resolved-bindings/",
    ".aota/task_return_receipts/",
    ".pytest_cache/",
    ".ruff_cache/",
)
_RUNTIME_ARTIFACT_DIRECTORY_PATHS = frozenset(
    path
    for prefix in _RUNTIME_ARTIFACT_DIRECTORY_PREFIXES
    for path in (prefix.rstrip("/"), prefix)
)
_OPENCODE_INSTANCE_KEY_RE = re.compile(r"^[A-Za-z0-9._-]{1,96}$")
_OPENCODE_ENVELOPE_FILE_RE = re.compile(r"^[A-Za-z0-9._-]{1,128}\.json$")
_PYTHON_BYTECODE_FILE_RE = re.compile(r"^[A-Za-z0-9._-]+\.pyc$")
_TRUSTED_AUTHORITY_CAPABILITY = object()


class ReviewAdoptionError(TaskMainCoordinatorError):
    """Fail-closed refusal to establish or consume review-only adoption."""


@dataclass(frozen=True)
class TrustedPlanAuthority:
    """Operator-bound Plan source facts used to validate durable adoption."""

    project_id: str
    plan_id: str
    authority_source_kind: str
    authority_ref: str
    source_revision: str
    source_digest: str
    _capability: object = field(default=None, repr=False, compare=False)

    def __post_init__(self) -> None:
        if self._capability is not _TRUSTED_AUTHORITY_CAPABILITY:
            raise TypeError("TrustedPlanAuthority must be created by trusted composition")
        object.__setattr__(self, "project_id", _require_non_empty_str(self.project_id, "project_id"))
        object.__setattr__(self, "plan_id", _require_non_empty_str(self.plan_id, "plan_id"))
        object.__setattr__(
            self,
            "authority_source_kind",
            _require_non_empty_str(self.authority_source_kind, "authority_source_kind"),
        )
        object.__setattr__(self, "authority_ref", _require_non_empty_str(self.authority_ref, "authority_ref"))
        object.__setattr__(
            self,
            "source_revision",
            _require_non_empty_str(self.source_revision, "source_revision", max_length=128),
        )
        object.__setattr__(self, "source_digest", _require_sha256(self.source_digest, "source_digest"))

    def canonical_dict(self) -> dict[str, str]:
        return {
            "authority_ref": self.authority_ref,
            "authority_source_kind": self.authority_source_kind,
            "plan_id": self.plan_id,
            "project_id": self.project_id,
            "source_digest": self.source_digest,
            "source_revision": self.source_revision,
        }


def trusted_plan_authority_from_binding(
    *,
    project_id: str,
    binding: PlanAuthorityBinding,
    authoritative_reader: PlanAuthorityReadAdapter,
) -> TrustedPlanAuthority:
    """Create the review authority carrier from a re-read bound source."""

    if not isinstance(binding, PlanAuthorityBinding):
        raise TypeError(f"binding must be PlanAuthorityBinding, got {type(binding).__name__}")
    if not isinstance(authoritative_reader, PlanAuthorityReadAdapter):
        raise TypeError(
            "authoritative_reader must be a PlanAuthorityReadAdapter"
        )
    if binding.source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
        raise ReviewAdoptionError(
            "review-only adoption requires an observed local_governance Plan source"
        )
    if binding.source_revision is None or binding.source_digest is None:
        raise ReviewAdoptionError(
            "review-only adoption requires source revision and digest from the bound Plan authority"
        )
    observed_binding = getattr(authoritative_reader, "binding", None)
    if observed_binding != binding:
        raise ReviewAdoptionError(
            "authoritative Plan reader does not expose the requested bound source"
        )
    try:
        snapshot = authoritative_reader.load()
    except Exception as exc:
        raise ReviewAdoptionError(
            f"authoritative Plan source could not be re-read: {exc}"
        ) from exc
    if (
        snapshot.revision != binding.source_revision
        or snapshot.digest != binding.source_digest
    ):
        raise ReviewAdoptionError(
            "authoritative Plan source revision/digest changed during authority binding"
        )
    return TrustedPlanAuthority(
        project_id=project_id,
        plan_id=binding.plan_id,
        authority_source_kind=binding.source_kind,
        authority_ref=binding.authority_ref,
        source_revision=str(binding.source_revision),
        source_digest=binding.source_digest,
        _capability=_TRUSTED_AUTHORITY_CAPABILITY,
    )


def _is_known_runtime_artifact_path(path: str) -> bool:
    normalized = path.strip().replace("\\", "/")
    if normalized.startswith("./"):
        normalized = normalized[2:]
    if not normalized or normalized.startswith("/") or "\x00" in normalized:
        return False
    if normalized in _RUNTIME_ARTIFACT_DIRECTORY_PATHS:
        return True
    if any(part in ("", ".", "..") for part in normalized.split("/")):
        return False
    if normalized in _RUNTIME_ARTIFACT_EXACT_PATHS:
        return True
    if _PYTHON_BYTECODE_FILE_RE.fullmatch(normalized.rsplit("/", 1)[-1]) is not None:
        return True
    if normalized == ".aota/opencode/active_binding.json":
        return True
    opencode_parts = normalized.split("/")
    if (
        len(opencode_parts) == 7
        and opencode_parts[:3] == [".aota", "opencode", "instances"]
        and _OPENCODE_INSTANCE_KEY_RE.fullmatch(opencode_parts[3]) is not None
        and opencode_parts[4:6] == [".aota", "opencode"]
        and opencode_parts[6] == "active_binding.json"
    ):
        return True
    if (
        len(opencode_parts) == 7
        and opencode_parts[:3] == [".aota", "opencode", "instances"]
        and _OPENCODE_INSTANCE_KEY_RE.fullmatch(opencode_parts[3]) is not None
        and opencode_parts[4:6] == [".aota", "pre-resolved-bindings"]
        and _OPENCODE_ENVELOPE_FILE_RE.fullmatch(opencode_parts[6]) is not None
    ):
        return True
    if (
        normalized.startswith(".aota/")
        and "/" not in normalized[len(".aota/") :]
        and _OBSERVATION_EVIDENCE_FILE_RE.fullmatch(normalized[len(".aota/") :])
        is not None
    ):
        return True
    for prefix in _RUNTIME_ARTIFACT_DIRECTORY_PREFIXES:
        if not normalized.startswith(prefix):
            continue
        filename = normalized[len(prefix) :]
        if prefix in (".pytest_cache/", ".ruff_cache/"):
            return True
        if "/" in filename:
            return False
        if prefix == ".aota/handoffs/":
            return _HANDOFF_FILE_RE.fullmatch(filename) is not None
        if prefix == ".aota/durable_payloads/":
            return _RUNTIME_DIGEST_FILE_RE.fullmatch(filename) is not None
        if prefix == ".aota/pre-resolved-bindings/":
            return _WORKER_BINDING_FILE_RE.fullmatch(filename) is not None
        if prefix == ".aota/task_return_receipts/":
            return _HANDOFF_FILE_RE.fullmatch(filename) is not None
    return False


def _require_non_empty_str(value: Any, label: str, *, max_length: int = _MAX_REF_LENGTH) -> str:
    if not isinstance(value, str) or type(value) is not str:
        raise TypeError(f"{label} must be a string, got {type(value).__name__}")
    stripped = value.strip()
    if not stripped:
        raise ValueError(f"{label} must be a non-empty string")
    if len(stripped) > max_length:
        raise ValueError(f"{label} length ({len(stripped)}) exceeds maximum {max_length}")
    if "\x00" in stripped:
        raise ValueError(f"{label} must not contain NUL")
    return stripped


def normalize_reviewer_attempt(value: Any) -> int:
    """Validate the bounded trusted runtime reviewer-attempt selector."""

    if type(value) is not int:
        raise TypeError(f"reviewer_attempt must be an integer, got {type(value).__name__}")
    if value not in _SUPPORTED_REVIEWER_ATTEMPTS:
        raise ValueError(
            "reviewer_attempt must be one of the bounded supported attempts: 1 or 2"
        )
    return value


def normalize_review_cycle(value: Any) -> ReviewCycle:
    try:
        return parse_review_cycle(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"review_cycle must be RV1 or RV2: {exc}") from exc


def _reviewer_task_id_match(value: Any) -> re.Match[str]:
    task_id = _require_non_empty_str(value, "reviewer_canonical_task_id")
    match = _REVIEWER_TASK_ID_RE.search(task_id)
    if match is None:
        raise ValueError(
            "reviewer_canonical_task_id must end with ':RV1|RV2:attempt-<1|2>'"
        )
    return match


def reviewer_attempt_from_canonical_task_id(value: Any) -> int:
    """Extract the bounded attempt from a canonical RV1/RV2 task identity."""

    match = _reviewer_task_id_match(value)
    try:
        return normalize_reviewer_attempt(int(match.group("attempt")))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"reviewer task identity has an unsupported attempt: {value!r}") from exc


def review_cycle_from_canonical_task_id(value: Any) -> ReviewCycle:
    """Extract the bounded review cycle from a canonical reviewer identity."""

    match = _reviewer_task_id_match(value)
    try:
        return parse_review_cycle(match.group("review_cycle"))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"reviewer task identity has an unsupported review cycle: {value!r}") from exc


def _require_commit(value: Any, label: str) -> str:
    normalized = _require_non_empty_str(value, label, max_length=40).lower()
    if not _SHA1_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be a full lowercase Git commit SHA")
    return normalized


def _require_sha256(value: Any, label: str) -> str:
    normalized = _require_non_empty_str(value, label, max_length=64).lower()
    if not _SHA256_RE.fullmatch(normalized):
        raise ValueError(f"{label} must be a full lowercase SHA-256 digest")
    return normalized


@dataclass(frozen=True)
class AcceptedFrontierAuthorization:
    """Trusted exact authorization for one review-only adoption.

    This value is minted by trusted composition from the current governed
    Plan/projection.  It is not accepted from model input or from a review
    result.
    """

    project_id: str
    plan_id: str
    plan_authority: str
    authority_source_kind: str
    plan_digest: str
    plan_source_revision: str
    milestone_id: str
    entry_base: str
    candidate_frontier_ref: str
    authorization_ref: str
    reviewer_canonical_task_id: str
    provenance_digest: str = ""
    review_cycle: ReviewCycle = ReviewCycle.RV1

    def __post_init__(self) -> None:
        for label in (
            "project_id",
            "plan_id",
            "plan_authority",
            "authority_source_kind",
            "plan_digest",
            "plan_source_revision",
            "milestone_id",
            "authorization_ref",
            "reviewer_canonical_task_id",
        ):
            object.__setattr__(self, label, _require_non_empty_str(getattr(self, label), label))
        object.__setattr__(self, "review_cycle", normalize_review_cycle(self.review_cycle))
        if review_cycle_from_canonical_task_id(self.reviewer_canonical_task_id) != self.review_cycle:
            raise ValueError("review_cycle does not match reviewer_canonical_task_id")
        object.__setattr__(self, "entry_base", _require_commit(self.entry_base, "entry_base"))
        object.__setattr__(
            self,
            "candidate_frontier_ref",
            _require_commit(self.candidate_frontier_ref, "candidate_frontier_ref"),
        )
        if not isinstance(self.provenance_digest, str) or type(self.provenance_digest) is not str:
            raise TypeError("provenance_digest must be a string")
        if self.provenance_digest:
            object.__setattr__(
                self,
                "provenance_digest",
                _require_sha256(self.provenance_digest, "provenance_digest"),
            )

    @property
    def frontier_ref(self) -> str:
        """Compatibility name for the authorized candidate frontier."""

        return self.candidate_frontier_ref


@dataclass(frozen=True)
class TrustedGitFrontier:
    """Read-only observation of the trusted current worktree frontier."""

    worktree_root: str
    entry_base: str
    frontier_ref: str
    tree_ref: str
    clean: bool

    def __post_init__(self) -> None:
        object.__setattr__(self, "worktree_root", _require_non_empty_str(self.worktree_root, "worktree_root"))
        object.__setattr__(self, "entry_base", _require_commit(self.entry_base, "entry_base"))
        object.__setattr__(self, "frontier_ref", _require_commit(self.frontier_ref, "frontier_ref"))
        object.__setattr__(self, "tree_ref", _require_commit(self.tree_ref, "tree_ref"))
        if type(self.clean) is not bool:
            raise TypeError("clean must be bool")
        if not self.clean:
            raise ValueError("trusted Git frontier must be clean")

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "clean": self.clean,
            "entry_base": self.entry_base,
            "frontier_ref": self.frontier_ref,
            "tree_ref": self.tree_ref,
            "worktree_root": self.worktree_root,
        }


def _authorization_provenance_payload(
    authorization: AcceptedFrontierAuthorization,
    frontier: TrustedGitFrontier,
) -> dict[str, Any]:
    return {
        "authorization": {
            "authority_source_kind": authorization.authority_source_kind,
            "authorization_ref": authorization.authorization_ref,
            "candidate_frontier_ref": authorization.candidate_frontier_ref,
            "entry_base": authorization.entry_base,
            "milestone_id": authorization.milestone_id,
            "plan_authority": authorization.plan_authority,
            "plan_digest": authorization.plan_digest,
            "plan_id": authorization.plan_id,
            "plan_source_revision": authorization.plan_source_revision,
            "project_id": authorization.project_id,
            "reviewer_canonical_task_id": authorization.reviewer_canonical_task_id,
            "review_cycle": authorization.review_cycle.value,
        },
        "git": frontier.canonical_dict(),
    }


def _legacy_authorization_provenance_payload(
    authorization: AcceptedFrontierAuthorization,
    frontier: TrustedGitFrontier,
) -> dict[str, Any]:
    """Reconstruct the pre-review-cycle provenance payload for durable replay."""
    return {
        "authorization": {
            "authority_source_kind": authorization.authority_source_kind,
            "authorization_ref": authorization.authorization_ref,
            "candidate_frontier_ref": authorization.candidate_frontier_ref,
            "entry_base": authorization.entry_base,
            "milestone_id": authorization.milestone_id,
            "plan_authority": authorization.plan_authority,
            "plan_digest": authorization.plan_digest,
            "plan_id": authorization.plan_id,
            "plan_source_revision": authorization.plan_source_revision,
            "project_id": authorization.project_id,
            "reviewer_canonical_task_id": authorization.reviewer_canonical_task_id,
        },
        "git": frontier.canonical_dict(),
    }


def _authorization_provenance_digest(
    authorization: AcceptedFrontierAuthorization,
    frontier: TrustedGitFrontier,
) -> str:
    encoded = canonical_json(
        canonicalize(
            _authorization_provenance_payload(authorization, frontier),
            path="AcceptedFrontierAuthorizationProvenance",
        )
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _legacy_authorization_provenance_digest(
    authorization: AcceptedFrontierAuthorization,
    frontier: TrustedGitFrontier,
) -> str:
    encoded = canonical_json(
        canonicalize(
            _legacy_authorization_provenance_payload(authorization, frontier),
            path="AcceptedFrontierAuthorizationProvenance",
        )
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def _canonical_authorization_ref(
    authority: TrustedPlanAuthority,
    *,
    milestone_id: str,
    frontier_ref: str,
) -> str:
    _require_commit(frontier_ref, "frontier_ref")
    return (
        "review-authority://"
        f"{authority.authority_source_kind}/{authority.project_id}/"
        f"{authority.plan_id}/{_require_non_empty_str(milestone_id, 'milestone_id')}"
    )


def _authorization_provenance_reason(
    authorization: AcceptedFrontierAuthorization,
    frontier: TrustedGitFrontier,
) -> str:
    """Encode only the provenance not already held by the coordinator.

    The coordinator's existing Plan, Milestone, review-marker, and frontier
    fields reconstruct the authorization identity.  Only the Git observation
    residual that has no existing durable owner stays in ``reason``.  This is
    deliberately an uncompressed canonical representation: compression is not
    part of the trust or size contract.
    """

    payload = {
        "version": _AUTHORIZATION_PROVENANCE_VERSION,
        "git": {
            "clean": frontier.clean,
            "tree_ref": frontier.tree_ref,
            "worktree_root": frontier.worktree_root,
        },
    }
    result = f"authprov{_AUTHORIZATION_PROVENANCE_VERSION}:{canonical_json(canonicalize(payload, path='AcceptedFrontierAuthorizationRecord'))}"
    if len(result) > _MAX_REF_LENGTH:
        raise ReviewAdoptionError("authorization provenance exceeds the durable coordinator bound")
    return result


def _authorization_provenance_from_legacy_reason(
    reason: str,
) -> tuple[AcceptedFrontierAuthorization, TrustedGitFrontier]:
    prefix = f"authprov{_AUTHORIZATION_PROVENANCE_LEGACY_VERSION}:"
    encoded = reason[len(prefix) :]
    try:
        raw = zlib.decompress(base64.urlsafe_b64decode(encoded.encode("ascii")))
        if len(raw) > 32 * 1024:
            raise ValueError("provenance payload exceeds bound")
        record = json.loads(raw.decode("utf-8"))
    except Exception as exc:
        raise ReviewAdoptionError(f"durable authorization provenance cannot be decoded: {exc}") from exc
    if not isinstance(record, Mapping) or record.get("version") != _AUTHORIZATION_PROVENANCE_LEGACY_VERSION:
        raise ReviewAdoptionError("durable authorization provenance version is invalid")
    payload = record.get("payload")
    if not isinstance(payload, Mapping):
        raise ReviewAdoptionError("durable authorization provenance payload is malformed")
    authorization_raw = payload.get("authorization")
    frontier_raw = payload.get("git")
    if not isinstance(authorization_raw, Mapping) or not isinstance(frontier_raw, Mapping):
        raise ReviewAdoptionError("durable authorization provenance facts are incomplete")
    try:
        frontier = TrustedGitFrontier(**dict(frontier_raw))
        authorization = AcceptedFrontierAuthorization(**dict(authorization_raw))
    except Exception as exc:
        raise ReviewAdoptionError(f"durable authorization provenance facts are invalid: {exc}") from exc
    if authorization.provenance_digest:
        raise ReviewAdoptionError("durable authorization provenance must not self-embed its digest")
    authorization = replace(
        authorization,
        provenance_digest=_authorization_provenance_digest(authorization, frontier),
    )
    return authorization, frontier


def _coordinator_marker_ref(
    state: TaskMainCoordinatorState,
    field: str,
) -> str:
    marker = getattr(state, field)
    if not isinstance(marker, Mapping):
        raise ReviewAdoptionError(f"durable authorization is missing coordinator {field} marker")
    try:
        return _require_non_empty_str(marker.get("ref"), f"{field}.ref")
    except (TypeError, ValueError) as exc:
        raise ReviewAdoptionError(f"durable coordinator {field}.ref is invalid: {exc}") from exc


def _plan_id_from_coordinator_authority(state: TaskMainCoordinatorState) -> str:
    prefix = f"local-governance:{state.project_id}/"
    authority = state.plan_authority
    if not authority.startswith(prefix):
        raise ReviewAdoptionError("durable coordinator Plan authority is not canonical local_governance")
    plan_id = authority[len(prefix) :]
    if not plan_id or "/" in plan_id:
        raise ReviewAdoptionError("durable coordinator Plan authority has no canonical Plan identity")
    try:
        if local_plan_authority_reference(state.project_id, plan_id) != authority:
            raise ReviewAdoptionError("durable coordinator Plan authority does not round-trip canonically")
    except ReviewAdoptionError:
        raise
    except Exception as exc:
        raise ReviewAdoptionError(f"durable coordinator Plan identity is invalid: {exc}") from exc
    return plan_id


def _authorization_from_coordinator_state(
    state: TaskMainCoordinatorState,
) -> AcceptedFrontierAuthorization:
    """Rebuild authorization identity from existing durable coordinator facts."""

    if not isinstance(state, TaskMainCoordinatorState):
        raise TypeError(f"state must be TaskMainCoordinatorState, got {type(state).__name__}")
    try:
        reviewer_task = _coordinator_marker_ref(state, "integrated_review")
        return AcceptedFrontierAuthorization(
            project_id=state.project_id,
            plan_id=_plan_id_from_coordinator_authority(state),
            plan_authority=state.plan_authority,
            authority_source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
            plan_digest=state.plan_digest,
            plan_source_revision=state.plan_source_revision or "",
            milestone_id=state.milestone_id,
            entry_base=state.entry_base,
            candidate_frontier_ref=_coordinator_marker_ref(state, "frontier_ref"),
            authorization_ref=_coordinator_marker_ref(state, "review_gates"),
            reviewer_canonical_task_id=reviewer_task,
            review_cycle=review_cycle_from_canonical_task_id(reviewer_task),
        )
    except ReviewAdoptionError:
        raise
    except (TypeError, ValueError) as exc:
        raise ReviewAdoptionError(f"durable coordinator authorization facts are invalid: {exc}") from exc


def _authorization_provenance_from_reason(
    reason: Any,
    *,
    state: TaskMainCoordinatorState | None = None,
) -> tuple[AcceptedFrontierAuthorization, TrustedGitFrontier]:
    if not isinstance(reason, str) or type(reason) is not str:
        raise ReviewAdoptionError("review-only adoption is missing durable authorization provenance")
    if len(reason) > _MAX_REF_LENGTH:
        raise ReviewAdoptionError("durable authorization provenance exceeds the coordinator bound")

    legacy_prefix = f"authprov{_AUTHORIZATION_PROVENANCE_LEGACY_VERSION}:"
    current_prefix = f"authprov{_AUTHORIZATION_PROVENANCE_VERSION}:"
    if reason.startswith(legacy_prefix):
        return _authorization_provenance_from_legacy_reason(reason)
    if not reason.startswith(current_prefix):
        raise ReviewAdoptionError("review-only adoption is missing durable authorization provenance")
    if not isinstance(state, TaskMainCoordinatorState):
        raise ReviewAdoptionError(
            "authprov2 durable authorization requires the existing coordinator facts"
        )

    encoded = reason[len(current_prefix) :]
    try:
        record = json.loads(encoded)
        if canonical_json(record) != encoded:
            raise ValueError("provenance encoding is not canonical")
    except Exception as exc:
        raise ReviewAdoptionError(f"durable authorization provenance cannot be decoded: {exc}") from exc
    if not isinstance(record, Mapping) or set(record) != {"git", "version"}:
        raise ReviewAdoptionError("durable authorization provenance record is malformed")
    if record.get("version") != _AUTHORIZATION_PROVENANCE_VERSION:
        raise ReviewAdoptionError("durable authorization provenance version is invalid")
    git_raw = record.get("git")
    if not isinstance(git_raw, Mapping) or set(git_raw) != {"clean", "tree_ref", "worktree_root"}:
        raise ReviewAdoptionError("durable authorization provenance Git residual is malformed")
    if type(git_raw.get("clean")) is not bool:
        raise ReviewAdoptionError("durable authorization provenance clean flag is invalid")
    try:
        authorization = _authorization_from_coordinator_state(state)
        frontier = TrustedGitFrontier(
            worktree_root=git_raw["worktree_root"],
            entry_base=authorization.entry_base,
            frontier_ref=authorization.candidate_frontier_ref,
            tree_ref=git_raw["tree_ref"],
            clean=git_raw["clean"],
        )
    except ReviewAdoptionError:
        raise
    except (TypeError, ValueError) as exc:
        raise ReviewAdoptionError(f"durable authorization provenance facts are invalid: {exc}") from exc
    authorization = replace(
        authorization,
        provenance_digest=_authorization_provenance_digest(authorization, frontier),
    )
    return authorization, frontier


def _has_trusted_authorization_provenance(
    authorization: AcceptedFrontierAuthorization,
    frontier: TrustedGitFrontier,
) -> bool:
    if not authorization.provenance_digest:
        return False
    expected = _authorization_provenance_digest(authorization, frontier)
    legacy = _legacy_authorization_provenance_digest(authorization, frontier)
    return authorization.provenance_digest in {expected, legacy}


@dataclass(frozen=True)
class ReviewOnlyAdoption:
    """Validated marker reconstructed from existing coordinator fields."""

    authorization_ref: str
    frontier_ref: str
    reviewer_canonical_task_id: str
    proof_digest: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "authorization_ref", _require_non_empty_str(self.authorization_ref, "authorization_ref"))
        object.__setattr__(self, "frontier_ref", _require_commit(self.frontier_ref, "frontier_ref"))
        object.__setattr__(
            self,
            "reviewer_canonical_task_id",
            _require_non_empty_str(self.reviewer_canonical_task_id, "reviewer_canonical_task_id"),
        )
        object.__setattr__(self, "proof_digest", _require_sha256(self.proof_digest, "proof_digest"))

    @property
    def reviewer_attempt(self) -> int:
        return reviewer_attempt_from_canonical_task_id(self.reviewer_canonical_task_id)

    @property
    def review_cycle(self) -> ReviewCycle:
        return review_cycle_from_canonical_task_id(self.reviewer_canonical_task_id)


def read_trusted_git_frontier(
    worktree_root: WorktreeSandboxBoundary,
    *,
    entry_base: str,
    git: GitPort | None = None,
) -> TrustedGitFrontier:
    """Observe a clean canonical Git HEAD and verify ``entry_base`` ancestry."""

    if not isinstance(worktree_root, WorktreeSandboxBoundary):
        raise TypeError(
            "worktree_root must be the trusted WorktreeSandboxBoundary, "
            f"got {type(worktree_root).__name__}"
        )
    root = Path(worktree_root.worktree_root)
    if root.is_symlink():
        raise ReviewAdoptionError("trusted worktree root must not be a symlink")
    try:
        root = root.resolve(strict=True)
    except OSError as exc:
        raise ReviewAdoptionError(f"trusted worktree cannot be resolved: {exc}") from exc
    if not root.is_dir():
        raise ReviewAdoptionError("trusted worktree root must be a canonical directory")

    entry = _require_commit(entry_base, "entry_base")
    try:
        observation = inspect_git(
            root,
            boundary=root,
            max_entries=_TRUSTED_GIT_MAX_OBSERVED_PATHS,
        )
    except Exception as exc:
        raise ReviewAdoptionError(f"trusted Git inspection failed: {exc}") from exc
    if observation.get("available") is not True:
        raise ReviewAdoptionError("trusted Git source is unavailable")
    if not _trusted_source_is_clean(observation):
        raise ReviewAdoptionError("trusted Git source is dirty")
    head = observation.get("head_sha")
    try:
        head = _require_commit(head, "trusted Git HEAD")
    except (TypeError, ValueError) as exc:
        raise ReviewAdoptionError(str(exc)) from exc

    port = git or SubprocessGitPort()
    if not isinstance(port, GitPort):
        raise TypeError(f"git must be GitPort or None, got {type(port).__name__}")
    try:
        if not port.is_ancestor(root, entry, head):
            raise ReviewAdoptionError(
                f"trusted Git HEAD {head!r} is not a descendant of entry_base {entry!r}"
            )
        tree = port.resolve_tree(root, head)
    except ReviewAdoptionError:
        raise
    except Exception as exc:
        raise ReviewAdoptionError(f"trusted Git ancestry/tree verification failed: {exc}") from exc
    try:
        tree = _require_commit(tree, "trusted Git tree")
    except (TypeError, ValueError) as exc:
        raise ReviewAdoptionError(str(exc)) from exc
    return TrustedGitFrontier(
        worktree_root=str(root),
        entry_base=entry,
        frontier_ref=head,
        tree_ref=tree,
        clean=True,
    )


def _proof_digest(
    authorization: AcceptedFrontierAuthorization,
    frontier: TrustedGitFrontier,
) -> str:
    payload = {
        "authorization": {
            "authorization_ref": authorization.authorization_ref,
            "authority_source_kind": authorization.authority_source_kind,
            "candidate_frontier_ref": authorization.candidate_frontier_ref,
            "entry_base": authorization.entry_base,
            "milestone_id": authorization.milestone_id,
            "plan_authority": authorization.plan_authority,
            "plan_digest": authorization.plan_digest,
            "plan_id": authorization.plan_id,
            "plan_source_revision": authorization.plan_source_revision,
            "project_id": authorization.project_id,
            "reviewer_canonical_task_id": authorization.reviewer_canonical_task_id,
            "review_cycle": authorization.review_cycle.value,
        },
        "git": frontier.canonical_dict(),
    }
    return hashlib.sha256(
        canonical_json(canonicalize(payload, path="ReviewOnlyAdoptionProof")).encode("utf-8")
    ).hexdigest()


def _legacy_proof_digest(
    authorization: AcceptedFrontierAuthorization,
    frontier: TrustedGitFrontier,
) -> str:
    """Reconstruct the pre-review-cycle adoption proof for durable replay."""
    payload = {
        "authorization": {
            "authorization_ref": authorization.authorization_ref,
            "authority_source_kind": authorization.authority_source_kind,
            "candidate_frontier_ref": authorization.candidate_frontier_ref,
            "entry_base": authorization.entry_base,
            "milestone_id": authorization.milestone_id,
            "plan_authority": authorization.plan_authority,
            "plan_digest": authorization.plan_digest,
            "plan_id": authorization.plan_id,
            "plan_source_revision": authorization.plan_source_revision,
            "project_id": authorization.project_id,
            "reviewer_canonical_task_id": authorization.reviewer_canonical_task_id,
        },
        "git": frontier.canonical_dict(),
    }
    return hashlib.sha256(
        canonical_json(canonicalize(payload, path="ReviewOnlyAdoptionProof")).encode("utf-8")
    ).hexdigest()


def _expected_reviewer_task_id(
    project_id: str,
    milestone_id: str,
    reviewer_attempt: int = DEFAULT_REVIEWER_ATTEMPT,
    *,
    review_cycle: ReviewCycle | str = ReviewCycle.RV1,
) -> str:
    attempt = normalize_reviewer_attempt(reviewer_attempt)
    cycle = normalize_review_cycle(review_cycle)
    return f"{project_id}:{milestone_id}:{cycle.value}:attempt-{attempt}"


def reviewer_task_id(
    project_id: str,
    milestone_id: str,
    reviewer_attempt: int = DEFAULT_REVIEWER_ATTEMPT,
    *,
    review_cycle: ReviewCycle | str = ReviewCycle.RV1,
) -> str:
    """Build one canonical bounded RV1/RV2 reviewer identity."""

    return _expected_reviewer_task_id(
        project_id,
        milestone_id,
        reviewer_attempt,
        review_cycle=review_cycle,
    )


def _reported_paths(
    observation: Mapping[str, Any],
    categories: tuple[str, ...],
) -> tuple[tuple[str, str], ...]:
    paths: list[tuple[str, str]] = []
    for category in categories:
        payload = observation.get(category)
        if not isinstance(payload, Mapping):
            continue
        raw_paths = payload.get("paths")
        if not isinstance(raw_paths, (tuple, list)):
            continue
        for raw_path in raw_paths:
            if isinstance(raw_path, str) and raw_path.strip():
                path = raw_path.strip().replace("\\", "/")
                if path.startswith("./"):
                    path = path[2:]
                paths.append((category, path))
    return tuple(paths)


def _reported_dirty_paths(observation: Mapping[str, Any]) -> tuple[tuple[str, str], ...]:
    return _reported_paths(observation, ("staged", "unstaged", "untracked"))


def _trusted_source_is_clean(observation: Mapping[str, Any]) -> bool:
    """Apply the source-cleanliness policy to a trusted Git observation."""

    ignored_payload = observation.get("ignored")
    if not isinstance(ignored_payload, Mapping):
        return False
    for category in ("staged", "unstaged", "untracked", "ignored"):
        payload = observation.get(category)
        if isinstance(payload, Mapping) and payload.get("truncated") is True:
            return False
    ignored_paths = _reported_paths(observation, ("ignored",))
    if any(not _is_known_runtime_artifact_path(path) for _, path in ignored_paths):
        return False
    if observation.get("is_dirty") is False:
        return not _reported_dirty_paths(observation)
    if observation.get("is_dirty") is not True:
        return False
    dirty_paths = _reported_dirty_paths(observation)
    if not dirty_paths:
        return False
    return all(category == "untracked" and _is_known_runtime_artifact_path(path) for category, path in dirty_paths)


def _validate_authorization(
    *,
    state: TaskMainCoordinatorState,
    view: MilestonePlanView,
    authorization: AcceptedFrontierAuthorization,
    frontier: TrustedGitFrontier,
    trusted_authority: TrustedPlanAuthority,
) -> None:
    if authorization.authority_source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
        raise ReviewAdoptionError("review-only adoption requires local_governance authority")
    _validate_authorization_against_trusted_authority(
        state=state,
        view=view,
        authorization=authorization,
        frontier=frontier,
        trusted_authority=trusted_authority,
    )
    try:
        expected_authority = local_plan_authority_reference(
            authorization.project_id, authorization.plan_id
        )
    except Exception as exc:
        raise ReviewAdoptionError(f"authorization Plan identity is invalid: {exc}") from exc
    if authorization.plan_authority != expected_authority:
        raise ReviewAdoptionError("authorization Plan authority is not the trusted local_governance authority")
    try:
        reviewer_attempt = reviewer_attempt_from_canonical_task_id(
            authorization.reviewer_canonical_task_id
        )
    except (TypeError, ValueError) as exc:
        raise ReviewAdoptionError(str(exc)) from exc
    checks = (
        (authorization.project_id, state.project_id, "project identity"),
        (authorization.plan_authority, state.plan_authority, "Plan authority"),
        (authorization.plan_authority, view.plan_authority, "live Plan authority"),
        (authorization.plan_digest, state.plan_digest, "Plan digest"),
        (authorization.plan_digest, view.plan_digest, "live Plan digest"),
        (authorization.plan_source_revision, state.plan_source_revision, "Plan source revision"),
        (authorization.plan_source_revision, view.plan_source_revision, "live Plan source revision"),
        (authorization.milestone_id, state.milestone_id, "Milestone"),
        (authorization.milestone_id, view.milestone_id, "live Milestone"),
        (authorization.entry_base, state.entry_base, "entry base"),
        (authorization.entry_base, view.entry_base, "live entry base"),
        (authorization.entry_base, frontier.entry_base, "observed entry base"),
        (authorization.candidate_frontier_ref, frontier.frontier_ref, "candidate frontier"),
        (
            authorization.reviewer_canonical_task_id,
            _expected_reviewer_task_id(
                state.project_id,
                state.milestone_id,
                reviewer_attempt,
                review_cycle=authorization.review_cycle,
            ),
            "reviewer task identity",
        ),
    )
    for supplied, expected, label in checks:
        if supplied != expected:
            raise ReviewAdoptionError(
                f"{label} mismatch: authorized {supplied!r}, trusted {expected!r}"
            )
    if not frontier.clean:
        raise ReviewAdoptionError("trusted Git frontier is not clean")


def _validate_authorization_against_state(
    *,
    state: TaskMainCoordinatorState,
    authorization: AcceptedFrontierAuthorization,
    frontier: TrustedGitFrontier,
    trusted_authority: TrustedPlanAuthority,
) -> None:
    """Validate durable authorization facts without requiring a live Plan view."""

    if authorization.authority_source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
        raise ReviewAdoptionError("durable review authorization has an untrusted authority source")
    _validate_authorization_against_trusted_authority(
        state=state,
        view=None,
        authorization=authorization,
        frontier=frontier,
        trusted_authority=trusted_authority,
    )
    try:
        expected_authority = local_plan_authority_reference(
            authorization.project_id, authorization.plan_id
        )
    except Exception as exc:
        raise ReviewAdoptionError(f"durable authorization Plan identity is invalid: {exc}") from exc
    if authorization.plan_authority != expected_authority:
        raise ReviewAdoptionError("durable authorization Plan authority is not trusted")
    try:
        reviewer_attempt = reviewer_attempt_from_canonical_task_id(
            authorization.reviewer_canonical_task_id
        )
    except (TypeError, ValueError) as exc:
        raise ReviewAdoptionError(str(exc)) from exc
    checks = (
        (authorization.project_id, state.project_id, "project identity"),
        (authorization.plan_authority, state.plan_authority, "Plan authority"),
        (authorization.plan_digest, state.plan_digest, "Plan digest"),
        (authorization.plan_source_revision, state.plan_source_revision, "Plan source revision"),
        (authorization.milestone_id, state.milestone_id, "Milestone"),
        (authorization.entry_base, state.entry_base, "entry base"),
        (authorization.entry_base, frontier.entry_base, "observed entry base"),
        (authorization.candidate_frontier_ref, frontier.frontier_ref, "candidate frontier"),
        (
            authorization.reviewer_canonical_task_id,
            _expected_reviewer_task_id(
                state.project_id,
                state.milestone_id,
                reviewer_attempt,
                review_cycle=authorization.review_cycle,
            ),
            "reviewer task identity",
        ),
    )
    for supplied, expected, label in checks:
        if supplied != expected:
            raise ReviewAdoptionError(
                f"{label} mismatch: authorized {supplied!r}, durable state {expected!r}"
            )
    if not frontier.clean:
        raise ReviewAdoptionError("durable trusted Git frontier is not clean")


def _validate_authorization_against_trusted_authority(
    *,
    state: TaskMainCoordinatorState,
    view: MilestonePlanView | None,
    authorization: AcceptedFrontierAuthorization,
    frontier: TrustedGitFrontier,
    trusted_authority: TrustedPlanAuthority,
) -> None:
    if not isinstance(trusted_authority, TrustedPlanAuthority):
        raise ReviewAdoptionError("review-only adoption requires a trusted Plan authority binding")
    try:
        expected_authority = local_plan_authority_reference(
            trusted_authority.project_id, trusted_authority.plan_id
        )
    except Exception as exc:
        raise ReviewAdoptionError(f"trusted Plan authority identity is invalid: {exc}") from exc
    if trusted_authority.authority_source_kind != PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE:
        raise ReviewAdoptionError("trusted review authority source is not local_governance")
    if trusted_authority.authority_ref != expected_authority:
        raise ReviewAdoptionError("trusted review authority reference is not canonical")
    if trusted_authority.project_id != state.project_id:
        raise ReviewAdoptionError("trusted review authority project identity mismatch")
    if authorization.plan_id != trusted_authority.plan_id:
        raise ReviewAdoptionError("authorization Plan identity does not match the trusted authority")
    if authorization.plan_authority != trusted_authority.authority_ref:
        raise ReviewAdoptionError("authorization Plan authority does not match the trusted authority")
    if authorization.authority_source_kind != trusted_authority.authority_source_kind:
        raise ReviewAdoptionError("authorization source kind does not match the trusted authority")
    if authorization.plan_digest != trusted_authority.source_digest:
        raise ReviewAdoptionError("authorization Plan digest does not match the trusted authority source")
    if authorization.plan_source_revision != trusted_authority.source_revision:
        raise ReviewAdoptionError("authorization Plan source revision does not match the trusted authority source")
    if view is not None and (
        view.plan_digest != trusted_authority.source_digest
        or (view.plan_source_revision or "") != trusted_authority.source_revision
    ):
        raise ReviewAdoptionError("live Plan view does not match the trusted authority source")
    expected_ref = _canonical_authorization_ref(
        trusted_authority,
        milestone_id=authorization.milestone_id,
        frontier_ref=authorization.candidate_frontier_ref,
    )
    if authorization.authorization_ref != expected_ref:
        raise ReviewAdoptionError("authorization reference is not derived from the trusted authority")
    if frontier.worktree_root != frontier.worktree_root.strip():
        raise ReviewAdoptionError("trusted Git worktree identity is malformed")


def mint_accepted_frontier_authorization(
    *,
    state: TaskMainCoordinatorState,
    live_plan_view: MilestonePlanView,
    plan_id: str,
    authorization_ref: str | None = None,
    trusted_frontier: TrustedGitFrontier,
    trusted_authority: TrustedPlanAuthority,
    reviewer_attempt: int = DEFAULT_REVIEWER_ATTEMPT,
    review_cycle: ReviewCycle | str = ReviewCycle.RV1,
) -> AcceptedFrontierAuthorization:
    """Mint the value object from a trusted bound Plan source and Git frontier."""

    if not isinstance(state, TaskMainCoordinatorState):
        raise TypeError(f"state must be TaskMainCoordinatorState, got {type(state).__name__}")
    if not isinstance(live_plan_view, MilestonePlanView):
        raise TypeError(f"live_plan_view must be MilestonePlanView, got {type(live_plan_view).__name__}")
    if not isinstance(trusted_frontier, TrustedGitFrontier):
        raise TypeError(
            f"trusted_frontier must be TrustedGitFrontier, got {type(trusted_frontier).__name__}"
        )
    if not isinstance(trusted_authority, TrustedPlanAuthority):
        raise TypeError(
            f"trusted_authority must be TrustedPlanAuthority, got {type(trusted_authority).__name__}"
        )
    if plan_id != trusted_authority.plan_id:
        raise ReviewAdoptionError("minted authorization Plan identity does not match the trusted authority")
    try:
        reviewer_attempt = normalize_reviewer_attempt(reviewer_attempt)
    except (TypeError, ValueError) as exc:
        raise ReviewAdoptionError(str(exc)) from exc
    try:
        review_cycle = normalize_review_cycle(review_cycle)
    except (TypeError, ValueError) as exc:
        raise ReviewAdoptionError(str(exc)) from exc
    try:
        _check_live_binding(state, live_plan_view)
    except PlanDriftError:
        raise
    except Exception as exc:
        raise ReviewAdoptionError(f"trusted runtime Plan binding failed: {exc}") from exc
    canonical_ref = _canonical_authorization_ref(
        trusted_authority,
        milestone_id=live_plan_view.milestone_id,
        frontier_ref=trusted_frontier.frontier_ref,
    )
    if authorization_ref is not None and authorization_ref != canonical_ref:
        raise ReviewAdoptionError("authorization_ref must be derived from the trusted Plan authority")
    authorization = AcceptedFrontierAuthorization(
        project_id=state.project_id,
        plan_id=plan_id,
        plan_authority=live_plan_view.plan_authority,
        authority_source_kind=PLAN_AUTHORITY_SOURCE_LOCAL_GOVERNANCE,
        plan_digest=live_plan_view.plan_digest,
        plan_source_revision=live_plan_view.plan_source_revision or "",
        milestone_id=live_plan_view.milestone_id,
        entry_base=live_plan_view.entry_base,
        candidate_frontier_ref=trusted_frontier.frontier_ref,
        authorization_ref=canonical_ref,
        reviewer_canonical_task_id=_expected_reviewer_task_id(
            state.project_id,
            state.milestone_id,
            reviewer_attempt,
            review_cycle=review_cycle,
        ),
        review_cycle=review_cycle,
    )
    authorization = replace(
        authorization,
        provenance_digest=_authorization_provenance_digest(authorization, trusted_frontier),
    )
    _validate_authorization(
        state=state,
        view=live_plan_view,
        authorization=authorization,
        frontier=trusted_frontier,
        trusted_authority=trusted_authority,
    )
    return authorization


def _require_no_active_execution(execution_store: ExecutionStateStore) -> None:
    for record in execution_store.list_all():
        if not record.canonical_task_state.is_terminal:
            raise ReviewAdoptionError(
                f"active execution {record.canonical_task_id!r} blocks review-only adoption"
            )


def _require_no_conflicting_active_execution(
    execution_store: ExecutionStateStore,
    allowed_reviewer_task_id: str,
) -> None:
    for record in execution_store.list_all():
        if (
            not record.canonical_task_state.is_terminal
            and record.canonical_task_id != allowed_reviewer_task_id
        ):
            raise ReviewAdoptionError(
                f"active execution {record.canonical_task_id!r} conflicts with review-only adoption"
            )


def _adoption_updates(
    *,
    authorization: AcceptedFrontierAuthorization,
    frontier: TrustedGitFrontier,
    proof_digest: str,
) -> dict[str, dict[str, Any]]:
    return {
        "review_gates": {
            "ref": authorization.authorization_ref,
            "digest": proof_digest,
            "freshness": "trusted_git_clean_descendant",
            "reason": _authorization_provenance_reason(authorization, frontier),
            "status": REVIEW_ONLY_GATE_STATUS,
            "required": True,
            "integrated_state": REVIEW_ONLY_ADOPTION_STATE,
        },
        "integrated_review": {
            "ref": authorization.reviewer_canonical_task_id,
            "digest": proof_digest,
            "freshness": "exact_authorized_frontier",
            "status": REVIEW_ONLY_ADOPTION_STATUS,
            "required": True,
            "integrated_state": REVIEW_ONLY_ADOPTION_STATE,
        },
        "frontier_ref": {
            "ref": authorization.candidate_frontier_ref,
            "digest": proof_digest,
            "freshness": "trusted_git_clean_descendant",
            "status": REVIEW_ONLY_FRONTIER_STATUS,
            "required": True,
            "integrated_state": REVIEW_ONLY_ADOPTION_STATE,
        },
    }


def _existing_exact_adoption(
    state: TaskMainCoordinatorState,
    updates: Mapping[str, Mapping[str, Any]],
) -> bool:
    for field, value in updates.items():
        current = getattr(state, field)
        expected = dict(value)
        if field == "integrated_review" and isinstance(current, Mapping):
            # Dispatch adds its exact reviewer binding after the adoption
            # marker is written; that binding is part of the same marker.
            current = dict(current)
            current.pop("reviewer_binding", None)
        if current != expected:
            return False
    return True


def adopt_accepted_frontier_for_review(
    *,
    store: TaskMainCoordinatorStore,
    execution_store: ExecutionStateStore,
    coordinator_id: str,
    live_plan_view: MilestonePlanView,
    authorization: AcceptedFrontierAuthorization,
    worktree_root: WorktreeSandboxBoundary,
    git: GitPort | None = None,
    trusted_authority: TrustedPlanAuthority | None = None,
) -> TaskMainCoordinatorState:
    """CAS-adopt an exact clean Git frontier for the existing RV1 path.

    Only the coordinator's existing review/frontier working fields are
    modified.  A repeated identical call returns the durable state unchanged;
    a conflicting marker or any execution/progression drift fails closed.
    """

    if not isinstance(store, TaskMainCoordinatorStore):
        raise TypeError(f"store must be TaskMainCoordinatorStore, got {type(store).__name__}")
    if not isinstance(execution_store, ExecutionStateStore):
        raise TypeError(
            f"execution_store must be ExecutionStateStore, got {type(execution_store).__name__}"
        )
    if not isinstance(live_plan_view, MilestonePlanView):
        raise TypeError(
            f"live_plan_view must be MilestonePlanView, got {type(live_plan_view).__name__}"
        )
    if not isinstance(authorization, AcceptedFrontierAuthorization):
        raise TypeError(
            "authorization must be AcceptedFrontierAuthorization, "
            f"got {type(authorization).__name__}"
        )
    if not isinstance(trusted_authority, TrustedPlanAuthority):
        raise TypeError("trusted_authority must be TrustedPlanAuthority")
    coordinator_id = _require_non_empty_str(coordinator_id, "coordinator_id")
    state = store.get(coordinator_id)
    if state is None:
        raise ReviewAdoptionError(f"coordinator {coordinator_id!r} does not exist")
    try:
        _check_live_binding(state, live_plan_view)
    except PlanDriftError:
        raise
    except Exception as exc:
        raise ReviewAdoptionError(f"live Plan binding failed closed: {exc}") from exc
    if state.status != CoordinatorStatus.ACTIVE:
        raise ReviewAdoptionError(f"coordinator status {state.status.value!r} blocks adoption")
    if not state.user_approval_satisfied or live_plan_view.user_gate_blocked:
        raise ReviewAdoptionError("Milestone approval or live user gate blocks adoption")
    if state.human_brake is not None and state.human_brake.get("state") != "NONE":
        raise ReviewAdoptionError("Human Brake blocks adoption")

    if any(status != WorkItemCoordinatorStatus.PENDING.value for status in state.wi_status.values()):
        raise ReviewAdoptionError("existing Work Item progression blocks review-only adoption")
    if state.bindings or state.wi_semantic_status:
        raise ReviewAdoptionError("existing Work Item execution/reconciliation truth blocks adoption")
    if state.work_projections:
        raise ReviewAdoptionError("existing Work Item working truth blocks adoption")

    frontier = read_trusted_git_frontier(
        worktree_root,
        entry_base=authorization.entry_base,
        git=git,
    )
    _validate_authorization(
        state=state,
        view=live_plan_view,
        authorization=authorization,
        frontier=frontier,
        trusted_authority=trusted_authority,
    )
    if not _has_trusted_authorization_provenance(authorization, frontier):
        raise ReviewAdoptionError(
            "accepted frontier authorization lacks trusted runtime provenance"
        )
    proof_digest = _proof_digest(authorization, frontier)
    updates = _adoption_updates(
        authorization=authorization,
        frontier=frontier,
        proof_digest=proof_digest,
    )
    if state.integrated_review is not None or state.review_gates is not None or state.frontier_ref is not None:
        stored_authorization, stored_frontier = _authorization_provenance_from_reason(
            state.review_gates.get("reason")
            if isinstance(state.review_gates, Mapping)
            else None,
            state=state,
        )
        _validate_authorization_against_state(
            state=state,
            authorization=stored_authorization,
            frontier=stored_frontier,
            trusted_authority=trusted_authority,
        )
        if stored_authorization == authorization and stored_frontier == frontier:
            _require_no_conflicting_active_execution(
                execution_store,
                authorization.reviewer_canonical_task_id,
            )
            allowed_review_tasks = {
                stored_authorization.reviewer_canonical_task_id,
                authorization.reviewer_canonical_task_id,
            }
            if authorization.review_cycle is ReviewCycle.RV2:
                # The preceding RV1 may have used a different bounded retry
                # attempt. Preserve the actual durable RV1 receipt identity
                # instead of deriving it from the new RV2 task.
                for completion in state.reconciled_completions.values():
                    if not isinstance(completion, Mapping):
                        continue
                    if completion.get("completion_kind") != "MILESTONE_REVIEW":
                        continue
                    task_id = completion.get("canonical_task_id")
                    try:
                        if review_cycle_from_canonical_task_id(task_id) is ReviewCycle.RV1:
                            allowed_review_tasks.add(task_id)
                    except (TypeError, ValueError):
                        raise ReviewAdoptionError(
                            "review-only adoption contains an invalid reviewer completion identity"
                        ) from None
            for payload in state.reconciled_completions.values():
                if (
                    not isinstance(payload, Mapping)
                    or payload.get("completion_kind") != "MILESTONE_REVIEW"
                    or payload.get("canonical_task_id") not in allowed_review_tasks
                ):
                    raise ReviewAdoptionError(
                        "review-only adoption contains non-review completion truth"
                    )
            return state

        if (
            stored_authorization.review_cycle is ReviewCycle.RV1
            and authorization.review_cycle is ReviewCycle.RV2
        ):
            if stored_frontier.frontier_ref == frontier.frontier_ref:
                raise ReviewAdoptionError(
                    "RV2 adoption must use a changed trusted frontier"
                )
            prior_receipt_found = False
            from aota_forge.runtime.task_main.reconciliation import (
                CompletionReconciliationReceipt,
            )
            for payload in state.reconciled_completions.values():
                try:
                    receipt = CompletionReconciliationReceipt.from_dict(payload)
                except (TypeError, ValueError) as exc:
                    raise ReviewAdoptionError(
                        f"durable review receipt is invalid during RV2 rollover: {exc}"
                    ) from exc
                if receipt.completion_kind != "MILESTONE_REVIEW":
                    raise ReviewAdoptionError(
                        "RV2 adoption cannot roll over Work Item completion truth"
                    )
                if receipt.canonical_task_id == stored_authorization.reviewer_canonical_task_id:
                    prior_receipt_found = True
            if not prior_receipt_found:
                raise ReviewAdoptionError(
                    "RV2 adoption requires the durable RV1 review receipt"
                )
            _require_no_conflicting_active_execution(
                execution_store,
                authorization.reviewer_canonical_task_id,
            )
            try:
                return store.compare_and_swap(
                    coordinator_id,
                    state.coordinator_revision,
                    updates,
                    state.revision_token,
                )
            except StaleCoordinatorRevisionError:
                refreshed = store.get(coordinator_id)
                if refreshed is not None and _existing_exact_adoption(refreshed, updates):
                    return refreshed
                raise ReviewAdoptionError("RV2 adoption CAS lost a conflicting race") from None
            except Exception as exc:
                raise ReviewAdoptionError(f"RV2 adoption CAS failed: {exc}") from exc

        if stored_authorization != authorization or stored_frontier != frontier:
            raise ReviewAdoptionError(
                "conflicting durable authorization provenance blocks adoption"
            )
        raise ReviewAdoptionError("conflicting coordinator review/frontier state blocks adoption")

    if state.reconciled_completions or state.working_truth is not None:
        raise ReviewAdoptionError("existing Work Item working truth blocks adoption")

    _require_no_active_execution(execution_store)
    try:
        return store.compare_and_swap(
            coordinator_id,
            state.coordinator_revision,
            updates,
            state.revision_token,
        )
    except StaleCoordinatorRevisionError:
        refreshed = store.get(coordinator_id)
        if refreshed is not None and _existing_exact_adoption(refreshed, updates):
            return refreshed
        raise ReviewAdoptionError("review-only adoption CAS lost a conflicting race") from None
    except Exception as exc:
        raise ReviewAdoptionError(f"review-only adoption CAS failed: {exc}") from exc


def review_only_adoption_from_state(
    state: TaskMainCoordinatorState,
    *,
    trusted_authority: TrustedPlanAuthority | None = None,
) -> ReviewOnlyAdoption | None:
    """Parse and cross-check the existing optional adoption marker."""

    if not isinstance(state, TaskMainCoordinatorState):
        raise TypeError(f"state must be TaskMainCoordinatorState, got {type(state).__name__}")
    fields = (state.review_gates, state.integrated_review, state.frontier_ref)
    if all(value is None for value in fields):
        return None
    if not isinstance(trusted_authority, TrustedPlanAuthority):
        raise ReviewAdoptionError(
            "durable review-only adoption requires a trusted Plan authority binding"
        )
    if any(not isinstance(value, Mapping) for value in fields):
        raise ReviewAdoptionError("review-only adoption marker is malformed")
    gates, review, frontier = fields
    assert gates is not None and review is not None and frontier is not None
    expected_state = REVIEW_ONLY_ADOPTION_STATE
    if gates.get("status") != REVIEW_ONLY_GATE_STATUS or review.get("status") != REVIEW_ONLY_ADOPTION_STATUS:
        raise ReviewAdoptionError("coordinator review marker is not a review-only adoption")
    if frontier.get("status") != REVIEW_ONLY_FRONTIER_STATUS:
        raise ReviewAdoptionError("coordinator frontier marker is not review-only")
    if any(
        value.get("integrated_state") != expected_state or value.get("required") is not True
        for value in (gates, review, frontier)
    ):
        raise ReviewAdoptionError("review-only adoption marker has conflicting state")
    if gates.get("freshness") != "trusted_git_clean_descendant":
        raise ReviewAdoptionError("review-only gate marker has invalid freshness")
    if review.get("freshness") != "exact_authorized_frontier":
        raise ReviewAdoptionError("review-only reviewer marker has invalid freshness")
    if frontier.get("freshness") != "trusted_git_clean_descendant":
        raise ReviewAdoptionError("review-only frontier marker has invalid freshness")
    refs = (gates.get("ref"), review.get("ref"), frontier.get("ref"))
    if any(not isinstance(value, str) or not value.strip() for value in refs):
        raise ReviewAdoptionError("review-only adoption marker has missing refs")
    digests = (gates.get("digest"), review.get("digest"), frontier.get("digest"))
    if any(not isinstance(value, str) or not value.strip() for value in digests):
        raise ReviewAdoptionError("review-only adoption marker has missing proof digest")
    if len(set(digests)) != 1 or not _SHA256_RE.fullmatch(digests[0]):
        raise ReviewAdoptionError("review-only adoption marker proof digests conflict")
    authorization, authorized_frontier = _authorization_provenance_from_reason(
        gates.get("reason"), state=state
    )
    _validate_authorization_against_state(
        state=state,
        authorization=authorization,
        frontier=authorized_frontier,
        trusted_authority=trusted_authority,
    )
    if not _has_trusted_authorization_provenance(authorization, authorized_frontier):
        raise ReviewAdoptionError("durable authorization provenance digest is invalid")
    adoption = ReviewOnlyAdoption(
        authorization_ref=refs[0],
        reviewer_canonical_task_id=refs[1],
        frontier_ref=refs[2],
        proof_digest=digests[0],
    )
    try:
        reviewer_attempt = adoption.reviewer_attempt
    except (TypeError, ValueError) as exc:
        raise ReviewAdoptionError(str(exc)) from exc
    if (
        authorization.authorization_ref != adoption.authorization_ref
        or authorization.reviewer_canonical_task_id != adoption.reviewer_canonical_task_id
        or authorized_frontier.frontier_ref != adoption.frontier_ref
        or (
            _proof_digest(authorization, authorized_frontier) != adoption.proof_digest
            and (
                authorization.review_cycle is not ReviewCycle.RV1
                or _legacy_proof_digest(authorization, authorized_frontier)
                != adoption.proof_digest
            )
        )
    ):
        raise ReviewAdoptionError("review-only adoption marker disagrees with durable authorization facts")
    if adoption.reviewer_canonical_task_id != _expected_reviewer_task_id(
        state.project_id,
        state.milestone_id,
        reviewer_attempt,
        review_cycle=adoption.review_cycle,
    ):
        raise ReviewAdoptionError("review-only marker reviewer identity is not canonical")
    reviewer_binding = review.get("reviewer_binding")
    if reviewer_binding is not None:
        if not isinstance(reviewer_binding, Mapping):
            raise ReviewAdoptionError("review-only reviewer binding is malformed")
        binding_checks = (
            (reviewer_binding.get("canonical_task_id"), adoption.reviewer_canonical_task_id, "reviewer task"),
            (reviewer_binding.get("attempt"), reviewer_attempt, "reviewer attempt"),
            (reviewer_binding.get("role"), AgentWorkRole.REVIEWER.value, "reviewer role"),
            (reviewer_binding.get("project_id"), state.project_id, "reviewer project"),
            (reviewer_binding.get("plan_authority"), state.plan_authority, "reviewer Plan authority"),
            (reviewer_binding.get("plan_digest"), state.plan_digest, "reviewer Plan digest"),
            (reviewer_binding.get("plan_source_revision"), state.plan_source_revision, "reviewer Plan source revision"),
            (reviewer_binding.get("milestone_id"), state.milestone_id, "reviewer Milestone"),
            (reviewer_binding.get("frontier_ref"), adoption.frontier_ref, "reviewer frontier"),
            (
                reviewer_binding.get("review_cycle"),
                adoption.review_cycle.value,
                "review cycle",
            ),
        )
        for supplied, expected, label in binding_checks:
            if supplied != expected:
                raise ReviewAdoptionError(f"{label} binding mismatch")
        handoff_ref = reviewer_binding.get("handoff_ref")
        handoff_digest = reviewer_binding.get("handoff_digest")
        if not isinstance(handoff_ref, str) or not handoff_ref.startswith("handoff://"):
            raise ReviewAdoptionError("reviewer binding has no durable handoff_ref")
        try:
            _require_sha256(handoff_digest, "reviewer binding handoff_digest")
        except (TypeError, ValueError) as exc:
            raise ReviewAdoptionError(str(exc)) from exc
    return adoption


def revalidate_adopted_frontier(
    *,
    state: TaskMainCoordinatorState,
    live_plan_view: MilestonePlanView,
    worktree_root: WorktreeSandboxBoundary | None,
    git: GitPort | None = None,
    trusted_authority: TrustedPlanAuthority | None = None,
) -> TrustedGitFrontier:
    """Re-observe the adopted frontier immediately before review work."""

    if not isinstance(state, TaskMainCoordinatorState):
        raise TypeError(f"state must be TaskMainCoordinatorState, got {type(state).__name__}")
    if not isinstance(live_plan_view, MilestonePlanView):
        raise TypeError(f"live_plan_view must be MilestonePlanView, got {type(live_plan_view).__name__}")
    if not isinstance(trusted_authority, TrustedPlanAuthority):
        raise TypeError("trusted_authority must be TrustedPlanAuthority")
    adoption = review_only_adoption_from_state(state, trusted_authority=trusted_authority)
    if adoption is None:
        raise ReviewAdoptionError("review frontier revalidation requires review-only adoption")
    try:
        _check_live_binding(state, live_plan_view)
    except PlanDriftError:
        raise
    except Exception as exc:
        raise ReviewAdoptionError(f"live Plan binding failed closed: {exc}") from exc
    if worktree_root is None:
        raise ReviewAdoptionError(
            "review-only adoption requires trusted source revalidation before dispatch/reconciliation"
        )
    authorization, authorized_frontier = _authorization_provenance_from_reason(
        state.review_gates.get("reason")
        if isinstance(state.review_gates, Mapping)
        else None,
        state=state,
    )
    frontier = read_trusted_git_frontier(
        worktree_root,
        entry_base=state.entry_base,
        git=git,
    )
    if frontier.canonical_dict() != authorized_frontier.canonical_dict():
        raise ReviewAdoptionError(
            f"adopted trusted Git observation changed: marker {authorized_frontier.frontier_ref!r}"
            f"/{authorized_frontier.tree_ref!r} vs trusted {frontier.frontier_ref!r}/{frontier.tree_ref!r}"
        )
    _validate_authorization_against_state(
        state=state,
        authorization=authorization,
        frontier=frontier,
        trusted_authority=trusted_authority,
    )
    if not _has_trusted_authorization_provenance(authorization, frontier):
        raise ReviewAdoptionError("durable authorization provenance digest is invalid")
    if _proof_digest(authorization, frontier) != adoption.proof_digest:
        raise ReviewAdoptionError("review-only proof digest no longer matches durable authorization")
    return frontier


def build_review_dispatch_binding(
    *,
    state: TaskMainCoordinatorState,
    adoption: ReviewOnlyAdoption,
    handoff: TaskHandoff,
    handoff_ref: str | None = None,
    handoff_digest: str | None = None,
) -> dict[str, Any]:
    """Build the exact reviewer identity persisted before physical dispatch."""

    if not isinstance(state, TaskMainCoordinatorState):
        raise TypeError(f"state must be TaskMainCoordinatorState, got {type(state).__name__}")
    if not isinstance(adoption, ReviewOnlyAdoption):
        raise TypeError(f"adoption must be ReviewOnlyAdoption, got {type(adoption).__name__}")
    if not isinstance(handoff, TaskHandoff):
        raise TypeError(f"handoff must be TaskHandoff, got {type(handoff).__name__}")
    if handoff.work_role != AgentWorkRole.REVIEWER:
        raise ReviewAdoptionError("integrated reviewer handoff must have reviewer role")
    if handoff.project_ref is not None and handoff.project_ref.ref != state.project_id:
        raise ReviewAdoptionError("reviewer handoff project identity contradicts coordinator")
    if handoff.plan_ref is not None:
        if handoff.plan_ref.ref != state.plan_authority:
            raise ReviewAdoptionError("reviewer handoff Plan authority contradicts coordinator")
        if handoff.plan_ref.digest is not None and handoff.plan_ref.digest != state.plan_digest:
            raise ReviewAdoptionError("reviewer handoff Plan digest contradicts coordinator")
    if handoff.milestone_ref is not None and handoff.milestone_ref.ref != state.milestone_id:
        raise ReviewAdoptionError("reviewer handoff Milestone contradicts coordinator")
    if handoff_ref is None or handoff_digest is None:
        raise ReviewAdoptionError(
            "reviewer durable handoff ref/digest are required before physical dispatch"
        )
    handoff_ref = _require_non_empty_str(handoff_ref, "reviewer handoff_ref")
    if not handoff_ref.startswith("handoff://"):
        raise ReviewAdoptionError("reviewer handoff_ref is not a durable handoff reference")
    handoff_digest = _require_sha256(handoff_digest, "reviewer handoff_digest")
    return {
        "canonical_task_id": adoption.reviewer_canonical_task_id,
        "attempt": adoption.reviewer_attempt,
        "role": AgentWorkRole.REVIEWER.value,
        "project_id": state.project_id,
        "plan_authority": state.plan_authority,
        "plan_digest": state.plan_digest,
        "plan_source_revision": state.plan_source_revision,
        "milestone_id": state.milestone_id,
        "frontier_ref": adoption.frontier_ref,
        "review_cycle": adoption.review_cycle.value,
        "task_handoff_digest": handoff.compute_handoff_digest(),
        "handoff_ref": handoff_ref,
        "handoff_digest": handoff_digest,
    }


def record_review_dispatch_binding(
    *,
    store: TaskMainCoordinatorStore,
    coordinator_id: str,
    binding: Mapping[str, Any],
    trusted_authority: TrustedPlanAuthority | None = None,
) -> TaskMainCoordinatorState:
    """CAS-bind reviewer identity before the reviewer can be dispatched."""

    if not isinstance(store, TaskMainCoordinatorStore):
        raise TypeError(f"store must be TaskMainCoordinatorStore, got {type(store).__name__}")
    coordinator_id = _require_non_empty_str(coordinator_id, "coordinator_id")
    if not isinstance(binding, Mapping):
        raise TypeError(f"binding must be a mapping, got {type(binding).__name__}")
    if not isinstance(trusted_authority, TrustedPlanAuthority):
        raise TypeError("trusted_authority must be TrustedPlanAuthority")
    handoff_ref = binding.get("handoff_ref")
    handoff_digest = binding.get("handoff_digest")
    if not isinstance(handoff_ref, str) or not handoff_ref.startswith("handoff://"):
        raise ReviewAdoptionError("reviewer durable binding requires a handoff_ref")
    try:
        handoff_digest = _require_sha256(
            handoff_digest, "reviewer durable binding handoff_digest"
        )
    except (TypeError, ValueError) as exc:
        raise ReviewAdoptionError(str(exc)) from exc
    state = store.get(coordinator_id)
    if state is None:
        raise ReviewAdoptionError(f"coordinator {coordinator_id!r} does not exist")
    from aota_forge.work_plane.handoff_store import HandoffRef

    try:
        parsed_ref = HandoffRef.from_value(handoff_ref)
    except (TypeError, ValueError) as exc:
        raise ReviewAdoptionError(f"reviewer durable binding handoff_ref is malformed: {exc}") from exc
    if (
        parsed_ref.ref != handoff_ref
        or parsed_ref.mode != "work_item"
        or parsed_ref.project_id != state.project_id
        or parsed_ref.worktree_id == ""
        or parsed_ref.artifact_id == ""
        or parsed_ref.digest != handoff_digest
    ):
        raise ReviewAdoptionError(
            "reviewer durable binding handoff_ref does not match its canonical identity"
        )
    review_only_adoption_from_state(state, trusted_authority=trusted_authority)
    current_marker = state.integrated_review
    if not isinstance(current_marker, Mapping):
        raise ReviewAdoptionError("review-only adoption has no integrated-review marker")
    existing = current_marker.get("reviewer_binding")
    if existing is not None:
        if dict(existing) != dict(binding):
            raise ReviewAdoptionError("conflicting durable reviewer binding blocks dispatch")
        return state
    updated_marker = dict(current_marker)
    updated_marker["reviewer_binding"] = dict(binding)
    try:
        return store.compare_and_swap(
            coordinator_id,
            state.coordinator_revision,
            {"integrated_review": updated_marker},
            state.revision_token,
        )
    except StaleCoordinatorRevisionError:
        refreshed = store.get(coordinator_id)
        if refreshed is not None and isinstance(refreshed.integrated_review, Mapping):
            winner = refreshed.integrated_review.get("reviewer_binding")
            if isinstance(winner, Mapping) and dict(winner) == dict(binding):
                return refreshed
        raise ReviewAdoptionError("reviewer binding CAS lost a conflicting race") from None


def review_only_progression_for(
    state: TaskMainCoordinatorState,
    adoption: ReviewOnlyAdoption,
) -> ProgressionDisposition:
    """Build a transient closure input without persisting Work completion."""

    if not isinstance(adoption, ReviewOnlyAdoption):
        raise TypeError("adoption must be ReviewOnlyAdoption")
    refs = tuple(sorted(state.work_items))
    return ProgressionDisposition(
        progression_complete_work_item_refs=refs,
        ready_work_item_refs=(),
        blocked_work_item_refs=(),
        milestone_review_ready=True,
        auto_progression_allowed=False,
        reasons=(
            "review-only frontier adoption supplies no Work Item history; "
            "completion is not persisted",
        ),
        evidence_refs=(adoption.proof_digest,),
    )


__all__ = [
    "NO_FAKE_WORK_COMPLETION_RECEIPTS",
    "NO_FAKE_WORK_COORDINATOR_STATE",
    "NO_NEW_REVIEW_ADOPTION_PROTOCOL",
    "NO_PUBLIC_REVIEW_ADOPTION_OPERATION",
    "NO_WORK_REDISPATCH_DURING_ADOPTION",
    "REVIEW_ONLY_ADOPTION_ENABLED",
    "REVIEW_ONLY_ADOPTION_STATE",
    "REVIEW_ONLY_ADOPTION_STATUS",
    "REVIEW_ONLY_FRONTIER_STATUS",
    "AcceptedFrontierAuthorization",
    "DEFAULT_REVIEWER_ATTEMPT",
    "ReviewAdoptionError",
    "ReviewOnlyAdoption",
    "TrustedPlanAuthority",
    "TrustedGitFrontier",
    "adopt_accepted_frontier_for_review",
    "build_review_dispatch_binding",
    "mint_accepted_frontier_authorization",
    "normalize_reviewer_attempt",
    "normalize_review_cycle",
    "read_trusted_git_frontier",
    "record_review_dispatch_binding",
    "revalidate_adopted_frontier",
    "reviewer_attempt_from_canonical_task_id",
    "review_cycle_from_canonical_task_id",
    "reviewer_task_id",
    "review_only_adoption_from_state",
    "review_only_progression_for",
    "trusted_plan_authority_from_binding",
]
