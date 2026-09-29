from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from uuid import UUID

import pytest

from epick_engine.source_collection.contracts import (
    CollectionStage,
    ExtractionStatus,
    Failure,
    PostingSection,
    PostingSectionKind,
)
from epick_engine.source_collection.service import (
    JobPostingContentRecord,
    JobPostingContentUnavailable,
    JobPostingEvidenceSnapshot,
    JobPostingFailureReference,
    JobPostingImportAccepted,
    JobPostingNotFound,
    JobPostingRecord,
    JobPostingRetryResult,
    JobPostingSelectionInvalid,
    JobPostingService,
)

OWNER_ID = UUID("00000000-0000-4000-8000-000000009101")
OTHER_OWNER_ID = UUID("00000000-0000-4000-8000-000000009102")
COMPANY_ID = UUID("00000000-0000-4000-8000-000000000201")
OTHER_COMPANY_ID = UUID("00000000-0000-4000-8000-000000000202")
SOURCE_ID = UUID("00000000-0000-4000-8000-000000000501")
OTHER_SOURCE_ID = UUID("00000000-0000-4000-8000-000000000502")
CURRENT_VERSION_ID = UUID("00000000-0000-4000-8000-000000000601")
EARLIER_VERSION_ID = UUID("00000000-0000-4000-8000-000000000602")
CURRENT_REVISION_ID = UUID("00000000-0000-4000-8000-000000000701")
LATER_PARTIAL_REVISION_ID = UUID("00000000-0000-4000-8000-000000000702")
EARLIER_REVISION_ID = UUID("00000000-0000-4000-8000-000000000703")
OTHER_REVISION_ID = UUID("00000000-0000-4000-8000-000000000704")
JOB_POSTING_ID = UUID("00000000-0000-4000-8000-000000000801")
CANONICAL_JOB_POSTING_ID = UUID("00000000-0000-4000-8000-000000000802")
IMPORT_JOB_ID = UUID("00000000-0000-4000-8000-000000000901")
RETRY_JOB_ID = UUID("00000000-0000-4000-8000-000000000902")
FIRST_EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000a01")
SECOND_EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000a02")
MISSING_EVIDENCE_ID = UUID("00000000-0000-4000-8000-000000000a03")
NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


@dataclass
class _Repository:
    owned: dict[tuple[UUID, UUID], JobPostingRecord] = field(default_factory=dict)
    content: tuple[JobPostingContentRecord, ...] = ()
    owned_calls: list[tuple[UUID, UUID]] = field(default_factory=list)
    content_calls: list[tuple[UUID, UUID]] = field(default_factory=list)

    def find_owned_job_posting(
        self,
        *,
        owner_user_id: UUID,
        job_posting_id: UUID,
    ) -> JobPostingRecord | None:
        self.owned_calls.append((owner_user_id, job_posting_id))
        return self.owned.get((owner_user_id, job_posting_id))

    def list_job_posting_content(
        self,
        *,
        source_id: UUID,
        company_id: UUID,
    ) -> tuple[JobPostingContentRecord, ...]:
        self.content_calls.append((source_id, company_id))
        return self.content


@dataclass
class _Jobs:
    import_accepted: JobPostingImportAccepted
    retry_result: JobPostingRetryResult
    failure: JobPostingFailureReference | None = None
    import_calls: list[dict[str, object]] = field(default_factory=list)
    retry_calls: list[dict[str, object]] = field(default_factory=list)
    failure_calls: list[tuple[UUID, UUID]] = field(default_factory=list)

    def import_job_posting(
        self,
        *,
        owner_user_id: UUID,
        company_id: UUID,
        url: str,
        analysis_request_id: str | None,
        idempotency_key: str,
    ) -> JobPostingImportAccepted:
        self.import_calls.append(
            {
                "owner_user_id": owner_user_id,
                "company_id": company_id,
                "url": url,
                "analysis_request_id": analysis_request_id,
                "idempotency_key": idempotency_key,
            }
        )
        return self.import_accepted

    def retry_job_posting(
        self,
        *,
        owner_user_id: UUID,
        job_posting_id: UUID,
        job_id: UUID,
        expected_input_version: int,
        expected_result_version: int,
        idempotency_key: str,
    ) -> JobPostingRetryResult:
        self.retry_calls.append(
            {
                "owner_user_id": owner_user_id,
                "job_posting_id": job_posting_id,
                "job_id": job_id,
                "expected_input_version": expected_input_version,
                "expected_result_version": expected_result_version,
                "idempotency_key": idempotency_key,
            }
        )
        return self.retry_result

    def read_failure_reference(
        self,
        *,
        owner_user_id: UUID,
        job_posting_id: UUID,
    ) -> JobPostingFailureReference | None:
        self.failure_calls.append((owner_user_id, job_posting_id))
        return self.failure


