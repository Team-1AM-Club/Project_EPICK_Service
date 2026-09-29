"""Cross-seam private-authority runtime coverage against approved PostgreSQL."""

from __future__ import annotations

import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, create_engine, func, select, text
from sqlalchemy.orm import Session, sessionmaker

from epick_engine.source_collection.collector import (
    StaticFetchResult,
    StaticResponseCandidate,
)
from epick_engine.source_collection.commit_gate_contracts import (
    CommitGateCommand,
    StagedResultProposal,
    parse_commit_gate_command,
)
from epick_engine.source_collection.commit_gate_runtime import (
    QueueDelivery,
    consume_once,
    relay_once,
)
from epick_engine.source_collection.commit_gate_store import (
    PrivateCommitGateAck,
    PrivateCommitStage,
    PrivateStagedOutbox,
)
from epick_engine.source_collection.contracts import SourceType
from epick_engine.source_collection.parsing import extract_static_candidate
from epick_engine.source_collection.persistence import (
    Base,
    CollectionRuntimeAttempt,
    Company,
    PrivateDeletionOwnerState,
    Source,
    SourcePolicyDecision,
)
from epick_engine.source_collection.policy import Representation as FetchRepresentation
from epick_engine.source_collection.policy import UntrustedDocument, ValidatedTarget
from epick_engine.source_collection.private_deletion_v2 import (
    PrivateDeletionAckV2,
    PrivateDeletionCommandV2,
    PrivateDeletionScope,
    process_private_deletion_v2,
)
from epick_engine.source_collection.source_runtime import (
    RuntimeAuthorizationError,
    handle_collection_dispatch,
)
from epick_engine.source_collection.source_runtime_input import (
    RuntimeSourceConfig,
    RuntimeSourceConfigFile,
    SqlAlchemyCollectionInputProvider,
)
from epick_engine.source_collection.w1_lookup_client import W1LookupClientError
from epick_engine.source_collection.w1_private_authority_contracts import (
    CurrentWriteScopeLookupResponse,
    GateAuthorityResponse,
    GateScopeLookupResponse,
    PrivateWriteAuthorityResponse,
    TerminalCleanupAuthorityResponse,
    W1GateBinding,
    W1PrivateBinding,
)
from epick_engine.source_collection.w1_transport import (
    LookupResponse,
    W1Dispatch,
    parse_w1_dispatch,
)

pytestmark = pytest.mark.approved_postgres
PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "w1_private_contract"
NOW = datetime(2026, 9, 26, 12, tzinfo=UTC)
EXPECTED_SENDER_ID = "AROASYNTHETICROLE01"


