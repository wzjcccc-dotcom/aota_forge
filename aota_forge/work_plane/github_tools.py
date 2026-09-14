"""Agent-Facing GitHub Governance Operations — AF #54 M5/W2.

Four canonical, thin, Plan-bound GitHub Issue operations exposed through the
existing single Agent-facing entry ``aota.invoke``:

    github.issue.read            github.issue.comments.read
    github.issue.update          github.issue.comment.update

Hard invariants
---------------
* NO_GENERIC_GITHUB_API=yes — there is no github.api/github.exec/github.request
  surface; only these four semantic operations exist.
* RAW_GH_NEVER_MODEL_VISIBLE=yes — the trusted gh backend is adapter-private;
  the model never composes a gh command, endpoint, repo, owner or issue.
* PLAN_BOUND_TARGETS_ONLY=yes — repo/owner/issue_number are grounded from the
  trusted current-Plan binding carried by the runtime binding, never from
  model input (the model does not repeat owner=/repo=/issue= per call).
* CROSS_PLAN_GITHUB_OPERATION_FAIL_CLOSED=yes — every mutation target (and
  every comment_id) is verified mechanically against the bound Issue.
* GITHUB_MUTATION_AUTHORITY_REQUIRED=yes — mutation needs trusted
  GitHubOperationAuthorityEvidence minted only for task-main.
* GITHUB_MUTATION_CAS_GUARDED=yes — stale reads cannot silently overwrite
  newer authoritative content (expected updated_at for the Issue body/state,
  expected body digest for comments; read-back verification after write).
* EXISTING_GOVERNANCE_MECHANICS_REUSED=yes — the trusted gh transport reuses
  the stable operator gh mechanics (GH_CONFIG_DIR + rtk gh, typed endpoints,
  structured argv, timeout, bounded output) already owned by
  adapters/plan_authority/github_read.py and work_plane/steward_finalizer.py
  (GhCliGitHubPort); no new auth system, no second Plan authority.
* CONTROL_PLANE_NEVER_NAMES_COMMENTS=yes — the Control Plane returns raw
  comment facts (id/author/updated_at/bounded body); deciding which comment is
  the progress index / decision log / etc. is task-main Skill semantics.
* CONTROL_PLANE_IS_GITHUB_WORKFLOW_DECISION_OWNER=no
* GITHUB_VIA_RESTRICTED_SHELL=no — this surface is dedicated; the restricted
  shell stays the residual fallback for unrelated needs.
* LARGE_BODY_CARD_FIRST=yes — reads default to a bounded card projection;
  larger payloads flow through the existing governed by_ref/result.hydrate
  transport. No second hydration framework.
* ONE_AGENT_FACING_AOTA_MCP_TOOL=yes — operations ride the existing
  single-entry transport; no new MCP tool is created.

Reuse
-----
* OperationContractDescriptor / ToolProvider-style invoke / ToolResponse
* WorktreeSandboxBoundary + TaskHandoff (identity + evidence digests)
* gh transport constants + JSON parsing from the existing plan-authority
  reader and the finalizer's GhCliGitHubPort.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import subprocess
import tempfile
from dataclasses import dataclass
from typing import Any, Mapping

from aota_forge.adapters.plan_authority.github_read import (
    GH_CONFIG_DEFAULT,
    GH_CONFIG_ENV,
    GH_RTK_BIN,
)
from aota_forge.core.contracts.descriptor import OperationContractDescriptor, READ_ONLY, WRITE_ONLY
from aota_forge.core.contracts.loader import discover_canonical_project_root, load_operation_descriptor_map
from aota_forge.core.contracts.errors import ForgeError
from aota_forge.core.providers.tool import ToolRequest, ToolResponse
from aota_forge.work_plane.handoff import TaskHandoff
from aota_forge.work_plane.worktree_sandbox import WorktreeSandboxBoundary

# ---------------------------------------------------------------------------
# Invariant flags (importable for tests / downstream seams)
# ---------------------------------------------------------------------------

NO_GENERIC_GITHUB_API: bool = True
GENERIC_GITHUB_ADAPTER_EXPOSED_TO_MODEL: bool = False
RAW_GH_NEVER_MODEL_VISIBLE: bool = True
PLAN_BOUND_TARGETS_ONLY: bool = True
CROSS_PLAN_GITHUB_OPERATION_FAIL_CLOSED: bool = True
GITHUB_MUTATION_AUTHORITY_REQUIRED: bool = True
GITHUB_MUTATION_CAS_GUARDED: bool = True
EXISTING_GOVERNANCE_MECHANICS_REUSED: bool = True
CONTROL_PLANE_NEVER_NAMES_COMMENTS: bool = True
CONTROL_PLANE_IS_GITHUB_WORKFLOW_DECISION_OWNER: bool = False
GITHUB_VIA_RESTRICTED_SHELL: bool = False
LARGE_BODY_CARD_FIRST: bool = True
NEW_RESULT_HYDRATION_FRAMEWORK_CREATED: bool = False
NEW_PERMISSION_ENGINE_CREATED: bool = False

# ---------------------------------------------------------------------------
# Bounds (M5 local constants)
# ---------------------------------------------------------------------------

MAX_COMMENTS_RETURNED: int = 100
# Governance 1.x read-before-write requires that managed comment bodies be
# fully readable by task-main (a comment update replaces the whole body).
# Per-comment truncation would break that; instead the whole result stays
# bounded through the EXISTING governed by_ref/result.hydrate transport
# (durable bound 64 KiB). Only genuinely oversized results degrade with an
# explicit truncation marker.
MAX_COMMENT_INLINE_BYTES: int = 32 * 1024
MAX_COMMENTS_RESULT_BYTES: int = 60 * 1024
MAX_ISSUE_CARD_BYTES: int = 8192
MAX_ISSUE_FULL_BYTES: int = 60 * 1024
MAX_UPDATE_BODY_BYTES: int = 64 * 1024
MAX_COMMENT_ID_LENGTH: int = 64
GH_READ_TIMEOUT: int = 20
GH_WRITE_TIMEOUT: int = 25

_SAFE_REPO_RE = re.compile(r"^[A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+$")
_SAFE_COMMENT_ID_RE = re.compile(r"^[0-9]{1,20}$")

# ---------------------------------------------------------------------------
# Canonical descriptors (single authority: .aota/contracts/operations.yaml)
# ---------------------------------------------------------------------------


def _load_canonical_descriptor(name: str) -> OperationContractDescriptor:
    root = discover_canonical_project_root()
    return load_operation_descriptor_map(root)[name]


GITHUB_ISSUE_READ_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("github.issue.read")
GITHUB_ISSUE_COMMENTS_READ_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("github.issue.comments.read")
GITHUB_ISSUE_UPDATE_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("github.issue.update")
GITHUB_ISSUE_COMMENT_UPDATE_DESCRIPTOR: OperationContractDescriptor = _load_canonical_descriptor("github.issue.comment.update")

assert GITHUB_ISSUE_READ_DESCRIPTOR.read_write == READ_ONLY
assert GITHUB_ISSUE_COMMENTS_READ_DESCRIPTOR.read_write == READ_ONLY
assert GITHUB_ISSUE_UPDATE_DESCRIPTOR.read_write == WRITE_ONLY
assert GITHUB_ISSUE_COMMENT_UPDATE_DESCRIPTOR.read_write == WRITE_ONLY

GITHUB_READ_OPERATIONS: tuple[str, ...] = ("github.issue.read", "github.issue.comments.read")
GITHUB_MUTATION_OPERATIONS: tuple[str, ...] = ("github.issue.update", "github.issue.comment.update")
ALL_GITHUB_OPERATIONS: tuple[str, ...] = GITHUB_READ_OPERATIONS + GITHUB_MUTATION_OPERATIONS
_ALLOWED_GITHUB_OPERATIONS: frozenset[str] = frozenset(ALL_GITHUB_OPERATIONS)


# ---------------------------------------------------------------------------
# Errors — fail-closed
# ---------------------------------------------------------------------------


class GitHubAuthorityError(ValueError):
    """Missing or invalid trusted GitHub invocation context (fail-closed)."""


class GitHubOperationError(ValueError):
    """Bounded GitHub operation failure (fail-closed)."""

    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(message)


# ---------------------------------------------------------------------------
# Trusted Plan identity (mechanical grounding of owner/repo/issue)
# ---------------------------------------------------------------------------

_PLAN_REF_RE = re.compile(r"^([A-Za-z0-9_.\-]+/[A-Za-z0-9_.\-]+)#([1-9][0-9]{0,9})$")


def parse_plan_ref(plan_ref: str) -> tuple[str, str, int]:
    """Parse a trusted ``owner/repo#number`` Plan reference, fail closed."""
    if not isinstance(plan_ref, str):
        raise GitHubAuthorityError("plan_ref must be a string")
    match = _PLAN_REF_RE.fullmatch(plan_ref.strip())
    if match is None:
        raise GitHubAuthorityError(f"plan_ref must be 'owner/repo#number', got {plan_ref!r}")
    repo = match.group(1)
    issue_number = int(match.group(2))
    owner = repo.split("/", 1)[0]
    return repo, owner, issue_number


