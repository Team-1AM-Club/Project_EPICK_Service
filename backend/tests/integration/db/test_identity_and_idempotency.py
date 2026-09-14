from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import Engine, inspect, text
from sqlalchemy.exc import DBAPIError, IntegrityError
from sqlalchemy.orm import Session

from app.db.session import set_local_owner_context
from app.models.identity import AuthIdentity, AuthSession, User
from app.repo.identity import IdentityRepository
from app.services.idempotency import IdempotencyConflictError, IdempotencyService
from app.services.identity import IdentityService

RUNTIME_ROLE_SQL = Path(__file__).parent / "sql" / "runtime_roles.sql"
RUNTIME_ROLE = "epick_runtime"


@pytest.fixture(autouse=True)
def clean_identity_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "TRUNCATE experience_field_provenance, episode_version_skills, "
                "episode_versions, episodes, activity_versions, activities, "
                "idempotency_records, auth_sessions, auth_identities, users"
            )
        )
    yield


def test_identity_constraints_store_only_refresh_hash_and_reject_duplicate_subject(
    db_session: Session,
) -> None:
    user = User(display_name="Synthetic User", locale="ko-KR", timezone="Asia/Seoul")
    identity = AuthIdentity(
        user=user,
        provider="OIDC",
        provider_subject="provider-subject-1",
        provider_email="user@example.test",
        provider_email_verified=True,
    )
    session_record = AuthSession(
        user=user,
        auth_identity=identity,
        refresh_token_hash="a" * 64,
        token_family_id=uuid4(),
        issued_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=1),
    )
    db_session.add_all([user, identity, session_record])
    db_session.commit()

    column_names = {
        column["name"] for column in inspect(db_session.bind).get_columns("auth_sessions")
    }
    assert "refresh_token_hash" in column_names
    assert "refresh_token" not in column_names

    duplicate = AuthIdentity(
        user_id=user.id,
        provider="OIDC",
        provider_subject="provider-subject-1",
        provider_email_verified=False,
    )
    db_session.add(duplicate)
    with pytest.raises(IntegrityError):
        db_session.commit()
    db_session.rollback()


def test_user_deletion_epoch_defaults_to_zero_and_rejects_negative_values(
    db_session: Session,
) -> None:
    user = User(display_name="Deletion epoch", locale="ko-KR", timezone="Asia/Seoul")
    db_session.add(user)
    db_session.commit()
    db_session.refresh(user)

    assert user.deletion_epoch == 0

    with pytest.raises(IntegrityError):
        db_session.execute(
            text("UPDATE users SET deletion_epoch = -1 WHERE id = :user_id"),
            {"user_id": user.id},
        )
        db_session.commit()
    db_session.rollback()


def test_identity_tables_enable_and_force_owner_row_level_security(migrated_engine: Engine) -> None:
    private_tables = {"users", "auth_identities", "auth_sessions", "idempotency_records"}
    with migrated_engine.connect() as connection:
        rls_rows = (
            connection.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE relname = ANY(:table_names)"
                ),
                {"table_names": list(private_tables)},
            )
            .mappings()
            .all()
        )
        policy_rows = (
            connection.execute(
                text("SELECT tablename FROM pg_policies WHERE tablename = ANY(:table_names)"),
                {"table_names": list(private_tables)},
            )
            .mappings()
            .all()
        )

    assert {row["relname"] for row in rls_rows} == private_tables
    assert all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in rls_rows)
    assert {row["tablename"] for row in policy_rows} == private_tables


def test_unverified_email_does_not_auto_link_another_user(db_session: Session) -> None:
    repository = IdentityRepository(db_session)
    service = IdentityService(repository)
    existing_user = User(
        display_name="Existing",
        email="shared@example.test",
        locale="ko-KR",
        timezone="Asia/Seoul",
    )
    db_session.add(existing_user)
    db_session.commit()

    created_identity = service.register_provider_identity(
        display_name="New identity",
        provider="OIDC",
        provider_subject="provider-subject-2",
        provider_email="shared@example.test",
        provider_email_verified=False,
        locale="ko-KR",
        timezone="Asia/Seoul",
    )
    db_session.commit()

    assert created_identity.user_id != existing_user.id
    assert created_identity.user.email is None