def _posting(
    *,
    job_posting_id: UUID = JOB_POSTING_ID,
    current_source_version_id: UUID | None = CURRENT_VERSION_ID,
) -> JobPostingRecord:
    return JobPostingRecord(
        job_posting_id=job_posting_id,
        company_id=COMPANY_ID,
        source_id=SOURCE_ID,
        current_source_version_id=current_source_version_id,
    )


def _content(
    *,
    source_version_id: UUID = CURRENT_VERSION_ID,
    source_id: UUID = SOURCE_ID,
    company_id: UUID = COMPANY_ID,
    extraction_revision_id: UUID = CURRENT_REVISION_ID,
    created_at: datetime = NOW,
    extraction_status: ExtractionStatus = ExtractionStatus.COMPLETE,
    posting_sections: tuple[PostingSection, ...] = (),
    evidence: tuple[JobPostingEvidenceSnapshot, ...] = (),
) -> JobPostingContentRecord:
    return JobPostingContentRecord(
        source_version_id=source_version_id,
        source_id=source_id,
        company_id=company_id,
        title="Synthetic platform engineer",
        extraction_revision_id=extraction_revision_id,
        created_at=created_at,
        extraction_status=extraction_status,
        posting_sections=posting_sections,
        limitations=(),
        evidence=evidence,
    )


def _evidence(
    *,
    evidence_id: UUID = FIRST_EVIDENCE_ID,
    source_version_id: UUID = CURRENT_VERSION_ID,
    text_excerpt: str = "First excerpt",
) -> JobPostingEvidenceSnapshot:
    return JobPostingEvidenceSnapshot(
        evidence_id=evidence_id,
        source_version_id=source_version_id,
        text_excerpt=text_excerpt,
    )


def _section(*, evidence_ids: tuple[UUID, ...]) -> PostingSection:
    return PostingSection(
        section_key="required-0",
        kind=PostingSectionKind.REQUIRED,
        heading_raw="Requirements",
        text_raw="Python experience",
        evidence_ids=list(evidence_ids),
        order=0,
        relation_text=None,
    )


def _import_accepted(
    *,
    job_posting_id: UUID = CANONICAL_JOB_POSTING_ID,
    source_id: UUID = SOURCE_ID,
    company_id: UUID = COMPANY_ID,
    job_id: UUID = IMPORT_JOB_ID,
) -> JobPostingImportAccepted:
    return JobPostingImportAccepted(
        job_posting_id=job_posting_id,
        source_id=source_id,
        company_id=company_id,
        job_id=job_id,
    )


def _failure_reference() -> JobPostingFailureReference:
    return JobPostingFailureReference(
        failure=Failure(
            source_id=SOURCE_ID,
            stage=CollectionStage.PARSE,
            code="PARTIAL_EXTRACTION",
            missing_sections=["deadline"],
            impact="일부 공고 구역을 확인하지 못했습니다.",
            core_decision_revision=1,
        ),
        checkpoint_ref="checkpoint:parse",
        required_actions=(),
    )


