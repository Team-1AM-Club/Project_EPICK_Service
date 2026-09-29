from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from epick_engine.source_collection.contracts import (
    DateStatus,
    DateValue,
    FreshnessStatus,
    RetentionScope,
)
from epick_engine.source_collection.policy import PolicyBlocked, PolicyOperation
from epick_engine.source_collection.service import (
    SOURCE_REFRESH_OPERATION,
    EvidenceReadRecord,
    OwnedSourceRecord,
    OwnedSourceVersionRecord,
    RetainedBodyReference,
    SourceCurrentReadState,
    SourceOriginEvidenceRelation,
    SourceRefreshAccepted,
    SourceRefreshIdempotencyConflict,
    SourceVersionEvidenceDependencyUnavailable,
    SourceVersionEvidenceInvalidInput,
    SourceVersionEvidenceNotFound,
    SourceVersionEvidenceService,
    SourceVersionReadRecord,
)

OWNER_ID = UUID("00000000-0000-4000-8000-000000009401")
OTHER_OWNER_ID = UUID("00000000-0000-4000-8000-000000009402")
COMPANY_ID = UUID("00000000-0000-4000-8000-000000000401")
SOURCE_ID = UUID("00000000-0000-4000-8000-000000000501")
OTHER_SOURCE_ID = UUID("00000000-0000-4000-8000-000000000502")
VERSION_OLD_ID = UUID("00000000-0000-4000-8000-000000000601")
VERSION_TIE_LOW_ID = UUID("00000000-0000-4000-8000-000000000602")
VERSION_TIE_HIGH_ID = UUID("00000000-0000-4000-8000-000000000603")
REVISION_ID = UUID("00000000-0000-4000-8000-000000000701")
OTHER_REVISION_ID = UUID("00000000-0000-4000-8000-000000000702")
EVIDENCE_FIRST_ID = UUID("00000000-0000-4000-8000-000000000801")
EVIDENCE_SECOND_ID = UUID("00000000-0000-4000-8000-000000000802")
ORIGIN_RELATION_ID = UUID("00000000-0000-4000-8000-000000000901")
COMPLETED_JOB_ID = UUID("00000000-0000-4000-8000-000000000a01")
REFRESH_JOB_ID = UUID("00000000-0000-4000-8000-000000000a02")
RETRY_JOB_ID = UUID("00000000-0000-4000-8000-000000000a03")
NOW = datetime(2026, 9, 11, 12, tzinfo=UTC)
UNKNOWN_DATE = DateValue(
    status=DateStatus.UNKNOWN,
    raw_text=None,
    value=None,
    precision=None,
    timezone=None,
)


@dataclass
class _Ownership:
    sources: dict[tuple[UUID, UUID], OwnedSourceRecord] = field(default_factory=dict)
    versions: dict[tuple[UUID, UUID], OwnedSourceVersionRecord] = field(default_factory=dict)
    revisions: dict[tuple[UUID, UUID, UUID], OwnedSourceVersionRecord] = field(default_factory=dict)

    def find_owned_source(
        self,
        *,
        owner_user_id: UUID,
        source_id: UUID,
    ) -> OwnedSourceRecord | None:
        return self.sources.get((owner_user_id, source_id))

    def find_owned_source_version(
        self,
        *,
        owner_user_id: UUID,
        source_version_id: UUID,
    ) -> OwnedSourceVersionRecord | None:
        return self.versions.get((owner_user_id, source_version_id))

    def find_owned_source_version_revision(
        self,
        *,
        owner_user_id: UUID,
        source_version_id: UUID,
        extraction_revision_id: UUID,
    ) -> OwnedSourceVersionRecord | None:
        return self.revisions.get((owner_user_id, source_version_id, extraction_revision_id))


@dataclass
class _Policy:
    blocked: set[PolicyOperation] = field(default_factory=set)
    blocked_states: dict[PolicyOperation, str] = field(default_factory=dict)
    calls: list[tuple[UUID, PolicyOperation]] = field(default_factory=list)

    def authorize_current(self, *, source_id: UUID, operation: PolicyOperation) -> None:
        self.calls.append((source_id, operation))
        if operation in self.blocked or operation in self.blocked_states:
            raise PolicyBlocked(self.blocked_states.get(operation, f"{operation} is blocked"))


