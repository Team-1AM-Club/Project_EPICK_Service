from __future__ import annotations

from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest
from pydantic import ValidationError

from epick_engine.source_collection.contracts import (
    CollectionCommand,
    CollectionResult,
    CollectionStage,
    CompletionKind,
    CoreFailureDecisionAction,
    CoreFailureDecisionContext,
    CoreSourceDecision,
    Failure,
    SourceReference,
    UserRetryAction,
    UserRetryContext,
)
from epick_engine.source_collection.persistence import PreparedCollectionCommit, StaleExecution
from epick_engine.source_collection.worker import (
    SourceCollectionWorker,
    WorkerAuthorizationError,
    WorkerContractViolation,
    WorkerExecutionContext,
    WorkerExecutionPermit,
)

NOW = datetime(2026, 9, 10, 12, tzinfo=UTC)


class _Control:
    def __init__(
        self,
        permit: WorkerExecutionPermit,
        events: list[str],
        *,
        stage_callback: Callable[
            [WorkerExecutionPermit, CollectionStage, int | None], WorkerExecutionPermit
        ]
        | None = None,
        finalize_error: Exception | None = None,
    ) -> None:
        self.permit = permit
        self.events = events
        self.stage_callback = stage_callback
        self.finalize_error = finalize_error
        self.stopped: list[tuple[WorkerExecutionPermit, bool, str]] = []
        self.stopped_checkpoints: list[str | None] = []
        self.finalized: list[tuple[WorkerExecutionPermit, CollectionResult]] = []

    def authorize_execution(self, command: CollectionCommand) -> WorkerExecutionPermit:
        self.events.append("authorize")
        return self.permit

    def authorize_and_record_stage(
        self,
        permit: WorkerExecutionPermit,
        stage: CollectionStage,
        *,
        policy_revision: int | None = None,
    ) -> WorkerExecutionPermit:
        self.events.append(f"stage:{stage.value}")
        if self.stage_callback is None:
            return permit
        return self.stage_callback(permit, stage, policy_revision)

    def finalize_execution(
        self,
        permit: WorkerExecutionPermit,
        result: CollectionResult,
        *,
        resources_closed: bool = True,
    ) -> None:
        self.events.append("finalize")
        self.finalized.append((permit, result))
        if self.finalize_error is not None:
            raise self.finalize_error

    def record_execution_stopped(
        self,
        permit: WorkerExecutionPermit,
        *,
        resources_closed: bool,
        error_code: str,
        checkpoint_ref: str | None = None,
    ) -> None:
        self.events.append("stopped")
        self.stopped.append((permit, resources_closed, error_code))
        self.stopped_checkpoints.append(checkpoint_ref)


class _LegacyStoppedControl(_Control):
    """W1 reporter shape before optional checkpoint handoff was added."""

    def record_execution_stopped(
        self,
        permit: WorkerExecutionPermit,
        *,
        resources_closed: bool,
        error_code: str,
    ) -> None:
        super().record_execution_stopped(
            permit,
            resources_closed=resources_closed,
            error_code=error_code,
        )


class _Execution:
    def __init__(
        self,
        events: list[str],
        run: Callable[[WorkerExecutionContext], PreparedCollectionCommit],
        *,
        close_error: Exception | None = None,
    ) -> None:
        self.events = events
        self.run = run
        self.close_error = close_error
        self.run_count = 0
        self.close_count = 0

    def run_once(self, context: WorkerExecutionContext) -> PreparedCollectionCommit:
        self.events.append("run")
        self.run_count += 1
        return self.run(context)

    def close(self) -> None:
        self.events.append("close")
        self.close_count += 1
        if self.close_error is not None:
            raise self.close_error


class _Factory:
    def __init__(self, events: list[str], execution: _Execution) -> None:
        self.events = events
        self.execution = execution
        self.permits: list[WorkerExecutionPermit] = []

    def __call__(self, permit: WorkerExecutionPermit) -> _Execution:
        self.events.append("factory")
        self.permits.append(permit)
        return self.execution


class _Committer:
    def __init__(self, events: list[str], *, error: Exception | None = None) -> None:
        self.events = events
        self.error = error
        self.commands: list[CollectionCommand] = []
        self.prepared: list[PreparedCollectionCommit] = []

    def __call__(
        self,
        session_factory: object,
        *,
        command: CollectionCommand,
        prepared: PreparedCollectionCommit,
        lock_authority: object,
    ) -> CollectionResult:
        self.events.append("commit")
        self.commands.append(command)
        self.prepared.append(prepared)
        if self.error is not None:
            raise self.error
        return prepared.result


def _command(
    *,
    resume_stage: CollectionStage = CollectionStage.FETCH,
    policy_revision: int | None = 1,
    is_core: bool = False,
) -> CollectionCommand:
    return CollectionCommand(
        schema_version="w2.collection.v1",
        command_id=uuid4(),
        job_id=uuid4(),
        authenticated_owner_ref=uuid4(),
        project_ref="project",
        company_id=uuid4(),
        source_id=uuid4(),
        input_version=1,
        execution_fence="fence-1",
        purpose_ref=uuid4(),
        core_source_decision=CoreSourceDecision(
            is_core=is_core,
            decided_by="owner",
            rationale="required source",
            decision_revision=1,
            analysis_input_version=1,
        ),
        resume_stage=resume_stage,
        policy_revision=policy_revision,
        owner_deletion_epoch=0,
    )


def _permit(
    command: CollectionCommand,
    *,
    all_core_decisions_received: bool = True,
    slot_acquired: bool = True,
    retry_not_before: datetime | None = None,
) -> WorkerExecutionPermit:
    return WorkerExecutionPermit(
        attempt_id=uuid4(),
        command=command,
        checkpoint_ref=None,
        all_core_decisions_received=all_core_decisions_received,
        slot_acquired=slot_acquired,
        retry_not_before=retry_not_before,
    )