@dataclass(frozen=True)
class TrustedPlanGitHubBinding:
    """Trusted Plan identity grounded at task-main launch by the runtime.

    Carries the mechanical target facts (repo/owner/issue_number) that the
    Control Plane uses to bind every GitHub operation. It decides no policy,
    interprets no Plan and assigns no semantic name to any comment.
    """

    plan_ref: str
    repo: str
    owner: str
    issue_number: int

    def __post_init__(self) -> None:
        repo, owner, issue_number = parse_plan_ref(self.plan_ref)
        if self.repo != repo or self.owner != owner or self.issue_number != issue_number:
            raise GitHubAuthorityError("trusted plan GitHub binding fields disagree with plan_ref")
        if issue_number <= 0:
            raise GitHubAuthorityError("issue_number must be positive")

    @classmethod
    def from_plan_ref(cls, plan_ref: str) -> "TrustedPlanGitHubBinding":
        repo, owner, issue_number = parse_plan_ref(plan_ref)
        return cls(plan_ref=plan_ref.strip(), repo=repo, owner=owner, issue_number=issue_number)

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "plan_ref": self.plan_ref,
            "repo": self.repo,
            "owner": self.owner,
            "issue_number": self.issue_number,
        }


# ---------------------------------------------------------------------------
# Authority evidence — bounded value object (mirrors git_tools discipline)
# ---------------------------------------------------------------------------