@dataclass
class _Reader:
    versions: tuple[SourceVersionReadRecord, ...] = ()
    evidence: dict[tuple[UUID, UUID], tuple[EvidenceReadRecord, ...] | None] = field(
        default_factory=dict
    )
    origins: tuple[SourceOriginEvidenceRelation, ...] = ()
    state: SourceCurrentReadState = field(
        default_factory=lambda: SourceCurrentReadState(
            current_restriction={"state": "current"},
            freshness_status=FreshnessStatus.CURRENT,
        )
    )
    version_calls: list[tuple[UUID, tuple[datetime, UUID] | None, int]] = field(
        default_factory=list
    )
    evidence_calls: list[tuple[UUID, UUID, tuple[int, UUID] | None, int]] = field(
        default_factory=list
    )
    origin_calls: list[tuple[UUID, tuple[UUID, ...]]] = field(default_factory=list)

    def current_state(self, *, source_id: UUID) -> SourceCurrentReadState:
        assert source_id == SOURCE_ID
        return self.state

    def list_source_versions(
        self,
        *,
        source_id: UUID,
        before: tuple[datetime, UUID] | None,
        limit: int,
    ) -> tuple[SourceVersionReadRecord, ...]:
        self.version_calls.append((source_id, before, limit))
        rows = tuple(
            row
            for row in self.versions
            if before is None or (row.collected_at, row.source_version_id) < before
        )
        return rows[:limit]

    def list_revision_evidence(
        self,
        *,
        source_version_id: UUID,
        extraction_revision_id: UUID,
        after: tuple[int, UUID] | None,
        limit: int,
    ) -> tuple[EvidenceReadRecord, ...] | None:
        self.evidence_calls.append((source_version_id, extraction_revision_id, after, limit))
        rows = self.evidence.get((source_version_id, extraction_revision_id))
        if rows is None:
            return None
        filtered = tuple(
            row for row in rows if after is None or (row.chunk_order, row.evidence_id) > after
        )
        return filtered[:limit]

    def list_origin_relations_for_evidence(
        self,
        *,
        source_id: UUID,
        evidence_ids: tuple[UUID, ...],
    ) -> tuple[SourceOriginEvidenceRelation, ...]:
        self.origin_calls.append((source_id, evidence_ids))
        return self.origins


@dataclass
class _RetainedBodies:
    result: RetainedBodyReference | None = None
    calls: list[tuple[UUID, UUID]] = field(default_factory=list)

    def get_current_body_reference(
        self,
        *,
        source_id: UUID,
        source_version_id: UUID,
    ) -> RetainedBodyReference | None:
        self.calls.append((source_id, source_version_id))
        return self.result


@dataclass
class _RefreshJobs:
    queued: bool = False
    records: dict[tuple[UUID, UUID, str], tuple[UUID | None, SourceRefreshAccepted]] = field(
        default_factory=dict
    )
    completed_jobs: dict[UUID, str] = field(default_factory=lambda: {COMPLETED_JOB_ID: "SUCCEEDED"})
    calls: list[dict[str, object]] = field(default_factory=list)
    fetch_calls: int = 0

    def accept_policy_refresh(
        self,
        *,
        owner_user_id: UUID,
        source_id: UUID,
        analysis_request_id: UUID | None,
        idempotency_key: str,
        operation: str,
    ) -> SourceRefreshAccepted:
        self.calls.append(
            {
                "owner_user_id": owner_user_id,
                "source_id": source_id,
                "analysis_request_id": analysis_request_id,
                "idempotency_key": idempotency_key,
                "operation": operation,
            }
        )
        key = (owner_user_id, source_id, idempotency_key)
        existing = self.records.get(key)
        if existing is not None:
            prior_request_id, prior_result = existing
            if prior_request_id != analysis_request_id:
                raise SourceRefreshIdempotencyConflict("different refresh payload")
            return prior_result
        result = SourceRefreshAccepted(source_id=source_id, job_id=REFRESH_JOB_ID)
        self.records[key] = (analysis_request_id, result)
        return result


