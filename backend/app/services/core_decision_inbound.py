from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.models.identity import User
from app.models.jobs import InboxReceipt, Job, JobCoreDecisionBinding, JobRequiredAction
from app.models.sources import AnalysisSourceDecision
from app.repo.core_decisions import CoreDecisionRepository
from app.repo.jobs import JobRepository
from app.runtime.core_decision_binding import (
    W3_CORE_DECISION_CONSUMER,
    W3_CORE_DECISION_PRODUCER,
    CoreDecisionReceipt,
    CoreDecisionReceiptOutcome,
    W3CoreDecisionEvent,
    parse_w3_core_decision_event,
)


class CoreDecisionInboundError(ValueError):
    """A terminal W3 decision rejection before any authoritative mutation."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


class CoreDecisionInboundService:
    """Apply one authenticated W3 decision inside the caller-owned transaction.

    Parsing and canonical hashing happen before authoritative rows are locked. The
    service then preserves User -> Job -> JobSourceLink/Source -> binding order and
    deliberately creates no command or outbox row. Explicit user retry is the sole
    boundary that can create the next execution generation.
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
    ) -> CoreDecisionReceipt:
        event = parse_w3_core_decision_event(body)
        if authenticated_principal != W3_CORE_DECISION_PRODUCER:
            return self._transient_terminal_receipt(
                event=event,
                outcome=CoreDecisionReceiptOutcome.REJECTED_PRINCIPAL,
                error_code="CORE_DECISION_PRINCIPAL_MISMATCH",
            )

        # This non-locking read only discovers the owner needed for the established
        # lock order. All state is reloaded and checked after both authoritative locks.
        job_hint = self.jobs.get_job_by_id(job_id=event.job_id)
        if job_hint is None:
            raise CoreDecisionInboundError("CORE_DECISION_JOB_NOT_FOUND")
        owner = self.jobs.get_owner_for_update(owner_user_id=job_hint.owner_user_id)
        if owner is None:
            raise CoreDecisionInboundError("CORE_DECISION_OWNER_NOT_FOUND")
        job = self.jobs.get_job_for_update(
            job_id=event.job_id,
            owner_user_id=owner.id,
        )
        if job is None:
            raise CoreDecisionInboundError("CORE_DECISION_JOB_OWNER_MISMATCH")

        existing_receipt = self.jobs.get_inbox_receipt(
            consumer_name=W3_CORE_DECISION_CONSUMER,
            event_id=event.message_id,
            for_update=True,
        )
        if existing_receipt is not None:
            if existing_receipt.payload_digest != event.payload_digest:
                return self._receipt_from_row(
                    event=event,
                    row=existing_receipt,
                    outcome=CoreDecisionReceiptOutcome.REJECTED_CONFLICT,
                    error_code="CORE_DECISION_MESSAGE_ID_CONFLICT",
                )
            binding = self.decisions.get_binding_by_origin(
                origin_message_id=event.message_id
            )
            return self._receipt_from_row(
                event=event,
                row=existing_receipt,
                outcome=CoreDecisionReceiptOutcome.DUPLICATE,
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
                outcome=CoreDecisionReceiptOutcome.REJECTED_BINDING,
                error_code="CORE_DECISION_OWNER_NOT_CURRENT",
            )
        if job.status != "WAITING_USER" or job.active_lease_id is not None:
            return self._record_terminal_receipt(
                event=event,
                outcome=CoreDecisionReceiptOutcome.REJECTED_BINDING,
                error_code="CORE_DECISION_JOB_NOT_WAITING",
            )
        if job.analysis_input_version != event.analysis_input_version:
            return self._record_terminal_receipt(
                event=event,
                outcome=CoreDecisionReceiptOutcome.REJECTED_BINDING,
                error_code="CORE_DECISION_INPUT_MISMATCH",
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
                outcome=CoreDecisionReceiptOutcome.REJECTED_BINDING,
                error_code="CORE_DECISION_SOURCE_NOT_BOUND",
            )
        if (
            source.company_id != event.company_id
            or source_link.analysis_input_version != event.analysis_input_version
        ):
            return self._record_terminal_receipt(
                event=event,
                outcome=CoreDecisionReceiptOutcome.REJECTED_BINDING,
                error_code="CORE_DECISION_BINDING_MISMATCH",
            )

        current = self.decisions.get_current_binding(
            job_id=job.id,
            source_id=event.source_id,
            analysis_input_version=event.analysis_input_version,
        )
        if current is not None and event.decision_version <= current.decision_version:
            if event.decision_version < current.decision_version:
                return self._record_terminal_receipt(
                    event=event,
                    outcome=CoreDecisionReceiptOutcome.STALE_DISCARDED,
                    error_code="CORE_DECISION_REVISION_STALE",
                )
            return self._record_terminal_receipt(
                event=event,
                outcome=CoreDecisionReceiptOutcome.REJECTED_CONFLICT,
                error_code="CORE_DECISION_REVISION_CONFLICT",
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
                outcome=CoreDecisionReceiptOutcome.REJECTED_BINDING,
                error_code="CORE_DECISION_ACTION_NOT_CURRENT",
            )
        receipt_row, inserted = self.jobs.reserve_inbox_receipt(
            consumer_name=W3_CORE_DECISION_CONSUMER,
            event_id=event.message_id,
            outcome_code=CoreDecisionReceiptOutcome.APPLIED.value,
            payload_digest=event.payload_digest,
            producer_name=event.producer,
            schema_version=event.schema_version,
        )
        if not inserted:
            return self._receipt_from_row(
                event=event,
                row=receipt_row,
                outcome=(
                    CoreDecisionReceiptOutcome.DUPLICATE
                    if receipt_row.payload_digest == event.payload_digest
                    else CoreDecisionReceiptOutcome.REJECTED_CONFLICT
                ),
                error_code=(
                    None
                    if receipt_row.payload_digest == event.payload_digest
                    else "CORE_DECISION_MESSAGE_ID_CONFLICT"
                ),
            )

        decision = AnalysisSourceDecision(
            decision_scope=event.decision_scope,
            company_id=event.company_id,
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
        binding = JobCoreDecisionBinding(
            job_id=job.id,
            owner_user_id=owner.id,
            source_id=event.source_id,
            analysis_source_decision_id=decision.id,
            origin_message_id=event.message_id,
            payload_digest=event.payload_digest,
            analysis_input_version=event.analysis_input_version,
            decision_version=event.decision_version,
            decision_code=event.decision_code,
            owner_deletion_epoch=owner.deletion_epoch,
        )
        self.decisions.add_binding(binding)
        action.action_status = "RESOLVED"
        action.resolved_at = datetime.now(UTC)
        next_action_code = "RETRY" if event.decision_code == "CORE_REQUIRED" else "STOP"
        next_context_code = (
            "CORE_DECISION_AVAILABLE"
            if event.decision_code == "CORE_REQUIRED"
            else "NON_CORE_DECISION_REQUIRES_STOP"
        )
        self.jobs.add_required_action(
            JobRequiredAction(
                job_id=job.id,
                owner_user_id=owner.id,
                action_code=next_action_code,
                action_status="OPEN",
                context_code=next_context_code,
                expected_input_version=event.analysis_input_version,
                expected_result_version=str(event.decision_version),
            )
        )
        self._assert_commit_currentness(owner=owner, job=job)
        self.session.flush()
        return CoreDecisionReceipt(
            message_id=event.message_id,
            payload_digest=event.payload_digest,
            outcome=CoreDecisionReceiptOutcome(receipt_row.outcome_code),
            retryable=False,
            error_code=None,
            decision_id=decision.id,
            received_at=receipt_row.processed_at or datetime.now(UTC),
        )

    def _record_terminal_receipt(
        self,
        *,
        event: W3CoreDecisionEvent,
        outcome: CoreDecisionReceiptOutcome,
        error_code: str,
    ) -> CoreDecisionReceipt:
        row, inserted = self.jobs.reserve_inbox_receipt(
            consumer_name=W3_CORE_DECISION_CONSUMER,
            event_id=event.message_id,
            outcome_code=outcome.value,
            payload_digest=event.payload_digest,
            producer_name=event.producer,
            schema_version=event.schema_version,
        )
        if not inserted and row.payload_digest != event.payload_digest:
            outcome = CoreDecisionReceiptOutcome.REJECTED_CONFLICT
            error_code = "CORE_DECISION_MESSAGE_ID_CONFLICT"
        return self._receipt_from_row(
            event=event,
            row=row,
            outcome=outcome,
            error_code=error_code,
        )

    @staticmethod
    def _receipt_from_row(
        *,
        event: W3CoreDecisionEvent,
        row: InboxReceipt,
        outcome: CoreDecisionReceiptOutcome,
        error_code: str | None,
        decision_id: UUID | None = None,
    ) -> CoreDecisionReceipt:
        return CoreDecisionReceipt(
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
        event: W3CoreDecisionEvent,
        outcome: CoreDecisionReceiptOutcome,
        error_code: str,
    ) -> CoreDecisionReceipt:
        return CoreDecisionReceipt(
            message_id=event.message_id,
            payload_digest=event.payload_digest,
            outcome=outcome,
            retryable=False,
            error_code=error_code,
            decision_id=None,
            received_at=datetime.now(UTC),
        )

    @staticmethod
    def _assert_commit_currentness(*, owner: User, job: Job) -> None:
        """Defensive final check while the authoritative User and Job locks are held."""

        if (
            owner.account_status != "ACTIVE"
            or owner.deleted_at is not None
            or job.status != "WAITING_USER"
            or job.active_lease_id is not None
            or job.owner_deletion_epoch != owner.deletion_epoch
        ):
            raise CoreDecisionInboundError("CORE_DECISION_CURRENTNESS_CHANGED_DURING_APPLY")