def _role_value(role: object) -> str:
    return str(getattr(role, "value", role) or "")


@dataclass(frozen=True)
class GitHubOperationAuthorityEvidence:
    """Trusted operation-authority evidence for one GitHub governance op.

    Minted only by runtime composition for a task-main handoff with a bound
    Plan. Model-supplied dicts cannot self-assert; workers are never minted
    one under the current architecture.
    """

    sandbox: WorktreeSandboxBoundary
    handoff: TaskHandoff
    operation: OperationContractDescriptor
    plan: TrustedPlanGitHubBinding
    evidence_id: str

    def __post_init__(self) -> None:
        if not isinstance(self.sandbox, WorktreeSandboxBoundary):
            raise GitHubAuthorityError(f"sandbox must be WorktreeSandboxBoundary, got {type(self.sandbox).__name__}")
        if not isinstance(self.handoff, TaskHandoff):
            raise GitHubAuthorityError(f"handoff must be TaskHandoff, got {type(self.handoff).__name__}")
        if not isinstance(self.operation, OperationContractDescriptor):
            raise GitHubAuthorityError(f"operation must be OperationContractDescriptor, got {type(self.operation).__name__}")
        if self.operation.name not in _ALLOWED_GITHUB_OPERATIONS:
            raise GitHubAuthorityError(f"unsupported github operation: {self.operation.name!r}")
        if self.operation.name in GITHUB_READ_OPERATIONS and self.operation.read_write != READ_ONLY:
            raise GitHubAuthorityError("github read operation must be read-classified")
        if self.operation.name in GITHUB_MUTATION_OPERATIONS:
            if self.operation.read_write != WRITE_ONLY:
                raise GitHubAuthorityError("github mutation operation must be write-classified")
        if _role_value(self.handoff.work_role) != "task-main":
            raise GitHubAuthorityError(
                "github governance authority requires a trusted task-main handoff (worker roles are not minted one)"
            )
        if not isinstance(self.plan, TrustedPlanGitHubBinding):
            raise GitHubAuthorityError("github authority requires the trusted bound-Plan identity")
        if not isinstance(self.evidence_id, str) or not self.evidence_id.strip():
            raise GitHubAuthorityError("evidence_id must be non-empty string")
        self.operation.validate()

    def canonical_dict(self) -> dict[str, Any]:
        return {
            "evidence_id": self.evidence_id,
            "operation": self.operation.name,
            "contract_hash": self.operation.contract_hash(),
            "sandbox_digest": self.sandbox.compute_digest(),
            "handoff_digest": self.handoff.handoff_digest,
            "plan": self.plan.canonical_dict(),
        }

    def evidence_digest(self) -> str:
        from aota_forge.core.contracts.canonical import canonical_json

        return hashlib.sha256(canonical_json(self.canonical_dict()).encode("utf-8")).hexdigest()