def _complete_result(command: CollectionCommand) -> CollectionResult:
    return CollectionResult(
        schema_version="w2.collection.v1",
        command_id=command.command_id,
        job_id=command.job_id,
        input_version=command.input_version,
        result_version=1,
        successful_source_refs=[
            SourceReference(
                source_id=command.source_id,
                source_version_id=uuid4(),
                extraction_revision_id=uuid4(),
            )
        ],
        failures=[],
        completion_kind=CompletionKind.COMPLETE,
        resume_stage=None,
        checkpoint_ref=None,
        retry_not_before=None,
        message_ko="수집을 완료했습니다.",
        required_actions=[],
        source_id=command.source_id,
        policy_revision=command.policy_revision,
    )


def _failure_result(
    command: CollectionCommand,
    *,
    code: str = "COLLECTION_FAILED",
    stage: CollectionStage = CollectionStage.FETCH,
    checkpoint_ref: str | None = None,
    resume_stage: CollectionStage | None = None,
    retry_not_before: datetime | None = NOW + timedelta(minutes=5),
    required_actions: list[object] | None = None,
) -> CollectionResult:
    return CollectionResult(
        schema_version="w2.collection.v1",
        command_id=command.command_id,
        job_id=command.job_id,
        input_version=command.input_version,
        result_version=1,
        successful_source_refs=[],
        failures=[
            Failure(
                source_id=command.source_id,
                stage=stage,
                code=code,
                missing_sections=[],
                impact="수집을 계속할 수 없습니다.",
                core_decision_revision=command.core_source_decision.decision_revision,
            )
        ],
        completion_kind=CompletionKind.NONE,
        resume_stage=resume_stage,
        checkpoint_ref=checkpoint_ref,
        retry_not_before=retry_not_before,
        message_ko="수집에 실패했습니다.",
        required_actions=required_actions or [],
        source_id=command.source_id,
        policy_revision=command.policy_revision,
    )


def _prepared(
    permit: WorkerExecutionPermit,
    result: CollectionResult,
) -> PreparedCollectionCommit:
    return PreparedCollectionCommit(
        attempt_id=permit.attempt_id,
        result=result,
        finalized_at=NOW,
    )


def _parse_and_prepare(context: WorkerExecutionContext) -> PreparedCollectionCommit:
    context.enter_stage(CollectionStage.PARSE, policy_revision=context.command.policy_revision)
    return _prepared(context.permit, _complete_result(context.command))


def _no_replay(
    _session_factory: object,
    *,
    command: CollectionCommand,
    attempt_id: object,
    lock_authority: object,
) -> None:
    del _session_factory, command, attempt_id, lock_authority


def _worker(
    control: _Control,
    factory: _Factory,
    committer: _Committer,
    *,
    replayer: Callable[..., CollectionResult | None] = _no_replay,
) -> SourceCollectionWorker:
    return SourceCollectionWorker(
        control=control,
        execution_factory=factory,
        session_factory=lambda: None,
        lock_authority=lambda *_args, **_kwargs: None,
        committer=committer,
        replayer=replayer,
        clock=lambda: NOW,
    )


def _standard_worker(
    command: CollectionCommand,
    *,
    stage_callback: Callable[
        [WorkerExecutionPermit, CollectionStage, int | None], WorkerExecutionPermit
    ]
    | None = None,
    run: Callable[[WorkerExecutionContext], PreparedCollectionCommit] = _parse_and_prepare,
    close_error: Exception | None = None,
    commit_error: Exception | None = None,
    finalize_error: Exception | None = None,
    replayer: Callable[..., CollectionResult | None] = _no_replay,
) -> tuple[SourceCollectionWorker, _Control, _Execution, _Factory, _Committer, list[str]]:
    events: list[str] = []
    control = _Control(
        _permit(command),
        events,
        stage_callback=stage_callback,
        finalize_error=finalize_error,
    )
    execution = _Execution(events, run, close_error=close_error)
    factory = _Factory(events, execution)
    committer = _Committer(events, error=commit_error)
    return (
        _worker(control, factory, committer, replayer=replayer),
        control,
        execution,
        factory,
        committer,
        events,
    )


def test_replayer_preflight_runs_before_factory_when_no_result_exists() -> None:
    command = _command()
    events: list[str] = []
    control = _Control(_permit(command), events)
    execution = _Execution(events, _parse_and_prepare)
    factory = _Factory(events, execution)
    committer = _Committer(events)

    def replayer(
        _session_factory: object,
        *,
        command: CollectionCommand,
        attempt_id: object,
        lock_authority: object,
    ) -> None:
        del _session_factory, command, attempt_id, lock_authority
        events.append("replay")

    worker = _worker(control, factory, committer, replayer=replayer)

    worker.handle(command.model_dump())

    assert events.index("replay") < events.index("factory")


def test_committed_result_skips_execution_and_committer_then_finalizes() -> None:
    command = _command(resume_stage=CollectionStage.DELIVER)
    events: list[str] = []
    permit = _permit(command)
    control = _Control(permit, events)
    execution = _Execution(events, _parse_and_prepare)
    factory = _Factory(events, execution)
    committer = _Committer(events)
    committed = _complete_result(command)
    replay_calls: list[tuple[CollectionCommand, object]] = []

    def replayer(
        _session_factory: object,
        *,
        command: CollectionCommand,
        attempt_id: object,
        lock_authority: object,
    ) -> CollectionResult:
        del _session_factory, lock_authority
        events.append("replay")
        replay_calls.append((command, attempt_id))
        return committed

    worker = _worker(control, factory, committer, replayer=replayer)

    result = worker.handle(command.model_dump())

    assert result == committed
    assert replay_calls == [(permit.command, permit.attempt_id)]
    assert events == ["authorize", "replay", "stage:deliver", "finalize"]
    assert factory.permits == []
    assert execution.run_count == 0
    assert execution.close_count == 0
    assert committer.prepared == []
    assert control.finalized == [(permit, committed)]
    assert control.stopped == []


