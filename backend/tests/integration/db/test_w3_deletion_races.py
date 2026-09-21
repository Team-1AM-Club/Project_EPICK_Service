from __future__ import annotations

import json
import sys
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, select, text
from sqlalchemy.orm import Session

from app.models.application_workspace import Company
from app.models.identity import User
from app.models.jobs import InboxReceipt, Job, OutboxMessage
from app.models.sources import JobSourceLink, Source
from app.runtime.outbox_relay import OutboxRelay, QueueUrlRegistry
from app.runtime.sqs import InMemorySqsPort
from app.runtime.w3_deletion_worker import W3RetentionReceiptService, receipt_digest
from app.services.deletion import DeletionOrchestrationService
from app.services.w3_authority import W3AuthorityService

ROOT = Path(__file__).parents[4]
sys.path.insert(0, str(ROOT / "w3" / "Project_EPICK_Service" / "src"))

from w3_knowledge.core_decision import DecisionContext  # noqa: E402
from w3_knowledge.core_runtime import AnalysisPlan, Authorization, CoreRuntime  # noqa: E402

pytestmark = pytest.mark.postgres


class Authority:
    def __init__(self, *, owner_id: UUID, owner_epoch: int, job_id: UUID, source_id: UUID) -> None:
        self.value = Authorization(
            context=DecisionContext(
                job_id=job_id,
                company_id=UUID(int=2),
                source_id=source_id,
                analysis_input_version="race-v1",
            ),
            owner_id=owner_id,
            owner_epoch=owner_epoch,
            active=True,
        )

    def current(self, job_id, source_id):
        return self.value


def _plan(authority: Authority, request_id: int) -> AnalysisPlan:
    return AnalysisPlan(
        context=authority.value.context,
        analysis_request_id=UUID(int=request_id),
        analysis_request_issued_at=100,
        required_sources=[authority.value.context.source_id],
        optional_sources=[],
    )


@pytest.fixture(autouse=True)
def clean_race_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))
    yield


def test_deletion_first_blocks_late_w3_creation(tmp_path: Path) -> None:
    owner_id = uuid4()
    authority = Authority(
        owner_id=owner_id,
        owner_epoch=0,
        job_id=uuid4(),
        source_id=UUID(int=3),
    )
    runtime = CoreRuntime(tmp_path / "core.db")

    assert runtime.delete_owner(owner_id, deletion_epoch=1, now=100) == 0
    with pytest.raises(ValueError, match="OWNER_DELETED"):
        runtime.supply(_plan(authority, 10), authority, "late", now=100)


def test_send_first_is_purged_and_other_owner_remains(tmp_path: Path) -> None:
    runtime = CoreRuntime(tmp_path / "core.db")
    first = Authority(
        owner_id=UUID(int=4), owner_epoch=0, job_id=UUID(int=1), source_id=UUID(int=3)
    )
    other = Authority(
        owner_id=UUID(int=5), owner_epoch=0, job_id=UUID(int=6), source_id=UUID(int=3)
    )
    first_event = runtime.supply(_plan(first, 10), first, "first", now=100)
    other_event = runtime.supply(_plan(other, 11), other, "other", now=100)

    assert runtime.delete_owner(first.value.owner_id, deletion_epoch=1, now=101) == 1
    states = {item["event_id"]: item["state"] for item in runtime.inspect()}
    assert states[str(first_event.message_id)] == "DELETED"
    assert states[str(other_event.message_id)] == "PENDING"