def _service(
    *,
    repository: _Repository | None = None,
    jobs: _Jobs | None = None,
) -> tuple[JobPostingService, _Repository, _Jobs]:
    posting = _posting()
    repository = repository or _Repository(
        owned={(OWNER_ID, JOB_POSTING_ID): posting},
        content=(_content(),),
    )
    jobs = jobs or _Jobs(
        import_accepted=_import_accepted(),
        retry_result=JobPostingRetryResult(
            job_posting_id=JOB_POSTING_ID,
            job_id=IMPORT_JOB_ID,
            resume_stage=CollectionStage.PARSE,
        ),
    )
    return JobPostingService(repository=repository, job_port=jobs), repository, jobs


def test_import_projects_atomic_canonical_acceptance_and_delegates_exact_payload() -> None:
    service, repository, jobs = _service()

    first = service.import_job_posting(
        owner_user_id=OWNER_ID,
        company_id=COMPANY_ID,
        url="https://careers.example.test/jobs/platform-engineer",
        analysis_request_id="analysis-001",
        idempotency_key="import-key-001",
    )
    replay = service.import_job_posting(
        owner_user_id=OWNER_ID,
        company_id=COMPANY_ID,
        url="https://careers.example.test/jobs/platform-engineer",
        analysis_request_id="analysis-001",
        idempotency_key="import-key-001",
    )

    assert first == replay
    assert first.job_posting_id == CANONICAL_JOB_POSTING_ID
    assert first.source_id == SOURCE_ID
    assert first.job_id == IMPORT_JOB_ID
    assert repository.owned_calls == []
    assert jobs.import_calls == [
        {
            "owner_user_id": OWNER_ID,
            "company_id": COMPANY_ID,
            "url": "https://careers.example.test/jobs/platform-engineer",
            "analysis_request_id": "analysis-001",
            "idempotency_key": "import-key-001",
        },
        {
            "owner_user_id": OWNER_ID,
            "company_id": COMPANY_ID,
            "url": "https://careers.example.test/jobs/platform-engineer",
            "analysis_request_id": "analysis-001",
            "idempotency_key": "import-key-001",
        },
    ]


def test_import_rejects_atomic_acceptance_with_other_company() -> None:
    jobs = _Jobs(
        import_accepted=_import_accepted(company_id=OTHER_COMPANY_ID),
        retry_result=JobPostingRetryResult(
            job_posting_id=JOB_POSTING_ID,
            job_id=IMPORT_JOB_ID,
            resume_stage=CollectionStage.PARSE,
        ),
    )
    service, repository, _jobs = _service(jobs=jobs)

    with pytest.raises(JobPostingSelectionInvalid):
        service.import_job_posting(
            owner_user_id=OWNER_ID,
            company_id=COMPANY_ID,
            url="https://careers.example.test/jobs/platform-engineer",
            analysis_request_id=None,
            idempotency_key="import-key-mismatch",
        )

    assert repository.owned_calls == []
    assert jobs.import_calls


def test_get_defaults_to_current_version_and_latest_partial_revision_with_failure_reference() -> (
    None
):
    partial = _content(
        extraction_revision_id=LATER_PARTIAL_REVISION_ID,
        extraction_status=ExtractionStatus.PARTIAL,
    )
    same_timestamp_complete = _content(extraction_revision_id=CURRENT_REVISION_ID)
    earlier = _content(
        extraction_revision_id=EARLIER_REVISION_ID,
        created_at=datetime(2026, 9, 9, 12, tzinfo=UTC),
    )
    failure = _failure_reference()
    repository = _Repository(
        owned={(OWNER_ID, JOB_POSTING_ID): _posting()},
        content=(earlier, same_timestamp_complete, partial),
    )
    jobs = _Jobs(
        import_accepted=_import_accepted(),
        retry_result=JobPostingRetryResult(
            job_posting_id=JOB_POSTING_ID,
            job_id=IMPORT_JOB_ID,
            resume_stage=CollectionStage.PARSE,
        ),
        failure=failure,
    )
    service, _repository, _jobs = _service(repository=repository, jobs=jobs)

    view = service.get_job_posting(
        owner_user_id=OWNER_ID,
        job_posting_id=JOB_POSTING_ID,
        source_version_id=None,
        extraction_revision_id=None,
    )

    assert view.source_version_id == CURRENT_VERSION_ID
    assert view.extraction_revision_id == LATER_PARTIAL_REVISION_ID
    assert view.extraction_status is ExtractionStatus.PARTIAL
    assert view.failure_reference == failure
    assert repository.content_calls == [(SOURCE_ID, COMPANY_ID)]
    assert jobs.failure_calls == [(OWNER_ID, JOB_POSTING_ID)]