def test_replayed_finalize_failure_does_not_record_stopped() -> None:
    command = _command(resume_stage=CollectionStage.DELIVER)
    events: list[str] = []
    permit = _permit(command)
    control = _Control(
        permit,
        events,
        finalize_error=RuntimeError("finalize failed"),
    )
    execution = _Execution(events, _parse_and_prepare)
    factory = _Factory(events, execution)
    committer = _Committer(events)
    committed = _complete_result(command)

    def replayer(
        _session_factory: object,
        *,
        command: CollectionCommand,
        attempt_id: object,
        lock_authority: object,
    ) -> CollectionResult:
        del _session_factory, command, attempt_id, lock_authority
        events.append("replay")
        return committed

    worker = _worker(control, factory, committer, replayer=replayer)

    with pytest.raises(RuntimeError, match="finalize failed"):
        worker.handle(command.model_dump())

    assert factory.permits == []
    assert execution.run_count == 0
    assert committer.prepared == []
    assert events == ["authorize", "replay", "stage:deliver", "finalize"]
    assert control.finalized == [(permit, committed)]
    assert control.stopped == []


def test_replayed_stage_permit_failure_skips_execution_without_stopping() -> None:
    command = _command(resume_stage=CollectionStage.DELIVER)
    events: list[str] = []
    permit = _permit(command)
    committed = _complete_result(command)

    def reject_stage_permit(
        current_permit: WorkerExecutionPermit,
        stage: CollectionStage,
        policy_revision: int | None,
    ) -> WorkerExecutionPermit:
        assert stage is CollectionStage.DELIVER
        assert policy_revision == committed.policy_revision
        return replace(current_permit, attempt_id=uuid4())

    control = _Control(permit, events, stage_callback=reject_stage_permit)
    execution = _Execution(events, _parse_and_prepare)
    factory = _Factory(events, execution)
    committer = _Committer(events)

    def replayer(
        _session_factory: object,
        *,
        command: CollectionCommand,
        attempt_id: object,
        lock_authority: object,
    ) -> CollectionResult:
        del _session_factory, command, attempt_id, lock_authority
        events.append("replay")
        return committed

    worker = _worker(control, factory, committer, replayer=replayer)

    with pytest.raises(WorkerAuthorizationError, match="changed attempt_id"):
        worker.handle(command.model_dump())

    assert events == ["authorize", "replay", "stage:deliver"]
    assert factory.permits == []
    assert execution.run_count == 0
    assert committer.prepared == []
    assert control.finalized == []
    assert control.stopped == []


def test_invalid_replayed_result_is_rejected_before_stage_gate_or_execution() -> None:
    command = _command()
    events: list[str] = []
    permit = _permit(command)
    valid_result = _failure_result(command)
    invalid_result = valid_result.model_copy(
        update={
            "failures": [
                valid_result.failures[0].model_copy(
                    update={
                        "core_decision_revision": (
                            command.core_source_decision.decision_revision + 1
                        )
                    }
                )
            ]
        }
    )
    stage_calls: list[CollectionStage] = []

    def count_stage_call(
        current_permit: WorkerExecutionPermit,
        stage: CollectionStage,
        policy_revision: int | None,
    ) -> WorkerExecutionPermit:
        del policy_revision
        stage_calls.append(stage)
        return current_permit

    control = _Control(permit, events, stage_callback=count_stage_call)
    execution = _Execution(events, _parse_and_prepare)
    factory = _Factory(events, execution)
    committer = _Committer(events)

    def replayer(
        _session_factory: object,
        *,
        command: CollectionCommand,
        attempt_id: object,
        lock_authority: object,
    ) -> CollectionResult:
        del _session_factory, command, attempt_id, lock_authority
        events.append("replay")
        return invalid_result

    worker = _worker(control, factory, committer, replayer=replayer)

    with pytest.raises(
        WorkerContractViolation,
        match="failure core_decision_revision",
    ):
        worker.handle(command.model_dump())

    assert events == ["authorize", "replay"]
    assert stage_calls == []
    assert factory.permits == []
    assert execution.run_count == 0
    assert committer.prepared == []
    assert control.finalized == []
    assert control.stopped == []


def test_fetch_resume_enters_stage_before_factory_and_finalizes_once() -> None:
    command = _command()
    worker, control, execution, _, committer, events = _standard_worker(command)

    result = worker.handle(command.model_dump())

    assert result.command_id == command.command_id
    assert result.policy_revision == command.policy_revision
    assert events == [
        "authorize",
        "stage:fetch",
        "factory",
        "run",
        "stage:parse",
        "close",
        "stage:persist",
        "commit",
        "stage:deliver",
        "finalize",
    ]
    assert execution.run_count == 1
    assert execution.close_count == 1
    assert len(committer.prepared) == 1
    assert len(control.finalized) == 1


@pytest.mark.parametrize(
    ("field", "value"),
    [("all_core_decisions_received", False), ("slot_acquired", False)],
)
def test_ineligible_permit_stops_before_runner_or_persistence(field: str, value: bool) -> None:
    command = _command()
    events: list[str] = []
    permit = replace(_permit(command), **{field: value})
    control = _Control(permit, events)
    execution = _Execution(events, _parse_and_prepare)
    factory = _Factory(events, execution)
    committer = _Committer(events)

    with pytest.raises(WorkerAuthorizationError):
        _worker(control, factory, committer).handle(command.model_dump())

    assert execution.run_count == 0
    assert "stage:fetch" not in events
    assert committer.prepared == []
    assert control.stopped == [(permit, False, "worker_authorization_failed")]