def create_github_authority(
    sandbox: WorktreeSandboxBoundary,
    handoff: TaskHandoff,
    operation: OperationContractDescriptor,
    plan: TrustedPlanGitHubBinding,
    *,
    evidence_id: str | None = None,
) -> GitHubOperationAuthorityEvidence:
    """Create trusted GitHub operation-authority evidence (runtime-side only)."""
    if not isinstance(operation, OperationContractDescriptor):
        raise GitHubAuthorityError(f"operation must be OperationContractDescriptor, got {type(operation).__name__}")
    operation.validate()
    if operation.name not in _ALLOWED_GITHUB_OPERATIONS:
        raise GitHubAuthorityError(f"unsupported github operation: {operation.name!r}")
    if evidence_id is None:
        seed = f"{sandbox.compute_digest()}:{operation.contract_hash()}:{handoff.handoff_digest}:{plan.plan_ref}"
        evidence_id = "ghe-" + hashlib.sha256(seed.encode("utf-8")).hexdigest()[:16]
    return GitHubOperationAuthorityEvidence(
        sandbox=sandbox,
        handoff=handoff,
        operation=operation,
        plan=plan,
        evidence_id=evidence_id,
    )


# ---------------------------------------------------------------------------
# Trusted gh backend (adapter-private; reuses stable operator gh mechanics)
# ---------------------------------------------------------------------------


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


class GhCliGovernancePort:
    """Typed gh-backed GitHub Issue/comment seam (no generic request surface).

    Mirrors the documented operator gh contract (GH_CONFIG_DIR + rtk gh),
    structured argv, shell=False, timeout, JSON parsing reused from the
    existing plan-authority reader and the finalizer's GhCliGitHubPort.
    Only the four bound semantics below exist; the repo/issue come exclusively
    from the trusted plan binding supplied by the caller evidence.
    """

    def __init__(self, *, gh_bin: str = GH_RTK_BIN, gh_config_dir: str | None = None) -> None:
        self._gh_bin = gh_bin
        self._gh_config_dir = gh_config_dir or os.environ.get(GH_CONFIG_ENV) or GH_CONFIG_DEFAULT

    def _env(self) -> dict[str, str]:
        return {**os.environ, GH_CONFIG_ENV: self._gh_config_dir}

    def _run(self, args: list[str], *, timeout: int) -> subprocess.CompletedProcess[str]:
        try:
            return subprocess.run(
                [self._gh_bin, *args],
                capture_output=True,
                text=True,
                timeout=timeout,
                env=self._env(),
            )
        except subprocess.TimeoutExpired as exc:
            raise GitHubOperationError("GITHUB_READ_FAILED", f"github call timed out: {exc}") from exc
        except Exception as exc:
            raise GitHubOperationError("GITHUB_READ_FAILED", f"github transport failed: {type(exc).__name__}") from exc

    # -- reads ---------------------------------------------------------------

    def get_issue(self, repo: str, issue_number: int) -> dict[str, Any]:
        proc = self._run(
            ["gh", "api", f"repos/{repo}/issues/{issue_number}"],
            timeout=GH_READ_TIMEOUT,
        )
        if proc.returncode != 0:
            raise GitHubOperationError("GITHUB_READ_FAILED", (proc.stderr or proc.stdout).strip()[:300])
        try:
            data = json.loads(proc.stdout)
        except Exception as exc:
            raise GitHubOperationError("GITHUB_READ_FAILED", f"issue JSON parse failed: {type(exc).__name__}") from exc
        if not isinstance(data, dict):
            raise GitHubOperationError("GITHUB_READ_FAILED", "issue response is not an object")
        return data

    def list_comments(self, repo: str, issue_number: int) -> list[dict[str, Any]]:
        proc = self._run(
            ["gh", "api", f"repos/{repo}/issues/{issue_number}/comments?per_page=100"],
            timeout=GH_READ_TIMEOUT,
        )
        if proc.returncode != 0:
            raise GitHubOperationError("GITHUB_READ_FAILED", (proc.stderr or proc.stdout).strip()[:300])
        try:
            data = json.loads(proc.stdout)
        except Exception as exc:
            raise GitHubOperationError("GITHUB_READ_FAILED", f"comments JSON parse failed: {type(exc).__name__}") from exc
        if not isinstance(data, list):
            raise GitHubOperationError("GITHUB_READ_FAILED", "comments response is not a list")
        return [c for c in data if isinstance(c, dict)]

    # -- writes (typed, narrowest PATCH surface) -----------------------------

    def update_issue(
        self,
        repo: str,
        issue_number: int,
        *,
        body: str | None,
        state: str | None,
    ) -> dict[str, Any]:
        patch: dict[str, Any] = {}
        if body is not None:
            patch["body"] = body
        if state is not None:
            patch["state"] = state
        if not patch:
            raise GitHubOperationError("GITHUB_MUTATION_FAILED", "empty update patch")
        return self._patch(repo, f"repos/{repo}/issues/{issue_number}", patch)

    def update_comment(self, repo: str, comment_id: str, body: str) -> dict[str, Any]:
        if not _SAFE_COMMENT_ID_RE.fullmatch(comment_id):
            raise GitHubOperationError("FOREIGN_COMMENT_DENIED", f"comment_id must be a numeric REST id, got {comment_id!r}")
        return self._patch(repo, f"repos/{repo}/issues/comments/{comment_id}", {"body": body})

    def _patch(self, repo: str, endpoint: str, patch: Mapping[str, Any]) -> dict[str, Any]:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False, encoding="utf-8") as tmp:
            json.dump(dict(patch), tmp)
            tmp_path = tmp.name
        try:
            proc = self._run(
                ["gh", "api", endpoint, "-X", "PATCH", "--input", tmp_path],
                timeout=GH_WRITE_TIMEOUT,
            )
        finally:
            try:
                os.unlink(tmp_path)
            except Exception:
                pass
        if proc.returncode != 0:
            raise GitHubOperationError("GITHUB_MUTATION_FAILED", (proc.stderr or proc.stdout).strip()[:300])
        try:
            data = json.loads(proc.stdout)
        except Exception:
            return {}
        return data if isinstance(data, dict) else {}