def _version(
    source_version_id: UUID,
    *,
    collected_at: datetime,
) -> SourceVersionReadRecord:
    return SourceVersionReadRecord(
        source_id=SOURCE_ID,
        source_version_id=source_version_id,
        title="Synthetic version",
        source_type="career_page",
        canonical_url="https://careers.example.test/openings",
        content_hash="a" * 64,
        hash_profile_version="epick-content-sha256-v1",
        representation="static_html",
        collected_at=collected_at,
        published_at=UNKNOWN_DATE,
    )


def _evidence(evidence_id: UUID, *, chunk_order: int) -> EvidenceReadRecord:
    return EvidenceReadRecord(
        evidence_id=evidence_id,
        source_version_id=VERSION_TIE_HIGH_ID,
        section_title="Requirements",
        text_excerpt="Synthetic excerpt",
        locator={"kind": "normalized_text", "value": "Synthetic excerpt"},
        chunk_order=chunk_order,
        origin_kind="parser",
    )


def _service(
    *,
    ownership: _Ownership | None = None,
    policy: _Policy | None = None,
    reader: _Reader | None = None,
    retained_bodies: _RetainedBodies | None = None,
    jobs: _RefreshJobs | None = None,
) -> tuple[SourceVersionEvidenceService, _Ownership, _Policy, _Reader, _RefreshJobs]:
    ownership = ownership or _Ownership(
        sources={(OWNER_ID, SOURCE_ID): OwnedSourceRecord(SOURCE_ID, COMPANY_ID)},
        versions={
            (OWNER_ID, VERSION_TIE_HIGH_ID): OwnedSourceVersionRecord(
                SOURCE_ID,
                VERSION_TIE_HIGH_ID,
            )
        },
        revisions={
            (OWNER_ID, VERSION_TIE_HIGH_ID, REVISION_ID): OwnedSourceVersionRecord(
                SOURCE_ID,
                VERSION_TIE_HIGH_ID,
            )
        },
    )
    policy = policy or _Policy()
    reader = reader or _Reader()
    jobs = jobs or _RefreshJobs()
    return (
        SourceVersionEvidenceService(
            ownership_port=ownership,
            policy_port=policy,
            read_port=reader,
            refresh_job_port=jobs,
            retained_body_port=retained_bodies,
        ),
        ownership,
        policy,
        reader,
        jobs,
    )


def test_list_versions_uses_descending_keyset_and_keeps_unknown_publication_date() -> None:
    reader = _Reader(
        versions=(
            _version(VERSION_TIE_HIGH_ID, collected_at=NOW),
            _version(VERSION_TIE_LOW_ID, collected_at=NOW),
            _version(VERSION_OLD_ID, collected_at=NOW - timedelta(minutes=1)),
        )
    )
    service, _, _, reader, _ = _service(reader=reader)

    first = service.list_source_versions(
        owner_user_id=OWNER_ID,
        source_id=SOURCE_ID,
        cursor=None,
        limit=2,
    )
    cursor = first["next_cursor"]
    assert cursor == (NOW, VERSION_TIE_LOW_ID)
    second = service.list_source_versions(
        owner_user_id=OWNER_ID,
        source_id=SOURCE_ID,
        cursor=[NOW.isoformat().replace("+00:00", "Z"), str(VERSION_TIE_LOW_ID)],
        limit=2,
    )

    assert [item["source_version_id"] for item in first["items"]] == [
        VERSION_TIE_HIGH_ID,
        VERSION_TIE_LOW_ID,
    ]
    assert [item["source_version_id"] for item in second["items"]] == [VERSION_OLD_ID]
    assert reader.version_calls == [
        (SOURCE_ID, None, 3),
        (SOURCE_ID, (NOW, VERSION_TIE_LOW_ID), 3),
    ]
    assert "published_at" not in first["items"][0]
    assert reader.versions[0].published_at == UNKNOWN_DATE


def test_version_list_rejects_nonpositive_limit_and_malformed_cursor() -> None:
    service, _, _, _, _ = _service()

    with pytest.raises(SourceVersionEvidenceInvalidInput):
        service.list_source_versions(
            owner_user_id=OWNER_ID,
            source_id=SOURCE_ID,
            cursor=None,
            limit=0,
        )
    with pytest.raises(SourceVersionEvidenceInvalidInput):
        service.list_source_versions(
            owner_user_id=OWNER_ID,
            source_id=SOURCE_ID,
            cursor=["not-a-time", "not-a-uuid"],
            limit=1,
        )


