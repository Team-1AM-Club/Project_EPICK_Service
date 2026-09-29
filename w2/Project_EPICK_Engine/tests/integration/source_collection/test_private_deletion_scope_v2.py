"""PostgreSQL coverage for atomic W2-owned private deletion scope v2."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from time import monotonic, sleep
from typing import Literal
from uuid import UUID, uuid4

import pytest
from alembic import command as alembic_command
from alembic.config import Config
from sqlalchemy import Engine, create_engine, event, inspect, select, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from epick_engine.source_collection import private_deletion_operator_v2 as deletion_operator
from epick_engine.source_collection.commit_gate_contracts import (
    CommitGateCommand,
    parse_commit_gate_command,
    staged_result_digest,
)
from epick_engine.source_collection.commit_gate_store import (
    CommitGateRejected,
    PrivateCommitGateAck,
    PrivateCommitGateInbox,
    PrivateCommitGateReceipt,
    PrivateCommitStage,
    PrivateStagedOutbox,
    apply_commit_gate,
    canonical_ack_wire,
    stage_private_result,
)
from epick_engine.source_collection.contracts import CollectionCommand, CollectionResult
from epick_engine.source_collection.persistence import (
    Base,
    CollectionAttempt,
    CollectionRuntimeAttempt,
    Company,
    Evidence,
    OutboxEvent,
    ParserExecution,
    PersistenceConflict,
    PrivateDeletionOwnerState,
    PrivateDeletionProjectTombstone,
    PrivateDeletionReceipt,
    RequestDeduplication,
    Source,
    SourceObservation,
    SourcePolicyDecision,
    SourceVersion,
)
from epick_engine.source_collection.private_deletion_v2 import (
    PrivateDeletionAckV2,
    PrivateDeletionCommandV2,
    PrivateDeletionScope,
)
from epick_engine.source_collection.private_scope import (
    PrivateGateAuthority,
    PrivateScopeRejected,
    PrivateWriteAuthorityDecision,
    PrivateWriteScope,
    ScopeUnclassified,
)
from epick_engine.source_collection.source_runtime_store import reserve_collection_attempt
from epick_engine.source_collection.w1_private_authority_contracts import GateAuthorityResponse
from epick_engine.source_collection.w1_transport import W1Dispatch, parse_w1_dispatch

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures"
NOW = datetime(2032, 1, 2, 3, 4, tzinfo=UTC)
SIGNED_64_MAX = 9_223_372_036_854_775_807
OWNER_A = UUID("00000000-0000-4000-8000-000000008401")
OWNER_B = UUID("00000000-0000-4000-8000-000000008402")
PROJECT_A = UUID("00000000-0000-4000-8000-000000008411")
PROJECT_B = UUID("00000000-0000-4000-8000-000000008412")
PROJECT_FOREIGN = UUID("00000000-0000-4000-8000-000000008421")


@pytest.fixture
def database_engine(approved_postgres_url: URL) -> Iterator[Engine]:
    """Create a synthetic schema with bounded lock waits."""

    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)

    @event.listens_for(admin_engine, "connect")
    def set_isolated_test_timeouts(dbapi_connection, _) -> None:
        with dbapi_connection.cursor() as cursor:
            cursor.execute("SET lock_timeout = '1500ms'")
            cursor.execute("SET statement_timeout = '5000ms'")
        dbapi_connection.commit()

    schema_name = f"epick_private_deletion_v2_{uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    engine = admin_engine.execution_options(schema_translate_map={None: schema_name})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def session_factory(database_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(database_engine, expire_on_commit=False)


@contextmanager
def _migration_schema(
    approved_postgres_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Engine, Config, str]]:
    schema_name = f"epick_private_deletion_v2_fk_{uuid4().hex}"
    schema_url = approved_postgres_url.update_query_dict(
        {"options": f"-csearch_path={schema_name}"}
    )
    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema_engine = create_engine(schema_url, pool_pre_ping=True)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    monkeypatch.setenv(
        "EPICK_DATABASE_URL",
        schema_url.render_as_string(hide_password=False),
    )
    try:
        yield schema_engine, config, schema_name
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


@dataclass(frozen=True)
class _PublicIds:
    company_id: UUID
    source_id: UUID
    source_version_id: UUID
    evidence_id: UUID
    observation_id: UUID
    parser_execution_id: UUID
    outbox_event_id: UUID


@dataclass(frozen=True)
class _InventoryIds:
    attempt_id: UUID | None = None
    deduplication_id: UUID | None = None
    runtime_command_id: UUID | None = None
    stage_command_id: UUID | None = None
    staged_outbox_id: UUID | None = None
    ack_id: UUID | None = None
    receipt_operation_id: UUID | None = None
    inbox_id: UUID | None = None


@dataclass
class _PrivateDeletionAckCallbackV2:
    acknowledgements: list[PrivateDeletionAckV2] = field(default_factory=list)
    fail: bool = False

    def acknowledge(self, *, acknowledgement: PrivateDeletionAckV2) -> None:
        self.acknowledgements.append(acknowledgement)
        if self.fail:
            raise RuntimeError("synthetic W1 acknowledgement callback failure")


def _command(
    *,
    owner_user_id: UUID = OWNER_A,
    deletion_id: UUID | None = None,
    deletion_epoch: int = 1,
    kind: Literal["ACCOUNT", "PROJECT"] = "ACCOUNT",
    project_id: UUID | None = None,
) -> PrivateDeletionCommandV2:
    return PrivateDeletionCommandV2(
        deletion_id=deletion_id or uuid4(),
        owner_user_id=owner_user_id,
        deletion_epoch=deletion_epoch,
        scope=PrivateDeletionScope(kind=kind, project_id=project_id),
    )


def _apply_v2(session: Session, command: PrivateDeletionCommandV2) -> str:
    from epick_engine.source_collection.persistence import (  # noqa: PLC0415
        apply_private_deletion_v2,
    )

    return apply_private_deletion_v2(session, command)


def _process_v2(
    session_factory: sessionmaker[Session],
    command: PrivateDeletionCommandV2,
    callback: _PrivateDeletionAckCallbackV2,
):
    from epick_engine.source_collection.private_deletion_v2 import (  # noqa: PLC0415
        process_private_deletion_v2,
    )

    return process_private_deletion_v2(session_factory, command, callback)


def _seed_public(session: Session) -> _PublicIds:
    ids = _PublicIds(*(uuid4() for _ in range(7)))
    policy_id = uuid4()
    session.add(
        Company(
            company_id=ids.company_id,
            legal_name="Synthetic deletion v2 company",
            aliases=[],
            official_domains=["deletion-v2.example.test"],
            legal_identifiers={},
            identity_status="verified",
            identity_evidence=["synthetic://deletion-v2/company"],
        )
    )
    session.add(
        Source(
            source_id=ids.source_id,
            company_id=ids.company_id,
            source_type="job_posting",
            canonical_url=f"https://deletion-v2.example.test/{ids.source_id}",
            title="Synthetic public source",
        )
    )
    session.flush()
    session.add(
        SourcePolicyDecision(
            policy_decision_id=policy_id,
            source_id=ids.source_id,
            revision=1,
            official_status="verified",
            access_class="public",
            collection_permission="allowed",
            excerpt_storage_permission="allowed",
            body_storage_permission="allowed",
            redistribution_permission="unknown",
            evidence_refs=["synthetic://deletion-v2/policy"],
            checked_at=NOW,
            policy_version="deletion-v2-test",
        )
    )
    session.flush()
    session.add(
        SourceVersion(
            source_version_id=ids.source_version_id,
            source_id=ids.source_id,
            company_id=ids.company_id,
            title="Synthetic public source",
            source_type="job_posting",
            canonical_url=f"https://deletion-v2.example.test/{ids.source_id}",
            content_hash="a" * 64,
            hash_profile_version="test-v1",
            representation="html",
            first_parser_version="parser-v1",
            collected_at=NOW,
            published_at={"status": "unknown", "raw_text": None, "value": None},
            valid_from={"status": "unknown", "raw_text": None, "value": None},
            valid_to={"status": "unknown", "raw_text": None, "value": None},
            language="ko",
            policy_decision_id=policy_id,
        )
    )
    session.flush()
    session.add_all(
        [
            Evidence(
                evidence_id=ids.evidence_id,
                source_version_id=ids.source_version_id,
                evidence_key="required:0",
                section_title="Required",
                text_excerpt="Synthetic public evidence",
                locator={"kind": "css", "selector": "#required", "start": 0, "end": 8},
                chunk_order=0,
                origin_kind="direct",
            ),
            SourceObservation(
                observation_id=ids.observation_id,
                source_id=ids.source_id,
                source_version_id=ids.source_version_id,
                policy_decision_id=policy_id,
                observed_at=NOW,
                access_class="public",
                acquisition_status="AVAILABLE",
                http_status=200,
                checked_url=f"https://deletion-v2.example.test/{ids.source_id}",
                retrieval_validator=None,
                error_code=None,
                representation="html",
            ),
            ParserExecution(
                parser_execution_id=ids.parser_execution_id,
                source_id=ids.source_id,
                source_version_id=ids.source_version_id,
                content_hash="a" * 64,
                parser_version="parser-v1",
                output_hash=None,
                extraction_revision_id=None,
                status="failed",
                executed_at=NOW,
            ),
            OutboxEvent(
                event_id=ids.outbox_event_id,
                aggregate_id=ids.source_id,
                aggregate_revision=1,
                event_type="source.version.available",
                schema_version="w2.source-event.v1",
                payload={"source_id": str(ids.source_id)},
                occurred_at=NOW,
                delivery_state="pending",
            ),
        ]
    )
    session.flush()
    return ids


def _add_private_inventory(
    session: Session,
    *,
    owner_user_id: UUID,
    kind: Literal["UNKNOWN", "ACCOUNT", "PROJECT"],
    project_id: UUID | None,
    public: _PublicIds,
    label: str,
    parents: tuple[str, ...] = ("attempt", "deduplication", "runtime", "stage"),
    stage_children: bool = False,
) -> _InventoryIds:
    attempt_id = uuid4() if "attempt" in parents else None
    deduplication_id = uuid4() if "deduplication" in parents else None
    runtime_command_id = uuid4() if "runtime" in parents else None
    stage_command_id = uuid4() if "stage" in parents else None
    staged_outbox_id = uuid4() if stage_children else None
    ack_id = uuid4() if stage_children else None
    receipt_operation_id = uuid4() if stage_children else None
    inbox_id = uuid4() if stage_children else None

    if attempt_id is not None:
        session.add(
            CollectionAttempt(
                attempt_id=attempt_id,
                owner_user_id=owner_user_id,
                job_id=uuid4(),
                project_id=project_id,
                private_scope_kind=kind,
                command_id=uuid4(),
                input_version=1,
                target_ref=str(public.source_id),
                purpose_ref=f"synthetic:{label}",
                core_source_decision={},
                resume_stage="policy",
                policy_revision=None,
                result_version=1,
                execution_fence=f"fence:{label}",
                owner_deletion_epoch=0,
                parser_execution_id=public.parser_execution_id,
                checkpoint_ref=f"checkpoint://{label}",
                result_refs=[{"private": label}],
                failures=[],
                required_actions=[],
                result_payload={"private": label},
                finalized_at=NOW,
            )
        )
    if deduplication_id is not None:
        session.add(
            RequestDeduplication(
                request_deduplication_id=deduplication_id,
                owner_user_id=owner_user_id,
                private_scope_kind=kind,
                project_id=project_id,
                operation=f"collect:{label}",
                idempotency_key=f"key:{label}",
                request_hash="b" * 64,
                accepted_resource_ref=f"source:{public.source_id}",
                input_version=1,
                created_at=NOW,
            )
        )
    if runtime_command_id is not None:
        session.add(
            CollectionRuntimeAttempt(
                command_id=runtime_command_id,
                attempt_id=uuid4(),
                dispatch_digest="c" * 64,
                owner_ref=owner_user_id,
                job_id=uuid4(),
                private_scope_kind=kind,
                project_id=project_id,
                source_id=public.source_id,
                company_id=public.company_id,
                observation_order=int(runtime_command_id.int % SIGNED_64_MAX) + 1,
                effective_policy_revision=1,
                state="RESERVED",
                claim_token=None,
                claim_expires_at=None,
                observation_id=None,
                source_version_id=None,
                created_at=NOW,
                updated_at=NOW,
            )
        )
    if stage_command_id is not None:
        session.add(
            PrivateCommitStage(
                command_id=stage_command_id,
                owner_ref=owner_user_id,
                job_id=uuid4(),
                private_scope_kind=kind,
                project_id=project_id,
                execution_fence="0",
                owner_deletion_epoch="0",
                result_digest="sha256:" + "d" * 64,
                operation_id=uuid4(),
                operation_revision="1",
                max_purge_epoch="0",
                state="STAGED",
                stage_kind="PRIVATE_ONLY",
                result_payload={"private": label},
            )
        )
        if stage_children:
            assert staged_outbox_id is not None
            assert ack_id is not None
            assert receipt_operation_id is not None
            assert inbox_id is not None
            session.flush()
            session.add_all(
                [
                    PrivateStagedOutbox(
                        message_id=staged_outbox_id,
                        command_id=stage_command_id,
                        wire_hash="e" * 64,
                        payload={"private": label},
                        delivered_at=None,
                        relay_claim_token=None,
                        relay_claim_expires_at=None,
                    ),
                    PrivateCommitGateAck(
                        message_id=ack_id,
                        command_id=stage_command_id,
                        payload={"outcome": "FINALIZED"},
                        wire_digest=canonical_ack_wire({"outcome": "FINALIZED"})[1],
                        delivered_at=None,
                        relay_claim_token=None,
                        relay_claim_expires_at=None,
                    ),
                ]
            )
            session.flush()
            session.add_all(
                [
                    PrivateCommitGateReceipt(
                        operation_id=receipt_operation_id,
                        operation_revision="1",
                        command_id=stage_command_id,
                        content_hash="f" * 64,
                        ack_message_id=ack_id,
                    ),
                    PrivateCommitGateInbox(
                        message_id=inbox_id,
                        command_id=stage_command_id,
                        wire_hash="0" * 64,
                        ack_message_id=ack_id,
                    ),
                ]
            )
    return _InventoryIds(
        attempt_id=attempt_id,
        deduplication_id=deduplication_id,
        runtime_command_id=runtime_command_id,
        stage_command_id=stage_command_id,
        staged_outbox_id=staged_outbox_id,
        ack_id=ack_id,
        receipt_operation_id=receipt_operation_id,
        inbox_id=inbox_id,
    )


def _proof(
    *,
    owner_user_id: UUID,
    project_id: UUID | None,
    command_id: UUID,
    job_id: UUID,
    epoch: int = 0,
    kind: Literal["ACCOUNT", "PROJECT"] = "PROJECT",
) -> PrivateWriteScope:
    return PrivateWriteScope(
        PrivateWriteAuthorityDecision(
            owner_user_id=owner_user_id,
            owner_deletion_epoch=epoch,
            scope=PrivateDeletionScope(kind=kind, project_id=project_id),
            authority_ref="w1:test-deletion-v2",
            command_id=command_id,
            job_id=job_id,
        )
    )


def _runtime_dispatch(
    *,
    owner_user_id: UUID,
    project_id: UUID,
    public: _PublicIds,
) -> W1Dispatch:
    raw = json.loads(
        (FIXTURES / "w1_private_contract/private-w2-command-dispatch.json").read_text(
            encoding="utf-8"
        )
    )
    command_id, job_id = uuid4(), uuid4()
    raw["message_id"] = str(command_id)
    raw["payload"].update(
        command_id=str(command_id),
        job_id=str(job_id),
        authenticated_owner_ref=str(owner_user_id),
        project_ref=str(project_id),
        company_id=str(public.company_id),
        source_id=str(public.source_id),
        owner_deletion_epoch=0,
    )
    raw["lookup_request"].update(
        command_id=str(command_id),
        owner_deletion_epoch=0,
    )
    raw["core_decision_pin"].update(
        decision_id=str(uuid4()),
        company_id=str(public.company_id),
        source_id=str(public.source_id),
    )
    return parse_w1_dispatch(raw)


def _stage_pair(
    *,
    owner_user_id: UUID,
    project_id: UUID | None,
    public: _PublicIds,
) -> tuple[CollectionCommand, CollectionResult]:
    raw = json.loads(
        (FIXTURES / "w2_commit_gate_proposal/digest-vector.json").read_text(encoding="utf-8")
    )
    command_id, job_id = uuid4(), uuid4()
    raw["command"].update(
        command_id=str(command_id),
        job_id=str(job_id),
        authenticated_owner_ref=str(owner_user_id),
        project_ref=str(project_id) if project_id is not None else None,
        company_id=str(public.company_id),
        source_id=str(public.source_id),
        owner_deletion_epoch=0,
    )
    raw["result"].update(
        command_id=str(command_id),
        job_id=str(job_id),
        source_id=str(public.source_id),
    )
    for source_ref in raw["result"]["successful_source_refs"]:
        source_ref["source_id"] = str(public.source_id)
    for failure in raw["result"]["failures"]:
        failure["source_id"] = str(public.source_id)
    for required_action in raw["result"]["required_actions"]:
        required_action["context"]["source_id"] = str(public.source_id)
    return CollectionCommand.model_validate(raw["command"]), CollectionResult.model_validate(
        raw["result"]
    )


def _gate(
    command: CollectionCommand,
    result: CollectionResult,
    action: Literal["PREPARE", "FINALIZE", "ABORT"],
    *,
    operation_id: UUID,
    revision: int,
) -> CommitGateCommand:
    raw = json.loads(
        (FIXTURES / f"w1_private_contract/private-w2-commit-gate-{action.lower()}.json").read_text(
            encoding="utf-8"
        )
    )
    raw.update(
        message_id=str(uuid4()),
        operation_id=str(operation_id),
        operation_revision=revision,
        command_id=str(command.command_id),
        job_id=str(command.job_id),
        authenticated_owner_ref=str(command.authenticated_owner_ref),
        execution_fence=int(command.execution_fence),
        owner_deletion_epoch=command.owner_deletion_epoch,
        result_digest=staged_result_digest(command, result),
    )
    return parse_commit_gate_command(raw)


def _gate_authority(
    gate: CommitGateCommand,
    *,
    kind: Literal["ACCOUNT", "PROJECT"],
    project_id: UUID | None,
) -> PrivateGateAuthority:
    scope = (
        {"type": "ACCOUNT"}
        if kind == "ACCOUNT"
        else {"type": "PROJECT", "project_id": str(project_id)}
    )
    return PrivateGateAuthority.from_w1_response(
        GateAuthorityResponse.model_validate(
            {
                "schema_version": "w1.private.w2-gate-authority.v1",
                "owner_user_id": str(gate.authenticated_owner_ref),
                "owner_deletion_epoch": gate.owner_deletion_epoch,
                "command_id": str(gate.command_id),
                "job_id": str(gate.job_id),
                "execution_fence": gate.execution_fence,
                "scope": scope,
                "operation_id": str(gate.operation_id),
                "operation_revision": gate.operation_revision,
                "action": gate.action,
                "phase": "APPLY",
                "result_digest": gate.result_digest,
                "purge_owner_deletion_epoch": gate.purge_owner_deletion_epoch,
                "authority_ref": "w1:test-deletion-ack-control",
            }
        )
    )


def _private_snapshot(session: Session, owner_user_id: UUID) -> dict[str, object]:
    return {
        "attempts": tuple(
            session.scalars(
                select(CollectionAttempt.attempt_id)
                .where(CollectionAttempt.owner_user_id == owner_user_id)
                .order_by(CollectionAttempt.attempt_id)
            )
        ),
        "deduplications": tuple(
            session.scalars(
                select(RequestDeduplication.request_deduplication_id)
                .where(RequestDeduplication.owner_user_id == owner_user_id)
                .order_by(RequestDeduplication.request_deduplication_id)
            )
        ),
        "runtime": tuple(
            session.scalars(
                select(CollectionRuntimeAttempt.command_id)
                .where(CollectionRuntimeAttempt.owner_ref == owner_user_id)
                .order_by(CollectionRuntimeAttempt.command_id)
            )
        ),
        "stages": tuple(
            session.scalars(
                select(PrivateCommitStage.command_id)
                .where(PrivateCommitStage.owner_ref == owner_user_id)
                .order_by(PrivateCommitStage.command_id)
            )
        ),
        "owner_state": session.execute(
            select(
                PrivateDeletionOwnerState.latest_epoch,
                PrivateDeletionOwnerState.account_deleted,
            ).where(PrivateDeletionOwnerState.owner_user_id == owner_user_id)
        ).one_or_none(),
        "tombstones": tuple(
            session.execute(
                select(
                    PrivateDeletionProjectTombstone.project_id,
                    PrivateDeletionProjectTombstone.deletion_epoch,
                )
                .where(PrivateDeletionProjectTombstone.owner_user_id == owner_user_id)
                .order_by(PrivateDeletionProjectTombstone.project_id)
            )
        ),
        "receipts": tuple(
            session.execute(
                select(
                    PrivateDeletionReceipt.deletion_id,
                    PrivateDeletionReceipt.deletion_epoch,
                    PrivateDeletionReceipt.contract_version,
                )
                .where(PrivateDeletionReceipt.owner_user_id == owner_user_id)
                .order_by(PrivateDeletionReceipt.deletion_epoch)
            )
        ),
    }


def _ids_exist(session: Session, ids: _InventoryIds) -> dict[str, bool]:
    return {
        "attempt": ids.attempt_id is not None
        and session.get(CollectionAttempt, ids.attempt_id) is not None,
        "deduplication": ids.deduplication_id is not None
        and session.get(RequestDeduplication, ids.deduplication_id) is not None,
        "runtime": ids.runtime_command_id is not None
        and session.get(CollectionRuntimeAttempt, ids.runtime_command_id) is not None,
        "stage": ids.stage_command_id is not None
        and session.get(PrivateCommitStage, ids.stage_command_id) is not None,
        "staged_outbox": ids.staged_outbox_id is not None
        and session.get(PrivateStagedOutbox, ids.staged_outbox_id) is not None,
        "ack": ids.ack_id is not None and session.get(PrivateCommitGateAck, ids.ack_id) is not None,
        "receipt": ids.receipt_operation_id is not None
        and session.get(PrivateCommitGateReceipt, (ids.receipt_operation_id, "1")) is not None,
        "inbox": ids.inbox_id is not None
        and session.get(PrivateCommitGateInbox, ids.inbox_id) is not None,
    }


def _wait_for_postgres_lock(database_engine: Engine, backend_pid: int) -> None:
    deadline = monotonic() + 1.0
    with database_engine.connect() as connection:
        while monotonic() < deadline:
            if (
                connection.scalar(
                    text("SELECT wait_event_type FROM pg_stat_activity WHERE pid = :pid"),
                    {"pid": backend_pid},
                )
                == "Lock"
            ):
                return
            sleep(0.01)
    raise AssertionError("competing private scope transaction never waited on owner lock")


@pytest.mark.approved_postgres
def test_0010_private_deletion_fk_topology_matches_safe_delete_order(
    approved_postgres_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _migration_schema(approved_postgres_url, monkeypatch) as (engine, config, schema):
        alembic_command.upgrade(config, "0010_private_deletion_scope_v2")
        inspector = inspect(engine)

        runtime_targets = {
            fk["referred_table"]
            for fk in inspector.get_foreign_keys("collection_runtime_attempts", schema=schema)
        }
        assert runtime_targets == {"sources", "source_observations", "source_versions"}
        assert "collection_attempts" not in runtime_targets

        child_targets = {
            table_name: {
                fk["referred_table"] for fk in inspector.get_foreign_keys(table_name, schema=schema)
            }
            for table_name in (
                "private_staged_outbox",
                "private_commit_gate_acks",
                "private_commit_gate_receipts",
                "private_commit_gate_inbox",
            )
        }
        assert child_targets == {
            "private_staged_outbox": {"private_commit_stages"},
            "private_commit_gate_acks": {"private_commit_stages"},
            "private_commit_gate_receipts": {
                "private_commit_stages",
                "private_commit_gate_acks",
            },
            "private_commit_gate_inbox": {
                "private_commit_stages",
                "private_commit_gate_acks",
            },
        }
        for table_name in child_targets:
            assert all(
                fk["options"].get("ondelete") is None
                for fk in inspector.get_foreign_keys(table_name, schema=schema)
            )


@pytest.mark.approved_postgres
def test_0011_forward_head_preserves_fk_and_check_constraints(
    approved_postgres_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _migration_schema(approved_postgres_url, monkeypatch) as (engine, config, schema):
        alembic_command.upgrade(config, "0010_private_deletion_scope_v2")
        before = inspect(engine)
        child_tables = (
            "private_staged_outbox",
            "private_commit_gate_acks",
            "private_commit_gate_receipts",
            "private_commit_gate_inbox",
        )
        foreign_keys_before = {
            table_name: {
                (
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                    fk["options"].get("ondelete"),
                )
                for fk in before.get_foreign_keys(table_name, schema=schema)
            }
            for table_name in child_tables
        }

        alembic_command.upgrade(config, "0011_private_ack_control_retention")
        after = inspect(engine)

        version_columns = {
            column["name"]: column for column in after.get_columns("alembic_version", schema=schema)
        }
        assert version_columns["version_num"]["type"].length == 64
        columns = {
            column["name"]: column
            for column in after.get_columns("private_commit_stages", schema=schema)
        }
        assert columns["payload_purged"]["nullable"] is False
        assert str(columns["payload_purged"]["default"]).lower() == "false"
        constraints = {
            constraint["name"]: " ".join(constraint["sqltext"].split()).lower()
            for constraint in after.get_check_constraints("private_commit_stages", schema=schema)
        }
        assert "ck_private_commit_stages_payload_matches_state" in constraints
        payload_constraint = constraints["ck_private_commit_stages_payload_matches_state"]
        assert "payload_purged" in payload_constraint
        assert "result_payload is null" in payload_constraint
        assert {
            table_name: {
                (
                    tuple(fk["constrained_columns"]),
                    fk["referred_table"],
                    tuple(fk["referred_columns"]),
                    fk["options"].get("ondelete"),
                )
                for fk in after.get_foreign_keys(table_name, schema=schema)
            }
            for table_name in child_tables
        } == foreign_keys_before


@pytest.mark.approved_postgres
def test_0012_backfills_ack_wire_digest_and_matches_metadata(
    approved_postgres_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _migration_schema(approved_postgres_url, monkeypatch) as (engine, config, schema):
        alembic_command.upgrade(config, "0011_private_ack_control_retention")
        command_id = uuid4()
        ack_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO private_commit_stages ("
                    "command_id, owner_ref, job_id, execution_fence, owner_deletion_epoch, "
                    "result_digest, operation_revision, max_purge_epoch, state, result_payload"
                    ") VALUES ("
                    ":command_id, :owner_ref, :job_id, '1', '1', :result_digest, "
                    "'0', '1', 'STAGED', CAST(:result_payload AS jsonb))"
                ),
                {
                    "command_id": command_id,
                    "owner_ref": uuid4(),
                    "job_id": uuid4(),
                    "result_digest": "sha256:" + "a" * 64,
                    "result_payload": json.dumps({"private": "discarded"}),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO private_commit_gate_acks (message_id, command_id, payload) "
                    "VALUES (:message_id, :command_id, CAST(:payload AS jsonb))"
                ),
                {
                    "message_id": ack_id,
                    "command_id": command_id,
                    "payload": json.dumps(
                        {"z": "한글", "a": {"n": 1, "flag": True}},
                        ensure_ascii=False,
                    ),
                },
            )

        alembic_command.upgrade(config, "0012_private_ack_wire_digest")

        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT wire_digest FROM private_commit_gate_acks "
                        "WHERE message_id = :message_id"
                    ),
                    {"message_id": ack_id},
                )
                == "sha256:9d31fb98903c4dce6fa90bd97a4de61b689f54895c9b911622720b36ec789ab4"
            )
        inspector = inspect(engine)
        columns = {
            column["name"]: column
            for column in inspector.get_columns("private_commit_gate_acks", schema=schema)
        }
        assert columns["wire_digest"]["nullable"] is False
        assert columns["wire_digest"]["type"].length == 71
        constraints = {
            constraint["name"]: " ".join(constraint["sqltext"].split()).lower()
            for constraint in inspector.get_check_constraints(
                "private_commit_gate_acks", schema=schema
            )
        }
        assert "ck_private_commit_gate_acks_wire_digest_format" in constraints
        assert "sha256:" in constraints["ck_private_commit_gate_acks_wire_digest_format"]


@pytest.mark.approved_postgres
@pytest.mark.parametrize(
    "legacy_payload",
    ["legacy-scalar-ack", [{"legacy": "array-ack"}]],
    ids=["json-scalar", "json-array"],
)
def test_0012_invalid_legacy_ack_wire_aborts_and_rolls_back_schema(
    approved_postgres_url: URL,
    monkeypatch: pytest.MonkeyPatch,
    legacy_payload: object,
) -> None:
    with _migration_schema(approved_postgres_url, monkeypatch) as (engine, config, schema):
        alembic_command.upgrade(config, "0011_private_ack_control_retention")
        command_id = uuid4()
        ack_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO private_commit_stages ("
                    "command_id, owner_ref, job_id, execution_fence, owner_deletion_epoch, "
                    "result_digest, operation_revision, max_purge_epoch, state, result_payload"
                    ") VALUES ("
                    ":command_id, :owner_ref, :job_id, '1', '1', :result_digest, "
                    "'0', '1', 'STAGED', CAST(:result_payload AS jsonb))"
                ),
                {
                    "command_id": command_id,
                    "owner_ref": uuid4(),
                    "job_id": uuid4(),
                    "result_digest": "sha256:" + "a" * 64,
                    "result_payload": json.dumps({"private": "discarded"}),
                },
            )
            connection.execute(
                text(
                    "INSERT INTO private_commit_gate_acks (message_id, command_id, payload) "
                    "VALUES (:message_id, :command_id, CAST(:payload AS jsonb))"
                ),
                {
                    "message_id": ack_id,
                    "command_id": command_id,
                    "payload": json.dumps(legacy_payload),
                },
            )

        with pytest.raises(RuntimeError, match="ACK payload must be a JSON object"):
            alembic_command.upgrade(config, "0012_private_ack_wire_digest")

        with engine.connect() as connection:
            assert connection.scalar(text("SELECT version_num FROM alembic_version")) == (
                "0011_private_ack_control_retention"
            )
            assert (
                connection.scalar(
                    text(
                        "SELECT payload FROM private_commit_gate_acks "
                        "WHERE message_id = :message_id"
                    ),
                    {"message_id": ack_id},
                )
                == legacy_payload
            )
        columns = {
            column["name"]
            for column in inspect(engine).get_columns("private_commit_gate_acks", schema=schema)
        }
        assert "wire_digest" not in columns


@pytest.mark.approved_postgres
def test_0013_preserves_existing_v2_receipts_as_unconfirmed(
    approved_postgres_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _migration_schema(approved_postgres_url, monkeypatch) as (engine, config, schema):
        alembic_command.upgrade(config, "0012_private_ack_wire_digest")
        deletion_id = uuid4()
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO private_deletion_receipts "
                    "(deletion_id, owner_user_id, deletion_epoch, contract_version, "
                    "command_digest, outcome, created_at) VALUES "
                    "(:deletion_id, :owner_user_id, 1, 'w2.private-deletion.v2', "
                    ":command_digest, 'APPLIED', :created_at)"
                ),
                {
                    "deletion_id": deletion_id,
                    "owner_user_id": OWNER_A,
                    "command_digest": "a" * 64,
                    "created_at": NOW,
                },
            )

        alembic_command.upgrade(config, "0013_deletion_ack_confirmed")

        with engine.connect() as connection:
            assert (
                connection.scalar(
                    text(
                        "SELECT ack_confirmed_at FROM private_deletion_receipts "
                        "WHERE deletion_id = :deletion_id"
                    ),
                    {"deletion_id": deletion_id},
                )
                is None
            )
        columns = {
            column["name"]: column
            for column in inspect(engine).get_columns("private_deletion_receipts", schema=schema)
        }
        assert columns["ack_confirmed_at"]["nullable"] is True


@pytest.mark.approved_postgres
@pytest.mark.parametrize("unknown_parent", ["attempt", "deduplication", "runtime", "stage"])
def test_v2_project_deletion_rolls_back_on_any_unclassified_owner_row(
    session_factory: sessionmaker[Session],
    unknown_parent: str,
) -> None:
    callback = _PrivateDeletionAckCallbackV2()
    command = _command(kind="PROJECT", project_id=PROJECT_A)
    with session_factory.begin() as session:
        public = _seed_public(session)
        session.add(
            PrivateDeletionOwnerState(
                owner_user_id=OWNER_A,
                latest_epoch=0,
                account_deleted=False,
            )
        )
        _add_private_inventory(
            session,
            owner_user_id=OWNER_A,
            kind="PROJECT",
            project_id=PROJECT_A,
            public=public,
            label=f"target-{unknown_parent}",
        )
        _add_private_inventory(
            session,
            owner_user_id=OWNER_A,
            kind="UNKNOWN",
            project_id=PROJECT_B,
            public=public,
            label=f"unknown-{unknown_parent}",
            parents=(unknown_parent,),
        )
    with session_factory() as session:
        before = _private_snapshot(session, OWNER_A)

    with pytest.raises(ScopeUnclassified):
        _process_v2(session_factory, command, callback)

    with session_factory() as session:
        assert _private_snapshot(session, OWNER_A) == before
    assert callback.acknowledgements == []


@pytest.mark.approved_postgres
def test_v2_project_deletion_isolates_projects_owners_and_removes_ackless_stages(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        public = _seed_public(session)
        session.add_all(
            [
                PrivateDeletionOwnerState(
                    owner_user_id=OWNER_A, latest_epoch=0, account_deleted=False
                ),
                PrivateDeletionOwnerState(
                    owner_user_id=OWNER_B, latest_epoch=0, account_deleted=False
                ),
            ]
        )
        selected = _add_private_inventory(
            session,
            owner_user_id=OWNER_A,
            kind="PROJECT",
            project_id=PROJECT_A,
            public=public,
            label="owner-a-project-a",
        )
        same_owner_other_project = _add_private_inventory(
            session,
            owner_user_id=OWNER_A,
            kind="PROJECT",
            project_id=PROJECT_B,
            public=public,
            label="owner-a-project-b",
            stage_children=True,
        )
        foreign_owner = _add_private_inventory(
            session,
            owner_user_id=OWNER_B,
            kind="PROJECT",
            project_id=PROJECT_FOREIGN,
            public=public,
            label="owner-b-project",
            stage_children=True,
        )

    callback = _PrivateDeletionAckCallbackV2()
    command = _command(kind="PROJECT", project_id=PROJECT_A)
    ack = _process_v2(session_factory, command, callback)

    assert ack is not None and ack.outcome == "APPLIED"
    assert callback.acknowledgements == [ack]
    with session_factory() as session:
        assert not any(_ids_exist(session, selected).values())
        assert all(_ids_exist(session, same_owner_other_project).values())
        assert all(_ids_exist(session, foreign_owner).values())
        tombstone = session.get(
            PrivateDeletionProjectTombstone,
            (OWNER_A, PROJECT_A),
        )
        assert tombstone is not None and tombstone.deletion_epoch == 1
        assert session.get(Source, public.source_id) is not None


@pytest.mark.approved_postgres
def test_v2_project_stage_purge_uses_constant_bind_count_for_large_scope(
    database_engine: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    stage_count = 256
    with session_factory.begin() as session:
        public = _seed_public(session)
        session.add_all(
            [
                PrivateDeletionOwnerState(
                    owner_user_id=OWNER_A, latest_epoch=0, account_deleted=False
                ),
                PrivateDeletionOwnerState(
                    owner_user_id=OWNER_B, latest_epoch=0, account_deleted=False
                ),
            ]
        )
        selected_stage_ids = {
            inventory.stage_command_id
            for index in range(stage_count)
            if (
                inventory := _add_private_inventory(
                    session,
                    owner_user_id=OWNER_A,
                    kind="PROJECT",
                    project_id=PROJECT_A,
                    public=public,
                    label=f"large-project-stage-{index}",
                    parents=("stage",),
                )
            ).stage_command_id
            is not None
        }
        same_owner_other_project = _add_private_inventory(
            session,
            owner_user_id=OWNER_A,
            kind="PROJECT",
            project_id=PROJECT_B,
            public=public,
            label="large-project-preserved",
            parents=("stage",),
        )
        foreign_owner = _add_private_inventory(
            session,
            owner_user_id=OWNER_B,
            kind="PROJECT",
            project_id=PROJECT_A,
            public=public,
            label="large-owner-preserved",
            parents=("stage",),
        )

    stage_tables = (
        "private_commit_gate_receipts",
        "private_commit_gate_inbox",
        "private_staged_outbox",
        "private_commit_gate_acks",
        "private_commit_stages",
    )
    stage_delete_bind_counts: list[int] = []

    def capture_stage_delete_binds(
        _connection, _cursor, statement, parameters, _context, _executemany
    ) -> None:
        normalized = statement.lower()
        if normalized.lstrip().startswith("delete from") and any(
            table_name in normalized for table_name in stage_tables
        ):
            stage_delete_bind_counts.append(len(parameters))

    event.listen(database_engine, "before_cursor_execute", capture_stage_delete_binds)
    try:
        with session_factory.begin() as session:
            assert (
                _apply_v2(
                    session,
                    _command(kind="PROJECT", project_id=PROJECT_A),
                )
                == "APPLIED"
            )
    finally:
        event.remove(database_engine, "before_cursor_execute", capture_stage_delete_binds)

    assert len(selected_stage_ids) == stage_count
    assert len(stage_delete_bind_counts) == len(stage_tables)
    assert max(stage_delete_bind_counts) <= 4
    with session_factory() as session:
        assert not set(
            session.scalars(
                select(PrivateCommitStage.command_id).where(
                    PrivateCommitStage.command_id.in_(selected_stage_ids)
                )
            )
        )
        assert _ids_exist(session, same_owner_other_project)["stage"] is True
        assert _ids_exist(session, foreign_owner)["stage"] is True


@pytest.mark.approved_postgres
def test_v2_account_deletion_preserves_other_owner_and_public_history(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        public = _seed_public(session)
        session.add_all(
            [
                PrivateDeletionOwnerState(
                    owner_user_id=OWNER_A, latest_epoch=0, account_deleted=False
                ),
                PrivateDeletionOwnerState(
                    owner_user_id=OWNER_B, latest_epoch=0, account_deleted=False
                ),
            ]
        )
        owner_a = _add_private_inventory(
            session,
            owner_user_id=OWNER_A,
            kind="ACCOUNT",
            project_id=None,
            public=public,
            label="owner-a-account",
        )
        owner_a_project = _add_private_inventory(
            session,
            owner_user_id=OWNER_A,
            kind="PROJECT",
            project_id=PROJECT_A,
            public=public,
            label="owner-a-project",
        )
        owner_a_unknown = _add_private_inventory(
            session,
            owner_user_id=OWNER_A,
            kind="UNKNOWN",
            project_id=PROJECT_B,
            public=public,
            label="owner-a-unknown",
        )
        owner_b = _add_private_inventory(
            session,
            owner_user_id=OWNER_B,
            kind="ACCOUNT",
            project_id=None,
            public=public,
            label="owner-b-account",
            stage_children=True,
        )

    ack = _process_v2(
        session_factory,
        _command(kind="ACCOUNT"),
        _PrivateDeletionAckCallbackV2(),
    )

    assert ack is not None and ack.outcome == "APPLIED"
    with session_factory() as session:
        assert not any(_ids_exist(session, owner_a).values())
        assert not any(_ids_exist(session, owner_a_project).values())
        assert not any(_ids_exist(session, owner_a_unknown).values())
        assert all(_ids_exist(session, owner_b).values())
        owner_state = session.get(PrivateDeletionOwnerState, OWNER_A)
        assert owner_state is not None and owner_state.account_deleted is True
        assert session.get(Company, public.company_id) is not None
        assert session.get(Source, public.source_id) is not None
        assert session.get(SourceVersion, public.source_version_id) is not None
        assert session.get(Evidence, public.evidence_id) is not None
        assert session.get(SourceObservation, public.observation_id) is not None
        assert session.get(ParserExecution, public.parser_execution_id) is not None
        assert session.get(OutboxEvent, public.outbox_event_id) is not None


@pytest.mark.approved_postgres
@pytest.mark.parametrize(
    ("kind", "project_id"),
    [("ACCOUNT", None), ("PROJECT", PROJECT_A)],
)
def test_deletion_scrubs_payload_but_keeps_original_ack_control(
    session_factory: sessionmaker[Session],
    kind: Literal["ACCOUNT", "PROJECT"],
    project_id: UUID | None,
) -> None:
    with session_factory.begin() as session:
        public = _seed_public(session)
        target_pairs = [
            _stage_pair(
                owner_user_id=OWNER_A,
                project_id=project_id,
                public=public,
            )
            for _ in range(2)
        ]
        gates: list[CommitGateCommand] = []
        ack_ids: list[UUID] = []
        staged_ids: list[UUID] = []
        ack_payloads: list[dict[str, object]] = []
        for (command, result), action in zip(target_pairs, ("PREPARE", "ABORT"), strict=True):
            staged_id = uuid4()
            stage_private_result(
                session,
                command,
                result,
                message_id=staged_id,
                occurred_at=NOW,
                private_scope=_proof(
                    owner_user_id=OWNER_A,
                    project_id=project_id,
                    command_id=command.command_id,
                    job_id=command.job_id,
                    kind=kind,
                ),
            )
            gate = _gate(
                command,
                result,
                action,
                operation_id=uuid4(),
                revision=1,
            )
            ack_id = uuid4()
            ack = apply_commit_gate(
                session,
                gate,
                ack_message_id=ack_id,
                occurred_at=NOW,
                private_gate_authority=_gate_authority(
                    gate,
                    kind=kind,
                    project_id=project_id,
                ),
            )
            gates.append(gate)
            ack_ids.append(ack_id)
            staged_ids.append(staged_id)
            ack_payloads.append(ack.model_dump(mode="json"))
        session.get(PrivateCommitGateAck, ack_ids[0]).delivered_at = NOW
        session.get(PrivateCommitGateAck, ack_ids[1]).delivered_at = None
        selected_without_ack = _add_private_inventory(
            session,
            owner_user_id=OWNER_A,
            kind=kind,
            project_id=project_id,
            public=public,
            label=f"{kind.lower()}-without-ack",
        )
        same_owner_other_project = None
        if kind == "PROJECT":
            same_owner_other_project = _add_private_inventory(
                session,
                owner_user_id=OWNER_A,
                kind="PROJECT",
                project_id=PROJECT_B,
                public=public,
                label="same-owner-other-project-ack-control",
                stage_children=True,
            )
        foreign_owner = _add_private_inventory(
            session,
            owner_user_id=OWNER_B,
            kind="PROJECT",
            project_id=PROJECT_FOREIGN,
            public=public,
            label="foreign-owner-ack-control",
            stage_children=True,
        )

    with session_factory.begin() as session:
        assert (
            _apply_v2(
                session,
                _command(kind=kind, project_id=project_id),
            )
            == "APPLIED"
        )

    with session_factory.begin() as session:
        assert not any(_ids_exist(session, selected_without_ack).values())
        if same_owner_other_project is not None:
            assert all(_ids_exist(session, same_owner_other_project).values())
        assert all(_ids_exist(session, foreign_owner).values())
        for index, ((command, _), gate, ack_id, staged_id) in enumerate(
            zip(target_pairs, gates, ack_ids, staged_ids, strict=True)
        ):
            stage = session.get(PrivateCommitStage, command.command_id)
            assert stage is not None
            assert stage.payload_purged is True
            assert stage.result_payload is None
            assert session.get(PrivateStagedOutbox, staged_id) is None
            stored_ack = session.get(PrivateCommitGateAck, ack_id)
            assert stored_ack is not None
            assert stored_ack.payload == ack_payloads[index]
            assert stored_ack.delivered_at == (NOW if index == 0 else None)
            receipt = session.get(
                PrivateCommitGateReceipt,
                (gate.operation_id, str(gate.operation_revision)),
            )
            inbox = session.get(PrivateCommitGateInbox, gate.message_id)
            assert receipt is not None and receipt.ack_message_id == ack_id
            assert inbox is not None and inbox.ack_message_id == ack_id

        replay_gate = gates[1]
        replay = apply_commit_gate(
            session,
            replay_gate,
            ack_message_id=uuid4(),
            occurred_at=NOW,
            private_gate_authority=_gate_authority(
                replay_gate,
                kind=kind,
                project_id=project_id,
            ),
        )
        assert replay.message_id == ack_ids[1]
        assert replay.model_dump(mode="json") == ack_payloads[1]

        changed_body = replay_gate.model_copy(
            update={"issued_at": datetime(2032, 1, 2, 3, 5, tzinfo=UTC)}
        )
        with pytest.raises(CommitGateRejected, match="message conflict"):
            apply_commit_gate(
                session,
                changed_body,
                ack_message_id=uuid4(),
                occurred_at=NOW,
                private_gate_authority=_gate_authority(
                    changed_body,
                    kind=kind,
                    project_id=project_id,
                ),
            )

        # Isolate the payload marker from the owner tombstone/fence: even if a
        # current scope were restored, deletion cannot resurrect the scrubbed stage.
        owner_state = session.get(PrivateDeletionOwnerState, OWNER_A)
        assert owner_state is not None
        owner_state.latest_epoch = 0
        owner_state.account_deleted = False
        if project_id is not None:
            tombstone = session.get(
                PrivateDeletionProjectTombstone,
                (OWNER_A, project_id),
            )
            assert tombstone is not None
            session.delete(tombstone)
        session.flush()

        purged_command, purged_result = target_pairs[0]
        with pytest.raises(CommitGateRejected, match="payload was purged"):
            stage_private_result(
                session,
                purged_command,
                purged_result,
                message_id=staged_ids[0],
                occurred_at=NOW,
                private_scope=_proof(
                    owner_user_id=OWNER_A,
                    project_id=project_id,
                    command_id=purged_command.command_id,
                    job_id=purged_command.job_id,
                    kind=kind,
                ),
            )

        finalize = _gate(
            purged_command,
            purged_result,
            "FINALIZE",
            operation_id=gates[0].operation_id,
            revision=2,
        )
        with pytest.raises(CommitGateRejected, match="payload was purged"):
            apply_commit_gate(
                session,
                finalize,
                ack_message_id=uuid4(),
                occurred_at=NOW,
                private_gate_authority=_gate_authority(
                    finalize,
                    kind=kind,
                    project_id=project_id,
                ),
            )


@pytest.mark.approved_postgres
def test_v2_receipt_rejects_changed_scope_and_v1_reuse(
    session_factory: sessionmaker[Session],
) -> None:
    deletion_id = uuid4()
    original = _command(
        deletion_id=deletion_id,
        deletion_epoch=1,
        kind="PROJECT",
        project_id=PROJECT_A,
    )
    with session_factory.begin() as session:
        assert _apply_v2(session, original) == "APPLIED"

    changed_scope = _command(
        deletion_id=deletion_id,
        deletion_epoch=1,
        kind="PROJECT",
        project_id=PROJECT_B,
    )
    with pytest.raises(PersistenceConflict):
        with session_factory.begin() as session:
            _apply_v2(session, changed_scope)

    v1_id = uuid4()
    with session_factory.begin() as session:
        session.add(
            PrivateDeletionReceipt(
                deletion_id=v1_id,
                owner_user_id=OWNER_B,
                deletion_epoch=1,
                contract_version="w2.private-deletion.v1",
                command_digest="1" * 64,
                outcome="APPLIED",
                created_at=NOW,
            )
        )
        session.add(
            PrivateDeletionOwnerState(
                owner_user_id=OWNER_B,
                latest_epoch=1,
                account_deleted=False,
            )
        )
    v2_reusing_v1_id = _command(
        owner_user_id=OWNER_B,
        deletion_id=v1_id,
        deletion_epoch=1,
        kind="ACCOUNT",
    )
    with pytest.raises(PersistenceConflict):
        with session_factory.begin() as session:
            _apply_v2(session, v2_reusing_v1_id)


@pytest.mark.approved_postgres
def test_v2_epoch_boundaries_same_epoch_conflict_and_old_same_id_stale(
    session_factory: sessionmaker[Session],
) -> None:
    first = _command(deletion_epoch=1, kind="PROJECT", project_id=PROJECT_A)
    second = _command(deletion_epoch=2, kind="PROJECT", project_id=PROJECT_B)
    with session_factory.begin() as session:
        assert _apply_v2(session, first) == "APPLIED"
    with pytest.raises(PersistenceConflict):
        with session_factory.begin() as session:
            _apply_v2(
                session,
                _command(deletion_epoch=1, kind="PROJECT", project_id=PROJECT_B),
            )
    with session_factory.begin() as session:
        assert _apply_v2(session, second) == "APPLIED"
    with session_factory.begin() as session:
        assert _apply_v2(session, first) == "STALE"

    maximum = _command(
        owner_user_id=OWNER_B,
        deletion_epoch=SIGNED_64_MAX,
        kind="ACCOUNT",
    )
    with session_factory.begin() as session:
        assert _apply_v2(session, maximum) == "APPLIED"
    with session_factory() as session:
        owner_state = session.get(PrivateDeletionOwnerState, OWNER_B)
        assert owner_state is not None and owner_state.latest_epoch == SIGNED_64_MAX


@pytest.mark.approved_postgres
def test_v2_callback_failure_retries_same_receipt_after_restart(
    session_factory: sessionmaker[Session],
) -> None:
    command = _command(kind="PROJECT", project_id=PROJECT_A)
    first_process = _PrivateDeletionAckCallbackV2(fail=True)
    with pytest.raises(RuntimeError, match="acknowledgement callback"):
        _process_v2(session_factory, command, first_process)
    assert first_process.acknowledgements == [
        PrivateDeletionAckV2(
            deletion_id=command.deletion_id,
            owner_user_id=OWNER_A,
            deletion_epoch=1,
            scope=command.scope,
            outcome="APPLIED",
        )
    ]
    with session_factory() as session:
        receipt = session.get(PrivateDeletionReceipt, command.deletion_id)
        assert receipt is not None and receipt.contract_version == "w2.private-deletion.v2"

    restarted_process = _PrivateDeletionAckCallbackV2()
    duplicate_ack = _process_v2(session_factory, command, restarted_process)
    assert duplicate_ack is not None and duplicate_ack.outcome == "DUPLICATE"
    assert restarted_process.acknowledgements == [duplicate_ack]

    newer = _command(deletion_epoch=2, kind="PROJECT", project_id=PROJECT_B)
    assert _process_v2(session_factory, newer, _PrivateDeletionAckCallbackV2()) is not None
    stale_callback = _PrivateDeletionAckCallbackV2()
    assert _process_v2(session_factory, command, stale_callback) is None
    assert stale_callback.acknowledgements == []


@pytest.mark.approved_postgres
def test_v2_ack_confirmation_is_durable_only_after_w1_accepts(
    session_factory: sessionmaker[Session],
) -> None:
    command = _command(kind="ACCOUNT")
    with pytest.raises(RuntimeError, match="acknowledgement callback"):
        _process_v2(session_factory, command, _PrivateDeletionAckCallbackV2(fail=True))

    with session_factory() as session:
        receipt = session.get(PrivateDeletionReceipt, command.deletion_id)
        assert receipt is not None
        assert receipt.ack_confirmed_at is None

    acknowledgement = _process_v2(session_factory, command, _PrivateDeletionAckCallbackV2())
    assert acknowledgement is not None and acknowledgement.outcome == "DUPLICATE"

    with session_factory() as session:
        receipt = session.get(PrivateDeletionReceipt, command.deletion_id)
        assert receipt is not None
        confirmed_at = receipt.ack_confirmed_at
        assert confirmed_at is not None

    _process_v2(session_factory, command, _PrivateDeletionAckCallbackV2())
    with session_factory() as session:
        receipt = session.get(PrivateDeletionReceipt, command.deletion_id)
        assert receipt is not None
        assert receipt.ack_confirmed_at == confirmed_at


@pytest.mark.approved_postgres
def test_pending_ack_count_excludes_v1_and_w1_confirmed_v2_receipts(
    database_engine: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    unconfirmed = _command(owner_user_id=OWNER_A)
    with pytest.raises(RuntimeError, match="acknowledgement callback"):
        _process_v2(session_factory, unconfirmed, _PrivateDeletionAckCallbackV2(fail=True))
    confirmed = _command(owner_user_id=OWNER_B)
    _process_v2(session_factory, confirmed, _PrivateDeletionAckCallbackV2())
    with session_factory.begin() as session:
        session.add(
            PrivateDeletionReceipt(
                deletion_id=uuid4(),
                owner_user_id=OWNER_A,
                deletion_epoch=2,
                contract_version="w2.private-deletion.v1",
                command_digest="a" * 64,
                outcome="APPLIED",
                created_at=NOW,
            )
        )

    assert deletion_operator.count_pending_private_deletion_acks(database_engine) == 1


@pytest.mark.approved_postgres
def test_v2_ack_confirmation_write_failure_keeps_receipt_unconfirmed_for_retry(
    session_factory: sessionmaker[Session],
) -> None:
    command = _command(kind="ACCOUNT")
    accepted = _PrivateDeletionAckCallbackV2()

    def fail_confirmation(session, _flush_context, _instances):
        if any(
            isinstance(row, PrivateDeletionReceipt) and row.ack_confirmed_at is not None
            for row in session.dirty
        ):
            raise RuntimeError("synthetic ACK confirmation write failure")

    event.listen(Session, "before_flush", fail_confirmation)
    try:
        with pytest.raises(RuntimeError, match="ACK confirmation write failure"):
            _process_v2(session_factory, command, accepted)
    finally:
        event.remove(Session, "before_flush", fail_confirmation)

    assert len(accepted.acknowledgements) == 1
    with session_factory() as session:
        receipt = session.get(PrivateDeletionReceipt, command.deletion_id)
        assert receipt is not None and receipt.ack_confirmed_at is None

    retry = _PrivateDeletionAckCallbackV2()
    acknowledgement = _process_v2(session_factory, command, retry)
    assert acknowledgement is not None and acknowledgement.outcome == "DUPLICATE"
    with session_factory() as session:
        receipt = session.get(PrivateDeletionReceipt, command.deletion_id)
        assert receipt is not None and receipt.ack_confirmed_at is not None


@pytest.mark.approved_postgres
@pytest.mark.parametrize("writer_kind", ["runtime", "stage"])
def test_v2_deletion_serializes_late_private_writers(
    database_engine: Engine,
    session_factory: sessionmaker[Session],
    writer_kind: Literal["runtime", "stage"],
) -> None:
    with session_factory.begin() as session:
        public = _seed_public(session)

    if writer_kind == "runtime":
        writer_first_dispatch = _runtime_dispatch(
            owner_user_id=OWNER_A,
            project_id=PROJECT_A,
            public=public,
        )
        writer_first_id = writer_first_dispatch.payload.command_id

        def write_first(session: Session) -> None:
            command = writer_first_dispatch.payload
            reserve_collection_attempt(
                session,
                writer_first_dispatch,
                effective_policy_revision=1,
                now=NOW,
                uuid_factory=uuid4,
                private_scope=_proof(
                    owner_user_id=OWNER_A,
                    project_id=PROJECT_A,
                    command_id=command.command_id,
                    job_id=command.job_id,
                ),
            )

    else:
        staged_writer_command, staged_writer_result = _stage_pair(
            owner_user_id=OWNER_A,
            project_id=PROJECT_A,
            public=public,
        )
        writer_first_id = staged_writer_command.command_id

        def write_first(session: Session) -> None:
            stage_private_result(
                session,
                staged_writer_command,
                staged_writer_result,
                message_id=uuid4(),
                occurred_at=NOW,
                private_scope=_proof(
                    owner_user_id=OWNER_A,
                    project_id=PROJECT_A,
                    command_id=staged_writer_command.command_id,
                    job_id=staged_writer_command.job_id,
                ),
            )

    writer_first_deletion = _command(kind="PROJECT", project_id=PROJECT_A)
    deletion_ready = Event()
    deletion_pid: list[int] = []
    deletion_result: list[str] = []
    deletion_errors: list[BaseException] = []

    def delete_after_writer() -> None:
        try:
            with session_factory.begin() as session:
                deletion_pid.append(int(session.scalar(text("SELECT pg_backend_pid()"))))
                deletion_ready.set()
                deletion_result.append(_apply_v2(session, writer_first_deletion))
        except BaseException as exc:  # pragma: no cover - surfaced below
            deletion_errors.append(exc)

    with session_factory() as writer_session:
        with writer_session.begin():
            write_first(writer_session)
            thread = Thread(target=delete_after_writer, daemon=True)
            thread.start()
            assert deletion_ready.wait(timeout=1)
            _wait_for_postgres_lock(database_engine, deletion_pid[0])
    thread.join(timeout=2)
    assert not thread.is_alive()
    assert deletion_errors == []
    assert deletion_result == ["APPLIED"]
    with session_factory() as session:
        writer_model = CollectionRuntimeAttempt if writer_kind == "runtime" else PrivateCommitStage
        assert session.get(writer_model, writer_first_id) is None

    deletion_first_command = _command(
        owner_user_id=OWNER_B,
        kind="PROJECT",
        project_id=PROJECT_FOREIGN,
    )
    writer_ready = Event()
    writer_pid: list[int] = []
    writer_errors: list[BaseException] = []

    if writer_kind == "runtime":
        late_dispatch = _runtime_dispatch(
            owner_user_id=OWNER_B,
            project_id=PROJECT_FOREIGN,
            public=public,
        )

        def write_late(session: Session) -> None:
            command = late_dispatch.payload
            reserve_collection_attempt(
                session,
                late_dispatch,
                effective_policy_revision=1,
                now=NOW,
                uuid_factory=uuid4,
                private_scope=_proof(
                    owner_user_id=OWNER_B,
                    project_id=PROJECT_FOREIGN,
                    command_id=command.command_id,
                    job_id=command.job_id,
                ),
            )

    else:
        late_command, late_result = _stage_pair(
            owner_user_id=OWNER_B,
            project_id=PROJECT_FOREIGN,
            public=public,
        )

        def write_late(session: Session) -> None:
            stage_private_result(
                session,
                late_command,
                late_result,
                message_id=uuid4(),
                occurred_at=NOW,
                private_scope=_proof(
                    owner_user_id=OWNER_B,
                    project_id=PROJECT_FOREIGN,
                    command_id=late_command.command_id,
                    job_id=late_command.job_id,
                ),
            )

    def write_after_deletion() -> None:
        try:
            with session_factory.begin() as session:
                writer_pid.append(int(session.scalar(text("SELECT pg_backend_pid()"))))
                writer_ready.set()
                write_late(session)
        except BaseException as exc:  # pragma: no cover - asserted below
            writer_errors.append(exc)

    with session_factory() as deletion_session:
        with deletion_session.begin():
            assert _apply_v2(deletion_session, deletion_first_command) == "APPLIED"
            late_writer = Thread(target=write_after_deletion, daemon=True)
            late_writer.start()
            assert writer_ready.wait(timeout=1)
            _wait_for_postgres_lock(database_engine, writer_pid[0])
    late_writer.join(timeout=2)
    assert not late_writer.is_alive()
    assert len(writer_errors) == 1
    assert isinstance(writer_errors[0], PrivateScopeRejected)


@pytest.mark.approved_postgres
def test_v2_ack_failure_keeps_receipt_for_full_scope_retry(
    session_factory: sessionmaker[Session],
) -> None:
    command = _command(kind="ACCOUNT")
    failed = _PrivateDeletionAckCallbackV2(fail=True)
    with pytest.raises(RuntimeError, match="acknowledgement callback"):
        _process_v2(session_factory, command, failed)
    assert len(failed.acknowledgements) == 1
    assert failed.acknowledgements[0].outcome == "APPLIED"

    retried = _PrivateDeletionAckCallbackV2()
    ack = _process_v2(session_factory, command, retried)
    assert ack is not None and ack.outcome == "DUPLICATE"
    assert retried.acknowledgements == [ack]