# ---------------------------------------------------------------------------
# Projection helpers
# ---------------------------------------------------------------------------


def _bounded_body(body: str, max_bytes: int) -> tuple[str, bool]:
    encoded = body.encode("utf-8")
    if len(encoded) <= max_bytes:
        return body, False
    # deterministic UTF-8-safe cut (explicit truncation, never silent)
    cut = encoded[:max_bytes]
    truncated_text = cut.decode("utf-8", errors="ignore")
    return truncated_text, True


def _issue_projection(data: Mapping[str, Any], *, card: bool) -> dict[str, Any]:
    body = data.get("body")
    if not isinstance(body, str):
        body = ""
    limit = MAX_ISSUE_CARD_BYTES if card else MAX_ISSUE_FULL_BYTES
    bounded, truncated = _bounded_body(body, limit)
    comments = data.get("comments")
    return {
        "plan_number": data.get("number"),
        "state": data.get("state"),
        "title": (data.get("title") or "")[:512],
        "updated_at": data.get("updated_at"),
        "body_digest": _digest(body),
        "body_bytes": len(body.encode("utf-8")),
        "body": bounded,
        "body_truncated": truncated,
        "comment_count": comments if isinstance(comments, int) else None,
        "html_url": (data.get("html_url") or "")[:512],
    }


def _comment_projection(c: Mapping[str, Any]) -> dict[str, Any]:
    body = c.get("body")
    if not isinstance(body, str):
        body = ""
    bounded, truncated = _bounded_body(body, MAX_COMMENT_INLINE_BYTES)
    author = c.get("user") if isinstance(c.get("user"), Mapping) else None
    login = (author or {}).get("login") if author else ""
    return {
        "comment_id": str(c.get("id", ""))[:MAX_COMMENT_ID_LENGTH],
        "author": str(login or "")[:128],
        "updated_at": c.get("updated_at"),
        "body_digest": _digest(body),
        "body_bytes": len(body.encode("utf-8")),
        "body": bounded,
        "body_truncated": truncated,
    }


# ---------------------------------------------------------------------------
# Provider — implements the existing ToolProvider contract shape
# ---------------------------------------------------------------------------


