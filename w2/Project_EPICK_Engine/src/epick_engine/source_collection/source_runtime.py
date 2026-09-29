"""Fresh-lookup-gated execution for one initial W2 collection dispatch."""

from __future__ import annotations

import inspect
import json
import math
from collections.abc import Callable, Mapping, Sequence
from contextlib import AbstractContextManager
from dataclasses import dataclass, field
from datetime import datetime
from threading import Event, Thread
from typing import Literal, Protocol, cast
from uuid import UUID, uuid4

from sqlalchemy.orm import Session

from epick_engine.source_collection.collector import (
    StaticFetchRequest,
    StaticFetchResult,
    StaticResponseCandidate,
)
from epick_engine.source_collection.commit_gate_contracts import (
    CommitGateCommand,
    StagedResultProposal,
    parse_commit_gate_command,
)
from epick_engine.source_collection.commit_gate_runtime import (
    BeforeSend,
    ConsumeResult,
    GateApplier,
    QueueDelivery,
    RelayAuthorityClient,
    _resolve_gate_apply_authority,
    _sender_matches,
    _strict_json_object,
)
from epick_engine.source_collection.commit_gate_store import PrivateCommitGateAck
from epick_engine.source_collection.contracts import CollectionCommand, CollectionStage
from epick_engine.source_collection.parsing import StaticParseResult
from epick_engine.source_collection.persistence import (
    CollectionRuntimeAttempt,
    commit_collection_candidate,
    replay_staged_collection,
)
from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionScope
from epick_engine.source_collection.private_scope import (
    PrivateScopeRejected,
    PrivateTerminalCleanupAuthority,
    PrivateWriteAuthorityDecision,
    PrivateWriteScope,
)
from epick_engine.source_collection.service import (
    StaticCollectionExecution,
    StaticCollectionInput,
)
from epick_engine.source_collection.source_runtime_gate import apply_collection_commit_gate
from epick_engine.source_collection.source_runtime_input import RuntimeSourceConfigFile
from epick_engine.source_collection.source_runtime_store import (
    CollectionRuntimeConflict,
    _validated_dispatch,
    claim_collection_attempt,
    dispatch_digest,
    load_bound_collection_attempt,
    release_collection_claim,
    release_collection_reservation,
    renew_collection_claim,
    reserve_collection_attempt,
)
from epick_engine.source_collection.w1_lookup_client import W1LookupClientError
from epick_engine.source_collection.w1_private_authority_contracts import (
    CleanupKind,
    CurrentWriteScopeLookupResponse,
    GateAuthorityResponse,
    GatePhase,
    GateScopeLookupResponse,
    PrivateWriteAuthorityResponse,
    TerminalCleanupAuthorityResponse,
    W1GateBinding,
    W1PrivateBinding,
)
from epick_engine.source_collection.w1_transport import (
    LookupRequest,
    LookupResponse,
    W1Dispatch,
    W1LookupError,
    W1WireContractError,
    parse_w1_dispatch,
    validate_dispatch_lookup,
)


class RuntimeAuthorizationError(RuntimeError):
    """The W1 lookup or durable runtime state no longer authorizes execution."""


class _CurrentWriteSemanticallyDenied(RuntimeAuthorizationError):
    """A validated W1 HTTP 403 that may permit a separate cleanup request."""


type SessionFactory = Callable[[], Session]
type Clock = Callable[[], datetime]
type UUIDFactory = Callable[[], UUID]
type StaticParser = Callable[[StaticResponseCandidate], StaticParseResult]


class LookupClient(Protocol):
    def lookup_dispatch(self, dispatch: W1Dispatch) -> LookupResponse: ...


class RelayLookupClient(Protocol):
    def lookup(self, request: LookupRequest) -> LookupResponse: ...


class PrivateAuthorityClient(RelayAuthorityClient, Protocol):
    def lookup_current_scope(
        self,
        binding: W1PrivateBinding,
    ) -> CurrentWriteScopeLookupResponse: ...

    def authorize_write(
        self,
        binding: W1PrivateBinding,
        scope: PrivateDeletionScope,
    ) -> PrivateWriteAuthorityResponse: ...

    def lookup_gate_scope(
        self,
        gate: W1GateBinding,
        phase: GatePhase,
    ) -> GateScopeLookupResponse: ...

    def authorize_gate(
        self,
        gate: W1GateBinding,
        phase: GatePhase,
        scope: PrivateDeletionScope,
    ) -> GateAuthorityResponse: ...

    def authorize_terminal_cleanup(
        self,
        binding: W1PrivateBinding,
        scope: PrivateDeletionScope,
        cleanup_kind: CleanupKind,
    ) -> TerminalCleanupAuthorityResponse: ...