def test_postgres_deletion_fences_authority_before_late_delivery(db_session: Session) -> None:
    owner = User(display_name="Race owner", locale="ko-KR", timezone="Asia/Seoul")
    company = Company(legal_name="Race Company", display_name="Race Company")
    db_session.add_all([owner, company])
    db_session.flush()
    source = Source(
        company_id=company.id,
        source_type="CAREERS",
        canonical_url="https://example.test/race",
        canonical_url_hash=f"race-{uuid4()}",
        url_normalization_version="v1",
        policy_version="policy-v1",
        policy_checked_at=datetime.now(UTC),
    )
    job = Job(
        owner_user_id=owner.id,
        job_type="SOURCE_COLLECTION",
        status="RUNNING",
        dispatch_status="CLAIMED",
        owner_deletion_epoch=0,
        analysis_input_version="race-v1",
    )
    db_session.add_all([source, job])
    db_session.flush()
    db_session.add(
        JobSourceLink(
            job_id=job.id,
            owner_user_id=owner.id,
            source_id=source.id,
            source_version_id=None,
            command_id=None,
            purpose_ref="W3_CORE_DECISION",
            analysis_input_version="race-v1",
        )
    )
    deletion = DeletionOrchestrationService(db_session)
    preview = deletion.create_account_deletion_preview(
        owner_user_id=owner.id, preview_token="race-delete"
    )
    deletion.confirm_and_start_account_deletion(
        owner_user_id=owner.id,
        deletion_request_id=preview.request.id,
        preview_token=preview.preview_token,
    )

    late = W3AuthorityService(db_session).current(job_id=job.id, source_id=source.id)
    assert late.owner_epoch == 1
    assert late.active is False


@pytest.mark.parametrize(
    ("registry_state", "expected_count"),
    [
        ("PERMANENTLY_RETIRED", 1),
        ("TRANSIENTLY_UNAVAILABLE", 0),
        ("UNKNOWN", 0),
    ],
)
def test_only_permanent_source_retirement_stages_w3_command(
    db_session: Session,
    registry_state: str,
    expected_count: int,
) -> None:
    from app.services.source_retirement import SourceRetirementService

    company = Company(legal_name=f"Source {registry_state}", display_name=registry_state)
    db_session.add(company)
    db_session.flush()
    source = Source(
        company_id=company.id,
        source_type="CAREERS",
        canonical_url=f"https://example.test/{registry_state.lower()}",
        canonical_url_hash=f"retirement-{uuid4()}",
        url_normalization_version="v1",
        policy_version="policy-v1",
        policy_checked_at=datetime.now(UTC),
    )
    db_session.add(source)
    db_session.flush()

    service = SourceRetirementService(db_session)
    first = service.stage_if_permanent(
        company_id=company.id,
        source_id=source.id,
        registry_revision="registry-v1",
        registry_state=registry_state,
    )
    repeated = service.stage_if_permanent(
        company_id=company.id,
        source_id=source.id,
        registry_revision="registry-v1",
        registry_state=registry_state,
    )

    commands = list(
        db_session.scalars(
            select(OutboxMessage).where(
                OutboxMessage.aggregate_type == "W3_SOURCE_RETIREMENT",
                OutboxMessage.aggregate_id == source.id,
            )
        )
    )
    assert len(commands) == expected_count
    if commands:
        assert first is repeated is commands[0]
        assert set(commands[0].payload) == {
            "schema_version",
            "command_id",
            "operation",
            "company_id",
            "source_id",
            "retired_at",
            "target_type",
            "target_ref",
        }
        assert commands[0].payload["target_ref"] == str(source.id)
    else:
        assert first is repeated is None


