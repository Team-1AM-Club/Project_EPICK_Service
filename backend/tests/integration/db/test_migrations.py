from __future__ import annotations

import re
from pathlib import Path
from urllib.parse import urlsplit, urlunsplit
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError, OperationalError

from app.core.config import settings

BACKEND_ROOT = Path(__file__).parents[3]
DATABASE_NAME_PATTERN = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
RUNTIME_ROLE_TEMPLATE_SQL = BACKEND_ROOT / "infra" / "postgres" / "runtime_roles.sql"


def _admin_database_url(database_url: str) -> tuple[str, str]:
    parsed = urlsplit(database_url)
    database_name = parsed.path.removeprefix("/")
    if not DATABASE_NAME_PATTERN.fullmatch(database_name):
        raise ValueError("test database name must be a simple PostgreSQL identifier")

    return urlunsplit((parsed.scheme, parsed.netloc, "/postgres", "", "")), database_name


@pytest.fixture(scope="session")
def alembic_config() -> Config:
    admin_url, database_name = _admin_database_url(settings.test_database_url)
    admin_engine = create_engine(admin_url, isolation_level="AUTOCOMMIT")
    try:
        with admin_engine.connect() as connection:
            exists = connection.execute(
                text("SELECT 1 FROM pg_database WHERE datname = :database_name"),
                {"database_name": database_name},
            ).scalar()
            if exists is None:
                connection.execute(text(f'CREATE DATABASE "{database_name}"'))
    finally:
        admin_engine.dispose()

    role_engine = create_engine(settings.test_database_url)
    try:
        with role_engine.begin() as connection:
            connection.execute(text(RUNTIME_ROLE_TEMPLATE_SQL.read_text(encoding="utf-8")))
    finally:
        role_engine.dispose()

    config = Config(str(BACKEND_ROOT / "alembic.ini"))
    config.set_main_option("sqlalchemy.url", settings.test_database_url)
    return config


@pytest.mark.postgres
def test_blank_database_upgrades_downgrades_and_reupgrades(alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "head")


@pytest.mark.postgres
def test_orm_table_and_column_inventory_matches_the_migration_head(
    alembic_config: Config,
) -> None:
    """Keep Alembic's ORM-owned inventory check executable in CI.

    Relational constraints, partial indexes, RLS policies, and triggers are
    hand-authored PostgreSQL contracts and have dedicated integration tests.
    """

    command.upgrade(alembic_config, "head")
    command.check(alembic_config)


@pytest.mark.postgres
def test_existing_pre_completion_database_upgrades_forward_to_the_current_head(
    alembic_config: Config,
) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "002_create_experience_repository")
    command.upgrade(alembic_config, "head")

    engine = create_engine(settings.test_database_url)
    try:
        columns = {column["name"] for column in inspect(engine).get_columns("users")}
    finally:
        engine.dispose()

    assert "deletion_epoch" in columns


@pytest.mark.postgres
def test_existing_pg1_database_upgrades_forward_to_snapshot_head(alembic_config: Config) -> None:
    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "004_app_workspace_jobs")
    existing_owner_id = uuid4()
    pre_upgrade_engine = create_engine(settings.test_database_url)
    try:
        with pre_upgrade_engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO users (id, display_name, locale, timezone) "
                    "VALUES (:id, :display_name, :locale, :timezone)"
                ),
                {
                    "id": existing_owner_id,
                    "display_name": "Existing PG-1 owner",
                    "locale": "ko-KR",
                    "timezone": "Asia/Seoul",
                },
            )
    finally:
        pre_upgrade_engine.dispose()
    command.upgrade(alembic_config, "head")

    engine = create_engine(settings.test_database_url)
    try:
        inspector = inspect(engine)
        snapshot_columns = {column["name"] for column in inspector.get_columns("project_snapshots")}
        input_columns = {column["name"] for column in inspector.get_columns("job_input_refs")}
        with engine.connect() as connection:
            retained_owner_id = connection.execute(
                text("SELECT id FROM users WHERE id = :id"), {"id": existing_owner_id}
            ).scalar_one_or_none()
    finally:
        engine.dispose()

    assert {"project_version_id", "snapshot_no", "status"} <= snapshot_columns
    assert "snapshot_id" in input_columns
    assert retained_owner_id == existing_owner_id