class CollectionInputProvider(Protocol):
    def load(self, command: CollectionCommand) -> StaticCollectionInput: ...


class CollectorFactory(Protocol):
    def __call__(self) -> object: ...


class PrivateWriteAuthorityProvider(Protocol):
    """Return one decision authenticated outside the W2 wire contract."""

    def __call__(
        self,
        subject: W1Dispatch | CommitGateCommand,
    ) -> PrivateWriteAuthorityDecision: ...


type SourceRuntimeMode = Literal["mixed", "collection", "gate"]


class GateSessionFactory(Protocol):
    def __call__(self) -> Session: ...

    def begin(self) -> AbstractContextManager[Session]: ...


class SourceRuntimeQueue(Protocol):
    def receive(self) -> Sequence[QueueDelivery]: ...

    def delete(self, receipt_handle: str) -> None: ...

    def extend_visibility(self, receipt_handle: str) -> None: ...


class _ReceiptVisibilityHeartbeat:
    def __init__(
        self,
        queue: SourceRuntimeQueue,
        receipt_handle: str,
        *,
        interval_seconds: float,
    ) -> None:
        if (
            not isinstance(interval_seconds, int | float)
            or isinstance(interval_seconds, bool)
            or not math.isfinite(interval_seconds)
            or interval_seconds <= 0
        ):
            raise ValueError("visibility heartbeat interval must be positive and finite")
        self._queue = queue
        self._receipt_handle = receipt_handle
        self._interval_seconds = float(interval_seconds)
        self._stop = Event()
        self._lost = Event()
        self._thread = Thread(
            target=self._run,
            name="w2-source-receipt-visibility",
            daemon=True,
        )

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> None:
        # Establish one full configured lease before starting durable work.
        self._queue.extend_visibility(self._receipt_handle)
        self._thread.start()

    def stop_and_join(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join()

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                self._queue.extend_visibility(self._receipt_handle)
            except Exception:
                self._lost.set()
                self._stop.set()
                return


def build_collection_relay_authorizer(
    lookup_client: RelayLookupClient,
) -> BeforeSend:
    """Authorize staged collection delivery from its immutable stored command only."""

    def authorize(kind: str, payload: Mapping[str, object]) -> None:
        if kind == "ACK":
            return
        if kind != "STAGED":
            raise RuntimeAuthorizationError("source runtime relay kind is invalid")
        try:
            encoded = json.dumps(
                dict(payload),
                ensure_ascii=False,
                allow_nan=False,
                separators=(",", ":"),
            )
            proposal = StagedResultProposal.model_validate_json(encoded, strict=True)
            command = proposal.command
            fence = command.execution_fence
            if (
                not fence.isascii()
                or not fence.isdecimal()
                or str(int(fence)) != fence
                or int(fence) <= 0
            ):
                raise ValueError
            request = LookupRequest(
                schema_version="w1.private.command-lookup.v1",
                command_id=command.command_id,
                execution_fence=int(fence),
                owner_deletion_epoch=command.owner_deletion_epoch,
            )
            response = lookup_client.lookup(request)
        except (
            TypeError,
            ValueError,
            UnicodeError,
            RecursionError,
            W1LookupClientError,
            W1LookupError,
            W1WireContractError,
        ) as exc:
            raise RuntimeAuthorizationError("source runtime relay lookup is invalid") from exc
        if (
            not isinstance(response, LookupResponse)
            or response.status != "AVAILABLE"
            or response.command_id != command.command_id
            or response.command != command
        ):
            raise RuntimeAuthorizationError("source runtime relay is unavailable")

    return authorize


@dataclass(frozen=True, slots=True)
class RuntimeStoreOperations:
    """Injectable durable-operation boundary used by the runtime and heartbeat."""

    load_attempt: Callable[..., CollectionRuntimeAttempt | None] = field(
        default_factory=lambda: load_bound_collection_attempt
    )
    reserve_attempt: Callable[..., CollectionRuntimeAttempt] = field(
        default_factory=lambda: reserve_collection_attempt
    )
    claim_attempt: Callable[..., CollectionRuntimeAttempt] = field(
        default_factory=lambda: claim_collection_attempt
    )
    renew_claim: Callable[..., CollectionRuntimeAttempt] = field(
        default_factory=lambda: renew_collection_claim
    )
    release_claim: Callable[..., CollectionRuntimeAttempt] = field(
        default_factory=lambda: release_collection_claim
    )
    release_reservation: Callable[..., CollectionRuntimeAttempt] = field(
        default_factory=lambda: release_collection_reservation
    )
    commit_candidate: Callable[..., StagedResultProposal] = field(
        default_factory=lambda: commit_collection_candidate
    )
    replay_candidate: Callable[..., StagedResultProposal] = field(
        default_factory=lambda: replay_staged_collection
    )


def _default_store_operations() -> RuntimeStoreOperations:
    """Bind the current operation seams when a handler invocation starts."""

    return RuntimeStoreOperations()


def _require_available(
    dispatch: W1Dispatch,
    lookup_client: LookupClient,
) -> LookupResponse:
    try:
        response = lookup_client.lookup_dispatch(dispatch)
        validate_dispatch_lookup(dispatch, response)
    except (
        W1LookupClientError,
        W1LookupError,
        W1WireContractError,
        ValueError,
        TypeError,
    ) as exc:
        raise RuntimeAuthorizationError("collection dispatch lookup is invalid") from exc
    if response.status != "AVAILABLE":
        raise RuntimeAuthorizationError("collection dispatch is unavailable")
    return response


def _derive_effective_command(
    dispatch: W1Dispatch,
    stage: CollectionStage,
    policy_revision: int,
) -> CollectionCommand:
    if (
        not isinstance(policy_revision, int)
        or isinstance(policy_revision, bool)
        or policy_revision <= 0
    ):
        raise RuntimeAuthorizationError("runtime stage requires effective policy revision")
    payload = dispatch.payload.model_dump(mode="python")
    payload.update({"resume_stage": stage, "policy_revision": policy_revision})
    try:
        return CollectionCommand.model_validate(payload)
    except ValueError as exc:
        raise RuntimeAuthorizationError("runtime effective command is invalid") from exc


@dataclass(slots=True)
class LookupGatedExecutionContext:
    """Keep W1's immutable dispatch separate from a local stage-effective command."""

    dispatch: W1Dispatch
    lookup_client: LookupClient
    _effective_command: CollectionCommand
    _claim_guard: Callable[[], None] | None = None

    @property
    def command(self) -> CollectionCommand:
        return self._effective_command

    def enter_stage(
        self,
        stage: CollectionStage,
        *,
        policy_revision: int | None = None,
    ) -> LookupResponse:
        if stage not in {
            CollectionStage.POLICY,
            CollectionStage.FETCH,
            CollectionStage.PARSE,
            CollectionStage.PERSIST,
        }:
            raise RuntimeAuthorizationError("runtime stage is not executable")
        response = _require_available(self.dispatch, self.lookup_client)
        if self._claim_guard is not None:
            self._claim_guard()
        if policy_revision is None:
            raise RuntimeAuthorizationError("runtime stage requires effective policy revision")
        self._effective_command = _derive_effective_command(
            self.dispatch,
            stage,
            policy_revision,
        )
        return response


class _FixedInputProvider:
    def __init__(self, input_value: StaticCollectionInput) -> None:
        self._input_value = input_value

    def load(self, command: CollectionCommand) -> StaticCollectionInput:
        return self._input_value


class _ClaimHeartbeat:
    def __init__(
        self,
        session_factory: SessionFactory,
        command_id: UUID,
        claim_token: UUID,
        *,
        lease_seconds: int,
        interval_seconds: float,
        renew_claim: Callable[..., CollectionRuntimeAttempt],
        authority_provider: Callable[[], PrivateWriteScope],
        expected_dispatch_digest: str,
    ) -> None:
        self._session_factory = session_factory
        self._command_id = command_id
        self._claim_token = claim_token
        self._lease_seconds = lease_seconds
        self._interval_seconds = interval_seconds
        self._renew_claim = renew_claim
        self._authority_provider = authority_provider
        self._expected_dispatch_digest = expected_dispatch_digest
        self._stop = Event()
        self._lost = Event()
        self._thread = Thread(
            target=self._run,
            name=f"w2-claim-{command_id}",
            daemon=True,
        )

    @property
    def lost(self) -> bool:
        return self._lost.is_set()

    def start(self) -> None:
        self._thread.start()

    def ensure_active(self) -> None:
        if self._lost.is_set():
            raise RuntimeAuthorizationError("collection runtime claim is no longer active")

    def stop_and_join(self) -> None:
        self._stop.set()
        if self._thread.is_alive():
            self._thread.join()

    def _run(self) -> None:
        while not self._stop.wait(self._interval_seconds):
            try:
                private_scope = self._authority_provider()
                self._renew_claim(
                    self._session_factory,
                    self._command_id,
                    claim_token=self._claim_token,
                    lease_seconds=self._lease_seconds,
                    expected_dispatch_digest=self._expected_dispatch_digest,
                    private_scope=private_scope,
                )
            except Exception:
                self._lost.set()
                self._stop.set()
                return


class _CancellationAwareCollector:
    """Adapt heartbeat loss to collectors that expose ``is_cancelled``."""

    def __init__(self, collector: object, heartbeat: _ClaimHeartbeat) -> None:
        self._collector = collector
        self._heartbeat = heartbeat

    def fetch(self, request: StaticFetchRequest) -> StaticFetchResult:
        fetch = getattr(self._collector, "fetch", None)
        if not callable(fetch):
            raise TypeError("collector must expose fetch")
        parameters = inspect.signature(fetch).parameters.values()
        supports_cancellation = any(
            parameter.name == "is_cancelled" or parameter.kind is inspect.Parameter.VAR_KEYWORD
            for parameter in parameters
        )
        if supports_cancellation:
            return cast(
                StaticFetchResult,
                fetch(request, is_cancelled=lambda: self._heartbeat.lost),
            )
        return cast(StaticFetchResult, fetch(request))

    def close(self) -> None:
        close = getattr(self._collector, "close", None)
        if callable(close):
            close()


def _validated_initial_dispatch(dispatch: W1Dispatch) -> W1Dispatch:
    try:
        validated = _validated_dispatch(dispatch)
    except CollectionRuntimeConflict as exc:
        raise RuntimeAuthorizationError("collection dispatch is invalid") from exc
    command = validated.payload
    if command.resume_stage is not CollectionStage.POLICY or command.policy_revision is not None:
        raise RuntimeAuthorizationError("runtime requires an initial POLICY dispatch")
    return validated


def _aware_now(clock: Clock) -> datetime:
    now = clock()
    if not isinstance(now, datetime) or now.tzinfo is None or now.utcoffset() is None:
        raise ValueError("clock must return a timezone-aware datetime")
    return now


def _load_attempt_state(
    session_factory: SessionFactory,
    dispatch: W1Dispatch,
    store_operations: RuntimeStoreOperations,
) -> _BoundAttemptState | None:
    with session_factory() as session, session.begin():
        attempt = store_operations.load_attempt(session, dispatch)
        if attempt is None:
            return None
        return _BoundAttemptState(
            state=attempt.state,
            effective_policy_revision=attempt.effective_policy_revision,
            attempt_id=attempt.attempt_id,
            private_scope=_stored_attempt_scope(attempt),
        )


@dataclass(frozen=True, slots=True)
class _BoundAttemptState:
    state: str
    effective_policy_revision: int
    attempt_id: UUID
    private_scope: PrivateDeletionScope


def _stored_attempt_scope(attempt: CollectionRuntimeAttempt) -> PrivateDeletionScope:
    if attempt.private_scope_kind == "ACCOUNT" and attempt.project_id is None:
        return PrivateDeletionScope(kind="ACCOUNT", project_id=None)
    if attempt.private_scope_kind == "PROJECT" and isinstance(attempt.project_id, UUID):
        return PrivateDeletionScope(kind="PROJECT", project_id=attempt.project_id)
    raise RuntimeAuthorizationError("collection runtime stored scope is unclassified")


def _response_matches_binding(
    response: (
        CurrentWriteScopeLookupResponse
        | PrivateWriteAuthorityResponse
        | TerminalCleanupAuthorityResponse
    ),
    binding: W1PrivateBinding,
) -> bool:
    return (
        response.owner_user_id == binding.owner_user_id
        and response.owner_deletion_epoch == binding.owner_deletion_epoch
        and response.command_id == binding.command_id
        and response.job_id == binding.job_id
        and response.execution_fence == binding.execution_fence
    )


def _scope_from_mapping(raw: object) -> PrivateDeletionScope:
    if raw == {"type": "ACCOUNT"}:
        return PrivateDeletionScope(kind="ACCOUNT", project_id=None)
    if not isinstance(raw, dict) or raw.keys() != {"type", "project_id"}:
        raise RuntimeAuthorizationError("W1 current scope lookup is invalid")
    project_ref = raw.get("project_id")
    if raw.get("type") != "PROJECT" or not isinstance(project_ref, str):
        raise RuntimeAuthorizationError("W1 current scope lookup is invalid")
    try:
        project_id = UUID(project_ref)
    except ValueError:
        raise RuntimeAuthorizationError("W1 current scope lookup is invalid") from None
    if project_ref != str(project_id):
        raise RuntimeAuthorizationError("W1 current scope lookup is invalid")
    return PrivateDeletionScope(kind="PROJECT", project_id=project_id)


def _assert_dispatch_project_ref(
    dispatch: W1Dispatch,
    scope: PrivateDeletionScope,
) -> None:
    project_ref = dispatch.payload.project_ref
    if scope.kind == "ACCOUNT":
        if project_ref is not None:
            raise RuntimeAuthorizationError("collection dispatch project binding does not match")
        return
    assert scope.project_id is not None
    if project_ref != str(scope.project_id):
        raise RuntimeAuthorizationError("collection dispatch project binding does not match")


def _lookup_initial_scope(
    dispatch: W1Dispatch,
    binding: W1PrivateBinding,
    authority_client: PrivateAuthorityClient,
) -> PrivateDeletionScope:
    try:
        response = authority_client.lookup_current_scope(binding)
    except (W1LookupClientError, W1LookupError, W1WireContractError, ValueError, TypeError) as exc:
        raise RuntimeAuthorizationError("W1 current scope lookup is invalid") from exc
    if not isinstance(response, CurrentWriteScopeLookupResponse) or not _response_matches_binding(
        response,
        binding,
    ):
        raise RuntimeAuthorizationError("W1 current scope lookup is invalid")
    scope = _scope_from_mapping(response.scope)
    _assert_dispatch_project_ref(dispatch, scope)
    return scope


def _authorize_write(
    binding: W1PrivateBinding,
    scope: PrivateDeletionScope,
    authority_client: PrivateAuthorityClient,
) -> PrivateWriteScope:
    try:
        response = authority_client.authorize_write(binding, scope)
    except W1LookupClientError as exc:
        if exc.code == "HTTP_403":
            raise _CurrentWriteSemanticallyDenied(
                "W1 private write authority was semantically denied"
            ) from exc
        raise RuntimeAuthorizationError("W1 private write authority is invalid") from exc
    except (W1LookupError, W1WireContractError, ValueError, TypeError) as exc:
        raise RuntimeAuthorizationError("W1 private write authority is invalid") from exc
    if (
        not isinstance(response, PrivateWriteAuthorityResponse)
        or not _response_matches_binding(response, binding)
        or response.scope != scope.to_mapping()
    ):
        raise RuntimeAuthorizationError("W1 private write authority is invalid")
    try:
        return PrivateWriteScope(
            PrivateWriteAuthorityDecision(
                owner_user_id=binding.owner_user_id,
                owner_deletion_epoch=binding.owner_deletion_epoch,
                scope=scope,
                authority_ref=response.authority_ref,
                command_id=binding.command_id,
                job_id=binding.job_id,
            )
        )
    except PrivateScopeRejected as exc:
        raise RuntimeAuthorizationError("W1 private write authority is invalid") from exc


def _authorize_terminal_cleanup(
    binding: W1PrivateBinding,
    scope: PrivateDeletionScope,
    cleanup_kind: CleanupKind,
    authority_client: PrivateAuthorityClient,
) -> PrivateTerminalCleanupAuthority:
    try:
        response = authority_client.authorize_terminal_cleanup(binding, scope, cleanup_kind)
    except (W1LookupClientError, W1LookupError, W1WireContractError, ValueError, TypeError) as exc:
        raise RuntimeAuthorizationError("W1 terminal cleanup authority is invalid") from exc
    if (
        not isinstance(response, TerminalCleanupAuthorityResponse)
        or not _response_matches_binding(response, binding)
        or response.scope != scope.to_mapping()
        or response.cleanup_kind != cleanup_kind
        or response.allowed_effect != "OWNER_LOCKED_PRIVATE_CLEANUP_ONLY"
    ):
        raise RuntimeAuthorizationError("W1 terminal cleanup authority is invalid")
    try:
        decision = PrivateTerminalCleanupAuthority.from_w1_response(response)
        decision.assert_bound_to(binding, cleanup_kind=cleanup_kind)
        return decision
    except PrivateScopeRejected as exc:
        raise RuntimeAuthorizationError("W1 terminal cleanup authority is invalid") from exc


def _release_after_clean_failure(
    session_factory: SessionFactory,
    command_id: UUID,
    claim_token: UUID,
    *,
    binding: W1PrivateBinding,
    scope: PrivateDeletionScope,
    authority_client: PrivateAuthorityClient,
    expected_dispatch_digest: str,
    store_operations: RuntimeStoreOperations,
) -> None:
    try:
        private_scope = _authorize_write(binding, scope, authority_client)
    except _CurrentWriteSemanticallyDenied:
        try:
            cleanup_authority = _authorize_terminal_cleanup(
                binding,
                scope,
                "CLAIM_RELEASE",
                authority_client,
            )
            store_operations.release_claim(
                session_factory,
                command_id,
                claim_token=claim_token,
                expected_dispatch_digest=expected_dispatch_digest,
                private_scope=cleanup_authority,
                private_binding=binding,
            )
        except Exception:
            # A denied or uncertain terminal cleanup remains recoverable by
            # DB-clock expiry; it must never become current write authority.
            return
        return
    except Exception:
        return
    try:
        store_operations.release_claim(
            session_factory,
            command_id,
            claim_token=claim_token,
            expected_dispatch_digest=expected_dispatch_digest,
            private_scope=private_scope,
        )
    except Exception:
        # Lost, expired, or unconfirmed claims are deliberately left for
        # DB-clock takeover; cleanup must not mask the execution failure.
        return


def _release_reservation_after_semantic_denial(
    session_factory: SessionFactory,
    command_id: UUID,
    *,
    binding: W1PrivateBinding,
    scope: PrivateDeletionScope,
    authority_client: PrivateAuthorityClient,
    expected_dispatch_digest: str,
    store_operations: RuntimeStoreOperations,
) -> None:
    try:
        cleanup_authority = _authorize_terminal_cleanup(
            binding,
            scope,
            "RESERVATION_RELEASE",
            authority_client,
        )
        store_operations.release_reservation(
            session_factory,
            command_id,
            expected_dispatch_digest=expected_dispatch_digest,
            private_scope=cleanup_authority,
            private_binding=binding,
        )
    except Exception:
        # A changed/absent/claimed row or uncertain cleanup stays intact for
        # investigation; terminal authority must never permit another mutation.
        return


def handle_collection_dispatch(
    dispatch: W1Dispatch,
    *,
    session_factory: SessionFactory,
    lookup_client: LookupClient,
    input_provider: CollectionInputProvider,
    collector_factory: CollectorFactory,
    rendered_collector_factory: Callable[[StaticCollectionInput], object] | None = None,
    parser: StaticParser,
    runtime_config: RuntimeSourceConfigFile,
    clock: Clock,
    uuid_factory: UUIDFactory,
    store_operations: RuntimeStoreOperations | None = None,
    private_authority_client: PrivateAuthorityClient | None = None,
) -> StagedResultProposal:
    """Execute an initial POLICY dispatch through durable PERSIST, never DELIVER."""

    operations = _default_store_operations() if store_operations is None else store_operations
    validated = _validated_initial_dispatch(dispatch)
    command = validated.payload
    _require_available(validated, lookup_client)

    state = _load_attempt_state(session_factory, validated, operations)
    if state is not None and state.state == "INVALIDATED":
        raise RuntimeAuthorizationError("collection runtime attempt is invalidated")
    if private_authority_client is None:
        raise RuntimeAuthorizationError("protected W1 private authority client is required")
    try:
        binding = W1PrivateBinding.from_collection(command)
    except W1WireContractError as exc:
        raise RuntimeAuthorizationError("collection private binding is invalid") from exc
    scope = (
        _lookup_initial_scope(validated, binding, private_authority_client)
        if state is None
        else state.private_scope
    )
    _assert_dispatch_project_ref(validated, scope)
    expected_dispatch_digest = dispatch_digest(validated)

    if state is not None and state.state in {"PERSISTED", "FINALIZED"}:
        private_scope = _authorize_write(binding, scope, private_authority_client)
        return operations.replay_candidate(
            session_factory,
            validated,
            private_scope=private_scope,
        )

    input_value = input_provider.load(validated.payload)
    if not isinstance(input_value, StaticCollectionInput):
        raise ValueError("collection input provider returned an invalid input")
    approved = runtime_config.sources.get(validated.payload.source_id)
    if approved is None:
        approved = runtime_config.approved_config_for_input(
            source_id=validated.payload.source_id,
            company_id=input_value.company_id,
            url=input_value.source_url,
            source_type=input_value.source_type,
        )
    if approved is None or approved.policy_revision != input_value.policy_revision:
        raise RuntimeAuthorizationError("approved collection policy revision is stale")
    if state is not None and state.effective_policy_revision != input_value.policy_revision:
        raise RuntimeAuthorizationError("collection runtime policy revision is stale")

    private_scope = _authorize_write(binding, scope, private_authority_client)
    with session_factory() as session, session.begin():
        attempt = operations.reserve_attempt(
            session,
            validated,
            input_value.policy_revision,
            _aware_now(clock),
            uuid_factory,
            private_scope=private_scope,
        )
        attempt_id = attempt.attempt_id
        effective_policy_revision = attempt.effective_policy_revision

    if effective_policy_revision != input_value.policy_revision:
        raise RuntimeAuthorizationError("collection runtime policy revision is stale")

    claim_token = uuid_factory()
    try:
        private_scope = _authorize_write(binding, scope, private_authority_client)
    except _CurrentWriteSemanticallyDenied:
        _release_reservation_after_semantic_denial(
            session_factory,
            validated.payload.command_id,
            binding=binding,
            scope=scope,
            authority_client=private_authority_client,
            expected_dispatch_digest=expected_dispatch_digest,
            store_operations=operations,
        )
        raise
    claimed = operations.claim_attempt(
        session_factory,
        validated.payload.command_id,
        claim_token=claim_token,
        lease_seconds=runtime_config.claim_lease_seconds,
        expected_dispatch_digest=expected_dispatch_digest,
        private_scope=private_scope,
    )
    if claimed.attempt_id != attempt_id:
        _release_after_clean_failure(
            session_factory,
            validated.payload.command_id,
            claim_token,
            binding=binding,
            scope=scope,
            authority_client=private_authority_client,
            expected_dispatch_digest=expected_dispatch_digest,
            store_operations=operations,
        )
        raise CollectionRuntimeConflict("collection runtime attempt identity conflict")

    heartbeat: _ClaimHeartbeat | None = None
    heartbeat_started = False
    execution: StaticCollectionExecution | None = None
    collector: _CancellationAwareCollector | None = None
    close_attempted = False
    cleanup_confirmed = True
    try:
        heartbeat = _ClaimHeartbeat(
            session_factory,
            validated.payload.command_id,
            claim_token,
            lease_seconds=runtime_config.claim_lease_seconds,
            interval_seconds=runtime_config.heartbeat_interval_seconds,
            renew_claim=operations.renew_claim,
            authority_provider=lambda: _authorize_write(
                binding,
                scope,
                private_authority_client,
            ),
            expected_dispatch_digest=expected_dispatch_digest,
        )
        heartbeat.start()
        heartbeat_started = True
        if input_value.collection_strategy == "rendered":
            if rendered_collector_factory is None:
                raise RuntimeAuthorizationError("approved rendered browser runtime is unavailable")
            selected_collector = rendered_collector_factory(input_value)
        elif input_value.collection_strategy == "static":
            selected_collector = collector_factory()
        else:
            raise RuntimeAuthorizationError("collection strategy is invalid")
        collector = _CancellationAwareCollector(selected_collector, heartbeat)
        cleanup_confirmed = False
        execution = StaticCollectionExecution(
            claimed.attempt_id,
            _FixedInputProvider(input_value),
            collector,
            parser,
            clock=clock,
            uuid_factory=uuid_factory,
        )
        context = LookupGatedExecutionContext(
            dispatch=validated,
            lookup_client=lookup_client,
            _effective_command=_derive_effective_command(
                validated,
                CollectionStage.POLICY,
                effective_policy_revision,
            ),
            _claim_guard=heartbeat.ensure_active,
        )
        prepared = execution.run_once(context)
        heartbeat.ensure_active()
        context.enter_stage(
            CollectionStage.PERSIST,
            policy_revision=effective_policy_revision,
        )
        heartbeat.ensure_active()
        private_scope = _authorize_write(binding, scope, private_authority_client)
        operations.renew_claim(
            session_factory,
            validated.payload.command_id,
            claim_token=claim_token,
            lease_seconds=runtime_config.claim_lease_seconds,
            expected_dispatch_digest=expected_dispatch_digest,
            private_scope=private_scope,
        )
        heartbeat.stop_and_join()
        heartbeat.ensure_active()

        close_attempted = True
        execution.close()
        cleanup_confirmed = True
        private_scope = _authorize_write(binding, scope, private_authority_client)
        return operations.commit_candidate(
            session_factory,
            validated,
            context.command,
            prepared,
            claim_token=claim_token,
            staged_message_id=uuid_factory(),
            occurred_at=_aware_now(clock),
            private_scope=private_scope,
        )
    except Exception:
        if heartbeat is not None and heartbeat_started:
            heartbeat.stop_and_join()
        if execution is not None and not close_attempted:
            close_attempted = True
            try:
                execution.close()
            except Exception:
                cleanup_confirmed = False
            else:
                cleanup_confirmed = True
        elif collector is not None and not close_attempted:
            close_attempted = True
            try:
                collector.close()
            except Exception:
                cleanup_confirmed = False
            else:
                cleanup_confirmed = True
        if cleanup_confirmed and (heartbeat is None or not heartbeat.lost):
            _release_after_clean_failure(
                session_factory,
                validated.payload.command_id,
                claim_token,
                binding=binding,
                scope=scope,
                authority_client=private_authority_client,
                expected_dispatch_digest=expected_dispatch_digest,
                store_operations=operations,
            )
        raise


def consume_source_runtime_once(
    session_factory: GateSessionFactory,
    queue: SourceRuntimeQueue,
    expected_sender_id: str,
    *,
    mode: SourceRuntimeMode,
    collection_handler: Callable[..., object],
    gate_applier: GateApplier = apply_collection_commit_gate,
    authority_provider: PrivateWriteAuthorityProvider | None = None,
    private_authority_client: PrivateAuthorityClient | None = None,
    clock: Clock,
    message_id_factory: Callable[[], UUID] = uuid4,
    visibility_heartbeat_seconds: float | None = None,
) -> ConsumeResult:
    """Route one authenticated source-runtime delivery to exactly one handler."""

    try:
        deliveries = queue.receive()
    except Exception:
        return ConsumeResult(status="RECEIVE_FAILED")
    if not deliveries:
        return ConsumeResult(status="EMPTY")
    delivery = deliveries[0]
    if not _sender_matches(delivery.sender_id, expected_sender_id):
        return ConsumeResult(status="UNAUTHENTICATED")
    try:
        payload = _strict_json_object(delivery.body)
    except (TypeError, ValueError, UnicodeError, RecursionError):
        return ConsumeResult(status="MALFORMED")

    message_type = payload.get("message_type")
    is_collection = message_type in {
        "w1.private.w2.collection-command.v1",
        "w1.private.w2.direct-source-registration.v1",
    }
    is_gate = message_type == "w1.private.w2.commit-gate.v1"
    if mode not in {"mixed", "collection", "gate"}:
        return ConsumeResult(status="REJECTED")
    if (is_collection and mode == "gate") or (is_gate and mode == "collection"):
        return ConsumeResult(status="REJECTED")
    if not is_collection and not is_gate:
        return ConsumeResult(status="MALFORMED")

    try:
        if is_collection:
            dispatch = parse_w1_dispatch(payload)
            if private_authority_client is None:
                raise RuntimeAuthorizationError("protected W1 private authority client is required")
            extend_visibility = getattr(queue, "extend_visibility", None)
            if visibility_heartbeat_seconds is None or extend_visibility is None:
                raise RuntimeError("source runtime visibility heartbeat is required")
            heartbeat = _ReceiptVisibilityHeartbeat(
                queue,
                delivery.receipt_handle,
                interval_seconds=visibility_heartbeat_seconds,
            )
            heartbeat.start()
            try:
                collection_handler(
                    dispatch,
                    private_authority_client=private_authority_client,
                )
            finally:
                heartbeat.stop_and_join()
            if heartbeat.lost:
                raise RuntimeError("source runtime receipt visibility is no longer active")
        else:
            gate = parse_commit_gate_command(payload)
            if private_authority_client is None:
                raise RuntimeAuthorizationError("protected W1 private authority client is required")
            private_gate_authority = _resolve_gate_apply_authority(
                session_factory,
                gate,
                private_authority_client,
            )
            with session_factory.begin() as session:
                ack = gate_applier(
                    session,
                    gate,
                    ack_message_id=message_id_factory(),
                    occurred_at=clock(),
                    private_gate_authority=private_gate_authority,
                )
                persisted_ack = session.get(
                    PrivateCommitGateAck,
                    ack.message_id,
                    populate_existing=True,
                )
                if persisted_ack is None:
                    raise RuntimeError("persisted private commit-gate ACK unavailable")
                persisted_ack.delivered_at = None
    except Exception:
        return ConsumeResult(status="REJECTED")

    try:
        queue.delete(delivery.receipt_handle)
    except Exception:
        return ConsumeResult(status="DELETE_FAILED")
    return ConsumeResult(status="APPLIED")