class BoundedGitHubToolProvider:
    """Bounded Plan-bound GitHub governance provider (four operations).

    Consumes trusted GitHubOperationAuthorityEvidence. Every target is
    mechanically the bound Plan Issue; foreign repos/issues/comments fail
    closed. Mutations require the task-main trusted authority and are
    CAS-guarded (read-verify-write, read-back verification).
    """

    def __init__(self, authority: GitHubOperationAuthorityEvidence, port: GhCliGovernancePort | None = None) -> None:
        if not isinstance(authority, GitHubOperationAuthorityEvidence):
            raise GitHubAuthorityError(
                f"authority must be GitHubOperationAuthorityEvidence (trusted evidence), got {type(authority).__name__}"
            )
        self._authority = authority
        self._port = port if port is not None else GhCliGovernancePort()

    @property
    def authority(self) -> GitHubOperationAuthorityEvidence:
        return self._authority

    def invoke(self, request: ToolRequest) -> ToolResponse:
        if not isinstance(request, ToolRequest):
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "request must be ToolRequest"})
        try:
            if request.operation.name != self._authority.operation.name:
                return ToolResponse.failure(
                    {"code": "OPERATION_MISMATCH", "message": f"request {request.operation.name!r} != authority {self._authority.operation.name!r}"}
                )
            if request.operation.contract_hash() != self._authority.operation.contract_hash():
                return ToolResponse.failure({"code": "CONTRACT_DRIFT", "message": "operation contract hash differs from authority"})
            if request.operation.name not in _ALLOWED_GITHUB_OPERATIONS:
                return ToolResponse.failure({"code": "UNSUPPORTED_GITHUB_OPERATION", "message": request.operation.name})
            handler = {
                "github.issue.read": self._handle_issue_read,
                "github.issue.comments.read": self._handle_comments_read,
                "github.issue.update": self._handle_issue_update,
                "github.issue.comment.update": self._handle_comment_update,
            }[request.operation.name]
            return handler(request.inputs)
        except GitHubOperationError as exc:
            return ToolResponse.failure({"code": exc.code, "message": str(exc)[:400]})
        except ForgeError as exc:
            return ToolResponse.failure({"code": exc.code, "message": exc.message[:400]})
        except Exception as exc:
            return ToolResponse.failure({"code": "GOVERNED_OPERATION_FAILURE", "message": f"{type(exc).__name__}"})

    # -- reads ---------------------------------------------------------------

    def _handle_issue_read(self, inputs: Mapping[str, Any]) -> ToolResponse:
        view = inputs.get("view")
        if view is None:
            view = "card"
        if view not in ("card", "full"):
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "view must be card|full"})
        plan = self._authority.plan
        data = self._port.get_issue(plan.repo, plan.issue_number)
        payload = _issue_projection(data, card=(view == "card"))
        payload["plan_ref"] = plan.plan_ref
        payload["target_bound_to_trusted_plan"] = True
        return ToolResponse.success(payload)

    def _handle_comments_read(self, inputs: Mapping[str, Any]) -> ToolResponse:
        max_comments = inputs.get("max_comments")
        if max_comments is None:
            limit = MAX_COMMENTS_RETURNED
        elif isinstance(max_comments, bool) or not isinstance(max_comments, int) or max_comments <= 0:
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "max_comments must be a positive int"})
        else:
            limit = min(max_comments, MAX_COMMENTS_RETURNED)
        plan = self._authority.plan
        comments = self._port.list_comments(plan.repo, plan.issue_number)
        projected: list[dict[str, Any]] = []
        used = 256  # envelope reserve
        index = 0
        for index, c in enumerate(comments):
            if index >= limit:
                break
            item = _comment_projection(c)
            size = len(json.dumps(item, ensure_ascii=False).encode("utf-8"))
            if used + size > MAX_COMMENTS_RESULT_BYTES:
                break
            projected.append(item)
            used += size
        dropped = len(comments) - len(projected)
        return ToolResponse.success(
            {
                "plan_ref": plan.plan_ref,
                "comments": projected,
                "total_comments": len(comments),
                "truncated": dropped > 0,
                "dropped_by_bound": max(dropped, 0),
                "note": "comment roles/names are task-main semantics; the Control Plane assigns none",
            }
        )

    # -- mutations (CAS-guarded, bound to the trusted Plan Issue) -------------

    def _handle_issue_update(self, inputs: Mapping[str, Any]) -> ToolResponse:
        plan = self._authority.plan
        body = inputs.get("body")
        state = inputs.get("state")
        section_marker = inputs.get("section_marker")
        section_content = inputs.get("section_content")
        expected_updated_at = inputs.get("expected_updated_at")
        if body is not None and (section_marker is not None or section_content is not None):
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "body and section upsert are mutually exclusive"})
        if section_marker is not None or section_content is not None:
            if not (isinstance(section_marker, str) and section_marker.strip() and isinstance(section_content, str) and section_content.strip()):
                return ToolResponse.failure({"code": "INVALID_INPUT", "message": "section upsert requires both section_marker and section_content"})
            body = None
        if body is None and section_marker is None and state is None:
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "body, section upsert, and/or state required"})
        for label, value in (("body", body), ("section_content", section_content)):
            if value is None:
                continue
            if not isinstance(value, str) or not value.strip():
                return ToolResponse.failure({"code": "INVALID_INPUT", "message": f"{label} must be a non-empty string"})
            if len(value.encode("utf-8")) > MAX_UPDATE_BODY_BYTES:
                return ToolResponse.failure({"code": "OVERSIZED_PAYLOAD", "message": f"{label} exceeds {MAX_UPDATE_BODY_BYTES} bytes"})
        if state is not None and state not in ("open", "closed"):
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "state must be open|closed"})
        if not isinstance(expected_updated_at, str) or not expected_updated_at.strip():
            return ToolResponse.failure(
                {"code": "INVALID_INPUT", "message": "expected_updated_at is required (read-before-write CAS guard)"}
            )
        current = self._port.get_issue(plan.repo, plan.issue_number)
        cur_updated = str(current.get("updated_at") or "")
        if cur_updated != expected_updated_at.strip():
            return ToolResponse.failure(
                {
                    "code": "GITHUB_CAS_CONFLICT",
                    "message": f"issue revised since read: observed {cur_updated!r}, expected {expected_updated_at!r}",
                }
            )
        cur_body = current.get("body") if isinstance(current.get("body"), str) else ""
        cur_state = current.get("state")
        candidate_body: str | None = None
        if body is not None:
            candidate_body = body
        elif section_marker is not None:
            # Mechanical marker-delimited subsection upsert (idempotent; the
            # same bounded helper the trusted finalizer already uses for
            # governance proof subsections). The Control Plane never decides
            # which section is semantically meaningful — the marker comes from
            # task-main.
            from aota_forge.work_plane.steward_finalizer import upsert_proof_subsection

            try:
                candidate_body = upsert_proof_subsection(cur_body, section_marker, section_content)
            except (TypeError, ValueError) as exc:
                return ToolResponse.failure({"code": "INVALID_INPUT", "message": f"section upsert rejected: {str(exc)[:200]}"})
        state_changed = state is not None and state != cur_state
        already = (candidate_body is None or candidate_body == cur_body) and not state_changed
        if already:
            return ToolResponse.success(
                {
                    "updated": False,
                    "already_applied": True,
                    "plan_ref": plan.plan_ref,
                    "state": cur_state,
                    "updated_at": cur_updated,
                    "body_digest": _digest(cur_body),
                }
            )
        if candidate_body is not None and len(candidate_body.encode("utf-8")) > MAX_UPDATE_BODY_BYTES:
            return ToolResponse.failure({"code": "OVERSIZED_PAYLOAD", "message": "merged body exceeds bound"})
        after = self._port.update_issue(plan.repo, plan.issue_number, body=candidate_body, state=state if state_changed else None)
        verified = self._port.get_issue(plan.repo, plan.issue_number)
        v_body = verified.get("body") if isinstance(verified.get("body"), str) else ""
        if candidate_body is not None and v_body != candidate_body:
            raise GitHubOperationError("GITHUB_CAS_CONFLICT", "issue body read-back verification failed after update")
        if state_changed and verified.get("state") != state:
            raise GitHubOperationError("GITHUB_CAS_CONFLICT", "issue state read-back verification failed after update")
        return ToolResponse.success(
            {
                "updated": True,
                "already_applied": False,
                "plan_ref": plan.plan_ref,
                "state": verified.get("state"),
                "updated_at": verified.get("updated_at"),
                "body_digest": _digest(v_body),
                "response_updated_at": (after or {}).get("updated_at"),
            }
        )

    def _handle_comment_update(self, inputs: Mapping[str, Any]) -> ToolResponse:
        plan = self._authority.plan
        comment_id = inputs.get("comment_id")
        body = inputs.get("body")
        expected_digest = inputs.get("expected_digest")
        if not isinstance(comment_id, str) or not _SAFE_COMMENT_ID_RE.fullmatch(comment_id.strip()):
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "comment_id must be a numeric REST id"})
        comment_id = comment_id.strip()
        if not isinstance(body, str) or not body.strip():
            return ToolResponse.failure({"code": "INVALID_INPUT", "message": "body must be a non-empty string"})
        if len(body.encode("utf-8")) > MAX_UPDATE_BODY_BYTES:
            return ToolResponse.failure({"code": "OVERSIZED_PAYLOAD", "message": f"body exceeds {MAX_UPDATE_BODY_BYTES} bytes"})
        # Mechanical membership only: the Control Plane verifies that the
        # comment belongs to the trusted bound Plan Issue. It never decides
        # which comment is "the" progress index or whether content is valid.
        comments = self._port.list_comments(plan.repo, plan.issue_number)
        target = None
        for c in comments:
            if str(c.get("id", "")) == comment_id:
                target = c
                break
        if target is None:
            return ToolResponse.failure(
                {"code": "FOREIGN_COMMENT_DENIED", "message": f"comment {comment_id} is not on the trusted bound Plan Issue"}
            )
        cur_body = target.get("body") if isinstance(target.get("body"), str) else ""
        if expected_digest is not None:
            if not isinstance(expected_digest, str) or not re.fullmatch(r"[0-9a-f]{64}", expected_digest.strip()):
                return ToolResponse.failure({"code": "INVALID_INPUT", "message": "expected_digest must be 64-hex"})
            if _digest(cur_body) != expected_digest.strip().lower():
                return ToolResponse.failure(
                    {
                        "code": "GITHUB_CAS_CONFLICT",
                        "message": f"comment revised since read (current digest {_digest(cur_body)[:12]}); re-read before writing",
                    }
                )
        if body == cur_body:
            return ToolResponse.success(
                {
                    "updated": False,
                    "already_applied": True,
                    "plan_ref": plan.plan_ref,
                    "comment_id": comment_id,
                    "body_digest": _digest(cur_body),
                    "updated_at": target.get("updated_at"),
                }
            )
        self._port.update_comment(plan.repo, comment_id, body)
        # read-back verify via re-list (typed, bounded)
        after_comments = self._port.list_comments(plan.repo, plan.issue_number)
        verified_body = None
        verified_updated = None
        for c in after_comments:
            if str(c.get("id", "")) == comment_id:
                verified_body = c.get("body")
                verified_updated = c.get("updated_at")
                break
        if verified_body != body:
            raise GitHubOperationError("GITHUB_CAS_CONFLICT", "comment read-back verification failed after update")
        return ToolResponse.success(
            {
                "updated": True,
                "already_applied": False,
                "plan_ref": plan.plan_ref,
                "comment_id": comment_id,
                "body_digest": _digest(body),
                "updated_at": verified_updated,
            }
        )