@pytest.mark.parametrize("outcome", ["APPLIED", "DUPLICATE", "STALE"])
def test_source_retirement_receipt_is_durable_and_idempotent(
    db_session: Session,
    outcome: str,
) -> None:
    from app.services.source_retirement import SourceRetirementService

    company = Company(legal_name=f"Receipt {outcome}", display_name=f"Receipt {outcome}")
    db_session.add(company)
    db_session.flush()
    source = Source(
        company_id=company.id,
        source_type="CAREERS",
        canonical_url=f"https://example.test/receipt/{outcome.lower()}",
        canonical_url_hash=f"receipt-{uuid4()}",
        url_normalization_version="v1",
        policy_version="policy-v1",
        policy_checked_at=datetime.now(UTC),
    )
    db_session.add(source)
    db_session.flush()
    message = SourceRetirementService(db_session).stage_if_permanent(
        company_id=company.id,
        source_id=source.id,
        registry_revision="registry-v1",
        registry_state="PERMANENTLY_RETIRED",
        retired_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    assert message is not None
    occurred_at = datetime.now(UTC).isoformat().replace("+00:00", "Z")
    body = json.dumps(
        {
            "schema_version": "w3.private.w1-lifecycle-receipt/1.0",
            "message_type": "w3.private.w1.lifecycle-receipt",
            "receipt_id": str(uuid4()),
            "occurred_at": occurred_at,
            "visibility_scope": "PRIVATE",
            "producer": "w3",
            "command_id": str(message.id),
            "target_ref": str(source.id),
            "operation": "RETIRE_SOURCE",
            "outcome": outcome,
            "affected_count": 1 if outcome == "APPLIED" else 0,
            "applied_epoch": None,
            "effective_at": "2026-09-20T00:00:00Z",
        }
    )
    service = W3RetentionReceiptService(db_session)

    first = service.apply(body=body)
    repeated = service.apply(body=body)

    assert first.duplicate_receipt is False
    assert repeated.duplicate_receipt is True
    receipt = db_session.get(InboxReceipt, ("w1.w3.retention-receipt", message.id))
    assert receipt is not None
    assert receipt.outcome_code == outcome
    assert receipt.payload_digest == receipt_digest(body)
    if outcome in {"APPLIED", "DUPLICATE"}:
        assert message.last_error_code is None
    else:
        assert message.last_error_code == f"W3_SOURCE_RETIREMENT_{outcome}"


def test_source_retirement_uses_same_dedicated_command_queue(
    db_session: Session,
    migrated_engine: Engine,
) -> None:
    from sqlalchemy.orm import sessionmaker

    from app.services.source_retirement import SourceRetirementService

    company = Company(legal_name="Relay Company", display_name="Relay Company")
    db_session.add(company)
    db_session.flush()
    source = Source(
        company_id=company.id,
        source_type="CAREERS",
        canonical_url="https://example.test/retirement-relay",
        canonical_url_hash=f"relay-{uuid4()}",
        url_normalization_version="v1",
        policy_version="policy-v1",
        policy_checked_at=datetime.now(UTC),
    )
    db_session.add(source)
    db_session.flush()
    message = SourceRetirementService(db_session).stage_if_permanent(
        company_id=company.id,
        source_id=source.id,
        registry_revision="registry-v1",
        registry_state="PERMANENTLY_RETIRED",
        retired_at=datetime(2026, 9, 20, tzinfo=UTC),
    )
    assert message is not None
    message_id = message.id
    expected_payload = dict(message.payload)
    db_session.commit()

    queue_url = "https://sqs.ap-northeast-2.amazonaws.com/123/w3-retention-command"
    sqs = InMemorySqsPort()
    factory = sessionmaker(bind=migrated_engine, autoflush=False, expire_on_commit=False)
    relay = OutboxRelay(
        session_factory=factory,
        sqs=sqs,
        queues=QueueUrlRegistry(
            w1_execution_queue_url=None,
            w3_retention_command_queue_url=queue_url,
        ),
        relay_id="w3-source-retirement-test",
    )

    result = relay.drain_once(limit=10)

    assert result.claimed == result.published == 1
    sent = sqs.sent_messages[0]
    assert sent.queue_url == queue_url
    assert json.loads(sent.body) == expected_payload
    assert sent.message_attributes == {
        "epick_message_type": "w1.private.w3.source-retirement.v1",
        "epick_schema_version": "w1.w3.source-retirement/1",
        "epick_message_id": str(message_id),
        "epick_command_id": str(message_id),
    }
    assert "company_id" not in sent.message_attributes
    assert "source_id" not in sent.message_attributes
