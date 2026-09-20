"""Prepare one isolated W1-owned ENGINE Run for the T104 AWS acceptance."""

from __future__ import annotations

import hashlib
import json
import os
import sys
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import settings  # noqa: E402
from app.models.deletion import DeletionRequest  # noqa: F401, E402
from app.models.identity import User  # noqa: E402
from app.models.jobs import OutboxMessage  # noqa: E402
from app.models.recommendation_execution import RecommendationExecutionBinding  # noqa: E402
from app.models.recommendations import RecommendationRun  # noqa: E402
from app.models.sources import Source, SourceVersion  # noqa: E402
from app.services.application_workspace import ApplicationWorkspaceService  # noqa: E402
from app.services.experience import ExperienceService  # noqa: E402
from app.services.recommendations import RecommendationService  # noqa: E402


class T104PreparationError(RuntimeError):
    """Safe refusal reason for a non-isolated or incorrectly configured run."""


@dataclass(frozen=True, slots=True)
class T104Configuration:
    seed_database_url: str
    database_name: str
    run_id: str


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise T104PreparationError(f"{name} must be set")
    return value


def _database_identity(value: str) -> tuple[str | None, int | None, str | None]:
    url: URL = make_url(value)
    return url.host, url.port, url.database


def _configuration() -> T104Configuration:
    if os.environ.get("W1_W4_T104_PREPARE_SYNTHETIC") != "YES":
        raise T104PreparationError(
            "set W1_W4_T104_PREPARE_SYNTHETIC=YES to permit fixture creation"
        )
    if settings.w1_recommendation_execution_mode != "ENGINE":
        raise T104PreparationError("T104 requires W1_RECOMMENDATION_EXECUTION_MODE=ENGINE")
    if settings.w4_recommendation_real_data_enabled:
        raise T104PreparationError("REAL W4 recommendation data is not approved")
    seed_url = _required("T104_SEED_DATABASE_URL")
    worker_url = _required("WORKER_DATABASE_URL")
    seed_identity = _database_identity(seed_url)
    if seed_identity != _database_identity(worker_url):
        raise T104PreparationError("seed and worker URLs must target the same database")
    if make_url(seed_url).username == make_url(worker_url).username:
        raise T104PreparationError("seed and worker URLs must use different principals")
    database_name = seed_identity[2] or ""
    if not all(token in database_name.lower() for token in ("w1", "w4", "t104")):
        raise T104PreparationError("database name must contain w1, w4 and t104")
    run_id = _required("W1_W4_T104_RUN_ID")
    if "t104" not in run_id.lower():
        raise T104PreparationError("W1_W4_T104_RUN_ID must contain t104")
    return T104Configuration(seed_url, database_name, run_id)


def _assert_empty(session: Session) -> None:
    populated = [
        model.__tablename__
        for model in (User, RecommendationRun, RecommendationExecutionBinding, OutboxMessage)
        if session.scalar(select(func.count()).select_from(model))
    ]
    if populated:
        raise T104PreparationError("T104 database is not empty: " + ", ".join(sorted(populated)))


def prepare() -> dict[str, object]:
    config = _configuration()
    engine = create_engine(config.seed_database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory.begin() as session:
            _assert_empty(session)
            owner = User(
                display_name=f"T104 synthetic {config.run_id}",
                locale="ko-KR",
                timezone="Asia/Seoul",
            )
            session.add(owner)
            session.flush()

            workspace = ApplicationWorkspaceService(session)
            company = workspace.create_company(
                legal_name=f"EPICK T104 {config.run_id}",
                display_name="EPICK T104 Synthetic",
            )
            project = workspace.create_project(
                owner_user_id=owner.id,
                company_id=company.id,
                title="T104 W4 recommendation acceptance",
                role_name="Backend engineer",
            )
            question = workspace.create_question(
                owner_user_id=owner.id,
                project_id=project.id,
                display_order=0,
                prompt="합성 경험 중 지원 업무와 연결되는 행동을 설명해 주세요.",
                source="USER",
            )

            experience = ExperienceService(session)
            activity = experience.create_activity(
                owner_user_id=owner.id,
                title="T104 synthetic activity",
                original_narrative="합성 통합 검증 전용 활동입니다.",
            )
            experience.create_episode(
                owner_user_id=owner.id,
                activity_id=activity.id,
                title="T104 synthetic validation episode",
                original_narrative=(
                    "저는 합성 파일 검증 도구의 개발 담당자였습니다.\n"
                    "저는 JSON Schema 규칙을 학습했습니다.\n"
                    "저는 학습한 규칙을 업로드 검증에 적용했습니다.\n"
                    "잘못된 합성 파일 저장 건수가 8건에서 0건으로 줄었습니다."
                ),
            )

            now = datetime.now(UTC)
            source = Source(
                company_id=company.id,
                source_type="CAREERS",
                canonical_url=f"https://t104.invalid/{config.run_id}",
                canonical_url_hash=f"t104-{config.run_id}",
                url_normalization_version="v1",
                official_status="VERIFIED",
                access_policy="ALLOWED",
                storage_policy="FULL_CONTENT_ALLOWED",
                reuse_policy="CROSS_USER_ALLOWED",
                policy_version="t104-synthetic-v1",
                policy_checked_at=now,
                last_collection_status="SUCCEEDED",
            )
            session.add(source)
            session.flush()
            source_version = SourceVersion(
                source_id=source.id,
                company_id=company.id,
                version_no=1,
                collected_at=now,
                content_hash=hashlib.sha256(
                    f"epick:t104:synthetic:{config.run_id}".encode()
                ).hexdigest(),
                parser_version="t104-v1",
                content_normalization_version="t104-v1",
                extraction_status="SUCCEEDED",
                access_policy_at_collection="ALLOWED",
                storage_policy_at_collection="FULL_CONTENT_ALLOWED",
                reuse_policy_at_collection="CROSS_USER_ALLOWED",
                policy_version_at_collection="t104-synthetic-v1",
                current_accuracy_status="VALID",
            )
            session.add(source_version)
            session.flush()
            source.current_version_id = source_version.id
            session.flush()

            run = RecommendationService(session).create_server_selected_recommendation_run(
                owner_user_id=owner.id,
                question_id=question.id,
                expected_question_version=1,
                expected_snapshot_no=0,
                requested_candidate_limit=3,
                include_excluded=False,
                allow_limited_analysis=True,
            )
            binding = session.scalar(
                select(RecommendationExecutionBinding).where(
                    RecommendationExecutionBinding.run_id == run.id
                )
            )
            outbox = session.scalar(
                select(OutboxMessage).where(OutboxMessage.recommendation_run_id == run.id)
            )
            if binding is None or outbox is None:
                raise T104PreparationError("ENGINE Run did not create binding and outbox state")
            result = {
                "status": "ready",
                "run_id": config.run_id,
                "database": config.database_name,
                "owner_id": str(owner.id),
                "recommendation_run_id": str(run.id),
                "binding_id": str(binding.id),
                "outbox_id": str(outbox.id),
                "result_origin": run.result_origin,
                "result_status": run.result_status,
                "real_data": "disabled",
            }
    finally:
        engine.dispose()
    return result


def main() -> None:
    print(json.dumps(prepare(), separators=(",", ":")))


if __name__ == "__main__":
    main()