def test_get_keeps_section_evidence_order_for_excerpt_projection() -> None:
    section = _section(evidence_ids=(SECOND_EVIDENCE_ID, FIRST_EVIDENCE_ID))
    evidence = (
        _evidence(evidence_id=FIRST_EVIDENCE_ID, text_excerpt="First excerpt"),
        _evidence(evidence_id=SECOND_EVIDENCE_ID, text_excerpt="Second excerpt"),
    )
    repository = _Repository(
        owned={(OWNER_ID, JOB_POSTING_ID): _posting()},
        content=(_content(posting_sections=(section,), evidence=evidence),),
    )
    service, _repository, _jobs = _service(repository=repository)

    view = service.get_job_posting(
        owner_user_id=OWNER_ID,
        job_posting_id=JOB_POSTING_ID,
    )

    evidence_by_id = {item.evidence_id: item for item in view.evidence}
    assert view.sections == (section,)
    assert tuple(
        evidence_by_id[evidence_id].text_excerpt for evidence_id in view.sections[0].evidence_ids
    ) == ("Second excerpt", "First excerpt")


@pytest.mark.parametrize(
    ("posting_sections", "evidence"),
    [
        pytest.param(
            (_section(evidence_ids=(MISSING_EVIDENCE_ID,)),),
            (_evidence(),),
            id="missing-section-evidence",
        ),
        pytest.param(
            (_section(evidence_ids=(FIRST_EVIDENCE_ID,)),),
            (
                _evidence(text_excerpt="First copy"),
                _evidence(text_excerpt="Duplicate copy"),
            ),
            id="duplicate-evidence-id",
        ),
        pytest.param(
            (_section(evidence_ids=(FIRST_EVIDENCE_ID,)),),
            (_evidence(source_version_id=EARLIER_VERSION_ID),),
            id="cross-version-evidence",
        ),
    ],
)
def test_get_rejects_evidence_snapshot_outside_selected_version(
    posting_sections: tuple[PostingSection, ...],
    evidence: tuple[JobPostingEvidenceSnapshot, ...],
) -> None:
    repository = _Repository(
        owned={(OWNER_ID, JOB_POSTING_ID): _posting()},
        content=(_content(posting_sections=posting_sections, evidence=evidence),),
    )
    service, _repository, jobs = _service(repository=repository)

    with pytest.raises(JobPostingSelectionInvalid):
        service.get_job_posting(
            owner_user_id=OWNER_ID,
            job_posting_id=JOB_POSTING_ID,
        )

    assert jobs.failure_calls == []


@pytest.mark.parametrize(
    "content,source_version_id,extraction_revision_id",
    [
        (
            (_content(source_version_id=CURRENT_VERSION_ID, source_id=OTHER_SOURCE_ID),),
            CURRENT_VERSION_ID,
            None,
        ),
        (
            (_content(source_version_id=CURRENT_VERSION_ID, company_id=OTHER_COMPANY_ID),),
            CURRENT_VERSION_ID,
            None,
        ),
        (
            (
                _content(),
                _content(
                    source_version_id=EARLIER_VERSION_ID, extraction_revision_id=OTHER_REVISION_ID
                ),
            ),
            CURRENT_VERSION_ID,
            OTHER_REVISION_ID,
        ),
    ],
)
def test_get_rejects_source_version_company_and_revision_membership_mismatches(
    content: tuple[JobPostingContentRecord, ...],
    source_version_id: UUID | None,
    extraction_revision_id: UUID | None,
) -> None:
    repository = _Repository(
        owned={(OWNER_ID, JOB_POSTING_ID): _posting()},
        content=content,
    )
    service, _repository, jobs = _service(
        repository=repository,
        jobs=_Jobs(
            import_accepted=_import_accepted(),
            retry_result=JobPostingRetryResult(
                job_posting_id=JOB_POSTING_ID,
                job_id=IMPORT_JOB_ID,
                resume_stage=CollectionStage.PARSE,
            ),
            failure=_failure_reference(),
        ),
    )

    with pytest.raises(JobPostingSelectionInvalid):
        service.get_job_posting(
            owner_user_id=OWNER_ID,
            job_posting_id=JOB_POSTING_ID,
            source_version_id=source_version_id,
            extraction_revision_id=extraction_revision_id,
        )

    assert jobs.failure_calls == []