def test_future_aware_retry_not_before_stops_before_runner() -> None:
    command = _command()
    events: list[str] = []
    permit = _permit(command, retry_not_before=NOW + timedelta(seconds=1))
    control = _Control(permit, events)
    execution = _Execution(events, _parse_and_prepare)
    factory = _Factory(events, execution)
    committer = _Committer(events)

    with pytest.raises(WorkerAuthorizationError, match="has not elapsed"):
        _worker(control, factory, committer).handle(command.model_dump())

    assert execution.run_count == 0
    assert committer.prepared == []
    assert control.stopped == [(permit, False, "worker_authorization_failed")]


def test_naive_retry_not_before_stops_before_runner() -> None:
    command = _command()
    events: list[str] = []
    permit = _permit(command, retry_not_before=NOW.replace(tzinfo=None))
    control = _Control(permit, events)
    execution = _Execution(events, _parse_and_prepare)
    factory = _Factory(events, execution)
    committer = _Committer(events)

    with pytest.raises(WorkerAuthorizationError, match="timezone-aware"):
        _worker(control, factory, committer).handle(command.model_dump())

    assert execution.run_count == 0
    assert committer.prepared == []
    assert control.stopped == [(permit, False, "worker_authorization_failed")]


def _changed_attempt(permit: WorkerExecutionPermit) -> WorkerExecutionPermit:
    return replace(permit, attempt_id=uuid4())


def _changed_owner(permit: WorkerExecutionPermit) -> WorkerExecutionPermit:
    return replace(
        permit,
        command=permit.command.model_copy(update={"authenticated_owner_ref": uuid4()}),
    )


def _changed_fence(permit: WorkerExecutionPermit) -> WorkerExecutionPermit:
    return replace(
        permit,
        command=permit.command.model_copy(update={"execution_fence": "another-fence"}),
    )


def _changed_input(permit: WorkerExecutionPermit) -> WorkerExecutionPermit:
    decision = permit.command.core_source_decision.model_copy(update={"analysis_input_version": 2})
    return replace(
        permit,
        command=permit.command.model_copy(
            update={"input_version": 2, "core_source_decision": decision}
        ),
    )


def _changed_core_decision(permit: WorkerExecutionPermit) -> WorkerExecutionPermit:
    decision = permit.command.core_source_decision.model_copy(update={"decision_revision": 2})
    return replace(
        permit,
        command=permit.command.model_copy(update={"core_source_decision": decision}),
    )


def _changed_purpose(permit: WorkerExecutionPermit) -> WorkerExecutionPermit:
    return replace(
        permit,
        command=permit.command.model_copy(update={"purpose_ref": uuid4()}),
    )


@pytest.mark.parametrize(
    "mutator",
    [
        _changed_attempt,
        _changed_owner,
        _changed_fence,
        _changed_input,
        _changed_core_decision,
        _changed_purpose,
    ],
    ids=["attempt", "owner", "fence", "input", "core-decision", "purpose"],
)
def test_stage_permit_identity_changes_fail_before_runner(
    mutator: Callable[[WorkerExecutionPermit], WorkerExecutionPermit],
) -> None:
    command = _command()

    def change_identity(
        permit: WorkerExecutionPermit,
        stage: CollectionStage,
        policy_revision: int | None,
    ) -> WorkerExecutionPermit:
        return mutator(permit)

    worker, control, execution, _, committer, _ = _standard_worker(
        command,
        stage_callback=change_identity,
    )

    with pytest.raises(WorkerAuthorizationError):
        worker.handle(command.model_dump())

    assert execution.run_count == 0
    assert committer.prepared == []
    assert len(control.stopped) == 1


