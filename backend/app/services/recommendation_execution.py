from __future__ import annotations

from typing import Protocol
from uuid import UUID

from sqlalchemy.orm import Session

from app.repo.recommendations import RecommendationRepository
from app.services.recommendations import (
    RecommendationNotFoundError,
    RecommendationService,
    RecommendationValidationError,
)


class RecommendationExecutionPort(Protocol):
    """Asynchronous execution boundary owned by a future W4 adapter."""

    def execute(self, *, owner_user_id: UUID, run_id: UUID) -> None:
        """Persist a completed result for an already-accepted Run."""


class SyntheticRecommendationAdapter:
    """Deterministic development/test executor; never invoked by HTTP routes.

    It only uses snapshot-pinned owner Experience versions and writes the
    intentionally limited Candidate summary read model.  It does not know any
    W3 ACK, W4 payload, source, or egress contract.
    """

    RESULT_VERSION = "synthetic-v1"

    def __init__(self, session: Session) -> None:
        self.session = session
        self.repository = RecommendationRepository(session)
        self.service = RecommendationService(session)

    def execute(self, *, owner_user_id: UUID, run_id: UUID) -> None:
        run = self.repository.get_run_for_update(run_id=run_id, owner_user_id=owner_user_id)
        if run is None:
            raise RecommendationNotFoundError("recommendation run does not exist for this owner")
        if run.result_origin != "SYNTHETIC":
            raise RecommendationValidationError("synthetic executor cannot run an engine result")
        if run.status != "PENDING" or run.result_status != "PENDING":
            raise RecommendationValidationError("recommendation run has already been executed")

        episode_version_ids = self.repository.list_snapshot_episode_version_ids(
            snapshot_id=run.snapshot_id,
            owner_user_id=owner_user_id,
        )
        episode_versions = self.repository.get_episode_versions(
            episode_version_ids=episode_version_ids,
            owner_user_id=owner_user_id,
        )
        if len(episode_versions) != len(episode_version_ids):
            raise RecommendationValidationError("snapshot episode version is unavailable")

        run.status = "RUNNING"
        self.session.flush()
        for rank, episode_version in enumerate(
            episode_versions[: run.requested_candidate_limit], start=1
        ):
            self.service.record_candidate(
                owner_user_id=owner_user_id,
                run_id=run.id,
                episode_version_id=episode_version.id,
                match_status="DIRECT_MATCH",
                short_reason="합성 검증용 후보입니다. 실제 Engine 추천 결과가 아닙니다.",
                strength_summary=None,
                limitation_summary=None,
                validation_status="PASSED",
                result_version=self.RESULT_VERSION,
                internal_rank=rank,
            )
        self.service.complete_synthetic_run(
            owner_user_id=owner_user_id,
            run_id=run.id,
            limited=run.limited_analysis,
        )