@pytest.mark.parametrize(
    ("posting", "content", "expected_source_version_id"),
    [
        pytest.param(
            _posting(current_source_version_id=None),
            (),
            None,
            id="no-current-version",
        ),
        pytest.param(
            _posting(),
            (_content(extraction_status=ExtractionStatus.FAILED),),
            CURRENT_VERSION_ID,
            id="current-version-has-no-usable-revision",
        ),
    ],
)
def test_get_returns_safe_failure_when_content_is_unavailable(
    posting: JobPostingRecord,
    content: tuple[JobPostingContentRecord, ...],
    expected_source_version_id: UUID | None,
) -> None:
    failure = _failure_reference()
    repository = _Repository(
        owned={(OWNER_ID, JOB_POSTING_ID): posting},
        content=content,
    )
    jobs = _Jobs(
        import_accepted=_import_accepted(),
        retry_result=JobPostingRetryResult(
            job_posting_id=JOB_POSTING_ID,
            job_id=IMPORT_JOB_ID,
            resume_stage=CollectionStage.PARSE,
        ),
        failure=failure,
    )
    service, _repository, _jobs = _service(repository=repository, jobs=jobs)

    view = service.get_job_posting(
        owner_user_id=OWNER_ID,
        job_posting_id=JOB_POSTING_ID,
    )

    assert view.source_version_id == expected_source_version_id
    assert view.extraction_revision_id is None
    assert view.title is None
    assert view.extraction_status is None
    assert view.sections == ()
    assert view.limitations == ()
    assert view.evidence == ()
    assert view.failure_reference == failure
    assert jobs.failure_calls == [(OWNER_ID, JOB_POSTING_ID)]


def test_get_without_content_or_failure_is_unavailable() -> None:
    repository = _Repository(owned={(OWNER_ID, JOB_POSTING_ID): _posting()}, content=())
    service, _repository, jobs = _service(repository=repository)

    with pytest.raises(JobPostingContentUnavailable):
        service.get_job_posting(
            owner_user_id=OWNER_ID,
            job_posting_id=JOB_POSTING_ID,
        )

    assert jobs.failure_calls == [(OWNER_ID, JOB_POSTING_ID)]


def test_get_hides_unknown_and_non_owned_postings_before_job_port() -> None:
    service, repository, jobs = _service()

    with pytest.raises(JobPostingNotFound) as unknown:
        service.get_job_posting(
            owner_user_id=OWNER_ID,
            job_posting_id=UUID("00000000-0000-4000-8000-000000000899"),
            source_version_id=None,
            extraction_revision_id=None,
        )
    with pytest.raises(JobPostingNotFound) as non_owned:
        service.get_job_posting(
            owner_user_id=OTHER_OWNER_ID,
            job_posting_id=JOB_POSTING_ID,
            source_version_id=None,
            extraction_revision_id=None,
        )
    assert str(unknown.value) == str(non_owned.value)
    assert repository.content_calls == []
    assert jobs.failure_calls == []


def test_job_posting_service_does_not_expose_a_user_retry_boundary() -> None:
    service, _, _ = _service()

    assert not hasattr(service, "retry_job_posting")
