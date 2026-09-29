"""Synchronous W2 adapter for one W1-authorized collection attempt.

This module deliberately owns no job, retry, queue, or slot state.  W1 grants
the private execution permit and records every stage; W2 only runs an already
authorized execution and commits its prepared output through the W1 transaction
authority lock.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from inspect import Parameter, signature
from typing import Literal, Protocol, Self, cast
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from epick_engine.source_collection.contracts import (
    CollectionCommand,
    CollectionResult,
    CollectionStage,
    CoreFailureDecisionAction,
    Failure,
    SourceEvent,
    SourceEventType,
    UserRetryAction,
)
from epick_engine.source_collection.persistence import (
    ExecutionAuthorityLocker,
    OutboxEvent,
    PreparedCollectionCommit,
    apply_private_deletion,
    commit_prepared_collection,
    replay_committed_collection,
)


class WorkerExecutionError(RuntimeError):
    """Base error for a W1-controlled collection worker execution."""


class WorkerAuthorizationError(WorkerExecutionError):
    """Raised when a W1 permit or stage transition is not safe to execute."""


class WorkerContractViolation(WorkerExecutionError):
    """Raised when a runner or committer returns data outside the W2 contract."""


PrivateDeletionOutcome = Literal["APPLIED", "DUPLICATE", "STALE"]

_PRIVATE_DELETION_COMMAND_KEYS = frozenset(
    {
        "schema_version",
        "deletion_id",
        "owner_user_id",
        "deletion_epoch",
        "attempt_ids",
        "request_deduplication_ids",
        "private_reference_keys",
    }
)
_PRIVATE_DELETION_ACK_KEYS = frozenset(
    {"schema_version", "deletion_id", "owner_user_id", "deletion_epoch", "outcome"}
)
_PRIVATE_DELETION_OUTCOMES = frozenset({"APPLIED", "DUPLICATE", "STALE"})
_MAX_PRIVATE_DELETION_EPOCH = 9_223_372_036_854_775_807


def _require_private_deletion_keys(
    raw: Mapping[str, object], *, expected: frozenset[str], payload_name: str
) -> None:
    if frozenset(raw) != expected:
        raise WorkerContractViolation(f"{payload_name} fields are invalid")


def _require_private_deletion_uuid(raw: Mapping[str, object], field: str) -> UUID:
    value = raw[field]
    if not isinstance(value, str):
        raise WorkerContractViolation(f"{field} must be a UUID string")
    try:
        parsed = UUID(value)
    except ValueError as error:
        raise WorkerContractViolation(f"{field} must be a UUID string") from error
    if str(parsed) != value:
        raise WorkerContractViolation(f"{field} must use canonical UUID text")
    return parsed


def _require_private_deletion_epoch(raw: Mapping[str, object]) -> int:
    value = raw["deletion_epoch"]
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise WorkerContractViolation("deletion_epoch must be a positive integer")
    return value


def _require_private_deletion_command_epoch(raw: Mapping[str, object]) -> int:
    value = _require_private_deletion_epoch(raw)
    if value > _MAX_PRIVATE_DELETION_EPOCH:
        raise WorkerContractViolation("deletion_epoch exceeds the signed 64-bit maximum")
    return value


def _require_private_deletion_uuid_set(raw: Mapping[str, object], field: str) -> frozenset[UUID]:
    value = raw[field]
    if not isinstance(value, list):
        raise WorkerContractViolation(f"{field} must be a UUID array")
    parsed = tuple(_require_private_deletion_uuid({field: item}, field) for item in value)
    if len(parsed) != len(frozenset(parsed)):
        raise WorkerContractViolation(f"{field} must contain unique UUIDs")
    return frozenset(parsed)


def _require_private_reference_keys(raw: Mapping[str, object]) -> frozenset[str]:
    value = raw["private_reference_keys"]
    if not isinstance(value, list) or any(not isinstance(item, str) or not item for item in value):
        raise WorkerContractViolation("private_reference_keys must contain non-empty strings")
    if len(value) != len(frozenset(value)):
        raise WorkerContractViolation("private_reference_keys must contain unique strings")
    return frozenset(value)


@dataclass(frozen=True, slots=True)
class PrivateDeletionCommand:
    """Validated private-only deletion payload after the W1 transport boundary."""

    deletion_id: UUID
    owner_user_id: UUID
    deletion_epoch: int
    attempt_ids: frozenset[UUID]
    request_deduplication_ids: frozenset[UUID]
    private_reference_keys: frozenset[str]

    def __post_init__(self) -> None:
        if not isinstance(self.deletion_id, UUID):
            raise WorkerContractViolation("deletion_id must be a UUID")
        if not isinstance(self.owner_user_id, UUID):
            raise WorkerContractViolation("owner_user_id must be a UUID")
        if (
            isinstance(self.deletion_epoch, bool)
            or not isinstance(self.deletion_epoch, int)
            or self.deletion_epoch < 1
            or self.deletion_epoch > _MAX_PRIVATE_DELETION_EPOCH
        ):
            raise WorkerContractViolation("deletion_epoch must be a positive signed 64-bit integer")
        if not isinstance(self.attempt_ids, frozenset) or any(
            not isinstance(attempt_id, UUID) for attempt_id in self.attempt_ids
        ):
            raise WorkerContractViolation("attempt_ids must be a frozenset of UUIDs")
        if not isinstance(self.request_deduplication_ids, frozenset) or any(
            not isinstance(request_id, UUID) for request_id in self.request_deduplication_ids
        ):
            raise WorkerContractViolation("request_deduplication_ids must be a frozenset of UUIDs")
        if not isinstance(self.private_reference_keys, frozenset) or any(
            not isinstance(reference, str) or not reference
            for reference in self.private_reference_keys
        ):
            raise WorkerContractViolation(
                "private_reference_keys must be a frozenset of non-empty strings"
            )

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> Self:
        _require_private_deletion_keys(
            raw,
            expected=_PRIVATE_DELETION_COMMAND_KEYS,
            payload_name="private deletion command",
        )
        if raw["schema_version"] != "w2.private-deletion.v1":
            raise WorkerContractViolation("private deletion schema version is invalid")
        return cls(
            deletion_id=_require_private_deletion_uuid(raw, "deletion_id"),
            owner_user_id=_require_private_deletion_uuid(raw, "owner_user_id"),
            deletion_epoch=_require_private_deletion_command_epoch(raw),
            attempt_ids=_require_private_deletion_uuid_set(raw, "attempt_ids"),
            request_deduplication_ids=_require_private_deletion_uuid_set(
                raw, "request_deduplication_ids"
            ),
            private_reference_keys=_require_private_reference_keys(raw),
        )


@dataclass(frozen=True, slots=True)
class PrivateDeletionAcknowledgement:
    """Validated private ACK returned for one private deletion command."""

    deletion_id: UUID
    owner_user_id: UUID
    deletion_epoch: int
    outcome: PrivateDeletionOutcome

    def __post_init__(self) -> None:
        if not isinstance(self.deletion_id, UUID):
            raise WorkerContractViolation("deletion_id must be a UUID")
        if not isinstance(self.owner_user_id, UUID):
            raise WorkerContractViolation("owner_user_id must be a UUID")
        if (
            isinstance(self.deletion_epoch, bool)
            or not isinstance(self.deletion_epoch, int)
            or self.deletion_epoch < 1
            or self.deletion_epoch > _MAX_PRIVATE_DELETION_EPOCH
        ):
            raise WorkerContractViolation("deletion_epoch must be a positive signed 64-bit integer")
        if not isinstance(self.outcome, str) or self.outcome not in _PRIVATE_DELETION_OUTCOMES:
            raise WorkerContractViolation("private deletion ACK outcome is invalid")

    @classmethod
    def from_mapping(cls, raw: Mapping[str, object]) -> Self:
        _require_private_deletion_keys(
            raw,
            expected=_PRIVATE_DELETION_ACK_KEYS,
            payload_name="private deletion acknowledgement",
        )
        if raw["schema_version"] != "w2.private-deletion-ack.v1":
            raise WorkerContractViolation("private deletion ACK schema version is invalid")
        outcome = raw["outcome"]
        if not isinstance(outcome, str) or outcome not in _PRIVATE_DELETION_OUTCOMES:
            raise WorkerContractViolation("private deletion ACK outcome is invalid")
        return cls(
            deletion_id=_require_private_deletion_uuid(raw, "deletion_id"),
            owner_user_id=_require_private_deletion_uuid(raw, "owner_user_id"),
            deletion_epoch=_require_private_deletion_command_epoch(raw),
            outcome=cast(PrivateDeletionOutcome, outcome),
        )


class PrivateDeletionSideEffects(Protocol):
    """W1-owned private purge and acknowledgement boundary."""

    def purge_private_references(
        self,
        *,
        owner_user_id: UUID,
        private_reference_keys: frozenset[str],
    ) -> None:
        """Purge W1-private references after the W2 transaction commits."""

    def acknowledge(self, *, deletion_id: UUID, deletion_epoch: int) -> None:
        """Acknowledge a successfully purged deletion delivery."""


def process_private_deletion(
    *,
    session_factory: sessionmaker[Session],
    command: PrivateDeletionCommand,
    side_effects: PrivateDeletionSideEffects,
) -> PrivateDeletionAcknowledgement:
    """Commit one private deletion before invoking its W1-owned side effects."""

    with session_factory() as session:
        with session.begin():
            outcome = apply_private_deletion(session, command)

    acknowledgement = PrivateDeletionAcknowledgement(
        deletion_id=command.deletion_id,
        owner_user_id=command.owner_user_id,
        deletion_epoch=command.deletion_epoch,
        outcome=outcome,
    )
    if outcome == "STALE":
        return acknowledgement

    side_effects.purge_private_references(
        owner_user_id=command.owner_user_id,
        private_reference_keys=command.private_reference_keys,
    )
    side_effects.acknowledge(
        deletion_id=command.deletion_id,
        deletion_epoch=command.deletion_epoch,
    )
    return acknowledgement


class OutboxDeliveryError(RuntimeError):
    """Raised when a public source event cannot be safely delivered."""


@dataclass(frozen=True, slots=True)
class WorkerExecutionPermit:
    """Immutable W1 dispatch authorization for one private collection attempt."""

    attempt_id: UUID
    command: CollectionCommand
    checkpoint_ref: str | None
    all_core_decisions_received: bool
    slot_acquired: bool
    retry_not_before: datetime | None


class W1WorkerControl(Protocol):
    """Private W1 authority boundary; W2 never persists its state locally."""

    def authorize_execution(self, command: CollectionCommand) -> WorkerExecutionPermit:
        """Return the dispatch permit when this command may execute now."""

    def authorize_and_record_stage(
        self,
        permit: WorkerExecutionPermit,
        stage: CollectionStage,
        *,
        policy_revision: int | None = None,
    ) -> WorkerExecutionPermit:
        """Atomically verify and record one stage before W2 crosses that boundary."""

    def finalize_execution(
        self,
        permit: WorkerExecutionPermit,
        result: CollectionResult,
        *,
        resources_closed: bool = True,
    ) -> None:
        """Idempotently accept a result, transition W1 state, and release its slot."""

    def record_execution_stopped(
        self,
        permit: WorkerExecutionPermit,
        *,
        resources_closed: bool,
        error_code: str,
        checkpoint_ref: str | None = None,
    ) -> None:
        """Record an execution that stopped before a durable W2 commit completed.

        ``checkpoint_ref`` is optional so pre-checkpoint stop reporters remain
        compatible while a running W2 execution can hand its latest W1 checkpoint
        back before W1 releases the execution slot.
        """


class CollectionExecution(Protocol):
    """One execution whose work begins only after ``run_once`` enters its stage gate."""

    def run_once(self, context: WorkerExecutionContext) -> PreparedCollectionCommit:
        """Perform all fetch/persist/external work after the required stage gate."""

    def close(self) -> None:
        """Release every runner-owned resource before any W2 commit is attempted."""


class CollectionExecutionFactory(Protocol):
    """Build an execution without fetching, persisting, or external side effects."""

    def __call__(self, permit: WorkerExecutionPermit) -> CollectionExecution:
        """Create one execution; ``run_once`` performs work after its stage gate."""


class PreparedCollectionCommitter(Protocol):
    """Commit prepared data under the W1 same-transaction authority lock."""

    def __call__(
        self,
        session_factory: Callable[[], Session],
        *,
        command: CollectionCommand,
        prepared: PreparedCollectionCommit,
        lock_authority: ExecutionAuthorityLocker,
    ) -> CollectionResult:
        """Persist one prepared attempt or return its idempotent replay result."""


class CommittedCollectionReplayer(Protocol):
    """Load a finalized result under the W1 same-transaction authority lock."""

    def __call__(
        self,
        session_factory: Callable[[], Session],
        *,
        command: CollectionCommand,
        attempt_id: UUID,
        lock_authority: ExecutionAuthorityLocker,
    ) -> CollectionResult | None:
        """Return the committed result for an attempt, when one exists."""


Clock = Callable[[], datetime]

_STAGE_ORDER = {
    CollectionStage.POLICY: 0,
    CollectionStage.FETCH: 1,
    CollectionStage.PARSE: 2,
    CollectionStage.PERSIST: 3,
    CollectionStage.DELIVER: 4,
}


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _is_aware(value: datetime) -> bool:
    return value.tzinfo is not None and value.utcoffset() is not None


def _command_identity(command: CollectionCommand) -> tuple[object, ...]:
    """Return every command field W1 is not allowed to alter during a stage gate."""

    return (
        command.schema_version,
        command.command_id,
        command.job_id,
        command.authenticated_owner_ref,
        command.project_ref,
        command.company_id,
        command.source_id,
        command.input_version,
        command.execution_fence,
        command.purpose_ref,
        command.core_source_decision,
        command.resume_stage,
        command.owner_deletion_epoch,
    )


def _require_positive_policy_revision(value: int) -> None:
    if isinstance(value, bool) or value < 1:
        raise WorkerAuthorizationError("policy_revision must be a positive integer")


class WorkerExecutionContext:
    """Runner context that gates each irreversible collection stage through W1."""

    def __init__(
        self,
        *,
        control: W1WorkerControl,
        permit: WorkerExecutionPermit,
        clock: Clock = _utc_now,
    ) -> None:
        self._control = control
        self._clock = clock
        self._permit = permit
        self._last_stage: CollectionStage | None = None
        self._validate_eligible_permit(permit)

    @property
    def permit(self) -> WorkerExecutionPermit:
        """Return the most recent effective W1 permit."""

        return self._permit

    @property
    def command(self) -> CollectionCommand:
        """Return the effective immutable command from the current permit."""

        return self._permit.command

    def enter_stage(
        self,
        stage: CollectionStage,
        *,
        policy_revision: int | None = None,
    ) -> WorkerExecutionPermit:
        """Authorize and record ``stage`` before the runner performs that work."""

        if not isinstance(stage, CollectionStage):
            raise WorkerAuthorizationError("stage must be a CollectionStage")
        if policy_revision is not None:
            _require_positive_policy_revision(policy_revision)
        if self._last_stage is None:
            if stage is not self._permit.command.resume_stage:
                raise WorkerAuthorizationError(
                    "first recorded stage must equal command resume_stage"
                )
        elif _STAGE_ORDER[stage] < _STAGE_ORDER[self._last_stage]:
            raise WorkerAuthorizationError("collection stages cannot move backwards")

        next_permit = self._control.authorize_and_record_stage(
            self._permit,
            stage,
            policy_revision=policy_revision,
        )
        self._validate_stage_permit(
            previous=self._permit,
            next_permit=next_permit,
            stage=stage,
            requested_policy_revision=policy_revision,
        )
        self._permit = next_permit
        self._last_stage = stage
        return next_permit

    def _validate_eligible_permit(self, permit: WorkerExecutionPermit) -> None:
        if not isinstance(permit, WorkerExecutionPermit):
            raise WorkerAuthorizationError("W1 did not return a WorkerExecutionPermit")
        if not isinstance(permit.attempt_id, UUID):
            raise WorkerAuthorizationError("W1 permit attempt_id must be a UUID")
        if not isinstance(permit.command, CollectionCommand):
            raise WorkerAuthorizationError("W1 permit command must be a CollectionCommand")
        if permit.all_core_decisions_received is not True:
            raise WorkerAuthorizationError("all core decisions must be received before execution")
        if permit.slot_acquired is not True:
            raise WorkerAuthorizationError("W1 slot must be acquired before execution")
        if permit.retry_not_before is not None:
            if not _is_aware(permit.retry_not_before):
                raise WorkerAuthorizationError("permit retry_not_before must be timezone-aware")
            now = self._clock()
            if not _is_aware(now):
                raise WorkerAuthorizationError("worker clock must return a timezone-aware datetime")
            if permit.retry_not_before > now:
                raise WorkerAuthorizationError("permit retry_not_before has not elapsed")

    def _validate_stage_permit(
        self,
        *,
        previous: WorkerExecutionPermit,
        next_permit: WorkerExecutionPermit,
        stage: CollectionStage,
        requested_policy_revision: int | None,
    ) -> None:
        self._validate_eligible_permit(next_permit)
        if next_permit.attempt_id != previous.attempt_id:
            raise WorkerAuthorizationError("W1 stage permit changed attempt_id")
        if _command_identity(next_permit.command) != _command_identity(previous.command):
            raise WorkerAuthorizationError("W1 stage permit changed immutable command identity")

        previous_revision = previous.command.policy_revision
        next_revision = next_permit.command.policy_revision
        if previous_revision is None:
            if next_revision is None:
                if stage is not CollectionStage.POLICY or requested_policy_revision is not None:
                    raise WorkerAuthorizationError("policy revision is required before fetch")
            elif (
                stage not in {CollectionStage.POLICY, CollectionStage.FETCH}
                or requested_policy_revision != next_revision
            ):
                raise WorkerAuthorizationError(
                    "policy revision may be established only while completing policy "
                    "or entering fetch"
                )
        elif next_revision != previous_revision:
            raise WorkerAuthorizationError("policy revision changed after it was established")

        if requested_policy_revision is not None and next_revision != requested_policy_revision:
            raise WorkerAuthorizationError(
                "W1 stage permit did not retain requested policy revision"
            )


class SourceCollectionWorker:
    """Execute exactly one W1-authorized W2 collection attempt synchronously."""

    def __init__(
        self,
        *,
        control: W1WorkerControl,
        execution_factory: CollectionExecutionFactory,
        session_factory: Callable[[], Session],
        lock_authority: ExecutionAuthorityLocker,
        committer: PreparedCollectionCommitter = commit_prepared_collection,
        replayer: CommittedCollectionReplayer = replay_committed_collection,
        clock: Clock = _utc_now,
    ) -> None:
        self._control = control
        self._execution_factory = execution_factory
        self._session_factory = session_factory
        self._lock_authority = lock_authority
        self._committer = committer
        self._replayer = replayer
        self._clock = clock

    def handle(self, payload: object) -> CollectionResult:
        """Validate, run, close, persist, deliver, and finalize one command without retrying."""

        command = CollectionCommand.model_validate(payload)
        permit_to_stop: WorkerExecutionPermit | None = None
        resources_closed = False
        commit_completed = False
        stop_error_code = "worker_authorization_failed"

        try:
            issued_permit = self._control.authorize_execution(command)
            if isinstance(issued_permit, WorkerExecutionPermit):
                permit_to_stop = issued_permit
            self._validate_initial_permit(command, issued_permit)

            stop_error_code = "committed_result_replay_failed"
            replayed_result = self._replayer(
                self._session_factory,
                command=issued_permit.command,
                attempt_id=issued_permit.attempt_id,
                lock_authority=self._lock_authority,
            )
            if replayed_result is not None:
                resources_closed = True
                commit_completed = True
                validation_command = issued_permit.command
                if (
                    validation_command.resume_stage is CollectionStage.POLICY
                    and validation_command.policy_revision is None
                ):
                    validation_command = validation_command.model_copy(
                        update={"policy_revision": replayed_result.policy_revision}
                    )
                self._validate_result(replayed_result, command=validation_command)
                context = WorkerExecutionContext(
                    control=self._control,
                    permit=issued_permit,
                    clock=self._clock,
                )
                resume_stage = context.command.resume_stage
                context.enter_stage(
                    resume_stage,
                    policy_revision=replayed_result.policy_revision,
                )
                permit_to_stop = context.permit
                if resume_stage is not CollectionStage.DELIVER:
                    context.enter_stage(
                        CollectionStage.DELIVER,
                        policy_revision=replayed_result.policy_revision,
                    )
                    permit_to_stop = context.permit
                self._validate_result(replayed_result, command=context.command)
                self._control.finalize_execution(
                    context.permit,
                    replayed_result,
                    resources_closed=True,
                )
                return replayed_result

            if issued_permit.command.resume_stage is CollectionStage.DELIVER:
                raise WorkerAuthorizationError(
                    "deliver-stage replay requires the W1 committed-result adapter"
                )

            context = WorkerExecutionContext(
                control=self._control,
                permit=issued_permit,
                clock=self._clock,
            )
            if context.command.resume_stage is not CollectionStage.POLICY:
                context.enter_stage(
                    context.command.resume_stage,
                    policy_revision=context.command.policy_revision,
                )
                permit_to_stop = context.permit

            stop_error_code = "execution_factory_failed"
            execution = self._execution_factory(context.permit)
            try:
                stop_error_code = "execution_failed"
                prepared = execution.run_once(context)
            finally:
                permit_to_stop = context.permit
                try:
                    execution.close()
                except Exception:
                    stop_error_code = "execution_close_failed"
                    raise
                resources_closed = True

            stop_error_code = "prepared_validation_failed"
            self._validate_prepared(prepared, permit=context.permit)

            stop_error_code = "persist_authorization_failed"
            context.enter_stage(
                CollectionStage.PERSIST,
                policy_revision=context.command.policy_revision,
            )
            permit_to_stop = context.permit

            stop_error_code = "commit_failed"
            result = self._committer(
                self._session_factory,
                command=context.command,
                prepared=prepared,
                lock_authority=self._lock_authority,
            )
            commit_completed = True
            self._validate_result(result, command=context.command)

            context.enter_stage(
                CollectionStage.DELIVER,
                policy_revision=context.command.policy_revision,
            )
            self._control.finalize_execution(
                context.permit,
                result,
                resources_closed=True,
            )
            return result
        except Exception:
            if permit_to_stop is not None and not commit_completed:
                self._record_stopped(
                    permit_to_stop,
                    resources_closed=resources_closed,
                    error_code=stop_error_code,
                )
            raise

    def _validate_initial_permit(
        self,
        command: CollectionCommand,
        permit: WorkerExecutionPermit,
    ) -> None:
        context = WorkerExecutionContext(
            control=self._control,
            permit=permit,
            clock=self._clock,
        )
        effective = context.command
        if _command_identity(effective) != _command_identity(command):
            raise WorkerAuthorizationError("W1 execution permit changed immutable command identity")
        if effective.policy_revision != command.policy_revision:
            raise WorkerAuthorizationError(
                "W1 execution permit changed the command policy revision"
            )

    def _validate_prepared(
        self,
        prepared: PreparedCollectionCommit,
        *,
        permit: WorkerExecutionPermit,
    ) -> None:
        if not isinstance(prepared, PreparedCollectionCommit):
            raise WorkerContractViolation("runner did not return PreparedCollectionCommit")
        if prepared.attempt_id != permit.attempt_id:
            raise WorkerContractViolation("prepared attempt_id does not match the W1 permit")
        self._validate_result(prepared.result, command=permit.command)

    def _validate_result(
        self,
        result: CollectionResult,
        *,
        command: CollectionCommand,
    ) -> None:
        if not isinstance(result, CollectionResult):
            raise WorkerContractViolation("collection result is not a CollectionResult")
        if (
            result.command_id != command.command_id
            or result.job_id != command.job_id
            or result.input_version != command.input_version
            or result.source_id != command.source_id
            or result.policy_revision != command.policy_revision
        ):
            raise WorkerContractViolation("collection result does not match the effective command")

        action_codes: set[str] = set()
        core_actions: list[CoreFailureDecisionAction] = []
        user_retry_actions: list[UserRetryAction] = []
        for failure in result.failures:
            if not isinstance(failure, Failure):
                raise WorkerContractViolation("collection result failure is invalid")
            if failure.source_id != command.source_id:
                raise WorkerContractViolation("failure source_id does not match the command")
            if failure.core_decision_revision != command.core_source_decision.decision_revision:
                raise WorkerContractViolation(
                    "failure core_decision_revision does not match the command"
                )

        for action in result.required_actions:
            code = getattr(action, "code", None)
            context = getattr(action, "context", None)
            if not isinstance(code, str) or context is None:
                raise WorkerContractViolation("required action is invalid")
            if code in action_codes:
                raise WorkerContractViolation("duplicate required action code")
            action_codes.add(code)
            if getattr(context, "source_id", None) != command.source_id:
                raise WorkerContractViolation(
                    "required action source_id does not match the command"
                )
            if code == "core_failure_decision":
                if not isinstance(action, CoreFailureDecisionAction):
                    raise WorkerContractViolation("core_failure_decision action is invalid")
                core_actions.append(action)
            elif code == "user_retry":
                if not isinstance(action, UserRetryAction):
                    raise WorkerContractViolation("user_retry action is invalid")
                user_retry_actions.append(action)

        has_rate_limit = any(failure.code == "RATE_LIMITED" for failure in result.failures)
        if has_rate_limit and (
            result.checkpoint_ref is None
            or result.resume_stage is None
            or len(user_retry_actions) != 1
        ):
            raise WorkerContractViolation(
                "RATE_LIMITED result requires checkpoint_ref, resume_stage, and user_retry"
            )
        if has_rate_limit:
            retry_context = user_retry_actions[0].context
            if (
                retry_context.resume_stage != result.resume_stage
                or retry_context.retry_not_before != result.retry_not_before
            ):
                raise WorkerContractViolation(
                    "RATE_LIMITED result and user_retry action must use the same resume values"
                )

        if command.core_source_decision.is_core:
            if result.failures and (
                len(core_actions) != 1
                or core_actions[0].context.core_decision_revision
                != command.core_source_decision.decision_revision
            ):
                raise WorkerContractViolation(
                    "core source failure requires a matching core_failure_decision action"
                )
        elif core_actions:
            raise WorkerContractViolation(
                "non-core source result must not emit core_failure_decision"
            )

    def _record_stopped(
        self,
        permit: WorkerExecutionPermit,
        *,
        resources_closed: bool,
        error_code: str,
    ) -> None:
        """Best-effort W1 stop report; it must not hide the originating failure."""

        try:
            reporter = self._control.record_execution_stopped
            if permit.checkpoint_ref is not None and _accepts_checkpoint_ref(reporter):
                reporter(
                    permit,
                    resources_closed=resources_closed,
                    error_code=error_code,
                    checkpoint_ref=permit.checkpoint_ref,
                )
            else:
                reporter(
                    permit,
                    resources_closed=resources_closed,
                    error_code=error_code,
                )
        except Exception:
            pass


def _accepts_checkpoint_ref(reporter: Callable[..., object]) -> bool:
    """Return whether a stop reporter accepts the optional checkpoint keyword.

    Inspecting before invocation keeps a legacy reporter's ``TypeError`` from
    being mistaken for a signature mismatch and invoked a second time.
    """

    try:
        parameters = signature(reporter).parameters.values()
    except (TypeError, ValueError):
        return False
    return any(
        parameter.name == "checkpoint_ref" or parameter.kind is Parameter.VAR_KEYWORD
        for parameter in parameters
    )


_DELIVERABLE_EVENT_TYPES = frozenset(
    {
        SourceEventType.VERSION_AVAILABLE,
        SourceEventType.OBSERVATION_CHANGED,
        SourceEventType.RESTRICTION_CHANGED,
    }
)


@dataclass(frozen=True, slots=True)
class SourceEventAcknowledgement:
    """Exact public-event acknowledgement returned by the W3 publisher."""

    event_id: UUID


class PublicSourceEventPublisher(Protocol):
    """Publish a public SourceEvent to W3 without accepting W1 result data."""

    def publish(self, source_event: SourceEvent) -> SourceEventAcknowledgement:
        """Publish one immutable public event snapshot and return its exact acknowledgement."""


class OutboxDeliveryStore(Protocol):
    """Persistence boundary for one-at-a-time public outbox delivery."""

    def load_pending(self, *, limit: int) -> tuple[SourceEvent, ...]:
        """Return the next pending immutable public event snapshots."""

    def load_for_replay(self, *, event_id: UUID) -> SourceEvent | None:
        """Return one immutable event snapshot for an internal replay."""

    def mark_delivered(self, *, event_id: UUID) -> None:
        """Record an acknowledged delivery in its own database transaction."""


def _require_delivery_limit(limit: int) -> None:
    if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
        raise OutboxDeliveryError("outbox delivery limit must be a positive integer")


def _validate_deliverable_event(source_event: SourceEvent) -> None:
    if not isinstance(source_event, SourceEvent):
        raise OutboxDeliveryError("outbox event is invalid")
    if source_event.event_type not in _DELIVERABLE_EVENT_TYPES:
        raise OutboxDeliveryError("outbox event is not deliverable")


def _source_event_from_outbox_row(row: OutboxEvent) -> SourceEvent:
    """Rebuild and validate only the immutable public contract snapshot in one row."""

    if row.event_type not in {event_type.value for event_type in _DELIVERABLE_EVENT_TYPES}:
        raise OutboxDeliveryError("outbox event is not deliverable")
    try:
        source_event = SourceEvent.model_validate(
            {
                "event_id": row.event_id,
                "event_type": row.event_type,
                "schema_version": row.schema_version,
                "aggregate_id": row.aggregate_id,
                "aggregate_revision": row.aggregate_revision,
                # PostgreSQL returns timestamptz in the session timezone. Keep
                # the public replay bytes stable across UTC/KST DB sessions.
                "occurred_at": row.occurred_at.astimezone(UTC),
                "payload": row.payload,
            }
        )
    except Exception:
        raise OutboxDeliveryError("outbox event is invalid") from None
    _validate_deliverable_event(source_event)
    return source_event


class SqlAlchemyOutboxDeliveryStore:
    """Small SQLAlchemy adapter that never mixes publishing with a source write transaction."""

    def __init__(self, *, session_factory: Callable[[], Session]) -> None:
        self._session_factory = session_factory

    def load_pending(self, *, limit: int) -> tuple[SourceEvent, ...]:
        _require_delivery_limit(limit)
        session = self._session_factory()
        try:
            rows = tuple(
                session.scalars(
                    select(OutboxEvent)
                    .where(OutboxEvent.delivery_state == "pending")
                    .order_by(OutboxEvent.occurred_at.asc(), OutboxEvent.event_id.asc())
                    .limit(limit)
                )
            )
            return tuple(_source_event_from_outbox_row(row) for row in rows)
        except OutboxDeliveryError:
            raise
        except Exception:
            raise OutboxDeliveryError("outbox events could not be loaded") from None
        finally:
            session.close()

    def load_for_replay(self, *, event_id: UUID) -> SourceEvent | None:
        if not isinstance(event_id, UUID):
            raise OutboxDeliveryError("outbox event identifier is invalid")
        session = self._session_factory()
        try:
            row = session.get(OutboxEvent, event_id)
            if row is None:
                return None
            if row.delivery_state not in {"pending", "delivered"}:
                raise OutboxDeliveryError("outbox event is not replayable")
            return _source_event_from_outbox_row(row)
        except OutboxDeliveryError:
            raise
        except Exception:
            raise OutboxDeliveryError("outbox event could not be loaded") from None
        finally:
            session.close()

    def mark_delivered(self, *, event_id: UUID) -> None:
        if not isinstance(event_id, UUID):
            raise OutboxDeliveryError("outbox event identifier is invalid")
        session = self._session_factory()
        try:
            row = session.get(OutboxEvent, event_id)
            if row is None:
                raise OutboxDeliveryError("outbox event could not be acknowledged")
            if row.delivery_state == "delivered":
                return
            if row.delivery_state != "pending":
                raise OutboxDeliveryError("outbox event could not be acknowledged")
            row.delivery_state = "delivered"
            session.commit()
        except OutboxDeliveryError:
            try:
                session.rollback()
            except Exception:
                pass
            raise
        except Exception:
            try:
                session.rollback()
            except Exception:
                pass
            raise OutboxDeliveryError("outbox acknowledgement could not be recorded") from None
        finally:
            session.close()


class SourceOutboxDeliveryWorker:
    """Deliver public outbox snapshots at least once after W2 has committed them."""

    def __init__(
        self,
        *,
        publisher: PublicSourceEventPublisher,
        store: OutboxDeliveryStore,
    ) -> None:
        self._publisher = publisher
        self._store = store

    def deliver_pending(self, *, limit: int) -> int:
        """Publish pending snapshots and mark each one only after its exact ACK."""

        _require_delivery_limit(limit)
        source_events = self._store.load_pending(limit=limit)
        for source_event in source_events:
            self._publish_with_exact_ack(source_event)
            self._store.mark_delivered(event_id=source_event.event_id)
        return len(source_events)

    def replay(self, *, event_id: UUID) -> None:
        """Republish an existing immutable snapshot without changing its delivery state."""

        source_event = self._store.load_for_replay(event_id=event_id)
        if source_event is None:
            raise OutboxDeliveryError("outbox event was not found")
        self._publish_with_exact_ack(source_event)

    def _publish_with_exact_ack(self, source_event: SourceEvent) -> None:
        _validate_deliverable_event(source_event)
        try:
            acknowledgement = self._publisher.publish(source_event)
        except Exception:
            raise OutboxDeliveryError("public source event publication failed") from None
        if (
            not isinstance(acknowledgement, SourceEventAcknowledgement)
            or not isinstance(acknowledgement.event_id, UUID)
            or acknowledgement.event_id != source_event.event_id
        ):
            raise OutboxDeliveryError("public source event acknowledgement is invalid")
