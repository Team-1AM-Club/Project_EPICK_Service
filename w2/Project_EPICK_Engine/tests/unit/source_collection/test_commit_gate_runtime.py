"""Queue-independent W2 commit-gate runtime behavior."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

import pytest

from epick_engine.source_collection import commit_gate_runtime, commit_gate_store
from epick_engine.source_collection.commit_gate_contracts import (
    CommitGateCommand,
    build_commit_gate_ack,
    parse_commit_gate_command,
)
from epick_engine.source_collection.commit_gate_runtime import (
    MAX_MESSAGE_BYTES,
    QueueDelivery,
    consume_once,
)
from epick_engine.source_collection.commit_gate_store import (
    PrivateCommitGateAck,
    PrivateCommitStage,
)
from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionScope
from epick_engine.source_collection.private_scope import PrivateGateAuthority
from epick_engine.source_collection.w1_lookup_client import W1LookupClientError
from epick_engine.source_collection.w1_private_authority_contracts import (
    GateAuthorityResponse,
    GateScopeLookupResponse,
    W1GateBinding,
)

EXPECTED_SENDER_ID = "AROASYNTHETICROLE01"
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures"


@dataclass
class FakeQueue:
    deliveries: list[QueueDelivery] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    receive_error: Exception | None = None

    def receive(self) -> list[QueueDelivery]:
        if self.receive_error is not None:
            raise self.receive_error
        return list(self.deliveries)

    def delete(self, receipt_handle: str) -> None:
        self.deleted.append(receipt_handle)

    def send(self, body: str) -> None:
        raise AssertionError("consumer must not send queue messages")


class NeverSessionFactory:
    def begin(self) -> object:
        raise AssertionError("rejected input must not open a database transaction")


class EmptySession:
    def __enter__(self) -> EmptySession:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def scalar(self, _statement: object) -> None:
        return None


class EmptySessionFactory:
    def __call__(self) -> EmptySession:
        return EmptySession()


@dataclass
class PersistedAck:
    delivered_at: datetime | None


@dataclass
class PersistedStage:
    command_id: UUID
    owner_ref: UUID
    job_id: UUID
    execution_fence: str
    owner_deletion_epoch: str
    result_digest: str
    private_scope_kind: str
    project_id: UUID | None


@dataclass(frozen=True)
class AppliedAck:
    message_id: UUID


class GateSession:
    def __init__(self, stage: PersistedStage | None = None) -> None:
        self.persisted_ack: PersistedAck | None = None
        self.stage = stage

    def __enter__(self) -> GateSession:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def get(self, model: object, _message_id: object, **_kwargs: object) -> object | None:
        if model is PrivateCommitStage:
            return self.stage
        if model is PrivateCommitGateAck:
            return self.persisted_ack
        raise AssertionError("unexpected model lookup")


class GateSessionFactory:
    def __init__(self, stage: PersistedStage | None = None) -> None:
        self.session = GateSession(stage)
        self.begin_calls = 0
        self.read_calls = 0

    def begin(self) -> GateSession:
        self.begin_calls += 1
        return self.session

    def __call__(self) -> GateSession:
        self.read_calls += 1
        return self.session


@dataclass
class FakeGateAuthorityClient:
    scope: PrivateDeletionScope
    lookup_error: Exception | None = None
    authorize_error: Exception | None = None
    lookup_calls: list[tuple[W1GateBinding, str]] = field(default_factory=list)
    authorize_calls: list[tuple[W1GateBinding, str, PrivateDeletionScope]] = field(
        default_factory=list
    )

    @staticmethod
    def _payload(gate: W1GateBinding, phase: str) -> dict[str, object]:
        payload: dict[str, object] = {
            "owner_user_id": str(gate.private.owner_user_id),
            "owner_deletion_epoch": gate.private.owner_deletion_epoch,
            "command_id": str(gate.private.command_id),
            "job_id": str(gate.private.job_id),
            "execution_fence": gate.private.execution_fence,
            "operation_id": str(gate.operation_id),
            "operation_revision": gate.operation_revision,
            "action": gate.action,
            "phase": phase,
            "result_digest": gate.result_digest,
        }
        if gate.purge_owner_deletion_epoch is not None:
            payload["purge_owner_deletion_epoch"] = gate.purge_owner_deletion_epoch
        return payload

    def lookup_gate_scope(
        self,
        gate: W1GateBinding,
        phase: str,
    ) -> GateScopeLookupResponse:
        self.lookup_calls.append((gate, phase))
        if self.lookup_error is not None:
            raise self.lookup_error
        return GateScopeLookupResponse.model_validate(
            {
                "schema_version": "w1.private.w2-gate-scope-lookup.v1",
                **self._payload(gate, phase),
                "scope": self.scope.to_mapping(),
            }
        )

    def authorize_gate(
        self,
        gate: W1GateBinding,
        phase: str,
        scope: PrivateDeletionScope,
    ) -> GateAuthorityResponse:
        self.authorize_calls.append((gate, phase, scope))
        if self.authorize_error is not None:
            raise self.authorize_error
        return GateAuthorityResponse.model_validate(
            {
                "schema_version": "w1.private.w2-gate-authority.v1",
                **self._payload(gate, phase),
                "scope": scope.to_mapping(),
                "authority_ref": "w1:test-gate-apply-authority",
            }
        )


def _gate_delivery(action: str) -> tuple[CommitGateCommand, QueueDelivery]:
    body = (
        FIXTURES / f"w1_private_contract/private-w2-commit-gate-{action.lower()}.json"
    ).read_text(encoding="utf-8")
    gate = parse_commit_gate_command(json.loads(body))
    return gate, QueueDelivery(
        receipt_handle=f"stage-less-{action.lower()}-receipt",
        body=body,
        sender_id=f"{EXPECTED_SENDER_ID}:synthetic-session",
    )


def _consume(delivery: QueueDelivery | None, *, receive_error: Exception | None = None):
    queue = FakeQueue(
        deliveries=[] if delivery is None else [delivery], receive_error=receive_error
    )
    result = consume_once(NeverSessionFactory(), queue, EXPECTED_SENDER_ID)
    return result, queue


def test_empty_receive_has_fixed_safe_status() -> None:
    result, queue = _consume(None)

    assert result.status == "EMPTY"
    assert queue.deleted == []


def test_receive_failure_has_fixed_safe_status() -> None:
    result, queue = _consume(None, receive_error=RuntimeError("synthetic transport secret"))

    assert result.status == "RECEIVE_FAILED"
    assert queue.deleted == []
    assert "secret" not in repr(result)


def test_system_sender_id_is_checked_before_untrusted_body() -> None:
    delivery = QueueDelivery(
        receipt_handle="synthetic-receipt",
        body='{"producer":"w1"}',
        sender_id="AROADIFFERENTROLE01:session-name",
    )

    result, queue = _consume(delivery)

    assert result.status == "UNAUTHENTICATED"
    assert queue.deleted == []


def test_non_ascii_system_sender_id_is_rejected_without_exception() -> None:
    delivery = QueueDelivery(
        receipt_handle="synthetic-receipt",
        body='{"producer":"w1"}',
        sender_id=f"{EXPECTED_SENDER_ID}\u00e9:session-name",
    )

    result, queue = _consume(delivery)

    assert result.status == "UNAUTHENTICATED"
    assert queue.deleted == []


def test_matching_stable_role_prefix_does_not_make_malformed_payload_authenticated_work() -> None:
    delivery = QueueDelivery(
        receipt_handle="synthetic-receipt",
        body="{",
        sender_id=f"{EXPECTED_SENDER_ID}:synthetic-session",
    )

    result, queue = _consume(delivery)

    assert result.status == "MALFORMED"
    assert queue.deleted == []


def test_duplicate_json_keys_are_rejected_without_deleting_receipt() -> None:
    delivery = QueueDelivery(
        receipt_handle="synthetic-receipt",
        body='{"schema_version":"first","schema_version":"second"}',
        sender_id=f"{EXPECTED_SENDER_ID}:synthetic-session",
    )

    result, queue = _consume(delivery)

    assert result.status == "MALFORMED"
    assert queue.deleted == []


def test_oversized_body_is_rejected_without_deleting_receipt() -> None:
    delivery = QueueDelivery(
        receipt_handle="synthetic-receipt",
        body="x" * (MAX_MESSAGE_BYTES + 1),
        sender_id=f"{EXPECTED_SENDER_ID}:synthetic-session",
    )

    result, queue = _consume(delivery)

    assert result.status == "MALFORMED"
    assert queue.deleted == []


def test_unsupported_collection_dispatch_is_not_deleted_from_gate_only_queue() -> None:
    body = json.dumps(
        json.loads(
            (FIXTURES / "w1_private_contract/private-w2-command-dispatch.json").read_text(
                encoding="utf-8"
            )
        )
    )
    delivery = QueueDelivery(
        receipt_handle="synthetic-receipt",
        body=body,
        sender_id=f"{EXPECTED_SENDER_ID}:synthetic-session",
    )

    result, queue = _consume(delivery)

    assert result.status == "MALFORMED"
    assert queue.deleted == []


def test_relay_reports_empty_when_no_durable_outbox_is_pending() -> None:
    result = commit_gate_runtime.relay_once(EmptySessionFactory(), FakeQueue())

    assert result.status == "EMPTY"


def test_private_command_lock_keeps_legacy_direct_import_alias() -> None:
    assert commit_gate_store._lock_command is commit_gate_store.lock_private_command


def test_consume_once_keeps_private_gate_applier_as_its_default(monkeypatch) -> None:
    """Replacing the CT15 default with the collection-aware applier must fail."""

    body = (FIXTURES / "w1_private_contract/private-w2-commit-gate-prepare.json").read_text(
        encoding="utf-8"
    )
    queue = FakeQueue(
        [
            QueueDelivery(
                receipt_handle="legacy-default-receipt",
                body=body,
                sender_id=f"{EXPECTED_SENDER_ID}:synthetic-session",
            )
        ]
    )
    session_factory = GateSessionFactory()
    calls: list[str] = []

    def private_applier(
        session: GateSession,
        _gate: object,
        *,
        ack_message_id: UUID,
        occurred_at: datetime,
        private_gate_authority: object,
    ) -> object:
        assert isinstance(ack_message_id, UUID)
        assert occurred_at == datetime(2026, 9, 20, tzinfo=UTC)
        calls.append("private")
        session.persisted_ack = PersistedAck(delivered_at=None)
        return AppliedAck(message_id=ack_message_id)

    def collection_applier(*_args: object, **_kwargs: object) -> object:
        calls.append("collection")
        raise AssertionError("legacy CT15 consume_once must not select the collection applier")

    monkeypatch.setattr(commit_gate_runtime, "apply_commit_gate", private_applier)
    monkeypatch.setattr(
        commit_gate_runtime,
        "apply_collection_commit_gate",
        collection_applier,
        raising=False,
    )

    result = consume_once(
        session_factory,
        queue,
        EXPECTED_SENDER_ID,
        clock=lambda: datetime(2026, 9, 20, tzinfo=UTC),
        message_id_factory=uuid4,
        private_authority_client=FakeGateAuthorityClient(
            PrivateDeletionScope(kind="ACCOUNT", project_id=None)
        ),
    )

    assert result.status == "APPLIED"
    assert calls == ["private"]
    assert queue.deleted == ["legacy-default-receipt"]


def test_consume_once_allows_explicit_collection_aware_applier(monkeypatch) -> None:
    """Ignoring the apply_gate keyword would route general-runtime gates incorrectly."""

    body = (FIXTURES / "w1_private_contract/private-w2-commit-gate-finalize.json").read_text(
        encoding="utf-8"
    )
    queue = FakeQueue(
        [
            QueueDelivery(
                receipt_handle="collection-aware-receipt",
                body=body,
                sender_id=f"{EXPECTED_SENDER_ID}:synthetic-session",
            )
        ]
    )
    session_factory = GateSessionFactory()
    calls: list[str] = []

    def explicit_applier(
        session: GateSession,
        _gate: object,
        *,
        ack_message_id: UUID,
        occurred_at: datetime,
        private_gate_authority: object,
    ) -> object:
        assert isinstance(ack_message_id, UUID)
        assert occurred_at == datetime(2026, 9, 20, tzinfo=UTC)
        calls.append("collection")
        session.persisted_ack = PersistedAck(delivered_at=None)
        return AppliedAck(message_id=ack_message_id)

    def unexpected_legacy(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("explicit general-runtime applier must override the legacy default")

    monkeypatch.setattr(commit_gate_runtime, "apply_commit_gate", unexpected_legacy)

    result = consume_once(
        session_factory,
        queue,
        EXPECTED_SENDER_ID,
        clock=lambda: datetime(2026, 9, 20, tzinfo=UTC),
        message_id_factory=uuid4,
        apply_gate=explicit_applier,
        private_authority_client=FakeGateAuthorityClient(
            PrivateDeletionScope(kind="ACCOUNT", project_id=None)
        ),
    )

    assert result.status == "APPLIED"
    assert calls == ["collection"]
    assert queue.deleted == ["collection-aware-receipt"]


def test_valid_gate_is_rejected_without_trusted_authority_provider() -> None:
    body = (FIXTURES / "w1_private_contract/private-w2-commit-gate-prepare.json").read_text(
        encoding="utf-8"
    )
    queue = FakeQueue(
        [
            QueueDelivery(
                receipt_handle="missing-provider-receipt",
                body=body,
                sender_id=f"{EXPECTED_SENDER_ID}:synthetic-session",
            )
        ]
    )

    result = consume_once(NeverSessionFactory(), queue, EXPECTED_SENDER_ID)

    assert result.status == "REJECTED"
    assert queue.deleted == []


@pytest.mark.parametrize(
    ("action", "scope"),
    [
        ("ABORT", PrivateDeletionScope(kind="ACCOUNT", project_id=None)),
        ("PURGE", PrivateDeletionScope(kind="PROJECT", project_id=uuid4())),
    ],
)
def test_stage_less_abort_purge_resolves_scope_without_guessing(
    action: str,
    scope: PrivateDeletionScope,
) -> None:
    gate, delivery = _gate_delivery(action)
    queue = FakeQueue([delivery])
    session_factory = GateSessionFactory()
    client = FakeGateAuthorityClient(scope)
    applied: list[object] = []

    def apply_gate(
        session: GateSession,
        applied_gate: CommitGateCommand,
        *,
        ack_message_id: UUID,
        occurred_at: datetime,
        private_gate_authority: object,
    ) -> AppliedAck:
        assert applied_gate == gate
        assert occurred_at == datetime(2026, 9, 20, tzinfo=UTC)
        applied.append(private_gate_authority)
        session.persisted_ack = PersistedAck(delivered_at=None)
        return AppliedAck(message_id=ack_message_id)

    result = consume_once(
        session_factory,
        queue,
        EXPECTED_SENDER_ID,
        clock=lambda: datetime(2026, 9, 20, tzinfo=UTC),
        message_id_factory=uuid4,
        apply_gate=apply_gate,
        private_authority_client=client,
    )

    assert result.status == "APPLIED"
    assert len(client.lookup_calls) == 1
    assert client.lookup_calls[0] == (W1GateBinding.from_gate(gate), "APPLY")
    assert client.authorize_calls == [(W1GateBinding.from_gate(gate), "APPLY", scope)]
    assert len(applied) == 1
    authority = applied[0]
    assert authority.phase == "APPLY"
    assert authority.action == action
    assert authority.scope == scope
    assert session_factory.read_calls == 1
    assert session_factory.begin_calls == 1
    assert queue.deleted == [delivery.receipt_handle]


def test_persisted_exact_stage_scope_skips_lookup_but_requires_fresh_apply_authority() -> None:
    gate, delivery = _gate_delivery("PREPARE")
    scope = PrivateDeletionScope(kind="PROJECT", project_id=uuid4())
    stage = PersistedStage(
        command_id=gate.command_id,
        owner_ref=gate.authenticated_owner_ref,
        job_id=gate.job_id,
        execution_fence=str(gate.execution_fence),
        owner_deletion_epoch=str(gate.owner_deletion_epoch),
        result_digest=gate.result_digest,
        private_scope_kind="PROJECT",
        project_id=scope.project_id,
    )
    session_factory = GateSessionFactory(stage)
    client = FakeGateAuthorityClient(scope)
    queue = FakeQueue([delivery])

    def apply_gate(
        session: GateSession,
        _gate: CommitGateCommand,
        **kwargs: object,
    ) -> AppliedAck:
        authority = kwargs["private_gate_authority"]
        assert isinstance(authority, PrivateGateAuthority)
        assert authority.scope == scope
        message_id = kwargs["ack_message_id"]
        assert isinstance(message_id, UUID)
        session.persisted_ack = PersistedAck(delivered_at=None)
        return AppliedAck(message_id=message_id)

    result = consume_once(
        session_factory,
        queue,
        EXPECTED_SENDER_ID,
        apply_gate=apply_gate,
        private_authority_client=client,
    )

    assert result.status == "APPLIED"
    assert client.lookup_calls == []
    assert client.authorize_calls == [(W1GateBinding.from_gate(gate), "APPLY", scope)]


def test_stage_less_gate_rejects_changed_authority_echo_before_transaction() -> None:
    gate, delivery = _gate_delivery("ABORT")
    scope = PrivateDeletionScope(kind="ACCOUNT", project_id=None)

    class ChangedEchoClient(FakeGateAuthorityClient):
        def authorize_gate(
            self,
            gate: W1GateBinding,
            phase: str,
            scope: PrivateDeletionScope,
        ) -> GateAuthorityResponse:
            response = super().authorize_gate(gate, phase, scope)
            return response.model_copy(update={"phase": "ACK_RELAY"})

    session_factory = GateSessionFactory()
    queue = FakeQueue([delivery])
    client = ChangedEchoClient(scope)

    result = consume_once(
        session_factory,
        queue,
        EXPECTED_SENDER_ID,
        private_authority_client=client,
    )

    assert result.status == "REJECTED"
    assert session_factory.read_calls == 1
    assert session_factory.begin_calls == 0
    assert queue.deleted == []


@pytest.mark.parametrize("error_code", ["HTTP_401", "HTTP_403", "HTTP_503", "TIMEOUT"])
def test_stage_less_abort_purge_resolves_scope_without_guessing_on_lookup_failure(
    error_code: str,
) -> None:
    gate, delivery = _gate_delivery("ABORT")
    queue = FakeQueue([delivery])
    session_factory = GateSessionFactory()
    client = FakeGateAuthorityClient(
        PrivateDeletionScope(kind="ACCOUNT", project_id=None),
        lookup_error=W1LookupClientError(error_code),
    )

    result = consume_once(
        session_factory,
        queue,
        EXPECTED_SENDER_ID,
        private_authority_client=client,
    )

    assert result.status == "REJECTED"
    assert client.lookup_calls == [(W1GateBinding.from_gate(gate), "APPLY")]
    assert client.authorize_calls == []
    assert session_factory.read_calls == 1
    assert session_factory.begin_calls == 0
    assert queue.deleted == []


@pytest.mark.parametrize("error_code", ["HTTP_401", "HTTP_403", "HTTP_503", "TIMEOUT"])
def test_gate_apply_rejects_authorize_failure_before_transaction(
    error_code: str,
) -> None:
    gate, delivery = _gate_delivery("ABORT")
    scope = PrivateDeletionScope(kind="ACCOUNT", project_id=None)
    queue = FakeQueue([delivery])
    session_factory = GateSessionFactory()
    client = FakeGateAuthorityClient(
        scope,
        authorize_error=W1LookupClientError(error_code),
    )
    apply_calls: list[object] = []

    def apply_gate(*_args: object, **_kwargs: object) -> AppliedAck:
        apply_calls.append(object())
        raise AssertionError("authorize failure must not invoke the gate applier")

    result = consume_once(
        session_factory,
        queue,
        EXPECTED_SENDER_ID,
        apply_gate=apply_gate,
        private_authority_client=client,
    )

    assert result.status == "REJECTED"
    assert client.lookup_calls == [(W1GateBinding.from_gate(gate), "APPLY")]
    assert client.authorize_calls == [(W1GateBinding.from_gate(gate), "APPLY", scope)]
    assert session_factory.begin_calls == 0
    assert apply_calls == []
    assert queue.deleted == []


@pytest.mark.parametrize(
    "scope",
    [
        PrivateDeletionScope(kind="ACCOUNT", project_id=None),
        PrivateDeletionScope(
            kind="PROJECT",
            project_id=UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"),
        ),
    ],
    ids=["ACCOUNT", "PROJECT"],
)
def test_ack_relay_authority_resolves_stage_less_scope_without_guessing(scope) -> None:
    gate, _delivery = _gate_delivery("ABORT")
    ack = build_commit_gate_ack(
        gate,
        outcome="APPLIED",
        message_id=uuid4(),
        occurred_at=datetime(2026, 9, 27, tzinfo=UTC),
    )
    sessions = GateSessionFactory()
    client = FakeGateAuthorityClient(scope=scope)

    authority = commit_gate_runtime._resolve_ack_relay_authority(sessions, ack, client)

    assert authority.phase == "ACK_RELAY"
    assert authority.scope == scope
    assert client.lookup_calls == [(W1GateBinding.from_ack(ack), "ACK_RELAY")]
    assert client.authorize_calls == [(W1GateBinding.from_ack(ack), "ACK_RELAY", scope)]
    assert sessions.begin_calls == 0


def test_ack_relay_authority_uses_stage_as_binding_not_authority() -> None:
    gate, _delivery = _gate_delivery("ABORT")
    ack = build_commit_gate_ack(
        gate,
        outcome="APPLIED",
        message_id=uuid4(),
        occurred_at=datetime(2026, 9, 27, tzinfo=UTC),
    )
    stage = PersistedStage(
        command_id=ack.command_id,
        owner_ref=ack.authenticated_owner_ref,
        job_id=ack.job_id,
        execution_fence=str(ack.execution_fence),
        owner_deletion_epoch=str(ack.owner_deletion_epoch),
        result_digest=ack.result_digest,
        private_scope_kind="ACCOUNT",
        project_id=None,
    )
    sessions = GateSessionFactory(stage)
    client = FakeGateAuthorityClient(scope=PrivateDeletionScope(kind="ACCOUNT", project_id=None))

    authority = commit_gate_runtime._resolve_ack_relay_authority(sessions, ack, client)

    assert authority.authority_ref == "w1:test-gate-apply-authority"
    assert authority.phase == "ACK_RELAY"
    assert client.lookup_calls == []
    assert client.authorize_calls == [
        (
            W1GateBinding.from_ack(ack),
            "ACK_RELAY",
            PrivateDeletionScope(kind="ACCOUNT", project_id=None),
        )
    ]


def test_ack_relay_scope_lookup_rejects_changed_echo_without_candidate_probe() -> None:
    gate, _delivery = _gate_delivery("ABORT")
    ack = build_commit_gate_ack(
        gate,
        outcome="APPLIED",
        message_id=uuid4(),
        occurred_at=datetime(2026, 9, 27, tzinfo=UTC),
    )
    scope = PrivateDeletionScope(kind="ACCOUNT", project_id=None)

    class ChangedEchoClient(FakeGateAuthorityClient):
        def lookup_gate_scope(
            self,
            gate: W1GateBinding,
            phase: str,
        ) -> GateScopeLookupResponse:
            response = super().lookup_gate_scope(gate, phase)
            return response.model_copy(
                update={"operation_revision": response.operation_revision + 1}
            )

    sessions = GateSessionFactory()
    client = ChangedEchoClient(scope=scope)

    with pytest.raises(RuntimeError, match="scope lookup is invalid"):
        commit_gate_runtime._resolve_ack_relay_authority(sessions, ack, client)

    assert client.lookup_calls == [(W1GateBinding.from_ack(ack), "ACK_RELAY")]
    assert client.authorize_calls == []
