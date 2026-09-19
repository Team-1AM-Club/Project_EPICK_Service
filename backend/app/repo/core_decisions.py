from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.jobs import JobCoreDecisionBinding
from app.models.sources import AnalysisSourceDecision, JobSourceLink, Source


class CoreDecisionRepository:
    """Locked persistence primitives for W3 Core Decision acceptance.

    The service acquires the User and Job locks through ``JobRepository`` first.
    These methods preserve the remaining JobSourceLink/Source/current-binding
    order so acceptance races serialize with cancellation and deletion.
    """

    def __init__(self, session: Session) -> None:
        self.session = session

    def get_source_link_for_update(
        self,
        *,
        job_id: UUID,
        owner_user_id: UUID,
        source_id: UUID,
    ) -> JobSourceLink | None:
        return self.session.scalar(
            select(JobSourceLink)
            .where(
                JobSourceLink.job_id == job_id,
                JobSourceLink.owner_user_id == owner_user_id,
                JobSourceLink.source_id == source_id,
            )
            .with_for_update()
        )

    def get_source_for_update(self, *, source_id: UUID) -> Source | None:
        return self.session.scalar(
            select(Source).where(Source.id == source_id).with_for_update()
        )

    def get_binding_by_origin(
        self, *, origin_producer: str, origin_message_id: UUID
    ) -> JobCoreDecisionBinding | None:
        """Read an immutable binding after the owning Job row is locked.

        Binding rows are append-only, so runtime roles intentionally have no
        UPDATE privilege.  PostgreSQL treats ``SELECT ... FOR UPDATE`` as an
        UPDATE-capable operation even when no row is changed.  The caller's
        authoritative Job lock already serializes decisions for this Job.
        """
        return self.session.scalar(
            select(JobCoreDecisionBinding)
            .where(
                JobCoreDecisionBinding.origin_producer == origin_producer,
                JobCoreDecisionBinding.origin_message_id == origin_message_id,
            )
        )

    def get_binding_by_origin_decision(
        self, *, origin_producer: str, origin_decision_id: UUID
    ) -> JobCoreDecisionBinding | None:
        """Find a producer-scoped immutable decision identity under the Job lock."""

        return self.session.scalar(
            select(JobCoreDecisionBinding).where(
                JobCoreDecisionBinding.origin_producer == origin_producer,
                JobCoreDecisionBinding.origin_decision_id == origin_decision_id,
            )
        )

    def get_current_binding(
        self,
        *,
        job_id: UUID,
        source_id: UUID,
        analysis_input_version: str,
        origin_producer: str,
        decision_scope: str,
        question_version_id: UUID | None,
    ) -> JobCoreDecisionBinding | None:
        """Read immutable decision history under the caller-held Job lock."""
        return self.session.scalar(
            select(JobCoreDecisionBinding)
            .where(
                JobCoreDecisionBinding.job_id == job_id,
                JobCoreDecisionBinding.source_id == source_id,
                JobCoreDecisionBinding.analysis_input_version == analysis_input_version,
                JobCoreDecisionBinding.origin_producer == origin_producer,
                JobCoreDecisionBinding.decision_scope == decision_scope,
                JobCoreDecisionBinding.question_version_id == question_version_id,
            )
            .order_by(JobCoreDecisionBinding.decision_version.desc())
            .limit(1)
        )

    def get_binding_for_job_decision(
        self,
        *,
        job_id: UUID,
        analysis_source_decision_id: UUID,
        for_update: bool = False,
    ) -> JobCoreDecisionBinding | None:
        statement = select(JobCoreDecisionBinding).where(
            JobCoreDecisionBinding.job_id == job_id,
            JobCoreDecisionBinding.analysis_source_decision_id
            == analysis_source_decision_id,
        )
        if for_update:
            statement = statement.with_for_update()
        return self.session.scalar(statement)

    def list_core_bindings_for_job(
        self,
        *,
        job_id: UUID,
        analysis_input_version: str,
    ) -> list[JobCoreDecisionBinding]:
        """Return immutable Core history after the owning Job row is locked."""

        return list(
            self.session.scalars(
                select(JobCoreDecisionBinding)
                .where(
                    JobCoreDecisionBinding.job_id == job_id,
                    JobCoreDecisionBinding.analysis_input_version
                    == analysis_input_version,
                    JobCoreDecisionBinding.decision_code == "CORE_REQUIRED",
                )
                .order_by(
                    JobCoreDecisionBinding.decision_version.desc(),
                    JobCoreDecisionBinding.created_at.desc(),
                    JobCoreDecisionBinding.id.desc(),
                )
            )
        )

    def get_decision(self, *, decision_id: UUID) -> AnalysisSourceDecision | None:
        return self.session.get(AnalysisSourceDecision, decision_id)

    def add_decision(self, decision: AnalysisSourceDecision) -> None:
        self.session.add(decision)

    def add_binding(self, binding: JobCoreDecisionBinding) -> None:
        self.session.add(binding)