def test_version_list_collapses_unknown_and_non_owned_sources_to_one_not_found_error() -> None:
    service, _, _, _, _ = _service()

    for owner_user_id, source_id in ((OWNER_ID, OTHER_SOURCE_ID), (OTHER_OWNER_ID, SOURCE_ID)):
        with pytest.raises(SourceVersionEvidenceNotFound):
            service.list_source_versions(
                owner_user_id=owner_user_id,
                source_id=source_id,
                cursor=None,
                limit=1,
            )


def test_evidence_collapses_unknown_version_and_mismatched_revision_to_one_not_found_error() -> (
    None
):
    ownership = _Ownership(
        sources={(OWNER_ID, SOURCE_ID): OwnedSourceRecord(SOURCE_ID, COMPANY_ID)},
        versions={
            (OWNER_ID, VERSION_TIE_HIGH_ID): OwnedSourceVersionRecord(
                SOURCE_ID,
                VERSION_TIE_HIGH_ID,
            )
        },
    )
    reader = _Reader()
    service, _, policy, reader, _ = _service(ownership=ownership, reader=reader)

    for source_version_id, extraction_revision_id in (
        (VERSION_TIE_HIGH_ID, REVISION_ID),
        (VERSION_OLD_ID, OTHER_REVISION_ID),
    ):
        with pytest.raises(SourceVersionEvidenceNotFound):
            service.list_evidence(
                owner_user_id=OWNER_ID,
                source_version_id=source_version_id,
                extraction_revision_id=extraction_revision_id,
                cursor=None,
                limit=1,
            )
    assert policy.calls == []
    assert reader.evidence_calls == []


def test_evidence_current_policy_block_is_distinct_from_not_found() -> None:
    reader = _Reader(
        evidence={
            (VERSION_TIE_HIGH_ID, REVISION_ID): (_evidence(EVIDENCE_FIRST_ID, chunk_order=0),)
        }
    )
    policy = _Policy(blocked={PolicyOperation.STORE_EXCERPT})
    service, _, policy, reader, _ = _service(policy=policy, reader=reader)

    with pytest.raises(PolicyBlocked):
        service.list_evidence(
            owner_user_id=OWNER_ID,
            source_version_id=VERSION_TIE_HIGH_ID,
            extraction_revision_id=REVISION_ID,
            cursor=None,
            limit=1,
        )
    assert policy.calls == [(SOURCE_ID, PolicyOperation.STORE_EXCERPT)]
    assert reader.evidence_calls == []
    assert reader.origin_calls == []


@pytest.mark.parametrize("blocked_state", ["denied", "unknown"])
def test_evidence_redistribution_policy_is_fail_closed_before_read_models(
    blocked_state: str,
) -> None:
    reader = _Reader(
        evidence={
            (VERSION_TIE_HIGH_ID, REVISION_ID): (_evidence(EVIDENCE_FIRST_ID, chunk_order=0),)
        }
    )
    retained_bodies = _RetainedBodies(RetainedBodyReference("retained-body:opaque-01"))
    policy = _Policy(blocked_states={PolicyOperation.REDISTRIBUTE: blocked_state})
    service, _, policy, reader, _ = _service(
        policy=policy,
        reader=reader,
        retained_bodies=retained_bodies,
    )

    with pytest.raises(PolicyBlocked):
        service.list_evidence(
            owner_user_id=OWNER_ID,
            source_version_id=VERSION_TIE_HIGH_ID,
            extraction_revision_id=REVISION_ID,
            cursor=None,
            limit=1,
        )

    assert policy.calls == [
        (SOURCE_ID, PolicyOperation.STORE_EXCERPT),
        (SOURCE_ID, PolicyOperation.REDISTRIBUTE),
    ]
    assert reader.evidence_calls == []
    assert reader.origin_calls == []
    assert retained_bodies.calls == []