def test_policy_revision_is_bound_by_runner_without_mutating_original_command() -> None:
    command = _command(resume_stage=CollectionStage.POLICY, policy_revision=None)

    def bind_policy_revision(
        permit: WorkerExecutionPermit,
        stage: CollectionStage,
        policy_revision: int | None,
    ) -> WorkerExecutionPermit:
        if stage is CollectionStage.POLICY and policy_revision == 1:
            return replace(
                permit,
                command=permit.command.model_copy(update={"policy_revision": 1}),
            )
        return permit

    def bind_then_parse(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        context.enter_stage(CollectionStage.POLICY, policy_revision=1)
        context.enter_stage(CollectionStage.FETCH, policy_revision=context.command.policy_revision)
        context.enter_stage(CollectionStage.PARSE, policy_revision=context.command.policy_revision)
        return _prepared(context.permit, _complete_result(context.command))

    worker, control, _, factory, committer, _ = _standard_worker(
        command,
        stage_callback=bind_policy_revision,
        run=bind_then_parse,
    )

    result = worker.handle(command.model_dump())

    assert command.policy_revision is None
    assert factory.permits[0].command.policy_revision is None
    assert committer.commands[0].policy_revision == 1
    assert result.policy_revision == 1
    assert control.finalized[0][0].command.policy_revision == 1


def test_policy_revision_missing_from_policy_permit_prevents_runner_output_commit() -> None:
    command = _command(resume_stage=CollectionStage.POLICY, policy_revision=None)

    def request_missing_revision(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        context.enter_stage(CollectionStage.POLICY, policy_revision=1)
        raise AssertionError("W1 should have rejected the policy permit")

    worker, control, execution, _, committer, _ = _standard_worker(
        command,
        run=request_missing_revision,
    )

    with pytest.raises(WorkerAuthorizationError, match="policy revision is required before fetch"):
        worker.handle(command.model_dump())

    assert execution.run_count == 1
    assert execution.close_count == 1
    assert committer.prepared == []
    assert control.stopped[0][1] is True


def test_initial_permit_cannot_bind_policy_revision_absent_from_raw_command() -> None:
    command = _command(resume_stage=CollectionStage.POLICY, policy_revision=None)
    issued_permit = replace(
        _permit(command),
        command=command.model_copy(update={"policy_revision": 1}),
    )
    events: list[str] = []
    control = _Control(issued_permit, events)
    execution = _Execution(events, _parse_and_prepare)
    factory = _Factory(events, execution)
    committer = _Committer(events)

    with pytest.raises(WorkerAuthorizationError):
        _worker(control, factory, committer).handle(command.model_dump())

    assert events == ["authorize", "stopped"]
    assert factory.permits == []
    assert execution.run_count == 0
    assert committer.prepared == []
    assert control.stopped == [(issued_permit, False, "worker_authorization_failed")]


def test_stage_regression_fails_closed_after_runner_has_advanced() -> None:
    command = _command()

    def regress_stage(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        context.enter_stage(CollectionStage.PARSE, policy_revision=context.command.policy_revision)
        context.enter_stage(CollectionStage.FETCH, policy_revision=context.command.policy_revision)
        raise AssertionError("backward stage transition should fail")

    worker, control, execution, _, committer, _ = _standard_worker(command, run=regress_stage)

    with pytest.raises(WorkerAuthorizationError, match="cannot move backwards"):
        worker.handle(command.model_dump())

    assert execution.close_count == 1
    assert committer.prepared == []
    assert control.stopped[0][1] is True


def test_runner_exception_closes_resources_and_reports_stopped() -> None:
    command = _command()

    def raise_from_runner(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        raise RuntimeError("runner failed")

    worker, control, execution, _, committer, _ = _standard_worker(command, run=raise_from_runner)

    with pytest.raises(RuntimeError, match="runner failed"):
        worker.handle(command.model_dump())

    assert execution.close_count == 1
    assert committer.prepared == []
    assert control.finalized == []
    assert control.stopped[0][1] is True


def test_close_exception_reports_resources_not_closed_without_commit() -> None:
    command = _command()
    worker, control, execution, _, committer, _ = _standard_worker(
        command,
        close_error=RuntimeError("close failed"),
    )

    with pytest.raises(RuntimeError, match="close failed"):
        worker.handle(command.model_dump())

    assert execution.close_count == 1
    assert committer.prepared == []
    assert control.finalized == []
    assert control.stopped[0][1] is False


def test_stale_commit_failure_reports_stopped_after_resources_close() -> None:
    command = _command()
    worker, control, execution, _, committer, _ = _standard_worker(
        command,
        commit_error=StaleExecution("lease expired"),
    )

    with pytest.raises(StaleExecution, match="lease expired"):
        worker.handle(command.model_dump())

    assert execution.close_count == 1
    assert len(committer.prepared) == 1
    assert control.finalized == []
    assert control.stopped[0][1] is True


@pytest.mark.parametrize("missing", ["checkpoint", "resume-stage", "user-retry"])
def test_rate_limited_result_requires_every_resume_contract_field(missing: str) -> None:
    command = _command()
    retry_action = UserRetryAction(
        code="user_retry",
        label_ko="다시 시도",
        context=UserRetryContext(
            source_id=command.source_id,
            resume_stage=CollectionStage.FETCH,
            retry_not_before=NOW + timedelta(minutes=5),
        ),
    )
    result = _failure_result(
        command,
        code="RATE_LIMITED",
        checkpoint_ref=None if missing == "checkpoint" else "checkpoint-1",
        resume_stage=None if missing == "resume-stage" else CollectionStage.FETCH,
        required_actions=[] if missing == "user-retry" else [retry_action],
    )

    def rate_limited(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        context.enter_stage(CollectionStage.PARSE, policy_revision=context.command.policy_revision)
        return _prepared(context.permit, result)

    worker, control, execution, _, committer, _ = _standard_worker(command, run=rate_limited)

    with pytest.raises(WorkerContractViolation, match="RATE_LIMITED"):
        worker.handle(command.model_dump())

    assert execution.run_count == 1
    assert committer.prepared == []
    assert control.finalized == []


def test_valid_rate_limited_result_commits_and_finalizes_without_retrying() -> None:
    command = _command()
    retry_action = UserRetryAction(
        code="user_retry",
        label_ko="다시 시도",
        context=UserRetryContext(
            source_id=command.source_id,
            resume_stage=CollectionStage.FETCH,
            retry_not_before=NOW + timedelta(minutes=5),
        ),
    )
    result = _failure_result(
        command,
        code="RATE_LIMITED",
        checkpoint_ref="checkpoint-1",
        resume_stage=CollectionStage.FETCH,
        required_actions=[retry_action],
    )

    def rate_limited(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        context.enter_stage(CollectionStage.PARSE, policy_revision=context.command.policy_revision)
        return _prepared(context.permit, result)

    worker, control, execution, _, committer, _ = _standard_worker(command, run=rate_limited)

    assert worker.handle(command.model_dump()) == result
    assert execution.run_count == 1
    assert len(committer.prepared) == 1
    assert len(control.finalized) == 1


@pytest.mark.parametrize(
    (
        "result_resume_stage",
        "result_retry_not_before",
        "action_resume_stage",
        "action_retry_not_before",
    ),
    [
        (
            CollectionStage.FETCH,
            NOW + timedelta(minutes=5),
            CollectionStage.PARSE,
            NOW + timedelta(minutes=5),
        ),
        (
            CollectionStage.FETCH,
            NOW + timedelta(minutes=5),
            CollectionStage.FETCH,
            NOW + timedelta(minutes=10),
        ),
    ],
    ids=["resume-stage", "retry-not-before"],
)
def test_rate_limited_result_and_user_retry_action_must_agree(
    result_resume_stage: CollectionStage,
    result_retry_not_before: datetime,
    action_resume_stage: CollectionStage,
    action_retry_not_before: datetime,
) -> None:
    command = _command()
    retry_action = UserRetryAction(
        code="user_retry",
        label_ko="다시 시도",
        context=UserRetryContext(
            source_id=command.source_id,
            resume_stage=action_resume_stage,
            retry_not_before=action_retry_not_before,
        ),
    )
    result = _failure_result(
        command,
        code="RATE_LIMITED",
        checkpoint_ref="checkpoint-1",
        resume_stage=result_resume_stage,
        retry_not_before=result_retry_not_before,
        required_actions=[retry_action],
    )

    def rate_limited(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        context.enter_stage(CollectionStage.PARSE, policy_revision=context.command.policy_revision)
        return _prepared(context.permit, result)

    worker, control, execution, _, committer, _ = _standard_worker(command, run=rate_limited)

    with pytest.raises(WorkerContractViolation):
        worker.handle(command.model_dump())

    assert execution.run_count == 1
    assert committer.prepared == []
    assert control.finalized == []


def test_rate_limited_result_allows_matching_null_retry_not_before() -> None:
    command = _command()
    retry_action = UserRetryAction(
        code="user_retry",
        label_ko="다시 시도",
        context=UserRetryContext(
            source_id=command.source_id,
            resume_stage=CollectionStage.FETCH,
            retry_not_before=None,
        ),
    )
    result = _failure_result(
        command,
        code="RATE_LIMITED",
        checkpoint_ref="checkpoint-1",
        resume_stage=CollectionStage.FETCH,
        retry_not_before=None,
        required_actions=[retry_action],
    )

    def rate_limited(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        context.enter_stage(CollectionStage.PARSE, policy_revision=context.command.policy_revision)
        return _prepared(context.permit, result)

    worker, control, execution, _, committer, _ = _standard_worker(command, run=rate_limited)

    assert worker.handle(command.model_dump()) == result
    assert execution.run_count == 1
    assert len(committer.prepared) == 1
    assert len(control.finalized) == 1


@pytest.mark.parametrize("revision", [None, 2], ids=["missing", "mismatched"])
def test_core_source_failure_requires_matching_core_failure_decision(
    revision: int | None,
) -> None:
    command = _command(is_core=True)
    actions: list[object] = []
    if revision is not None:
        actions.append(
            CoreFailureDecisionAction(
                code="core_failure_decision",
                label_ko="결정 필요",
                context=CoreFailureDecisionContext(
                    source_id=command.source_id,
                    core_decision_revision=revision,
                    choices=("continue_limited", "stop", "retry"),
                ),
            )
        )
    result = _failure_result(command, required_actions=actions)

    def core_failure(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        context.enter_stage(CollectionStage.PARSE, policy_revision=context.command.policy_revision)
        return _prepared(context.permit, result)

    worker, _, _, _, committer, _ = _standard_worker(command, run=core_failure)

    with pytest.raises(WorkerContractViolation, match="core source failure"):
        worker.handle(command.model_dump())

    assert committer.prepared == []


def test_matching_core_failure_decision_is_finalized_unchanged() -> None:
    command = _command(is_core=True)
    action = CoreFailureDecisionAction(
        code="core_failure_decision",
        label_ko="결정 필요",
        context=CoreFailureDecisionContext(
            source_id=command.source_id,
            core_decision_revision=command.core_source_decision.decision_revision,
            choices=("continue_limited", "stop", "retry"),
        ),
    )
    result = _failure_result(command, required_actions=[action])

    def core_failure(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        context.enter_stage(CollectionStage.PARSE, policy_revision=context.command.policy_revision)
        return _prepared(context.permit, result)

    worker, control, _, _, committer, _ = _standard_worker(command, run=core_failure)

    assert worker.handle(command.model_dump()) == result
    assert committer.prepared[0].result.required_actions == [action]
    assert control.finalized[0][1].required_actions == [action]


def test_finalize_failure_does_not_report_stopped_after_commit() -> None:
    command = _command()
    worker, control, _, _, committer, _ = _standard_worker(
        command,
        finalize_error=RuntimeError("finalize failed"),
    )

    with pytest.raises(RuntimeError, match="finalize failed"):
        worker.handle(command.model_dump())

    assert len(committer.prepared) == 1
    assert len(control.finalized) == 1
    assert control.stopped == []


def test_invalid_raw_payload_does_not_call_w1() -> None:
    command = _command()
    worker, control, execution, _, committer, _ = _standard_worker(command)

    with pytest.raises(ValidationError):
        worker.handle({"schema_version": "w2.collection.v1"})

    assert execution.run_count == 0
    assert committer.prepared == []
    assert control.events == []


def test_runner_failure_after_policy_binding_reports_latest_effective_permit() -> None:
    command = _command(resume_stage=CollectionStage.POLICY, policy_revision=None)

    def bind_policy_revision(
        permit: WorkerExecutionPermit,
        stage: CollectionStage,
        policy_revision: int | None,
    ) -> WorkerExecutionPermit:
        if stage is CollectionStage.POLICY and policy_revision == 1:
            return replace(
                permit,
                command=permit.command.model_copy(update={"policy_revision": 1}),
            )
        return permit

    def bind_then_raise(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        context.enter_stage(CollectionStage.POLICY, policy_revision=1)
        raise RuntimeError("runner failed after binding policy")

    worker, control, execution, _, committer, _ = _standard_worker(
        command,
        stage_callback=bind_policy_revision,
        run=bind_then_raise,
    )

    with pytest.raises(RuntimeError, match="after binding"):
        worker.handle(command.model_dump())

    assert execution.close_count == 1
    assert committer.prepared == []
    assert control.stopped[0][0].command.policy_revision == 1
    assert control.stopped[0][1] is True


def test_policy_failure_after_binding_revision_commits_without_fetch() -> None:
    command = _command(resume_stage=CollectionStage.POLICY, policy_revision=None)
    stage_calls: list[tuple[CollectionStage, int | None]] = []

    def bind_policy_revision(
        permit: WorkerExecutionPermit,
        stage: CollectionStage,
        policy_revision: int | None,
    ) -> WorkerExecutionPermit:
        stage_calls.append((stage, policy_revision))
        if stage is CollectionStage.POLICY and policy_revision == 1:
            return replace(
                permit,
                command=permit.command.model_copy(update={"policy_revision": 1}),
            )
        return permit

    def policy_failure(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        context.enter_stage(CollectionStage.POLICY, policy_revision=1)
        return _prepared(
            context.permit,
            _failure_result(context.command, stage=CollectionStage.POLICY),
        )

    worker, control, execution, _, committer, events = _standard_worker(
        command,
        stage_callback=bind_policy_revision,
        run=policy_failure,
    )

    result = worker.handle(command.model_dump())

    assert execution.run_count == 1
    assert events[:4] == ["authorize", "factory", "run", "stage:policy"]
    assert stage_calls[0] == (CollectionStage.POLICY, 1)
    assert (CollectionStage.POLICY, None) not in stage_calls
    assert "stage:fetch" not in events
    assert len(committer.prepared) == 1
    assert len(control.finalized) == 1
    assert result.policy_revision == 1


def test_deliver_resume_is_rejected_before_external_runner_side_effects() -> None:
    command = _command(resume_stage=CollectionStage.DELIVER)
    replay_calls: list[tuple[CollectionCommand, object]] = []

    def no_replay(
        _session_factory: object,
        *,
        command: CollectionCommand,
        attempt_id: object,
        lock_authority: object,
    ) -> None:
        del _session_factory, lock_authority
        replay_calls.append((command, attempt_id))

    worker, control, execution, _, committer, _ = _standard_worker(
        command,
        replayer=no_replay,
    )

    with pytest.raises(WorkerAuthorizationError):
        worker.handle(command.model_dump())

    assert replay_calls == [(control.permit.command, control.permit.attempt_id)]
    assert execution.run_count == 0
    assert committer.prepared == []
    assert len(control.stopped) == 1


def _mixed_core_rate_limited_result(command: CollectionCommand) -> CollectionResult:
    core_action = CoreFailureDecisionAction(
        code="core_failure_decision",
        label_ko="결정 필요",
        context=CoreFailureDecisionContext(
            source_id=command.source_id,
            core_decision_revision=command.core_source_decision.decision_revision,
            choices=("continue_limited", "stop", "retry"),
        ),
    )
    retry_action = UserRetryAction(
        code="user_retry",
        label_ko="다시 시도",
        context=UserRetryContext(
            source_id=command.source_id,
            resume_stage=CollectionStage.FETCH,
            retry_not_before=NOW + timedelta(minutes=5),
        ),
    )
    return CollectionResult(
        schema_version="w2.collection.v1",
        command_id=command.command_id,
        job_id=command.job_id,
        input_version=command.input_version,
        result_version=1,
        successful_source_refs=[
            SourceReference(
                source_id=command.source_id,
                source_version_id=uuid4(),
                extraction_revision_id=uuid4(),
            )
        ],
        failures=[
            Failure(
                source_id=command.source_id,
                stage=CollectionStage.PARSE,
                code="COLLECTION_FAILED",
                missing_sections=["job_description"],
                impact="핵심 공고 구역을 확인할 수 없습니다.",
                core_decision_revision=command.core_source_decision.decision_revision,
            ),
            Failure(
                source_id=command.source_id,
                stage=CollectionStage.FETCH,
                code="RATE_LIMITED",
                missing_sections=["job_requirements"],
                impact="요청 한도 때문에 재개 시각까지 수집할 수 없습니다.",
                core_decision_revision=command.core_source_decision.decision_revision,
            ),
        ],
        completion_kind=CompletionKind.PARTIAL,
        resume_stage=CollectionStage.FETCH,
        checkpoint_ref="checkpoint-rate-limited",
        retry_not_before=NOW + timedelta(minutes=5),
        message_ko="일부 근거를 수집했고, 사용자 확인과 재시도가 필요합니다.",
        required_actions=[core_action, retry_action],
        source_id=command.source_id,
        policy_revision=command.policy_revision,
    )


def test_mixed_core_and_rate_limited_partial_preserves_success_and_actions() -> None:
    command = _command(is_core=True)
    result = _mixed_core_rate_limited_result(command)

    def mixed_failure(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        context.enter_stage(CollectionStage.PARSE, policy_revision=context.command.policy_revision)
        return _prepared(context.permit, result)

    worker, control, _, _, committer, _ = _standard_worker(command, run=mixed_failure)

    assert worker.handle(command.model_dump()) == result
    assert committer.prepared[0].result.successful_source_refs == result.successful_source_refs
    assert committer.prepared[0].result.required_actions == result.required_actions
    assert control.finalized[0][1].successful_source_refs == result.successful_source_refs
    assert control.finalized[0][1].required_actions == result.required_actions


@pytest.mark.parametrize(
    ("invalid_result", "message"),
    [
        (
            lambda command: _failure_result(
                command,
                required_actions=[
                    CoreFailureDecisionAction(
                        code="core_failure_decision",
                        label_ko="결정 필요",
                        context=CoreFailureDecisionContext(
                            source_id=command.source_id,
                            core_decision_revision=command.core_source_decision.decision_revision,
                            choices=("continue_limited", "stop", "retry"),
                        ),
                    )
                ],
            ).model_copy(
                update={
                    "failures": [
                        _failure_result(command)
                        .failures[0]
                        .model_copy(
                            update={
                                "core_decision_revision": (
                                    command.core_source_decision.decision_revision + 1
                                )
                            }
                        )
                    ]
                }
            ),
            "failure core_decision_revision",
        ),
        (
            lambda command: _failure_result(
                command,
                required_actions=[
                    CoreFailureDecisionAction(
                        code="core_failure_decision",
                        label_ko="결정 필요",
                        context=CoreFailureDecisionContext(
                            source_id=command.source_id,
                            core_decision_revision=command.core_source_decision.decision_revision
                            + 1,
                            choices=("continue_limited", "stop", "retry"),
                        ),
                    )
                ],
            ),
            "core source failure",
        ),
        (
            lambda command: _failure_result(
                command,
                required_actions=[
                    CoreFailureDecisionAction(
                        code="core_failure_decision",
                        label_ko="결정 필요",
                        context=CoreFailureDecisionContext(
                            source_id=command.source_id,
                            core_decision_revision=command.core_source_decision.decision_revision,
                            choices=("continue_limited", "stop", "retry"),
                        ),
                    )
                ],
            ).model_copy(
                update={
                    "required_actions": [
                        CoreFailureDecisionAction(
                            code="core_failure_decision",
                            label_ko="결정 필요",
                            context=CoreFailureDecisionContext(
                                source_id=uuid4(),
                                core_decision_revision=command.core_source_decision.decision_revision,
                                choices=("continue_limited", "stop", "retry"),
                            ),
                        )
                    ]
                }
            ),
            "required action source_id",
        ),
    ],
    ids=["failure-revision", "core-action-revision", "cross-source-action"],
)
def test_worker_rejects_failure_and_action_mismatches(
    invalid_result: Callable[[CollectionCommand], CollectionResult],
    message: str,
) -> None:
    command = _command(is_core=True)
    result = invalid_result(command)

    def invalid(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        context.enter_stage(CollectionStage.PARSE, policy_revision=context.command.policy_revision)
        return _prepared(context.permit, result)

    worker, _, _, _, committer, _ = _standard_worker(command, run=invalid)

    with pytest.raises(WorkerContractViolation, match=message):
        worker.handle(command.model_dump())

    assert committer.prepared == []


def test_non_core_result_cannot_emit_core_failure_decision() -> None:
    command = _command(is_core=False)
    action = CoreFailureDecisionAction(
        code="core_failure_decision",
        label_ko="결정 필요",
        context=CoreFailureDecisionContext(
            source_id=command.source_id,
            core_decision_revision=command.core_source_decision.decision_revision,
            choices=("continue_limited", "stop", "retry"),
        ),
    )
    result = _failure_result(command, required_actions=[action])

    def non_core_failure(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        context.enter_stage(CollectionStage.PARSE, policy_revision=context.command.policy_revision)
        return _prepared(context.permit, result)

    worker, _, _, _, committer, _ = _standard_worker(command, run=non_core_failure)

    with pytest.raises(WorkerContractViolation, match="non-core"):
        worker.handle(command.model_dump())

    assert committer.prepared == []


def test_execution_exception_reports_latest_checkpoint_after_close() -> None:
    command = _command()

    def stage_checkpoint(
        permit: WorkerExecutionPermit,
        stage: CollectionStage,
        policy_revision: int | None,
    ) -> WorkerExecutionPermit:
        if stage is CollectionStage.FETCH:
            return replace(permit, checkpoint_ref="checkpoint-running")
        return permit

    def raise_from_runner(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        raise RuntimeError("runner failed after checkpoint")

    worker, control, execution, _, committer, events = _standard_worker(
        command,
        stage_callback=stage_checkpoint,
        run=raise_from_runner,
    )

    with pytest.raises(RuntimeError, match="after checkpoint"):
        worker.handle(command.model_dump())

    assert execution.close_count == 1
    assert committer.prepared == []
    assert control.stopped_checkpoints == ["checkpoint-running"]
    assert events.index("close") < events.index("stopped")


def test_execution_exception_legacy_reporter_still_records_stop_once() -> None:
    command = _command()
    events: list[str] = []

    def stage_checkpoint(
        permit: WorkerExecutionPermit,
        stage: CollectionStage,
        policy_revision: int | None,
    ) -> WorkerExecutionPermit:
        if stage is CollectionStage.FETCH:
            return replace(permit, checkpoint_ref="checkpoint-running")
        return permit

    def raise_from_runner(context: WorkerExecutionContext) -> PreparedCollectionCommit:
        raise RuntimeError("runner failed after checkpoint")

    control = _LegacyStoppedControl(
        _permit(command),
        events,
        stage_callback=stage_checkpoint,
    )
    execution = _Execution(events, raise_from_runner)
    factory = _Factory(events, execution)
    committer = _Committer(events)
    worker = _worker(control, factory, committer)

    with pytest.raises(RuntimeError, match="after checkpoint"):
        worker.handle(command.model_dump())

    assert execution.close_count == 1
    assert control.stopped[0][0].checkpoint_ref == "checkpoint-running"
    assert control.stopped_checkpoints == [None]
    assert events.count("stopped") == 1
    assert events.index("close") < events.index("stopped")