@pytest.fixture
def database_engine(approved_postgres_url: Any) -> Iterator[Engine]:
    admin = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_authority_{uuid4().hex}"
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = admin.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def session_factory(database_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(database_engine, autoflush=False, expire_on_commit=False)


def _runtime_config(source_id: UUID) -> RuntimeSourceConfigFile:
    return RuntimeSourceConfigFile.model_validate(
        {
            "schema_version": "w2.source-runtime-config.v1",
            "claim_lease_seconds": 120,
            "sources": {
                str(source_id): {
                    "policy_revision": 3,
                    "robots_permission": "allowed",
                    "result_version": 1,
                    "language": "ko",
                    "redirect_robots_permissions": [],
                    "limits": {
                        "site_concurrency": 1,
                        "global_concurrency": 2,
                        "source_ttl_seconds": 300,
                        "max_response_bytes": 1_048_576,
                        "max_decompressed_bytes": 2_097_152,
                        "connect_timeout_seconds": 3.0,
                        "read_timeout_seconds": 5.0,
                        "max_redirects": 2,
                        "general_retry_limit": 0,
                        "retention_days": 7,
                    },
                }
            },
        }
    )


@dataclass(frozen=True)
class _FastHeartbeatConfig:
    sources: Mapping[UUID, RuntimeSourceConfig]
    claim_lease_seconds: int = 120
    heartbeat_interval_seconds: float = 0.01


def _seed_source(session_factory: sessionmaker[Session]) -> tuple[UUID, UUID]:
    company_id, source_id = uuid4(), uuid4()
    with session_factory.begin() as session:
        session.add(
            Company(
                company_id=company_id,
                legal_name="Synthetic authority company",
                aliases=[],
                official_domains=["authority-runtime.test"],
                legal_identifiers={},
                identity_status="verified",
                identity_evidence=["synthetic:authority-runtime"],
            )
        )
        session.add(
            Source(
                source_id=source_id,
                company_id=company_id,
                source_type=SourceType.COMPANY_WEBSITE.value,
                canonical_url=f"https://authority-runtime.test/{source_id}",
                title="Synthetic authority source",
            )
        )
        session.add(
            SourcePolicyDecision(
                policy_decision_id=uuid4(),
                source_id=source_id,
                revision=3,
                official_status="verified",
                access_class="public",
                collection_permission="allowed",
                excerpt_storage_permission="allowed",
                body_storage_permission="denied",
                redistribution_permission="unknown",
                evidence_refs=["synthetic:authority-policy"],
                checked_at=NOW,
                policy_version="authority-policy-3",
            )
        )
    return company_id, source_id


def _dispatch(
    *,
    kind: str,
    owner_ref: UUID,
    company_id: UUID,
    source_id: UUID,
    scope: PrivateDeletionScope,
) -> W1Dispatch:
    fixture = (
        "private-w2-command-dispatch.json"
        if kind == "core"
        else "private-w2-direct-source-registration-dispatch.json"
    )
    raw = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    command_id, job_id = uuid4(), uuid4()
    raw["message_id"] = str(command_id)
    raw["payload"].update(
        command_id=str(command_id),
        job_id=str(job_id),
        authenticated_owner_ref=str(owner_ref),
        company_id=str(company_id),
        source_id=str(source_id),
        execution_fence="1",
        owner_deletion_epoch=0,
        project_ref=None if scope.project_id is None else str(scope.project_id),
    )
    raw["lookup_request"].update(
        command_id=str(command_id),
        execution_fence=1,
        owner_deletion_epoch=0,
    )
    if kind == "core":
        raw["core_decision_pin"].update(
            company_id=str(company_id),
            source_id=str(source_id),
            decision_id=str(uuid4()),
        )
    else:
        raw["direct_source_registration_pin"].update(
            company_id=str(company_id),
            source_id=str(source_id),
            registration_decision_id=str(uuid4()),
        )
    return parse_w1_dispatch(raw)


def _private_payload(binding: W1PrivateBinding) -> dict[str, object]:
    return {
        "owner_user_id": binding.owner_user_id,
        "owner_deletion_epoch": binding.owner_deletion_epoch,
        "command_id": binding.command_id,
        "job_id": binding.job_id,
        "execution_fence": binding.execution_fence,
    }


def _gate_payload(binding: W1GateBinding, phase: str) -> dict[str, object]:
    payload: dict[str, object] = {
        **_private_payload(binding.private),
        "operation_id": binding.operation_id,
        "operation_revision": binding.operation_revision,
        "action": binding.action,
        "phase": phase,
        "result_digest": binding.result_digest,
    }
    if binding.purge_owner_deletion_epoch is not None:
        payload["purge_owner_deletion_epoch"] = binding.purge_owner_deletion_epoch
    return payload


class _SyntheticW1Authority:
    def __init__(
        self,
        scopes: Mapping[UUID, PrivateDeletionScope],
        *,
        lookup_error: W1LookupClientError | None = None,
        denied_write_commands: frozenset[UUID] = frozenset(),
    ) -> None:
        self.scopes = scopes
        self.lookup_error = lookup_error
        self.denied_write_commands = denied_write_commands
        self.scope_calls: list[W1PrivateBinding] = []
        self.write_calls: list[W1PrivateBinding] = []
        self.gate_calls: list[tuple[W1GateBinding, str]] = []
        self.cleanup_calls: list[tuple[W1PrivateBinding, str]] = []
        self.heartbeat_seen = Event()

    def _scope(self, command_id: UUID) -> PrivateDeletionScope:
        return self.scopes[command_id]

    def lookup_current_scope(self, binding: W1PrivateBinding) -> CurrentWriteScopeLookupResponse:
        self.scope_calls.append(binding)
        if self.lookup_error is not None:
            raise self.lookup_error
        return CurrentWriteScopeLookupResponse.model_validate(
            {
                "schema_version": "w1.private.w2-current-write-scope-lookup.v1",
                **_private_payload(binding),
                "scope": self._scope(binding.command_id).to_mapping(),
            },
            strict=True,
        )

    def authorize_write(
        self,
        binding: W1PrivateBinding,
        scope: PrivateDeletionScope,
    ) -> PrivateWriteAuthorityResponse:
        self.write_calls.append(binding)
        if len(self.write_calls) >= 3:
            self.heartbeat_seen.set()
        if binding.command_id in self.denied_write_commands:
            raise W1LookupClientError("HTTP_403")
        return PrivateWriteAuthorityResponse.model_validate(
            {
                "schema_version": "w1.private.w2-write-authority.v1",
                **_private_payload(binding),
                "scope": scope.to_mapping(),
                "authority_ref": f"w1:synthetic:write:{len(self.write_calls)}",
            },
            strict=True,
        )

    def lookup_gate_scope(
        self,
        binding: W1GateBinding,
        phase: str,
    ) -> GateScopeLookupResponse:
        return GateScopeLookupResponse.model_validate(
            {
                "schema_version": "w1.private.w2-gate-scope-lookup.v1",
                **_gate_payload(binding, phase),
                "scope": self._scope(binding.private.command_id).to_mapping(),
            },
            strict=True,
        )

    def authorize_gate(
        self,
        binding: W1GateBinding,
        phase: str,
        scope: PrivateDeletionScope,
    ) -> GateAuthorityResponse:
        self.gate_calls.append((binding, phase))
        return GateAuthorityResponse.model_validate(
            {
                "schema_version": "w1.private.w2-gate-authority.v1",
                **_gate_payload(binding, phase),
                "scope": scope.to_mapping(),
                "authority_ref": f"w1:synthetic:gate:{len(self.gate_calls)}",
            },
            strict=True,
        )

    def authorize_terminal_cleanup(
        self,
        binding: W1PrivateBinding,
        scope: PrivateDeletionScope,
        cleanup_kind: str,
    ) -> TerminalCleanupAuthorityResponse:
        self.cleanup_calls.append((binding, cleanup_kind))
        return TerminalCleanupAuthorityResponse.model_validate(
            {
                "schema_version": "w1.private.w2-terminal-cleanup.v1",
                **_private_payload(binding),
                "scope": scope.to_mapping(),
                "cleanup_kind": cleanup_kind,
                "authority_ref": f"w1:synthetic:cleanup:{len(self.cleanup_calls)}",
                "allowed_effect": "OWNER_LOCKED_PRIVATE_CLEANUP_ONLY",
            },
            strict=True,
        )


class _AvailableLookup:
    def lookup_dispatch(self, dispatch: W1Dispatch) -> LookupResponse:
        return LookupResponse(
            schema_version="w1.private.command-lookup.v1",
            command_id=dispatch.payload.command_id,
            status="AVAILABLE",
            reason_code=None,
            command=dispatch.payload,
        )


class _Collector:
    def __init__(self, dispatch: W1Dispatch, heartbeat_seen: Event) -> None:
        self.dispatch = dispatch
        self.heartbeat_seen = heartbeat_seen

    def fetch(self, request: object, *, is_cancelled: Callable[[], bool]) -> StaticFetchResult:
        assert self.heartbeat_seen.wait(timeout=2), "fresh heartbeat authority was not observed"
        assert is_cancelled() is False
        document = "<html><body><main><h2>Requirements</h2><p>Python</p></main></body></html>"
        return StaticFetchResult(
            command_id=self.dispatch.payload.command_id,
            candidate=StaticResponseCandidate(
                final_target=ValidatedTarget(
                    url=f"https://authority-runtime.test/{self.dispatch.payload.source_id}",
                    hostname="authority-runtime.test",
                    port=443,
                    resolved_addresses=frozenset({"198.51.100.21"}),
                ),
                representation=FetchRepresentation.HTML,
                document=UntrustedDocument(text=document),
                http_status=200,
                raw_size=len(document.encode()),
                decompressed_size=len(document.encode()),
            ),
            failure_code=None,
        )

    def close(self) -> None:
        return None


@dataclass
class _Queue:
    deliveries: list[QueueDelivery] = field(default_factory=list)
    sent: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)

    def receive(self) -> list[QueueDelivery]:
        return list(self.deliveries)

    def delete(self, receipt_handle: str) -> None:
        self.deleted.append(receipt_handle)

    def send(self, body: str) -> None:
        self.sent.append(body)


