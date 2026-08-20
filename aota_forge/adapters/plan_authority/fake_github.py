"""M4-6 deterministic fake GitHub transport — fixture store + failure injection seam.

No production failure injector. Pure test double for deterministic simulation of:
- successful read/write/readback
- stale precondition
- duplicate/missing role
- timeout / lost response / unknown outcome
- candidate/original/third readback
- partial projection failure
- verify read failure
- auth preflight failure
- idempotency conflict

Never writes production Issue #9; isolated per test via in-memory store.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Callable

from aota_forge.adapters.plan_authority.github import (
    GitHubCommentSnapshot,
    GitHubStoreProtocol,
    GitHubAuthorityAdapter,
    CONTROL_ROLE_MARKERS,
    EVENT_LOG_MARKER,
    _digest,
    ERROR_CODE_STALE_AUTHORITY,
    ERROR_CODE_OUTCOME_UNKNOWN,
    ERROR_CODE_TIMEOUT,
    ERROR_CODE_KNOWN_REJECTION,
    ERROR_CODE_PERMISSION,
    ERROR_CODE_RATE_LIMIT,
)
from aota_forge.core.identity.refs import ObjectRef

DETERMINISTIC_FAILURE_INJECTION_SEAM = "PASS"
PRODUCTION_FAILURE_INJECTOR_IMPLEMENTED = False
PRODUCTION_GITHUB_ACCEPTANCE_WRITE_ALLOWED = "no"
FIXTURE_GITHUB_STORE_IMPLEMENTED = True


@dataclass
class InjectionHooks:
    """Deterministic hooks for GitHub fake — 10 injection points."""

    # Pre-read
    fail_before_read: bool = False
    # Stale precondition (read returns mismatched revision/digest)
    inject_stale_revision: str | int | None = None
    inject_stale_digest: str | None = None

    # During mutate
    timeout_during_mutate: bool = False
    known_rejection_during_mutate: bool = False
    permission_denied_during_mutate: bool = False
    rate_limited_during_mutate: bool = False
    crash_after_external_before_verify: bool = False

    # During verify read
    verify_fails: bool = False
    verify_returns_candidate: bool = False
    verify_returns_original: bool = False
    verify_returns_third: bool = False

    # Multi-object
    partial_projection_failure: bool = False
    event_log_append_fails: bool = False

    # Custom callable hooks for fine-grained injection
    during_write: Callable[[ObjectRef, str, str | int | None, str | None], tuple[bool, str | int | None, str | None, str | None] | None] | None = None
    during_read_issue: Callable[[ObjectRef], tuple[str | int | None, str | None, str] | None] | None = None
    during_read_comment: Callable[[str], tuple[str | int | None, str | None, str] | None] | None = None
    during_list_comments: Callable[[ObjectRef], list[GitHubCommentSnapshot] | None] | None = None
    during_create_comment: Callable[[str], tuple[str, str | int, str] | None] | None = None


class FixtureGitHubStore(GitHubStoreProtocol):
    """Disposable in-memory GitHub Issue store — deterministic revision/digest.

    Holds one Issue body + comment map. Revision bumps deterministically on
    successful write. No network, no gh CLI, no generic API.
    """

    def __init__(
        self,
        body: str = "original-body",
        revision: str | int = "1",
        comments: list[GitHubCommentSnapshot] | None = None,
        hooks: InjectionHooks | None = None,
    ) -> None:
        self._body = body
        self._revision: str | int = revision
        self._hooks = hooks or InjectionHooks()
        self._comments: dict[str, GitHubCommentSnapshot] = {}
        self._next_comment_id = 100
        # Store original for verify_returns_original/third
        self._original_body = body
        self._original_revision = revision
        self._original_digest = _digest(body)
        self._candidate_body: str | None = None
        self._candidate_revision: str | int | None = None
        self._candidate_digest: str | None = None
        self._third_body = "third-state-body-unrelated"
        self._third_digest = _digest(self._third_body)
        self._third_revision = f"{revision}-third"

        if comments:
            for c in comments:
                self._comments[c.comment_id] = c
                # keep next id high
                try:
                    nid = int(c.comment_id.split("-")[-1])
                    if nid >= self._next_comment_id:
                        self._next_comment_id = nid + 1
                except Exception:
                    pass

        self.read_issue_call_count = 0
        self.write_issue_call_count = 0
        self.list_comments_call_count = 0
        self.read_comment_call_count = 0
        self.update_comment_call_count = 0
        self.create_comment_call_count = 0

    @property
    def digest(self) -> str:
        return _digest(self._body)

    def _bump_revision(self) -> str | int:
        if isinstance(self._revision, int):
            self._revision += 1
            return self._revision
        if isinstance(self._revision, str) and self._revision.isdigit():
            self._revision = str(int(self._revision) + 1)
            return self._revision
        # opaque string like "2026-08-20T00:00:00Z" -> append
        self._revision = f"{self._revision}-next"
        return self._revision

    # --- Issue ops ---

    def read_issue(self, target: ObjectRef) -> tuple[str | int | None, str | None, str]:
        self.read_issue_call_count += 1
        if self._hooks.fail_before_read:
            raise RuntimeError("injected pre-read failure")
        if self._hooks.during_read_issue:
            injected = self._hooks.during_read_issue(target)
            if injected is not None:
                return injected
        # Stale injection: return mismatched revision/digest to trigger stale detection
        if self._hooks.inject_stale_revision is not None or self._hooks.inject_stale_digest is not None:
            rev = self._hooks.inject_stale_revision if self._hooks.inject_stale_revision is not None else self._revision
            dig = self._hooks.inject_stale_digest if self._hooks.inject_stale_digest is not None else self.digest
            return (rev, dig, self._body)
        # Verify hooks that force specific readback after mutate
        if self._hooks.verify_returns_candidate and self._candidate_body is not None:
            # Verify returns candidate state
            return (self._candidate_revision or self._revision, self._candidate_digest or self.digest, self._candidate_body)
        if self._hooks.verify_returns_original:
            return (self._original_revision, self._original_digest, self._original_body)
        if self._hooks.verify_returns_third:
            return (self._third_revision, self._third_digest, self._third_body)
        if self._hooks.verify_fails:
            raise RuntimeError("injected verify read failure")
        return (self._revision, self.digest, self._body)

    def write_issue(self, target: ObjectRef, candidate_body: str, expected_revision: str | int | None, expected_digest: str | None) -> tuple[bool, str | int | None, str | None, str | None]:
        self.write_issue_call_count += 1
        # Custom hook
        if self._hooks.during_write:
            injected = self._hooks.during_write(target, candidate_body, expected_revision, expected_digest)
            if injected is not None:
                return injected
        if self._hooks.timeout_during_mutate:
            raise TimeoutError("injected timeout during mutate -> unknown outcome; write may have happened but response lost")
        if self._hooks.known_rejection_during_mutate:
            return (False, self._revision, self.digest, ERROR_CODE_KNOWN_REJECTION)
        if self._hooks.permission_denied_during_mutate:
            return (False, self._revision, self.digest, ERROR_CODE_PERMISSION)
        if self._hooks.rate_limited_during_mutate:
            return (False, self._revision, self.digest, ERROR_CODE_RATE_LIMIT)
        # Normal CAS check — read-before-write already validated, but store also enforces
        if expected_revision is not None and expected_revision != self._revision:
            return (False, self._revision, self.digest, ERROR_CODE_STALE_AUTHORITY)
        if expected_digest is not None and expected_digest != self.digest:
            return (False, self._revision, self.digest, ERROR_CODE_STALE_AUTHORITY)
        # Perform write
        self._body = candidate_body
        new_rev = self._bump_revision()
        new_digest = self.digest
        # Remember candidate for verify_returns_candidate hook
        self._candidate_body = candidate_body
        self._candidate_revision = new_rev
        self._candidate_digest = new_digest
        if self._hooks.crash_after_external_before_verify:
            # Simulate crash by raising after successful write but before verify can happen
            raise RuntimeError("injected crash after external before verify — stranded APPLYING / J3 window")

        return (True, new_rev, new_digest, None)

    # --- Comment ops ---

    def list_comments(self, target: ObjectRef) -> list[GitHubCommentSnapshot]:
        self.list_comments_call_count += 1
        if self._hooks.during_list_comments:
            injected = self._hooks.during_list_comments(target)
            if injected is not None:
                return injected
        return list(self._comments.values())

    def read_comment(self, comment_id: str) -> tuple[str | int | None, str | None, str] | None:
        self.read_comment_call_count += 1
        if self._hooks.during_read_comment:
            injected = self._hooks.during_read_comment(comment_id)
            if injected is not None:
                return injected
        if self._hooks.verify_fails:
            raise RuntimeError("injected verify read failure for comment")
        c = self._comments.get(comment_id)
        if c is None:
            return None
        return (c.revision, c.digest, c.body)

    def update_comment(self, comment_id: str, candidate_body: str, expected_revision: str | int | None, expected_digest: str | None) -> tuple[bool, str | int | None, str | None, str | None]:
        self.update_comment_call_count += 1
        if self._hooks.partial_projection_failure:
            # Simulate stale or permission for projection
            return (False, None, None, ERROR_CODE_STALE_AUTHORITY)
        if self._hooks.timeout_during_mutate:
            raise TimeoutError("injected timeout during comment update -> unknown")
        c = self._comments.get(comment_id)
        if c is None:
            return (False, None, None, "NOT_FOUND")
        if expected_revision is not None and expected_revision != c.revision:
            return (False, c.revision, c.digest, ERROR_CODE_STALE_AUTHORITY)
        if expected_digest is not None and expected_digest != c.digest:
            return (False, c.revision, c.digest, ERROR_CODE_STALE_AUTHORITY)
        # Bump revision deterministically
        if isinstance(c.revision, int):
            new_rev = c.revision + 1
        elif isinstance(c.revision, str) and c.revision.isdigit():
            new_rev = str(int(c.revision) + 1)
        else:
            new_rev = f"{c.revision}-next"
        new_digest = _digest(candidate_body)
        new_c = GitHubCommentSnapshot(comment_id=comment_id, body=candidate_body, revision=new_rev, digest=new_digest)
        self._comments[comment_id] = new_c
        if self._hooks.crash_after_external_before_verify:
            raise RuntimeError("injected crash after comment update before verify")
        return (True, new_rev, new_digest, None)

    def create_comment(self, body: str) -> tuple[str, str | int, str]:
        self.create_comment_call_count += 1
        if self._hooks.during_create_comment:
            injected = self._hooks.during_create_comment(body)
            if injected is not None:
                return injected
        if self._hooks.event_log_append_fails:
            raise RuntimeError("injected Event Log append failure")
        if self._hooks.timeout_during_mutate:
            raise TimeoutError("injected timeout during Event Log append")
        comment_id = f"comment-{self._next_comment_id}"
        self._next_comment_id += 1
        revision: str | int = "1"
        digest = _digest(body)
        snap = GitHubCommentSnapshot(comment_id=comment_id, body=body, revision=revision, digest=digest)
        self._comments[comment_id] = snap
        return (comment_id, revision, digest)

    # Helper to seed control comments
    def seed_control_comment(self, role: str, body_suffix: str = "initial", revision: str | int = "1") -> str:
        marker = CONTROL_ROLE_MARKERS.get(role, "")
        body = f"{marker}\n{body_suffix} for {role}"
        digest = _digest(body)
        comment_id = f"comment-{self._next_comment_id}"
        self._next_comment_id += 1
        snap = GitHubCommentSnapshot(comment_id=comment_id, body=body, revision=revision, digest=digest)
        self._comments[comment_id] = snap
        return comment_id

    def seed_duplicate_control_role(self, role: str) -> tuple[str, str]:
        id1 = self.seed_control_comment(role, "first")
        id2 = self.seed_control_comment(role, "second")
        return (id1, id2)

    def reset_hooks(self) -> None:
        self._hooks = InjectionHooks()


class FakeGitHubAuthorityAdapter(GitHubAuthorityAdapter):
    """Fake adapter wrapping FixtureGitHubStore for deterministic tests — no production injector."""

    def __init__(self, store: FixtureGitHubStore | None = None, hooks: InjectionHooks | None = None, auth_preflight: Callable[[], bool] | None = None) -> None:
        self.fixture_store = store or FixtureGitHubStore(hooks=hooks)
        # Pass hooks to store if supplied separately
        if hooks is not None:
            self.fixture_store._hooks = hooks
        super().__init__(store=self.fixture_store, auth_preflight=auth_preflight)
        self.mutate_call_count = 0
        self.read_call_count = 0
        self.verify_call_count = 0

    def read_raw_authority(self, target: ObjectRef) -> tuple[str | int | None, str | None, str]:
        self.read_call_count += 1
        return super().read_raw_authority(target)

    def mutate(self, request):  # type: ignore
        self.mutate_call_count += 1
        return super().mutate(request)

    def verify(self, target: ObjectRef) -> tuple[str | int | None, str | None, str]:
        self.verify_call_count += 1
        return super().verify(target)

    @property
    def store(self) -> FixtureGitHubStore:
        return self.fixture_store


__all__ = [
    "FixtureGitHubStore",
    "FakeGitHubAuthorityAdapter",
    "InjectionHooks",
    "DETERMINISTIC_FAILURE_INJECTION_SEAM",
    "PRODUCTION_FAILURE_INJECTOR_IMPLEMENTED",
    "PRODUCTION_GITHUB_ACCEPTANCE_WRITE_ALLOWED",
    "FIXTURE_GITHUB_STORE_IMPLEMENTED",
]
