"""PostgreSQL coverage for public source-event outbox delivery."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import threading
import time
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, HTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, event, select, text
from sqlalchemy.orm import Session, sessionmaker

from epick_engine.source_collection.contracts import (
    SourceEnvelope,
    SourceEvent,
    SourceObservationSnapshot,
)
from epick_engine.source_collection.persistence import Base, Company, OutboxEvent, Source
from epick_engine.source_collection.w3_public_transport import W3PublicEventPublisher
from epick_engine.source_collection.worker import (
    OutboxDeliveryError,
    SourceEventAcknowledgement,
    SourceOutboxDeliveryWorker,
    SqlAlchemyOutboxDeliveryStore,
)

pytestmark = pytest.mark.approved_postgres

NOW = datetime(2026, 9, 11, tzinfo=UTC)
_DEFAULT_ACKNOWLEDGEMENT = object()


@dataclass
class RecordingPublisher:
    """In-memory W3 boundary that only accepts the public SourceEvent contract."""

    acknowledgement: object = _DEFAULT_ACKNOWLEDGEMENT
    error: Exception | None = None
    events: list[SourceEvent] = field(default_factory=list)
    private_result_calls: int = 0

    def publish(self, source_event: SourceEvent) -> SourceEventAcknowledgement:
        self.events.append(source_event)
        if self.error is not None:
            raise self.error
        if self.acknowledgement is not _DEFAULT_ACKNOWLEDGEMENT:
            return self.acknowledgement  # type: ignore[return-value]
        return SourceEventAcknowledgement(event_id=source_event.event_id)

    def publish_private_result(self, *_args: object, **_kwargs: object) -> None:
        self.private_result_calls += 1
        raise AssertionError("private result channel must not be used for public events")


@pytest.fixture
def database_engine(approved_postgres_url) -> Iterator[Engine]:
    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_w2_outbox_{uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))

    engine = admin_engine.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def session_factory(database_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(database_engine, expire_on_commit=False)


def _seed_source(session_factory: sessionmaker[Session]) -> Source:
    company = Company(
        company_id=uuid4(),
        legal_name="Outbox Test Company Ltd.",
        aliases=["Outbox Test Company"],
        official_domains=["example.test"],
        legal_identifiers={"registration": "OUTBOX-001"},
        identity_status="verified",
        identity_evidence=["synthetic://company-evidence"],
    )
    source = Source(
        source_id=uuid4(),
        company_id=company.company_id,
        source_type="job_posting",
        canonical_url=f"https://jobs.example.test/opening/{uuid4().hex}",
        title="Outbox Platform Engineer",
    )
    with session_factory.begin() as session:
        session.add_all([company, source])
    return source


def _observation_event(
    source: Source,
    *,
    aggregate_revision: int,
    occurred_at: datetime = NOW,
    event_id: UUID | None = None,
) -> SourceEvent:
    return SourceEvent(
        event_id=event_id or uuid4(),
        event_type="source.observation.changed",
        schema_version="w2.source.v1",
        aggregate_id=source.source_id,
        aggregate_revision=aggregate_revision,
        occurred_at=occurred_at,
        payload=SourceObservationSnapshot(
            observation_id=uuid4(),
            source_id=source.source_id,
            source_version_id=None,
            policy_decision_id=None,
            observed_at=occurred_at,
            access_class="public",
            acquisition_status="AVAILABLE",
            http_status=200,
            checked_url=source.canonical_url,
            error_code=None,
            representation="static_html",
        ),
    )


def _restriction_event(source: Source, *, aggregate_revision: int) -> SourceEvent:
    return SourceEvent.model_validate(
        {
            "event_id": uuid4(),
            "event_type": "source.restriction.changed",
            "schema_version": "w2.source.v1",
            "aggregate_id": source.source_id,
            "aggregate_revision": aggregate_revision,
            "occurred_at": NOW,
            "payload": {
                "restriction_id": uuid4(),
                "source_id": source.source_id,
                "source_version_id": None,
                "restriction_revision": 1,
                "restriction_status": "active",
                "accuracy_status": "error_confirmed",
                "reason_code": "CONFIRMED_ERROR",
                "changed_at": NOW,
                "replacement_ref": None,
            },
        }
    )


def _unknown_date_payload() -> dict[str, object]:
    return {
        "status": "unknown",
        "raw_text": None,
        "value": None,
        "precision": None,
        "timezone": None,
    }


def _version_event(
    source: Source,
    *,
    aggregate_revision: int,
    occurred_at: datetime = NOW,
) -> SourceEvent:
    source_version_id = uuid4()
    evidence_id = uuid4()
    return SourceEvent.model_validate(
        {
            "event_id": uuid4(),
            "event_type": "source.version.available",
            "schema_version": "w2.source.v1",
            "aggregate_id": source.source_id,
            "aggregate_revision": aggregate_revision,
            "occurred_at": occurred_at,
            "payload": {
                "schema_version": "w2.source.v1",
                "source_id": source.source_id,
                "source_version_id": source_version_id,
                "extraction_revision_id": uuid4(),
                "company_id": source.company_id,
                "source_type": source.source_type,
                "url_or_path": source.canonical_url,
                "title": source.title,
                "policy": {
                    "policy_decision_id": uuid4(),
                    "official_status": "verified",
                    "access_class": "public",
                    "collection_permission": "allowed",
                    "excerpt_storage_permission": "allowed",
                    "body_storage_permission": "denied",
                    "redistribution_permission": "denied",
                    "checked_at": occurred_at,
                    "policy_version": "outbox-policy-v1",
                },
                "acquisition_status": "AVAILABLE",
                "extraction_status": "complete",
                "accuracy_status": "unverified",
                "freshness_status": "current",
                "published_at": _unknown_date_payload(),
                "collected_at": occurred_at,
                "checked_at": occurred_at,
                "valid_from": _unknown_date_payload(),
                "valid_to": _unknown_date_payload(),
                "content_hash": "a" * 64,
                "hash_profile_version": "outbox-hash-v1",
                "parser_version": "outbox-parser-v1",
                "language": "en",
                "evidence_spans": [
                    {
                        "evidence_id": evidence_id,
                        "source_version_id": source_version_id,
                        "section_title": "Requirements",
                        "text_excerpt": "Python experience",
                        "locator": {
                            "kind": "css",
                            "value": "#requirements",
                            "normalization_version": None,
                            "start": None,
                            "end": None,
                        },
                        "chunk_order": 0,
                    }
                ],
                "posting_sections": [
                    {
                        "section_key": "required",
                        "kind": "required",
                        "heading_raw": "Requirements",
                        "text_raw": "Python experience",
                        "evidence_ids": [evidence_id],
                        "order": 0,
                        "relation_text": None,
                    }
                ],
                "retention_scope": "excerpts_only",
                "normalized_body_ref": None,
                "limitations": [],
                "metadata": {
                    "representation": "static_html",
                    "content_type": "text/html",
                    "normalization_version": "outbox-normalization-v1",
                },
            },
        }
    )


def _seed_outbox(
    session_factory: sessionmaker[Session],
    source_event: SourceEvent,
    *,
    delivery_state: str = "pending",
    event_type: str | None = None,
    payload: dict[str, object] | None = None,
) -> None:
    with session_factory.begin() as session:
        session.add(
            OutboxEvent(
                event_id=source_event.event_id,
                aggregate_id=source_event.aggregate_id,
                aggregate_revision=source_event.aggregate_revision,
                event_type=event_type or source_event.event_type.value,
                schema_version=source_event.schema_version,
                payload=payload or source_event.payload.model_dump(mode="json"),
                occurred_at=source_event.occurred_at,
                delivery_state=delivery_state,
            )
        )


def _delivery_state(session_factory: sessionmaker[Session], event_id: UUID) -> str:
    with session_factory() as session:
        state = session.scalar(
            select(OutboxEvent.delivery_state).where(OutboxEvent.event_id == event_id)
        )
    assert state is not None
    return state


def _worker(
    session_factory: Callable[[], Session],
    publisher: RecordingPublisher,
) -> SourceOutboxDeliveryWorker:
    return SourceOutboxDeliveryWorker(
        publisher=publisher,
        store=SqlAlchemyOutboxDeliveryStore(session_factory=session_factory),
    )


def _deep_keys(value: object) -> set[str]:
    if isinstance(value, Mapping):
        return set(value).union(*(_deep_keys(item) for item in value.values()))
    if isinstance(value, list):
        return set().union(*(_deep_keys(item) for item in value))
    return set()


def test_postgres_outbox_to_w3_http_failure_retry_duplicate_and_process_restart(
    session_factory: sessionmaker[Session], tmp_path: Path
) -> None:
    """Send a committed W2 event to a separate W3 process using real HTTP."""
    source = _seed_source(session_factory)
    source_event = _version_event(source, aggregate_revision=1)
    _seed_outbox(session_factory, source_event)

    class AuthorityHandler(BaseHTTPRequestHandler):
        def log_message(self, *_args: object) -> None:
            pass

        def do_GET(self) -> None:
            if self.path != f"/internal/v1/sources/{source.source_id}/authority":
                self.send_error(404)
                return
            body = json.dumps({"source_id": str(source.source_id), "registered": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    authority = HTTPServer(("127.0.0.1", 0), AuthorityHandler)
    authority_thread = threading.Thread(target=authority.serve_forever, daemon=True)
    authority_thread.start()
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        w3_port = probe.getsockname()[1]
    w3_base = f"http://127.0.0.1:{w3_port}"
    w3_root = Path(__file__).resolve().parents[5] / "w3" / "Project_EPICK_Service"
    w3_python = w3_root / ".venv" / "Scripts" / "python.exe"
    assert w3_python.is_file(), "W3 copy's Python environment is required"
    environment = os.environ.copy()
    environment["PYTHONPATH"] = str(w3_root / "src")
    environment.update(
        W3_W2_TOKEN="local-w2-token",
        W3_OPERATOR_TOKEN="local-operator-token",
        W3_W4_TOKEN="local-w4-token",
        W3_SOURCE_AUTHORITY_ENDPOINT=f"http://127.0.0.1:{authority.server_port}",
        W3_SOURCE_AUTHORITY_TOKEN="local-authority-token",
    )
    command = [
        str(w3_python),
        "-m",
        "w3_knowledge.c01.http",
        "--db",
        str(tmp_path / "w3-restart.sqlite"),
        "--port",
        str(w3_port),
        "--restriction-scope",
        "version",
        "--max-ttl-seconds",
        "3600",
        "--source-authority",
        "w3_knowledge.c01.source_authority_http:create_source_authority",
    ]
    worker = SourceOutboxDeliveryWorker(
        publisher=W3PublicEventPublisher(
            endpoint=f"{w3_base}/c01/v1/events", bearer_token="local-w2-token"
        ),
        store=SqlAlchemyOutboxDeliveryStore(session_factory=session_factory),
    )

    def status() -> dict[str, object]:
        request = Request(
            f"{w3_base}/c01/v1/status/{source.source_id}",
            headers={"Authorization": "Bearer local-w4-token"},
        )
        with urlopen(request, timeout=3) as response:
            return json.load(response)

    def start_w3() -> subprocess.Popen[bytes]:
        process = subprocess.Popen(
            command,
            env=environment,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError(f"W3 exited during startup: {process.returncode}")
            try:
                status()
                return process
            except OSError:
                time.sleep(0.05)
        process.terminate()
        process.wait(timeout=5)
        raise AssertionError("W3 startup timed out")

    def stop_w3(process: subprocess.Popen[bytes]) -> None:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)

    try:
        with pytest.raises(OutboxDeliveryError):
            worker.deliver_pending(limit=1)
        assert _delivery_state(session_factory, source_event.event_id) == "pending"
        first = start_w3()
        try:
            assert worker.deliver_pending(limit=1) == 1
            assert _delivery_state(session_factory, source_event.event_id) == "delivered"
            assert status()["event_cursor"] == 1
            worker.replay(event_id=source_event.event_id)
            assert status()["event_cursor"] == 1
        finally:
            stop_w3(first)
        second = start_w3()
        try:
            assert status()["event_cursor"] == 1
            worker.replay(event_id=source_event.event_id)
            assert status()["event_cursor"] == 1
        finally:
            stop_w3(second)
    finally:
        authority.shutdown()
        authority_thread.join(timeout=3)
        authority.server_close()


def test_committed_pending_event_is_published_then_acknowledged_once(
    session_factory: sessionmaker[Session],
) -> None:
    source = _seed_source(session_factory)
    source_event = _observation_event(source, aggregate_revision=1)
    _seed_outbox(session_factory, source_event)
    publisher = RecordingPublisher()
    worker = _worker(session_factory, publisher)

    assert worker.deliver_pending(limit=10) == 1
    assert publisher.events == [source_event]
    assert _delivery_state(session_factory, source_event.event_id) == "delivered"

    assert worker.deliver_pending(limit=10) == 0
    assert publisher.events == [source_event]


def test_restriction_event_is_reconstructed_delivered_and_replayable(
    session_factory: sessionmaker[Session],
) -> None:
    source = _seed_source(session_factory)
    source_event = _restriction_event(source, aggregate_revision=1)
    _seed_outbox(session_factory, source_event)
    publisher = RecordingPublisher()
    worker = _worker(session_factory, publisher)

    assert worker.deliver_pending(limit=1) == 1
    assert publisher.events == [source_event]
    assert _delivery_state(session_factory, source_event.event_id) == "delivered"

    worker.replay(event_id=source_event.event_id)
    assert publisher.events == [source_event, source_event]


def test_publisher_failure_preserves_committed_source_sql_and_pending_event(
    session_factory: sessionmaker[Session],
) -> None:
    source = _seed_source(session_factory)
    source_event = _observation_event(source, aggregate_revision=1)
    _seed_outbox(session_factory, source_event)
    publisher = RecordingPublisher(error=RuntimeError("W3 index failure"))

    with pytest.raises(OutboxDeliveryError) as raised:
        _worker(session_factory, publisher).deliver_pending(limit=1)

    assert "W3 index failure" not in str(raised.value)
    assert _delivery_state(session_factory, source_event.event_id) == "pending"
    with session_factory() as session:
        persisted_source = session.get(Source, source.source_id)
        persisted_event = session.get(OutboxEvent, source_event.event_id)
    assert persisted_source is not None
    assert persisted_source.canonical_url == source.canonical_url
    assert persisted_event is not None
    assert persisted_event.payload == source_event.payload.model_dump(mode="json")


def test_ack_commit_failure_republishes_identical_snapshot_on_next_run(
    session_factory: sessionmaker[Session],
) -> None:
    source = _seed_source(session_factory)
    source_event = _observation_event(source, aggregate_revision=1)
    _seed_outbox(session_factory, source_event)
    publisher = RecordingPublisher()
    session_calls = 0

    def fail_ack_commit_session_factory() -> Session:
        nonlocal session_calls
        session_calls += 1
        session = session_factory()
        if session_calls == 2:

            @event.listens_for(session, "before_commit", once=True)
            def fail_ack_commit(_session: Session) -> None:
                raise RuntimeError("ack commit failure")

        return session

    with pytest.raises(OutboxDeliveryError) as raised:
        _worker(fail_ack_commit_session_factory, publisher).deliver_pending(limit=1)

    assert "ack commit failure" not in str(raised.value)
    assert _delivery_state(session_factory, source_event.event_id) == "pending"

    assert _worker(session_factory, publisher).deliver_pending(limit=1) == 1
    assert publisher.events == [source_event, source_event]
    assert _delivery_state(session_factory, source_event.event_id) == "delivered"


@pytest.mark.parametrize(
    "acknowledgement",
    [None, SourceEventAcknowledgement(event_id=uuid4())],
)
def test_missing_or_wrong_ack_keeps_pending_event(
    session_factory: sessionmaker[Session],
    acknowledgement: SourceEventAcknowledgement | None,
) -> None:
    source = _seed_source(session_factory)
    source_event = _observation_event(source, aggregate_revision=1)
    _seed_outbox(session_factory, source_event)
    publisher = RecordingPublisher(acknowledgement=acknowledgement)

    with pytest.raises(OutboxDeliveryError):
        _worker(session_factory, publisher).deliver_pending(limit=1)

    assert publisher.events == [source_event]
    assert _delivery_state(session_factory, source_event.event_id) == "pending"


def test_explicit_replay_republishes_delivered_immutable_snapshot(
    session_factory: sessionmaker[Session],
) -> None:
    source = _seed_source(session_factory)
    source_event = _observation_event(source, aggregate_revision=1)
    _seed_outbox(session_factory, source_event)
    publisher = RecordingPublisher()
    worker = _worker(session_factory, publisher)

    assert worker.deliver_pending(limit=1) == 1
    worker.replay(event_id=source_event.event_id)

    assert publisher.events == [source_event, source_event]
    assert _delivery_state(session_factory, source_event.event_id) == "delivered"


def test_version_available_envelope_reconstructs_and_replays_as_public_snapshot(
    session_factory: sessionmaker[Session],
) -> None:
    source = _seed_source(session_factory)
    source_event = _version_event(source, aggregate_revision=1)
    _seed_outbox(session_factory, source_event)
    publisher = RecordingPublisher()
    worker = _worker(session_factory, publisher)
    expected_snapshot = source_event.model_dump(mode="json")

    assert worker.deliver_pending(limit=1) == 1
    assert _delivery_state(session_factory, source_event.event_id) == "delivered"
    worker.replay(event_id=source_event.event_id)

    assert [item.model_dump(mode="json") for item in publisher.events] == [
        expected_snapshot,
        expected_snapshot,
    ]
    delivered_payload = publisher.events[0].payload
    assert isinstance(delivered_payload, SourceEnvelope)
    assert delivered_payload.retention_scope == "excerpts_only"
    assert delivered_payload.normalized_body_ref is None
    forbidden_keys = {
        "authenticated_owner_ref",
        "owner_user_id",
        "job_id",
        "project_id",
        "project_ref",
        "purpose_ref",
        "execution_fence",
        "owner_deletion_epoch",
        "body_text",
        "result_payload",
        "result_refs",
        "failures",
        "required_actions",
    }
    assert forbidden_keys.isdisjoint(_deep_keys(expected_snapshot))


def test_out_of_order_and_duplicate_delivery_is_not_merged_or_revised(
    session_factory: sessionmaker[Session],
) -> None:
    source = _seed_source(session_factory)
    revision_two = _observation_event(
        source,
        aggregate_revision=2,
        occurred_at=NOW - timedelta(minutes=1),
    )
    revision_one = _observation_event(
        source,
        aggregate_revision=1,
        occurred_at=NOW,
    )
    _seed_outbox(session_factory, revision_two)
    _seed_outbox(session_factory, revision_one)
    publisher = RecordingPublisher()
    worker = _worker(session_factory, publisher)

    assert worker.deliver_pending(limit=10) == 2
    worker.replay(event_id=revision_two.event_id)

    assert [
        (item.event_id, item.aggregate_revision, item.payload) for item in publisher.events
    ] == [
        (revision_two.event_id, 2, revision_two.payload),
        (revision_one.event_id, 1, revision_one.payload),
        (revision_two.event_id, 2, revision_two.payload),
    ]


def test_public_delivery_never_uses_private_result_channel_or_private_fields(
    session_factory: sessionmaker[Session],
) -> None:
    source = _seed_source(session_factory)
    source_event = _observation_event(source, aggregate_revision=1)
    _seed_outbox(session_factory, source_event)
    publisher = RecordingPublisher()

    assert _worker(session_factory, publisher).deliver_pending(limit=1) == 1

    delivered = publisher.events[0].model_dump(mode="json")
    rendered = str(delivered)
    forbidden_keys = {
        "body_text",
        "owner_user_id",
        "authenticated_owner_ref",
        "job_id",
        "project_id",
        "project_ref",
        "execution_fence",
        "result_payload",
        "failures",
        "required_actions",
    }
    assert forbidden_keys.isdisjoint(delivered)
    assert all(key not in rendered for key in forbidden_keys)
    assert publisher.private_result_calls == 0


@pytest.mark.parametrize(
    ("event_type", "payload"),
    [
        ("source.restriction.changed", {"body_text": "PRIVATE_OUTBOX_SECRET"}),
        ("source.observation.changed", {"body_text": "PRIVATE_OUTBOX_SECRET"}),
    ],
)
def test_unsupported_or_malformed_outbox_rows_fail_closed_without_secret_reflection(
    session_factory: sessionmaker[Session],
    event_type: str,
    payload: dict[str, object],
) -> None:
    source = _seed_source(session_factory)
    source_event = _observation_event(source, aggregate_revision=1)
    _seed_outbox(session_factory, source_event, event_type=event_type, payload=payload)
    publisher = RecordingPublisher()

    with pytest.raises(OutboxDeliveryError) as raised:
        _worker(session_factory, publisher).deliver_pending(limit=1)

    assert "PRIVATE_OUTBOX_SECRET" not in str(raised.value)
    assert publisher.events == []
    assert _delivery_state(session_factory, source_event.event_id) == "pending"