@pytest.mark.postgres
def test_existing_claim_history_upgrades_without_mutating_append_only_rows(
    alembic_config: Config,
) -> None:
    """A populated revision-017 claim ledger must reach the current head.

    This specifically guards the compatibility defaults in revision 018.  A
    blank upgrade cannot prove this path because claim_versions is append-only
    before the new provenance fields are introduced.
    """

    command.downgrade(alembic_config, "base")
    command.upgrade(alembic_config, "017_deletion_orchestration")

    company_id = uuid4()
    source_id = uuid4()
    source_version_id = uuid4()
    evidence_span_id = uuid4()
    claim_id = uuid4()
    claim_version_id = uuid4()
    engine = create_engine(settings.test_database_url)
    try:
        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO companies (id, legal_name, display_name) "
                    "VALUES (:id, 'Legacy Company', 'Legacy Company')"
                ),
                {"id": company_id},
            )
            connection.execute(
                text(
                    "INSERT INTO sources ("
                    "id, company_id, source_type, canonical_url, canonical_url_hash, "
                    "url_normalization_version, policy_version, policy_checked_at"
                    ") VALUES ("
                    ":id, :company_id, 'CAREERS', 'https://legacy.example.test/jobs', "
                    "'legacy-url-hash', 'v1', 'policy-v1', now()"
                    ")"
                ),
                {"id": source_id, "company_id": company_id},
            )
            connection.execute(
                text(
                    "INSERT INTO source_versions ("
                    "id, source_id, company_id, version_no, collected_at, content_hash, "
                    "parser_version, content_normalization_version, extraction_status, "
                    "access_policy_at_collection, storage_policy_at_collection, "
                    "reuse_policy_at_collection, policy_version_at_collection"
                    ") VALUES ("
                    ":id, :source_id, :company_id, 1, now(), 'legacy-content-hash', "
                    "'parser-v1', 'normalization-v1', 'SUCCEEDED', 'ALLOWED', "
                    "'FULL_CONTENT_ALLOWED', 'CROSS_USER_ALLOWED', 'policy-v1'"
                    ")"
                ),
                {
                    "id": source_version_id,
                    "source_id": source_id,
                    "company_id": company_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO evidence_spans ("
                    "id, source_version_id, excerpt, locator_type, locator, chunk_order, "
                    "excerpt_hash"
                    ") VALUES ("
                    ":id, :source_version_id, 'Legacy official evidence', 'LINE_RANGE', "
                    "'1-1', 0, 'legacy-excerpt-hash'"
                    ")"
                ),
                {"id": evidence_span_id, "source_version_id": source_version_id},
            )
            connection.execute(
                text("INSERT INTO claims (id, company_id) VALUES (:id, :company_id)"),
                {"id": claim_id, "company_id": company_id},
            )
            connection.execute(
                text(
                    "INSERT INTO claim_versions ("
                    "id, claim_id, company_id, version_no, claim_type, subject_key, statement"
                    ") VALUES ("
                    ":id, :claim_id, :company_id, 1, 'CULTURE', 'engineering', "
                    "'Engineering quality is emphasized.'"
                    ")"
                ),
                {
                    "id": claim_version_id,
                    "claim_id": claim_id,
                    "company_id": company_id,
                },
            )
            connection.execute(
                text(
                    "INSERT INTO claim_evidence_links ("
                    "claim_version_id, evidence_span_id, relation_type"
                    ") VALUES (:claim_version_id, :evidence_span_id, 'SUPPORTS')"
                ),
                {
                    "claim_version_id": claim_version_id,
                    "evidence_span_id": evidence_span_id,
                },
            )

        command.upgrade(alembic_config, "head")

        with engine.connect() as connection:
            legacy_claim = (
                connection.execute(
                    text(
                        "SELECT predicate, extractor_version, extracted_at "
                        "FROM claim_versions WHERE id = :id"
                    ),
                    {"id": claim_version_id},
                )
                .mappings()
                .one()
            )

        assert legacy_claim["predicate"] == "__LEGACY_UNSPECIFIED__"
        assert legacy_claim["extractor_version"] == "legacy-pre-018"
        assert legacy_claim["extracted_at"] is not None

        with engine.begin() as connection:
            connection.execute(
                text(
                    "INSERT INTO claim_evidence_links ("
                    "claim_version_id, evidence_span_id, relation_type"
                    ") VALUES (:claim_version_id, :evidence_span_id, 'CONTEXT')"
                ),
                {
                    "claim_version_id": claim_version_id,
                    "evidence_span_id": evidence_span_id,
                },
            )

        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                with pytest.raises(IntegrityError):
                    connection.execute(
                        text(
                            "INSERT INTO claim_evidence_links ("
                            "claim_version_id, evidence_span_id, relation_type"
                            ") VALUES (:claim_version_id, :evidence_span_id, 'SUPPORTS')"
                        ),
                        {
                            "claim_version_id": claim_version_id,
                            "evidence_span_id": evidence_span_id,
                        },
                    )
            finally:
                transaction.rollback()

        with engine.connect() as connection:
            transaction = connection.begin()
            try:
                with pytest.raises(OperationalError):
                    connection.execute(
                        text("UPDATE claim_versions SET subject_text = 'rewritten' WHERE id = :id"),
                        {"id": claim_version_id},
                    )
            finally:
                transaction.rollback()
    finally:
        engine.dispose()
