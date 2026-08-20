"""M4-7 retry / fresh-authorization handoff — preserving history via lineage.

Governance:
- RETRYABLE_NO_EFFECT does NOT grant automatic authorization
- Blind old lease reuse denied
- Fresh authorization preserves historical evidence, creates new journal_id with same correlation_id
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Optional

from aota_forge.core.journal.model import JournalRecord, JournalState
from aota_forge.core.journal.store import DurableJournalEntry, DurableJournalStore
from aota_forge.core.journal.retry import is_retry_allowed

FRESH_AUTHORIZATION_REBIND_IMPLEMENTED = True
OLD_AUTHORIZATION_EVIDENCE_OVERWRITE_ALLOWED = False
RETRYABLE_NO_EFFECT_GRANTS_AUTOMATIC_AUTHORIZATION = False
RETRYABLE_NO_EFFECT_BLIND_OLD_LEASE_REUSE = False
FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY = True


class RetryHandoffError(Exception):
    pass


def _validate_fresh_authorization(
    has_fresh_authorization: bool,
    has_fresh_subject_precondition: bool,
    has_fresh_raw_authority_precondition: bool,
    has_new_bounded_lease: bool,
) -> bool:
    return bool(
        has_fresh_authorization
        and has_fresh_subject_precondition
        and has_fresh_raw_authority_precondition
        and has_new_bounded_lease
    )


def can_retry_after_retryable(
    entry: DurableJournalEntry,
    *,
    has_fresh_authorization: bool,
    has_fresh_subject_precondition: bool,
    has_fresh_raw_authority_precondition: bool,
    has_new_bounded_lease: bool,
) -> bool:
    """Check if retry after RETRYABLE_NO_EFFECT is allowed per M4-5 contract."""
    if entry.record.journal_state != JournalState.RETRYABLE_NO_EFFECT:
        return False
    return is_retry_allowed(
        current_state=entry.record.journal_state,
        has_fresh_authorization=has_fresh_authorization,
        has_fresh_subject_precondition=has_fresh_subject_precondition,
        has_fresh_raw_authority_precondition=has_fresh_raw_authority_precondition,
        has_new_bounded_lease=has_new_bounded_lease,
    )


def create_retry_journal(
    store: DurableJournalStore,
    original_entry: DurableJournalEntry,
    *,
    new_journal_id: str,
    new_attempt_id: str,
    new_authorization_reference: str | None,
    new_lease_reference: str | None,
    new_subject_expected_revision: int | None = None,
    new_authority_source_revision: str | int | None = None,
    new_authority_observed_raw_digest: str | None = None,
    new_candidate_raw_digest: str | None = None,
    new_normalized_plan_digest: str | None = None,
    has_fresh_authorization: bool = False,
    has_fresh_subject_precondition: bool = False,
    has_fresh_raw_authority_precondition: bool = False,
    has_new_bounded_lease: bool = False,
) -> DurableJournalEntry:
    """Create a fresh authorized retry lineage from a RETRYABLE_NO_EFFECT record.

    Preserves historical authorization evidence: old record remains immutable,
    new record gets new journal_id, new attempt_id, same correlation_id (lineage),
    fresh authorization evidence, and new complete_external_identity.
    Validates fresh preconditions fail-closed.
    """
    if original_entry.record.journal_state != JournalState.RETRYABLE_NO_EFFECT:
        raise RetryHandoffError(f"original must be RETRYABLE_NO_EFFECT, got {original_entry.record.journal_state}")

    # Validate fresh bindings
    if not _validate_fresh_authorization(
        has_fresh_authorization,
        has_fresh_subject_precondition,
        has_fresh_raw_authority_precondition,
        has_new_bounded_lease,
    ):
        raise RetryHandoffError("fresh authorization/subject/raw/lease required for retry")

    # Old lease reuse denied: new lease must differ from old
    if new_lease_reference is not None and new_lease_reference == original_entry.record.lease_reference:
        raise RetryHandoffError("blind old lease reuse denied")

    # Old authorization evidence must not be overwritten in original; we create new
    # New entry inherits correlation lineage but new identity
    old = original_entry.record
    # Build new JournalRecord with fresh bindings where supplied, else reuse old semantic fields
    # But ensure new authorization_reference/lease_reference are fresh
    from dataclasses import replace

    # For lineage: correlation_id same
    new_record = JournalRecord(
        journal_id=new_journal_id,
        correlation_id=old.correlation_id,  # lineage chain
        attempt_id=new_attempt_id,
        operation=old.operation,
        typed_target=old.typed_target,
        principal=old.principal,
        contract_hash=old.contract_hash,
        idempotency_key=old.idempotency_key,
        intent_fingerprint=old.intent_fingerprint,
        subject_expected_revision=new_subject_expected_revision if new_subject_expected_revision is not None else old.subject_expected_revision,
        authority_source_revision=new_authority_source_revision if new_authority_source_revision is not None else old.authority_source_revision,
        authority_observed_raw_digest=new_authority_observed_raw_digest if new_authority_observed_raw_digest is not None else old.authority_observed_raw_digest,
        candidate_raw_digest=new_candidate_raw_digest if new_candidate_raw_digest is not None else old.candidate_raw_digest,
        normalized_plan_digest=new_normalized_plan_digest if new_normalized_plan_digest is not None else old.normalized_plan_digest,
        authorization_reference=new_authorization_reference,
        lease_reference=new_lease_reference,
        authorization_basis=old.authorization_basis,
        journal_state=JournalState.PREPARED,
        observed_raw_digest=None,
        observed_revision=None,
        verification_state=None,
        original_raw_digest=old.authority_observed_raw_digest,
        evidence=dict(old.evidence),
    )
    # Add lineage evidence
    evidence = {"retry_lineage_from": old.journal_id, "retry_correlation_id": old.correlation_id}
    new_record = replace(new_record, evidence={**dict(new_record.evidence), **evidence})

    # Persist new PREPARED
    return store.create_prepared(new_record)


def compute_retry_lineage(correlation_id: str, original_journal_id: str, new_journal_id: str) -> dict[str, str]:
    return {
        "correlation_id": correlation_id,
        "original_journal_id": original_journal_id,
        "retry_journal_id": new_journal_id,
    }


__all__ = [
    "FRESH_AUTHORIZATION_REBIND_IMPLEMENTED",
    "OLD_AUTHORIZATION_EVIDENCE_OVERWRITE_ALLOWED",
    "RETRYABLE_NO_EFFECT_GRANTS_AUTOMATIC_AUTHORIZATION",
    "RETRYABLE_NO_EFFECT_BLIND_OLD_LEASE_REUSE",
    "FRESH_AUTHORIZATION_REQUIRED_FOR_RETRY",
    "RetryHandoffError",
    "can_retry_after_retryable",
    "create_retry_journal",
    "compute_retry_lineage",
]