__all__ = [
    "GITHUB_ISSUE_READ_DESCRIPTOR",
    "GITHUB_ISSUE_COMMENTS_READ_DESCRIPTOR",
    "GITHUB_ISSUE_UPDATE_DESCRIPTOR",
    "GITHUB_ISSUE_COMMENT_UPDATE_DESCRIPTOR",
    "GITHUB_READ_OPERATIONS",
    "GITHUB_MUTATION_OPERATIONS",
    "ALL_GITHUB_OPERATIONS",
    "TrustedPlanGitHubBinding",
    "parse_plan_ref",
    "GitHubOperationAuthorityEvidence",
    "create_github_authority",
    "GitHubAuthorityError",
    "GitHubOperationError",
    "GhCliGovernancePort",
    "BoundedGitHubToolProvider",
    "MAX_COMMENTS_RETURNED",
    "MAX_COMMENT_INLINE_BYTES",
    "MAX_ISSUE_CARD_BYTES",
    "MAX_UPDATE_BODY_BYTES",
    # flags
    "NO_GENERIC_GITHUB_API",
    "GENERIC_GITHUB_ADAPTER_EXPOSED_TO_MODEL",
    "RAW_GH_NEVER_MODEL_VISIBLE",
    "PLAN_BOUND_TARGETS_ONLY",
    "CROSS_PLAN_GITHUB_OPERATION_FAIL_CLOSED",
    "GITHUB_MUTATION_AUTHORITY_REQUIRED",
    "GITHUB_MUTATION_CAS_GUARDED",
    "EXISTING_GOVERNANCE_MECHANICS_REUSED",
    "CONTROL_PLANE_NEVER_NAMES_COMMENTS",
    "CONTROL_PLANE_IS_GITHUB_WORKFLOW_DECISION_OWNER",
    "GITHUB_VIA_RESTRICTED_SHELL",
    "LARGE_BODY_CARD_FIRST",
    "NEW_RESULT_HYDRATION_FRAMEWORK_CREATED",
]