@dataclass
class _DeletionAckCallback:
    acknowledgements: list[PrivateDeletionAckV2] = field(default_factory=list)

    def acknowledge(self, *, acknowledgement: PrivateDeletionAckV2) -> None:
        self.acknowledgements.append(acknowledgement)


def _gate(proposal: StagedResultProposal) -> CommitGateCommand:
    raw = json.loads((FIXTURES / "private-w2-commit-gate-prepare.json").read_text(encoding="utf-8"))
    raw.update(
        message_id=str(uuid4()),
        operation_id=str(uuid4()),
        operation_revision=1,
        command_id=str(proposal.command.command_id),
        job_id=str(proposal.command.job_id),
        authenticated_owner_ref=str(proposal.command.authenticated_owner_ref),
        execution_fence=int(proposal.command.execution_fence),
        owner_deletion_epoch=proposal.command.owner_deletion_epoch,
        result_digest=proposal.result_digest,
    )
    return parse_commit_gate_command(raw)


def _delivery(gate: CommitGateCommand) -> QueueDelivery:
    return QueueDelivery(
        receipt_handle="synthetic-authority-runtime-receipt",
        body=json.dumps(
            gate.model_dump(mode="json"),
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        ),
        sender_id=f"{EXPECTED_SENDER_ID}:synthetic-session",
    )


