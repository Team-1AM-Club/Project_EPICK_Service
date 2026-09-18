from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy.orm import Session

from app.repo.jobs import JobRepository
from app.services.jobs import JobService

pytestmark = pytest.mark.postgres


def test_same_id_same_digest_is_an_exact_replay(db_session: Session) -> None:
    event_id = uuid4()
    first = JobService(db_session).reserve_digest_aware_inbox_receipt(
        consumer_name="w1.w2-commit-gate",
        event_id=event_id,
        outcome_code="PROCESSING",
        payload_digest="sha256:" + "a" * 64,
        producer_name="w2",
        schema_version="w2.private.source-collection.staged-result.v1",
    )
    replay = JobService(db_session).reserve_digest_aware_inbox_receipt(
        consumer_name="w1.w2-commit-gate",
        event_id=event_id,
        outcome_code="PROCESSING",
        payload_digest="sha256:" + "a" * 64,
        producer_name="w2",
        schema_version="w2.private.source-collection.staged-result.v1",
    )
    assert first.inserted and not first.id_conflict
    assert not replay.inserted and not replay.id_conflict
    assert replay.receipt.outcome_code == "PROCESSING"


def test_same_id_different_digest_is_id_conflict_without_overwrite(db_session: Session) -> None:
    event_id = uuid4()
    repository = JobRepository(db_session)
    first = repository.reserve_digest_aware_inbox_receipt(
        consumer_name="w1.w2-commit-gate",
        event_id=event_id,
        outcome_code="APPLIED",
        payload_digest="sha256:" + "b" * 64,
        producer_name="w2",
        schema_version="w2.private.source-collection.staged-result.v1",
    )
    conflict = repository.reserve_digest_aware_inbox_receipt(
        consumer_name="w1.w2-commit-gate",
        event_id=event_id,
        outcome_code="PROCESSING",
        payload_digest="sha256:" + "c" * 64,
        producer_name="w2",
        schema_version="w2.private.source-collection.staged-result.v1",
    )
    assert first.inserted and not first.id_conflict
    assert not conflict.inserted and conflict.id_conflict
    assert conflict.receipt.payload_digest == "sha256:" + "b" * 64
    assert conflict.receipt.outcome_code == "APPLIED"
