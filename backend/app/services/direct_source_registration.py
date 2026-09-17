"""W1-owned creation of a non-core direct Source registration Job.

This is an internal service boundary, not a public request model.  Its method
accepts the Source target and normal idempotency inputs only; it deliberately
does not accept a caller supplied decision owner, scope, core flag, reason, or
version.  Those private values are created by W1 in the same transaction as the
Job, Source link, decision, and initial execution command.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models.jobs import Job, JobCommand
from app.models.sources import AnalysisSourceDecision, JobSourceLink, Source
from app.services.jobs import JobAcceptance, JobService

DIRECT_SOURCE_REGISTRATION_SCOPE = "DIRECT_SOURCE_REGISTRATION"
DIRECT_SOURCE_REGISTRATION_JOB_TYPE = "SOURCE_REGISTRATION"
DIRECT_SOURCE_REGISTRATION_PURPOSE = "DIRECT_SOURCE_REGISTRATION"
DIRECT_SOURCE_REGISTRATION_OWNER = "W1"
DIRECT_SOURCE_REGISTRATION_DECISION_CODE = "NON_CORE_OPTIONAL"
DIRECT_SOURCE_REGISTRATION_REASON_CODE = "DIRECT_REGISTRATION_REQUESTED"


class DirectSourceRegistrationError(ValueError):
    """The caller asked W1 to register a Source that cannot be dispatched."""


@dataclass(frozen=True)
class DirectSourceRegistrationAcceptance:
    job: Job
    command: JobCommand
    decision: AnalysisSourceDecision
    source_link: JobSourceLink
    replayed: bool


def direct_source_registration_pin(*, decision: AnalysisSourceDecision) -> dict[str, object]:
    """Return the sole W1-created pin representation for a direct registration."""

    if (
        decision.decision_scope != DIRECT_SOURCE_REGISTRATION_SCOPE
        or decision.company_id is None
        or decision.question_version_id is not None
        or decision.decision_owner != DIRECT_SOURCE_REGISTRATION_OWNER
        or decision.decision_code != DIRECT_SOURCE_REGISTRATION_DECISION_CODE
        or decision.reason_code is None
        or not decision.analysis_input_version
    ):
        raise DirectSourceRegistrationError("DIRECT_SOURCE_REGISTRATION_DECISION_INVALID")
    return {
        "registration_decision_id": str(decision.id),
        "decision_scope": DIRECT_SOURCE_REGISTRATION_SCOPE,
        "company_id": str(decision.company_id),
        "question_version_id": None,
        "source_id": str(decision.source_id),
        # This opaque W1 value is stored in analysis_source_decisions.analysis_input_version.
        "registration_input_version": decision.analysis_input_version,
        # W2's integer transport versions use this decision revision, never the opaque string.
        "decision_version": decision.decision_version,
        "is_core": False,
        "decision_code": DIRECT_SOURCE_REGISTRATION_DECISION_CODE,
        "decision_owner": DIRECT_SOURCE_REGISTRATION_OWNER,
        "reason_code": decision.reason_code,
        "purpose": DIRECT_SOURCE_REGISTRATION_PURPOSE,
    }


class DirectSourceRegistrationService:
    """Create the direct-registration ledger records under W1 authority."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def accept(
        self,
        *,
        owner_user_id: UUID,
        company_id: UUID,
        source_id: UUID,
        idempotency_key: str,
        request_hash: str,
    ) -> DirectSourceRegistrationAcceptance:
        source = self.session.scalar(
            select(Source)
            .where(Source.id == source_id, Source.company_id == company_id)
            .with_for_update()
        )
        if source is None:
            raise DirectSourceRegistrationError("DIRECT_SOURCE_REGISTRATION_SOURCE_NOT_FOUND")

        job_service = JobService(self.session)
        next_version = int(
            self.session.scalar(
                select(func.coalesce(func.max(AnalysisSourceDecision.decision_version), 0)).where(
                    AnalysisSourceDecision.decision_scope == DIRECT_SOURCE_REGISTRATION_SCOPE,
                    AnalysisSourceDecision.company_id == company_id,
                    AnalysisSourceDecision.source_id == source_id,
                )
            )
            or 0
        ) + 1
        registration_input_version = f"direct:{source_id}:{next_version}"
        accepted = job_service.accept_job(
            owner_user_id=owner_user_id,
            job_type=DIRECT_SOURCE_REGISTRATION_JOB_TYPE,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            analysis_input_version=registration_input_version,
            path_scope="/internal/source-registrations",
        )
        if accepted.command is None:
            raise DirectSourceRegistrationError("DIRECT_SOURCE_REGISTRATION_COMMAND_MISSING")

        if accepted.replayed:
            return self._replayed_acceptance(accepted)

        decision = AnalysisSourceDecision(
            decision_scope=DIRECT_SOURCE_REGISTRATION_SCOPE,
            company_id=company_id,
            question_version_id=None,
            source_id=source_id,
            source_version_id=None,
            analysis_input_version=registration_input_version,
            decision_version=next_version,
            decision_code=DIRECT_SOURCE_REGISTRATION_DECISION_CODE,
            decision_owner=DIRECT_SOURCE_REGISTRATION_OWNER,
            reason_code=DIRECT_SOURCE_REGISTRATION_REASON_CODE,
        )
        self.session.add(decision)
        self.session.flush()
        source_link = JobSourceLink(
            job_id=accepted.job.id,
            owner_user_id=owner_user_id,
            source_id=source_id,
            source_version_id=None,
            command_id=None,
            purpose_ref=DIRECT_SOURCE_REGISTRATION_PURPOSE,
            analysis_input_version=registration_input_version,
        )
        self.session.add(source_link)
        accepted.command.analysis_source_decision_id = decision.id
        accepted.command.payload = {
            "command_type": "EXECUTE_JOB",
            "dispatch_kind": DIRECT_SOURCE_REGISTRATION_SCOPE,
            "direct_source_registration_pin": direct_source_registration_pin(decision=decision),
        }
        self.session.flush()
        return DirectSourceRegistrationAcceptance(
            job=accepted.job,
            command=accepted.command,
            decision=decision,
            source_link=source_link,
            replayed=False,
        )

    def _replayed_acceptance(
        self, accepted: JobAcceptance
    ) -> DirectSourceRegistrationAcceptance:
        command = accepted.command
        if command is None or command.analysis_source_decision_id is None:
            raise DirectSourceRegistrationError("DIRECT_SOURCE_REGISTRATION_REPLAY_MISMATCH")
        decision = self.session.get(AnalysisSourceDecision, command.analysis_source_decision_id)
        source_link = self.session.scalar(
            select(JobSourceLink)
            .where(
                JobSourceLink.job_id == accepted.job.id,
                JobSourceLink.owner_user_id == accepted.job.owner_user_id,
                JobSourceLink.command_id.is_(None),
                JobSourceLink.purpose_ref == DIRECT_SOURCE_REGISTRATION_PURPOSE,
            )
            .order_by(JobSourceLink.created_at, JobSourceLink.id)
            .limit(1)
        )
        if decision is None or source_link is None:
            raise DirectSourceRegistrationError("DIRECT_SOURCE_REGISTRATION_REPLAY_MISMATCH")
        return DirectSourceRegistrationAcceptance(
            job=accepted.job,
            command=command,
            decision=decision,
            source_link=source_link,
            replayed=True,
        )