def test_core_direct_account_project_round_trip_with_revocation_and_restart(
    database_engine: Engine,
    session_factory: sessionmaker[Session],
) -> None:
    """Breaking any fresh-decision seam leaves a durable cross-runtime inconsistency."""

    proposals: dict[tuple[str, str], StagedResultProposal] = {}
    authorities: dict[tuple[str, str], _SyntheticW1Authority] = {}
    scopes: dict[UUID, PrivateDeletionScope] = {}

    for dispatch_kind in ("core", "direct"):
        for scope_kind in ("ACCOUNT", "PROJECT"):
            company_id, source_id = _seed_source(session_factory)
            scope = PrivateDeletionScope(
                kind=cast(Any, scope_kind),
                project_id=uuid4() if scope_kind == "PROJECT" else None,
            )
            dispatch = _dispatch(
                kind=dispatch_kind,
                owner_ref=uuid4(),
                company_id=company_id,
                source_id=source_id,
                scope=scope,
            )
            scopes[dispatch.payload.command_id] = scope
            authority = _SyntheticW1Authority(scopes)
            config = _runtime_config(source_id)
            proposal = handle_collection_dispatch(
                dispatch,
                session_factory=session_factory,
                lookup_client=_AvailableLookup(),
                input_provider=SqlAlchemyCollectionInputProvider(session_factory, config),
                collector_factory=lambda d=dispatch, a=authority: _Collector(d, a.heartbeat_seen),
                parser=extract_static_candidate,
                runtime_config=cast(
                    RuntimeSourceConfigFile,
                    _FastHeartbeatConfig(config.sources),
                ),
                clock=lambda: NOW,
                uuid_factory=uuid4,
                private_authority_client=authority,
            )
            proposals[(dispatch_kind, scope_kind)] = proposal
            authorities[(dispatch_kind, scope_kind)] = authority

            assert authority.scope_calls == [W1PrivateBinding.from_collection(dispatch.payload)]
            assert len(authority.write_calls) >= 5
            with session_factory() as session:
                attempt = session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
                stage = session.get(PrivateCommitStage, dispatch.payload.command_id)
                staged = session.get(PrivateStagedOutbox, proposal.message_id)
                assert attempt is not None and stage is not None and staged is not None
                assert attempt.state == "PERSISTED"
                assert attempt.claim_token is None
                assert stage.private_scope_kind == scope_kind
                assert stage.project_id == scope.project_id
                assert staged.payload is not None and staged.delivered_at is None

    timeout_company, timeout_source = _seed_source(session_factory)
    timeout_scope = PrivateDeletionScope(kind="ACCOUNT", project_id=None)
    timeout_dispatch = _dispatch(
        kind="core",
        owner_ref=uuid4(),
        company_id=timeout_company,
        source_id=timeout_source,
        scope=timeout_scope,
    )
    timeout_authority = _SyntheticW1Authority(
        {timeout_dispatch.payload.command_id: timeout_scope},
        lookup_error=W1LookupClientError("TIMEOUT"),
    )
    timeout_config = _runtime_config(timeout_source)
    with pytest.raises(RuntimeAuthorizationError, match="scope lookup"):
        handle_collection_dispatch(
            timeout_dispatch,
            session_factory=session_factory,
            lookup_client=_AvailableLookup(),
            input_provider=SqlAlchemyCollectionInputProvider(session_factory, timeout_config),
            collector_factory=lambda: pytest.fail("timeout must precede collection"),
            parser=extract_static_candidate,
            runtime_config=timeout_config,
            clock=lambda: NOW,
            uuid_factory=uuid4,
            private_authority_client=timeout_authority,
        )
    assert timeout_authority.write_calls == []
    with session_factory() as session:
        assert session.get(CollectionRuntimeAttempt, timeout_dispatch.payload.command_id) is None

    ack_proposal = proposals[("core", "PROJECT")]
    ack_gate = _gate(ack_proposal)
    gate_authority = _SyntheticW1Authority(scopes)
    gate_queue = _Queue(deliveries=[_delivery(ack_gate)])
    assert (
        consume_once(
            session_factory,
            gate_queue,
            EXPECTED_SENDER_ID,
            clock=lambda: NOW,
            private_authority_client=gate_authority,
        ).status
        == "APPLIED"
    )
    assert [phase for _, phase in gate_authority.gate_calls] == ["APPLY"]

    denied_proposal = proposals[("direct", "ACCOUNT")]
    denied_command_id = denied_proposal.command.command_id
    with session_factory.begin() as session:
        owner = session.get(
            PrivateDeletionOwnerState,
            denied_proposal.command.authenticated_owner_ref,
        )
        assert owner is not None
        owner.latest_epoch = 1
        owner.account_deleted = True
    cleanup_authority = _SyntheticW1Authority(
        scopes,
        denied_write_commands=frozenset({denied_command_id}),
    )
    denied_queue = _Queue()
    assert (
        relay_once(
            session_factory,
            denied_queue,
            command_id=denied_command_id,
            authority_client=cleanup_authority,
        ).status
        == "SEND_FAILED"
    )
    assert denied_queue.sent == []
    assert [kind for _, kind in cleanup_authority.cleanup_calls] == ["STAGED_OUTBOX"]
    with session_factory() as session:
        denied_stage = session.get(PrivateCommitStage, denied_command_id)
        denied_outbox = session.get(PrivateStagedOutbox, denied_proposal.message_id)
        assert denied_stage is not None and denied_outbox is not None
        assert denied_stage.payload_purged is True and denied_stage.result_payload is None
        assert denied_outbox.payload is None and denied_outbox.delivered_at is None

    with session_factory() as session:
        ack = session.scalar(
            select(PrivateCommitGateAck).where(
                PrivateCommitGateAck.command_id == ack_proposal.command.command_id
            )
        )
        assert ack is not None
        original_ack_id = ack.message_id
        original_ack_body = json.dumps(
            ack.payload,
            sort_keys=True,
            separators=(",", ":"),
            ensure_ascii=False,
            allow_nan=False,
        )

    deletion_callback = _DeletionAckCallback()
    deletion = process_private_deletion_v2(
        session_factory,
        PrivateDeletionCommandV2(
            deletion_id=uuid4(),
            owner_user_id=ack_proposal.command.authenticated_owner_ref,
            deletion_epoch=1,
            scope=scopes[ack_proposal.command.command_id],
        ),
        deletion_callback,
    )
    assert deletion is not None and deletion.outcome == "APPLIED"
    assert deletion_callback.acknowledgements == [deletion]

    restarted_session_factory = sessionmaker(
        database_engine,
        autoflush=False,
        expire_on_commit=False,
    )
    historical_authority = _SyntheticW1Authority(scopes)
    outbound = _Queue()
    assert (
        relay_once(
            restarted_session_factory,
            outbound,
            clock=lambda: NOW,
            command_id=ack_proposal.command.command_id,
            authority_client=historical_authority,
        ).status
        == "SENT"
    )
    assert outbound.sent == [original_ack_body]
    assert json.loads(outbound.sent[0])["message_id"] == str(original_ack_id)
    assert [phase for _, phase in historical_authority.gate_calls] == [
        "ACK_RELAY",
        "ACK_RELAY",
    ]
    assert historical_authority.write_calls == []

    with restarted_session_factory() as session:
        ack_stage = session.get(PrivateCommitStage, ack_proposal.command.command_id)
        retained_ack = session.get(PrivateCommitGateAck, original_ack_id)
        assert ack_stage is not None and retained_ack is not None
        assert ack_stage.payload_purged is True and ack_stage.result_payload is None
        assert retained_ack.delivered_at == NOW
        assert (
            session.scalar(
                select(func.count())
                .select_from(PrivateStagedOutbox)
                .where(
                    PrivateStagedOutbox.delivered_at.is_(None),
                    PrivateStagedOutbox.payload.is_not(None),
                )
            )
            == 2
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(PrivateCommitGateAck)
                .where(PrivateCommitGateAck.delivered_at.is_(None))
            )
            == 0
        )
        assert (
            session.scalar(
                select(func.count())
                .select_from(PrivateCommitStage)
                .where(PrivateCommitStage.payload_purged.is_(True))
            )
            == 2
        )
