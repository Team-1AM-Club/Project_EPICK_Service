from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.application_workspace import (
    ApplicationProject,
    ApplicationProjectVersion,
    ProjectQuestion,
    QuestionVersion,
)
from app.models.identity import User
from app.models.jobs import InboxReceipt, Job, JobCoreDecisionBinding, JobRequiredAction
from app.models.sources import AnalysisSourceDecision
from app.repo.core_decisions import CoreDecisionRepository
from app.repo.jobs import JobRepository
from app.runtime.w4_question_core_decision import (
    W4_QUESTION_CORE_CONSUMER,
    W4QuestionCoreEvent,
    W4QuestionCoreReceipt,
    W4QuestionCoreReceiptOutcome,
    parse_w4_question_core_event,
    verify_w4_question_core_principal,
)


class W4QuestionCoreInboundError(ValueError):
    """A terminal W4 rejection that cannot safely be attached to an unknown Job."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class W4QuestionCoreInboundService:
    """Atomically persist one authenticated W4 Question Core decision.

    The caller owns the database transaction. This service takes the durable
    lock order User -> Job -> Project -> ProjectVersion -> Question ->
    QuestionVersion -> JobSourceLink -> Source and does not create a W2
    command or outbox row. Only a later explicit retry can do that.
    """

    def __init__(self, session: Session) -> None:
        self.session = session
        self.jobs = JobRepository(session)
        self.decisions = CoreDecisionRepository(session)

    def apply(
        self,
        *,
        body: bytes | str | Mapping[str, object],
        authenticated_principal: str,
        expected_principal: str,
    ) -> W4QuestionCoreReceipt:
        event = parse_w4_question_core_event(body)
        try:
            verify_w4_question_core_principal(
                authenticated_principal=authenticated_principal,
                expected_principal=expected_principal,
            )
        except ValueError:
            return self._transient_terminal_receipt(
                event=event,
                outcome=W4QuestionCoreReceiptOutcome.REJECTED_PRINCIPAL,
                error_code="W4_QUESTION_CORE_PRINCIPAL_MISMATCH",
            )

        # An event must name a W1-issued Job. W1 never searches by owner,
        # project, question or Source as a substitute.
        job_hint = self.jobs.get_job_by_id(job_id=event.job_id)
        if job_hint is None:
            raise W4QuestionCoreInboundError("W4_QUESTION_CORE_JOB_NOT_FOUND")
        owner = self.jobs.get_owner_for_update(owner_user_id=job_hint.owner_user_id)
        if owner is None:
            raise W4QuestionCoreInboundError("W4_QUESTION_CORE_OWNER_NOT_FOUND")
        job = self.jobs.get_job_for_update(job_id=event.job_id, owner_user_id=owner.id)
        if job is None:
            raise W4QuestionCoreInboundError("W4_QUESTION_CORE_JOB_OWNER_MISMATCH")

        existing_receipt = self.jobs.get_inbox_receipt(
            consumer_name=W4_QUESTION_CORE_CONSUMER,
            event_id=event.message_id,
            for_update=True,
        )
        if existing_receipt is not None:
            if existing_receipt.payload_digest != event.payload_digest:
                return self._receipt_from_row(
                    event=event,
                    row=existing_receipt,
                    outcome=W4QuestionCoreReceiptOutcome.REJECTED_CONFLICT,
                    error_code="W4_QUESTION_CORE_MESSAGE_ID_CONFLICT",
                )
            binding = self.decisions.get_binding_by_origin(
                origin_producer=event.producer,
                origin_message_id=event.message_id,
            )
            return self._receipt_from_row(
                event=event,
                row=existing_receipt,
                outcome=W4QuestionCoreReceiptOutcome.DUPLICATE,
                error_code=None,
                decision_id=(
                    binding.analysis_source_decision_id if binding is not None else None
                ),
            )

        if (
            owner.account_status != "ACTIVE"
            or owner.deleted_at is not None
            or job.owner_deletion_epoch != owner.deletion_epoch
        ):
            return self._record_terminal_receipt(
                event=event,
                outcome=W4QuestionCoreReceiptOutcome.REJECTED_BINDING,
                error_code="W4_QUESTION_CORE_OWNER_NOT_CURRENT",
            )
        if job.status != "WAITING_USER" or job.active_lease_id is not None:
            return self._record_terminal_receipt(
                event=event,
                outcome=W4QuestionCoreReceiptOutcome.REJECTED_BINDING,
                error_code="W4_QUESTION_CORE_JOB_NOT_WAITING",
            )
        if job.analysis_input_version != event.analysis_input_version:
            return self._record_terminal_receipt(
                event=event,
                outcome=W4QuestionCoreReceiptOutcome.REJECTED_BINDING,
                error_code="W4_QUESTION_CORE_INPUT_MISMATCH",
            )

        project, project_version, question, question_version = self._lock_current_question(
            owner=owner,
            job=job,
            question_version_id=event.question_version_id,
        )
        if any(
            item is None for item in (project, project_version, question, question_version)
        ):
            return self._record_terminal_receipt(
                event=event,
                outcome=W4QuestionCoreReceiptOutcome.REJECTED_BINDING,
                error_code="W4_QUESTION_CORE_QUESTION_BINDING_MISMATCH",
            )

        source_link = self.decisions.get_source_link_for_update(
            job_id=job.id,
            owner_user_id=owner.id,
            source_id=event.source_id,
        )
        source = self.decisions.get_source_for_update(source_id=event.source_id)
        if source_link is None or source is None:
            return self._record_terminal_receipt(
                event=event,
                outcome=W4QuestionCoreReceiptOutcome.REJECTED_BINDING,
                error_code="W4_QUESTION_CORE_SOURCE_NOT_BOUND",
            )
        if source_link.analysis_input_version != event.analysis_input_version:
            return self._record_terminal_receipt(
                event=event,
                outcome=W4QuestionCoreReceiptOutcome.REJECTED_BINDING,
                error_code="W4_QUESTION_CORE_SOURCE_INPUT_MISMATCH",
            )

        existing_decision = self.decisions.get_binding_by_origin_decision(
            origin_producer=event.producer,
            origin_decision_id=event.decision_id,
        )
        if existing_decision is not None:
            if self._matches_existing_origin_decision(
                binding=existing_decision,
                event=event,
            ):
                return self._record_origin_decision_duplicate(
                    event=event,
                    decision_id=existing_decision.analysis_source_decision_id,
                )
            return self._record_terminal_receipt(
                event=event,
                outcome=W4QuestionCoreReceiptOutcome.REJECTED_CONFLICT,
                error_code="W4_QUESTION_CORE_DECISION_ID_CONFLICT",
            )

        current = self.decisions.get_current_binding(
            job_id=job.id,
            source_id=event.source_id,
            analysis_input_version=event.analysis_input_version,
            origin_producer=event.producer,
            decision_scope=event.decision_scope,
            question_version_id=event.question_version_id,
        )
        if current is not None and event.decision_version <= current.decision_version:
            return self._record_terminal_receipt(
                event=event,
                outcome=(
                    W4QuestionCoreReceiptOutcome.STALE_DISCARDED
                    if event.decision_version < current.decision_version
                    else W4QuestionCoreReceiptOutcome.REJECTED_CONFLICT
                ),
                error_code=(
                    "W4_QUESTION_CORE_REVISION_STALE"
                    if event.decision_version < current.decision_version
                    else "W4_QUESTION_CORE_REVISION_CONFLICT"
                ),
            )

        action = self.jobs.get_open_required_action_for_update(
            job_id=job.id,
            owner_user_id=owner.id,
            action_code="CORE_DECISION_REQUIRED",
        )
        if (
            action is None
            or action.action_status != "OPEN"
            or action.expected_input_version != event.analysis_input_version
        ):
            return self._record_terminal_receipt(
                event=event,
                outcome=W4QuestionCoreReceiptOutcome.REJECTED_BINDING,
                error_code="W4_QUESTION_CORE_ACTION_NOT_CURRENT",
            )

        receipt_row, inserted = self.jobs.reserve_inbox_receipt(
            consumer_name=W4_QUESTION_CORE_CONSUMER,
            event_id=event.message_id,
            outcome_code=W4QuestionCoreReceiptOutcome.APPLIED.value,
            payload_digest=event.payload_digest,
            producer_name=event.producer,
            schema_version=event.schema_version,
        )
        if not inserted:
            return self._receipt_from_row(
                event=event,
                row=receipt_row,
                outcome=(
                    W4QuestionCoreReceiptOutcome.DUPLICATE
                    if receipt_row.payload_digest == event.payload_digest
                    else W4QuestionCoreReceiptOutcome.REJECTED_CONFLICT
                ),
                error_code=(
                    None
                    if receipt_row.payload_digest == event.payload_digest
                    else "W4_QUESTION_CORE_MESSAGE_ID_CONFLICT"
                ),
            )

        decision = AnalysisSourceDecision(
            decision_scope=event.decision_scope,
            company_id=None,
            question_version_id=event.question_version_id,
            source_id=event.source_id,
            source_version_id=source_link.source_version_id,
            analysis_input_version=event.analysis_input_version,
            decision_version=event.decision_version,
            decision_code=event.decision_code,
            decision_owner=event.decision_owner,
            reason_code=event.reason_code,
        )
        self.decisions.add_decision(decision)
        self.session.flush()
        self.decisions.add_binding(
            JobCoreDecisionBinding(
                job_id=job.id,
                owner_user_id=owner.id,
                source_id=event.source_id,
                analysis_source_decision_id=decision.id,
                origin_producer=event.producer,
                decision_scope=event.decision_scope,
                question_version_id=event.question_version_id,
                origin_message_id=event.message_id,
                origin_decision_id=event.decision_id,
                payload_digest=event.payload_digest,
                analysis_input_version=event.analysis_input_version,
                decision_version=event.decision_version,
                decision_code=event.decision_code,
                owner_deletion_epoch=owner.deletion_epoch,
            )
        )
        action.action_status = "RESOLVED"
        action.resolved_at = datetime.now(UTC)
        self.jobs.add_required_action(
            JobRequiredAction(
                job_id=job.id,
                owner_user_id=owner.id,
                action_code="RETRY" if event.decision_code == "CORE_REQUIRED" else "STOP",
                action_status="OPEN",
                context_code=(
                    "W4_QUESTION_CORE_AVAILABLE"
                    if event.decision_code == "CORE_REQUIRED"
                    else "W4_NON_CORE_DECISION_REQUIRES_STOP"
                ),
                expected_input_version=event.analysis_input_version,
                expected_result_version=str(event.decision_version),
            )
        )
        self._assert_commit_currentness(
            owner=owner,
            job=job,
            project=project,
            project_version=project_version,
            question=question,
            question_version=question_version,
        )
        self.session.flush()
        return W4QuestionCoreReceipt(
            message_id=event.message_id,
            payload_digest=event.payload_digest,
            outcome=W4QuestionCoreReceiptOutcome(receipt_row.outcome_code),
            retryable=False,
            error_code=None,
            decision_id=decision.id,
            received_at=receipt_row.processed_at or datetime.now(UTC),
        )

    def _lock_current_question(
        self,
        *,
        owner: User,
        job: Job,
        question_version_id: UUID,
    ) -> tuple[
        ApplicationProject | None,
        ApplicationProjectVersion | None,
        ProjectQuestion | None,
        QuestionVersion | None,
    ]:
        if job.project_id is None:
            return None, None, None, None
        project = self.session.scalar(
            select(ApplicationProject)
            .where(
                ApplicationProject.id == job.project_id,
                ApplicationProject.owner_user_id == owner.id,
            )
            .with_for_update()
        )
        if project is None or project.current_version_id is None:
            return project, None, None, None
        project_version = self.session.scalar(
            select(ApplicationProjectVersion)
            .where(
                ApplicationProjectVersion.id == project.current_version_id,
                ApplicationProjectVersion.project_id == project.id,
                ApplicationProjectVersion.owner_user_id == owner.id,
            )
            .with_for_update()
        )
        question_version = self.session.scalar(
            select(QuestionVersion)
            .where(
                QuestionVersion.id == question_version_id,
                QuestionVersion.project_id == project.id,
                QuestionVersion.owner_user_id == owner.id,
            )
            .with_for_update()
        )
        if question_version is None:
            return project, project_version, None, None
        question = self.session.scalar(
            select(ProjectQuestion)
            .where(
                ProjectQuestion.id == question_version.question_id,
                ProjectQuestion.project_id == project.id,
                ProjectQuestion.owner_user_id == owner.id,
                ProjectQuestion.current_version_id == question_version.id,
                ProjectQuestion.status == "ACTIVE",
            )
            .with_for_update()
        )
        return project, project_version, question, question_version

    def _record_terminal_receipt(
        self,
        *,
        event: W4QuestionCoreEvent,
        outcome: W4QuestionCoreReceiptOutcome,
        error_code: str,
    ) -> W4QuestionCoreReceipt:
        row, inserted = self.jobs.reserve_inbox_receipt(
            consumer_name=W4_QUESTION_CORE_CONSUMER,
            event_id=event.message_id,
            outcome_code=outcome.value,
            payload_digest=event.payload_digest,
            producer_name=event.producer,
            schema_version=event.schema_version,
        )
        if not inserted:
            if row.payload_digest != event.payload_digest:
                outcome = W4QuestionCoreReceiptOutcome.REJECTED_CONFLICT
                error_code = "W4_QUESTION_CORE_MESSAGE_ID_CONFLICT"
            else:
                # A concurrent or redelivered terminal delivery must reuse the first
                # durable receipt rather than reclassifying it from later mutable state.
                outcome = W4QuestionCoreReceiptOutcome.DUPLICATE
                error_code = None
        return self._receipt_from_row(event=event, row=row, outcome=outcome, error_code=error_code)

    def _record_origin_decision_duplicate(
        self,
        *,
        event: W4QuestionCoreEvent,
        decision_id: UUID,
    ) -> W4QuestionCoreReceipt:
        """Record a new delivery of W4's already-applied immutable decision once.

        ``decision_id`` is W4's producer-scoped identity; ``message_id`` is only an
        at-least-once transport identity.  A lost SQS delete can therefore legitimately
        yield a new delivery ID for the same decision.  We write a second inbox receipt
        with ``DUPLICATE`` but never create another W1 decision, binding, action or W2
        command.  A conflicting reuse of the new delivery ID remains terminal conflict.
        """

        row, inserted = self.jobs.reserve_inbox_receipt(
            consumer_name=W4_QUESTION_CORE_CONSUMER,
            event_id=event.message_id,
            outcome_code=W4QuestionCoreReceiptOutcome.DUPLICATE.value,
            payload_digest=event.payload_digest,
            producer_name=event.producer,
            schema_version=event.schema_version,
        )
        if not inserted and row.payload_digest != event.payload_digest:
            return self._receipt_from_row(
                event=event,
                row=row,
                outcome=W4QuestionCoreReceiptOutcome.REJECTED_CONFLICT,
                error_code="W4_QUESTION_CORE_MESSAGE_ID_CONFLICT",
            )
        return self._receipt_from_row(
            event=event,
            row=row,
            outcome=W4QuestionCoreReceiptOutcome.DUPLICATE,
            error_code=None,
            decision_id=decision_id,
        )

    def _matches_existing_origin_decision(
        self,
        *,
        binding: JobCoreDecisionBinding,
        event: W4QuestionCoreEvent,
    ) -> bool:
        """Compare W4's immutable logical decision, excluding transport delivery ID."""

        if (
            binding.job_id != event.job_id
            or binding.source_id != event.source_id
            or binding.origin_producer != event.producer
            or binding.decision_scope != event.decision_scope
            or binding.question_version_id != event.question_version_id
            or binding.origin_decision_id != event.decision_id
            or binding.analysis_input_version != event.analysis_input_version
            or binding.decision_version != event.decision_version
            or binding.decision_code != event.decision_code
        ):
            return False
        decision = self.decisions.get_decision(
            decision_id=binding.analysis_source_decision_id,
        )
        return (
            decision is not None
            and decision.decision_scope == event.decision_scope
            and decision.company_id is None
            and decision.question_version_id == event.question_version_id
            and decision.source_id == event.source_id
            and decision.analysis_input_version == event.analysis_input_version
            and decision.decision_version == event.decision_version
            and decision.decision_code == event.decision_code
            and decision.decision_owner == event.decision_owner
            and decision.reason_code == event.reason_code
        )

    @staticmethod
    def _receipt_from_row(
        *,
        event: W4QuestionCoreEvent,
        row: InboxReceipt,
        outcome: W4QuestionCoreReceiptOutcome,
        error_code: str | None,
        decision_id: UUID | None = None,
    ) -> W4QuestionCoreReceipt:
        return W4QuestionCoreReceipt(
            message_id=event.message_id,
            payload_digest=event.payload_digest,
            outcome=outcome,
            retryable=False,
            error_code=error_code,
            decision_id=decision_id,
            received_at=row.processed_at or datetime.now(UTC),
        )

    @staticmethod
    def _transient_terminal_receipt(
        *,
        event: W4QuestionCoreEvent,
        outcome: W4QuestionCoreReceiptOutcome,
        error_code: str,
    ) -> W4QuestionCoreReceipt:
        return W4QuestionCoreReceipt(
            message_id=event.message_id,
            payload_digest=event.payload_digest,
            outcome=outcome,
            retryable=False,
            error_code=error_code,
            decision_id=None,
            received_at=datetime.now(UTC),
        )

    @staticmethod
    def _assert_commit_currentness(
        *,
        owner: User,
        job: Job,
        project: ApplicationProject,
        project_version: ApplicationProjectVersion,
        question: ProjectQuestion,
        question_version: QuestionVersion,
    ) -> None:
        if (
            owner.account_status != "ACTIVE"
            or owner.deleted_at is not None
            or job.status != "WAITING_USER"
            or job.active_lease_id is not None
            or job.owner_deletion_epoch != owner.deletion_epoch
            or job.project_id != project.id
            or project.current_version_id != project_version.id
            or question.project_id != project.id
            or question.owner_user_id != owner.id
            or question.current_version_id != question_version.id
            or question_version.owner_user_id != owner.id
        ):
            raise W4QuestionCoreInboundError(
                "W4_QUESTION_CORE_CURRENTNESS_CHANGED_DURING_APPLY"
            )