def test_evidence_bodyless_response_is_excerpts_only_without_historical_body_claim() -> None:
    reader = _Reader(
        evidence={
            (VERSION_TIE_HIGH_ID, REVISION_ID): (_evidence(EVIDENCE_FIRST_ID, chunk_order=0),)
        }
    )
    service, _, _, _, _ = _service(reader=reader)

    result = service.list_evidence(
        owner_user_id=OWNER_ID,
        source_version_id=VERSION_TIE_HIGH_ID,
        extraction_revision_id=REVISION_ID,
        cursor=None,
        limit=1,
    )

    assert result["retention_scope"] == RetentionScope.EXCERPTS_ONLY.value
    assert result["body_ref"] is None
    assert not {"body_text", "body_path", "historical_full_text"} & set(result)


def test_evidence_exposes_only_an_opaque_currently_permitted_body_reference() -> None:
    reader = _Reader(
        evidence={
            (VERSION_TIE_HIGH_ID, REVISION_ID): (_evidence(EVIDENCE_FIRST_ID, chunk_order=0),)
        }
    )
    retained_bodies = _RetainedBodies(RetainedBodyReference("retained-body:opaque-01"))
    service, _, policy, _, _ = _service(reader=reader, retained_bodies=retained_bodies)

    result = service.list_evidence(
        owner_user_id=OWNER_ID,
        source_version_id=VERSION_TIE_HIGH_ID,
        extraction_revision_id=REVISION_ID,
        cursor=None,
        limit=1,
    )

    assert result["retention_scope"] == RetentionScope.NORMALIZED_BODY.value
    assert result["body_ref"] == "retained-body:opaque-01"
    assert retained_bodies.calls == [(SOURCE_ID, VERSION_TIE_HIGH_ID)]
    assert policy.calls == [
        (SOURCE_ID, PolicyOperation.STORE_EXCERPT),
        (SOURCE_ID, PolicyOperation.REDISTRIBUTE),
        (SOURCE_ID, PolicyOperation.STORE_BODY),
    ]
    assert not {"body_text", "body_path"} & set(result)


@pytest.mark.parametrize(
    "body_ref",
    (
        "retained-body:C:\\private\\body",
        "retained-body:/private/body",
        "retained-body:",
        f"retained-body:{'a' * 129}",
        "retained-body:opaque:second",
        "retained-body:https://example.test/body",
        "retained-body:opaque\n",
    ),
)
def test_retained_body_reference_rejects_paths_and_nonopaque_tokens(body_ref: str) -> None:
    with pytest.raises(ValueError, match="retained body reference is invalid"):
        RetainedBodyReference(body_ref)


def test_invalid_adapter_body_reference_fails_closed_without_leaking_the_raw_value() -> None:
    invalid_body_ref = "retained-body:C:\\private\\body"
    invalid_reference = object.__new__(RetainedBodyReference)
    object.__setattr__(invalid_reference, "body_ref", invalid_body_ref)
    reader = _Reader(
        evidence={
            (VERSION_TIE_HIGH_ID, REVISION_ID): (_evidence(EVIDENCE_FIRST_ID, chunk_order=0),)
        }
    )
    retained_bodies = _RetainedBodies(invalid_reference)
    service, _, _, _, _ = _service(reader=reader, retained_bodies=retained_bodies)

    with pytest.raises(SourceVersionEvidenceDependencyUnavailable) as error:
        service.list_evidence(
            owner_user_id=OWNER_ID,
            source_version_id=VERSION_TIE_HIGH_ID,
            extraction_revision_id=REVISION_ID,
            cursor=None,
            limit=1,
        )

    assert invalid_body_ref not in str(error.value)
    assert retained_bodies.calls == [(SOURCE_ID, VERSION_TIE_HIGH_ID)]


