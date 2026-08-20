"""M4-5 deterministic failure injection seam — fake adapter + fixture authority.

No production failure injector. Pure test double for M4-6/M4-7 injection of:
- known failure, timeout/unknown, effect-before-verify crash,
- verification read failure, candidate/original/third observed.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from typing import Any, Callable

from aota_forge.core.identity.refs import ObjectRef
from aota_forge.adapters.plan_authority.port import (
    PlanAuthorityMutationPort,
    PortablePlanMutationRequest,
    PortablePlanMutationResponse,
)

DETERMINISTIC_FAILURE_INJECTION_SEAM = "PASS"
PRODUCTION_FAILURE_INJECTOR_IMPLEMENTED = False


def _digest(body: str) -> str:
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


@dataclass
class FixtureAuthority:
    """Disposable raw authority fixture (in-memory, deterministic revision/digest)."""

    body: str = "original-body"
    revision: str | int = "1"

    @property
    def digest(self) -> str:
        return _digest(self.body)

    def read(self) -> tuple[str | int, str, str]:
        return (self.revision, self.digest, self.body)

    def write(self, body: str, expected_revision: str | int | None, expected_digest: str | None) -> tuple[bool, str | int, str]:
        """Deterministic write with read-before-write CAS. Returns (success, new_revision, new_digest)."""
        if expected_revision is not None and expected_revision != self.revision:
            return (False, self.revision, self.digest)
        if expected_digest is not None and expected_digest != self.digest:
            return (False, self.revision, self.digest)
        self.body = body
        # bump revision
        if isinstance(self.revision, int):
            self.revision += 1
        elif isinstance(self.revision, str) and self.revision.isdigit():
            self.revision = str(int(self.revision) + 1)
        else:
            self.revision = f"{self.revision}-next"
        return (True, self.revision, self.digest)


@dataclass
class InjectionHooks:
    """Deterministic hooks for before/during/after injection."""

    before_persist_prepared: Callable[[], None] | None = None
    before_persist_applying: Callable[[], None] | None = None
    during_external_mutation: Callable[[PortablePlanMutationRequest], PortablePlanMutationResponse | None] | None = None
    after_external_before_verify: Callable[[], None] | None = None
    during_verify_read: Callable[[ObjectRef], tuple[str | int | None, str | None, str] | None] | None = None
    during_terminal_persist: Callable[[], None] | None = None

    # Simple fault flags
    fail_before_prepared: bool = False
    fail_before_applying: bool = False
    timeout_during_mutate: bool = False
    known_rejection_during_mutate: bool = False
    crash_after_external_before_verify: bool = False
    verify_returns_candidate: bool = False
    verify_returns_original: bool = False
    verify_returns_third: bool = False
    verify_fails: bool = False
    terminal_persist_fails: bool = False


class FakePlanAuthorityAdapter(PlanAuthorityMutationPort):
    """Fake in-memory adapter implementing the typed port for deterministic testing.

    No GitHub, no network, no generic write API.
    """

    def __init__(self, fixture: FixtureAuthority | None = None, hooks: InjectionHooks | None = None) -> None:
        self.fixture = fixture or FixtureAuthority()
        self.hooks = hooks or InjectionHooks()
        self.mutate_call_count = 0
        self.read_call_count = 0
        self.verify_call_count = 0
        self._original_body = self.fixture.body
        self._original_digest = self.fixture.digest
        self._original_revision = self.fixture.revision

    # PlanAuthorityMutationPort contract
    def read_raw_authority(self, target: ObjectRef) -> tuple[str | int | None, str | None, str]:
        self.read_call_count += 1
        if self.hooks.during_verify_read:
            # For read_raw, we reuse verify hook if set differently; but provide separate
            result = self.hooks.during_verify_read(target)
            if result is not None:
                return result
        if self.hooks.verify_fails:
            raise RuntimeError("injected verify read failure")
        return self.fixture.read()

    def mutate(self, request: PortablePlanMutationRequest) -> PortablePlanMutationResponse:
        self.mutate_call_count += 1
        # Hook: during mutation
        if self.hooks.during_external_mutation:
            injected = self.hooks.during_external_mutation(request)
            if injected is not None:
                return injected
        if self.hooks.timeout_during_mutate:
            # Simulate timeout leaving outcome unknown
            return PortablePlanMutationResponse(
                operation=request.operation,
                typed_target=request.typed_target.to_canonical(),
                correlation_id=request.correlation_id,
                adapter_success=False,
                error_code="TIMEOUT",
                error_message="injected timeout before verify",
            )
        if self.hooks.known_rejection_during_mutate:
            return PortablePlanMutationResponse(
                operation=request.operation,
                typed_target=request.typed_target.to_canonical(),
                correlation_id=request.correlation_id,
                adapter_success=False,
                observed_raw_digest=self.fixture.digest,
                observed_revision=self.fixture.revision,
                error_code="KNOWN_REJECTION",
                error_message="injected known failure with no effect",
            )
        # Normal optimistic write with stale check against request's observed precondition
        success, new_rev, new_digest = self.fixture.write(
            body=request.candidate_raw_body or f"candidate-{request.candidate_raw_digest}",
            expected_revision=request.authority_source_revision,
            expected_digest=request.authority_observed_raw_digest,
        )
        if not success:
            return PortablePlanMutationResponse(
                operation=request.operation,
                typed_target=request.typed_target.to_canonical(),
                correlation_id=request.correlation_id,
                adapter_success=False,
                observed_raw_digest=self.fixture.digest,
                observed_revision=self.fixture.revision,
                error_code="STALE_AUTHORITY",
                error_message="stale external authority fails closed",
            )
        # Success path: but adapter success alone != VERIFIED; verify will classify
        if self.hooks.crash_after_external_before_verify:
            # Simulate crash by raising or returning but not persisting terminal — caller will see stranded APPLYING
            raise RuntimeError("injected crash after external before verify")
        return PortablePlanMutationResponse(
            operation=request.operation,
            typed_target=request.typed_target.to_canonical(),
            correlation_id=request.correlation_id,
            adapter_success=True,
            observed_raw_digest=new_digest,
            observed_revision=new_rev,
        )

    def verify(self, target: ObjectRef) -> tuple[str | int | None, str | None, str]:
        self.verify_call_count += 1
        if self.hooks.during_verify_read:
            injected = self.hooks.during_verify_read(target)
            if injected is not None:
                return injected
        if self.hooks.verify_fails:
            raise RuntimeError("injected verify failure")
        if self.hooks.verify_returns_candidate:
            # Return candidate digest/body
            # Find candidate by reading fixture's current (which is candidate if mutate succeeded) else fabricate
            return self.fixture.read()
        if self.hooks.verify_returns_original:
            return (self._original_revision, self._original_digest, self._original_body)
        if self.hooks.verify_returns_third:
            third_body = "third-state-body-unrelated"
            return (f"{self._original_revision}-third", _digest(third_body), third_body)
        return self.fixture.read()

    # Helper to reset fixture to original for retry scenarios
    def reset_to_original(self) -> None:
        self.fixture.body = self._original_body
        self.fixture.revision = self._original_revision

    def set_candidate_as_current(self, candidate_body: str, candidate_revision: str | int | None = None) -> None:
        self.fixture.body = candidate_body
        if candidate_revision is not None:
            self.fixture.revision = candidate_revision

__all__ = [
    "FixtureAuthority",
    "InjectionHooks",
    "FakePlanAuthorityAdapter",
    "DETERMINISTIC_FAILURE_INJECTION_SEAM",
    "PRODUCTION_FAILURE_INJECTOR_IMPLEMENTED",
]
