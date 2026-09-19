"""Prepare one W1-issued context for the actual W4/W1 joint CT-12 run.

This command is intentionally bounded: it accepts only a disposable W1/W4
CT-12 database, refuses a populated evidence database, creates one synthetic
WAITING_USER fixture, and issues one opaque W4 context. It does not send an
SQS message or create a W2 command.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

from sqlalchemy import create_engine, func, select
from sqlalchemy.engine import URL, make_url
from sqlalchemy.orm import Session, sessionmaker

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.models.identity import User  # noqa: E402
from app.models.jobs import (  # noqa: E402
    InboxReceipt,
    Job,
    JobCommand,
    JobCoreDecisionBinding,
    OutboxMessage,
)
from app.models.sources import AnalysisSourceDecision  # noqa: E402
from app.models.w4_question_core import W4QuestionCoreContext  # noqa: E402
from app.runtime.w4_question_core_context import issue_w4_question_core_context  # noqa: E402
from scripts.run_w1_w4_ct12_synthetic import _seed_waiting_question_job  # noqa: E402


class W1W4Ct12JointPreparationError(RuntimeError):
    """A safe explanation for refusing an unsafe joint-run preparation."""


@dataclass(frozen=True, slots=True)
class JointPreparationConfiguration:
    seed_database_url: str
    database_name: str
    run_id: str
    context_ttl_seconds: int


def _value(name: str) -> str | None:
    value = os.environ.get(name)
    return value.strip() if value else None


def _required(name: str) -> str:
    value = _value(name)
    if not value:
        raise W1W4Ct12JointPreparationError(f"{name} must be set")
    return value


def _database_identity(value: str) -> tuple[str | None, int | None, str | None]:
    url: URL = make_url(value)
    return url.host, url.port, url.database


def _require_configuration() -> JointPreparationConfiguration:
    if _value("W1_W4_CT12_PREPARE_ACTUAL") != "YES":
        raise W1W4Ct12JointPreparationError(
            "set W1_W4_CT12_PREPARE_ACTUAL=YES to permit joint CT-12 preparation"
        )
    seed_database_url = _required("W1_W4_CT12_SEED_DATABASE_URL")
    worker_database_url = _required("WORKER_DATABASE_URL")
    seed_identity = _database_identity(seed_database_url)
    if seed_identity != _database_identity(worker_database_url):
        raise W1W4Ct12JointPreparationError(
            "W1_W4_CT12_SEED_DATABASE_URL and WORKER_DATABASE_URL must target the same database"
        )
    if make_url(seed_database_url).username == make_url(worker_database_url).username:
        raise W1W4Ct12JointPreparationError(
            "CT-12 seed and worker database URLs must use different database principals"
        )
    database_name = seed_identity[2] or ""
    lowered_name = database_name.lower()
    if not all(token in lowered_name for token in ("w1", "w4", "ct12")):
        raise W1W4Ct12JointPreparationError(
            "joint CT-12 requires a dedicated database containing w1, w4 and ct12 in its name"
        )
    run_id = _required("W1_W4_CT12_RUN_ID")
    if "ct12" not in run_id.lower():
        raise W1W4Ct12JointPreparationError("W1_W4_CT12_RUN_ID must contain ct12")
    raw_ttl = _value("W1_W4_CT12_CONTEXT_TTL_SECONDS") or "3600"
    try:
        context_ttl_seconds = int(raw_ttl)
    except ValueError as error:
        raise W1W4Ct12JointPreparationError(
            "W1_W4_CT12_CONTEXT_TTL_SECONDS must be an integer"
        ) from error
    if not 300 <= context_ttl_seconds <= 86400:
        raise W1W4Ct12JointPreparationError(
            "W1_W4_CT12_CONTEXT_TTL_SECONDS must be between 300 and 86400"
        )
    return JointPreparationConfiguration(
        seed_database_url=seed_database_url,
        database_name=database_name,
        run_id=run_id,
        context_ttl_seconds=context_ttl_seconds,
    )


def _assert_empty_database(session: Session) -> None:
    populated = [
        model.__tablename__
        for model in (
            User,
            InboxReceipt,
            AnalysisSourceDecision,
            JobCoreDecisionBinding,
            JobCommand,
            OutboxMessage,
            W4QuestionCoreContext,
        )
        if session.scalar(select(func.count()).select_from(model))
    ]
    if populated:
        raise W1W4Ct12JointPreparationError(
            "joint CT-12 database is not empty: " + ", ".join(sorted(populated))
        )


def prepare() -> dict[str, object]:
    config = _require_configuration()
    engine = create_engine(config.seed_database_url, pool_pre_ping=True)
    factory = sessionmaker(bind=engine, expire_on_commit=False)
    try:
        with factory.begin() as session:
            _assert_empty_database(session)
            seed = _seed_waiting_question_job(
                session=session,
                run_id=config.run_id,
                scenario="actual-w4",
            )
            job = session.get(Job, seed.job_id)
            if job is None:
                raise W1W4Ct12JointPreparationError("joint CT-12 seeded job is unavailable")
            context = issue_w4_question_core_context(
                session=session,
                job=job,
                question_version_id=seed.question_version_id,
                source_id=seed.source_id,
                valid_for=timedelta(seconds=config.context_ttl_seconds),
            )
            context_key = str(context.context_key)
    finally:
        engine.dispose()
    return {
        "status": "ready",
        "run_id": config.run_id,
        "database": config.database_name,
        "context_key": context_key,
        "plan": {
            "submission_key": f"{config.run_id}-actual-w4-v1",
            "decision_version": 1,
            "decision_code": "CORE_REQUIRED",
            "reason_code": "QUESTION_EVIDENCE_REQUIRED",
            "policy_revision": "ct12-synthetic-v1",
        },
        "sqs_messages_sent": 0,
        "w2_commands_created": 0,
    }


def main() -> None:
    print(json.dumps(prepare(), separators=(",", ":")))


if __name__ == "__main__":
    main()