@pytest.mark.parametrize(
    "freshness_status",
    [FreshnessStatus.CURRENT, FreshnessStatus.STALE, FreshnessStatus.UNKNOWN],
)
def test_evidence_uses_injected_current_freshness_without_ttl_inference(
    freshness_status: FreshnessStatus,
) -> None:
    reader = _Reader(
        evidence={
            (VERSION_TIE_HIGH_ID, REVISION_ID): (_evidence(EVIDENCE_FIRST_ID, chunk_order=0),)
        },
        state=SourceCurrentReadState(
            current_restriction={"state": "current"},
            freshness_status=freshness_status,
        ),
    )
    service, _, _, _, _ = _service(reader=reader)

    result = service.list_evidence(
        owner_user_id=OWNER_ID,
        source_version_id=VERSION_TIE_HIGH_ID,
        extraction_revision_id=REVISION_ID,
        cursor=None,
        limit=1,
    )

    assert result["freshness_status"] == freshness_status.value


def test_evidence_preserves_explicit_origin_evidence_relationship_without_inference() -> None:
    first = _evidence(EVIDENCE_FIRST_ID, chunk_order=0)
    second = _evidence(EVIDENCE_SECOND_ID, chunk_order=1)
    origin = SourceOriginEvidenceRelation(
        origin_relation_id=ORIGIN_RELATION_ID,
        relationship_kind="derived_from",
        verification_status="verified",
        origin_source_id=None,
        origin_url="https://careers.example.test/origin",
        evidence_ids=(EVIDENCE_SECOND_ID,),
    )
    reader = _Reader(
        evidence={(VERSION_TIE_HIGH_ID, REVISION_ID): (first, second)},
        origins=(origin,),
    )
    service, _, _, reader, _ = _service(reader=reader)

    result = service.list_evidence(
        owner_user_id=OWNER_ID,
        source_version_id=VERSION_TIE_HIGH_ID,
        extraction_revision_id=REVISION_ID,
        cursor=None,
        limit=2,
    )

    assert reader.origin_calls == [(SOURCE_ID, (EVIDENCE_FIRST_ID, EVIDENCE_SECOND_ID))]
    assert result["origin_relations"] == (
        {
            "origin_relation_id": ORIGIN_RELATION_ID,
            "relationship_kind": "derived_from",
            "verification_status": "verified",
            "origin_source_id": None,
            "origin_url": "https://careers.example.test/origin",
            "evidence_ids": (EVIDENCE_SECOND_ID,),
        },
    )


def test_refresh_rechecks_current_policy_and_delegates_replay_and_conflict_to_w1() -> None:
    service, _, policy, _, jobs = _service()

    first = service.refresh_source(
        owner_user_id=OWNER_ID,
        source_id=SOURCE_ID,
        analysis_request_id=REVISION_ID,
        idempotency_key="refresh-key",
    )
    replay = service.refresh_source(
        owner_user_id=OWNER_ID,
        source_id=SOURCE_ID,
        analysis_request_id=REVISION_ID,
        idempotency_key="refresh-key",
    )
    with pytest.raises(SourceRefreshIdempotencyConflict):
        service.refresh_source(
            owner_user_id=OWNER_ID,
            source_id=SOURCE_ID,
            analysis_request_id=OTHER_REVISION_ID,
            idempotency_key="refresh-key",
        )

    assert (
        first
        == replay
        == {
            "source_id": SOURCE_ID,
            "job_id": REFRESH_JOB_ID,
            "status_url": f"/api/v1/jobs/{REFRESH_JOB_ID}",
        }
    )
    assert policy.calls == [(SOURCE_ID, PolicyOperation.FETCH)] * 3
    assert [call["operation"] for call in jobs.calls] == [
        f"{SOURCE_REFRESH_OPERATION}#{SOURCE_ID}"
    ] * 3


def test_refresh_accepts_queued_w1_result_without_retry_fetch_or_completed_history_mutation() -> (
    None
):
    jobs = _RefreshJobs(queued=True)
    service, _, _, _, jobs = _service(jobs=jobs)
    completed_before = dict(jobs.completed_jobs)

    result = service.refresh_source(
        owner_user_id=OWNER_ID,
        source_id=SOURCE_ID,
        analysis_request_id=None,
        idempotency_key="queued-refresh",
    )

    assert result["job_id"] == REFRESH_JOB_ID
    assert jobs.completed_jobs == completed_before
    assert jobs.fetch_calls == 0


def test_version_service_does_not_expose_a_user_retry_boundary() -> None:
    service, _, _, _, _ = _service()

    assert not hasattr(service, "retry_source")