def test_same_idempotency_key_replays_first_result_and_hash_mismatch_conflicts(
    db_session: Session,
) -> None:
    user = User(display_name="Idempotent", locale="ko-KR", timezone="Asia/Seoul")
    db_session.add(user)
    db_session.commit()

    service = IdempotencyService(IdentityRepository(db_session))
    first_record, replayed = service.reserve(
        owner_user_id=user.id,
        method="POST",
        path_scope="/internal/synthetic",
        idempotency_key="synthetic-key",
        request_hash="b" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )
    service.complete(first_record, response_status=202, response_ref="job:synthetic")
    db_session.commit()

    replay_record, replayed = service.reserve(
        owner_user_id=user.id,
        method="POST",
        path_scope="/internal/synthetic",
        idempotency_key="synthetic-key",
        request_hash="b" * 64,
        expires_at=datetime.now(UTC) + timedelta(hours=1),
    )

    assert replayed is True
    assert replay_record.id == first_record.id
    assert replay_record.response_status == 202
    assert replay_record.response_ref == "job:synthetic"

    with pytest.raises(IdempotencyConflictError):
        service.reserve(
            owner_user_id=user.id,
            method="POST",
            path_scope="/internal/synthetic",
            idempotency_key="synthetic-key",
            request_hash="c" * 64,
            expires_at=datetime.now(UTC) + timedelta(hours=1),
        )


def test_runtime_role_cannot_read_or_mutate_another_users_identity_rows(
    db_session: Session, migrated_engine: Engine
) -> None:
    user_a = User(display_name="A", locale="ko-KR", timezone="Asia/Seoul")
    user_b = User(display_name="B", locale="ko-KR", timezone="Asia/Seoul")
    db_session.add_all([user_a, user_b])
    db_session.commit()

    with migrated_engine.begin() as connection:
        connection.execute(text(RUNTIME_ROLE_SQL.read_text(encoding="utf-8")))
        connection.execute(text("GRANT SELECT, INSERT, UPDATE, DELETE ON users TO epick_runtime"))
        connection.execute(
            text("GRANT SELECT, INSERT, UPDATE, DELETE ON auth_identities TO epick_runtime")
        )
        connection.execute(
            text("GRANT SELECT, INSERT, UPDATE, DELETE ON auth_sessions TO epick_runtime")
        )
        connection.execute(
            text("GRANT SELECT, INSERT, UPDATE, DELETE ON idempotency_records TO epick_runtime")
        )

    runtime_session = Session(migrated_engine)
    try:
        with runtime_session.begin():
            runtime_session.execute(text(f"SET LOCAL ROLE {RUNTIME_ROLE}"))
            set_local_owner_context(runtime_session, user_a.id)
            visible_ids = runtime_session.execute(text("SELECT id FROM users")).scalars().all()
            update_count = runtime_session.execute(
                text("UPDATE users SET display_name = 'blocked' WHERE id = :user_id"),
                {"user_id": user_b.id},
            ).rowcount
            delete_count = runtime_session.execute(
                text("DELETE FROM users WHERE id = :user_id"), {"user_id": user_b.id}
            ).rowcount
            blocked_insert = (
                "INSERT INTO users (id, display_name, account_status, "
                "locale, timezone) "
                "VALUES (:id, 'blocked', 'ACTIVE', 'ko-KR', 'Asia/Seoul')"
            )
            with pytest.raises(DBAPIError):
                with runtime_session.begin_nested():
                    runtime_session.execute(
                        text(blocked_insert),
                        {"id": uuid4()},
                    )

            assert visible_ids == [user_a.id]
            assert update_count == 0
            assert delete_count == 0
    finally:
        runtime_session.close()
