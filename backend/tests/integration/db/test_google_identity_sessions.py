from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import select, text

from app.models.identity import AuthSession
from app.repo.identity import IdentityRepository
from app.services.auth_sessions import AuthSessionService
from app.services.identity import IdentityService


@pytest.mark.postgres
def test_google_subject_is_reused_without_email_merge(db_session) -> None:
    subject = f"subject-{uuid4()}"
    now = datetime.now(UTC)
    with db_session.begin():
        first = IdentityService(IdentityRepository(db_session)).get_or_create_google_identity(
            provider_subject=subject,
            display_name="First Name",
            provider_email="first@example.test",
            provider_email_verified=True,
            locale="ko-KR",
            timezone="Asia/Seoul",
            login_at=now,
        )
        db_session.flush()
        identity_id = first.id
        user_id = first.user_id

    with db_session.begin():
        second = IdentityService(IdentityRepository(db_session)).get_or_create_google_identity(
            provider_subject=subject,
            display_name="Updated Name",
            provider_email="updated@example.test",
            provider_email_verified=True,
            locale="ko-KR",
            timezone="Asia/Seoul",
            login_at=now + timedelta(minutes=1),
        )
        assert second.id == identity_id
        assert second.user_id == user_id


@pytest.mark.postgres
def test_provider_subject_and_refresh_lookup_policies_are_narrow(migrated_engine) -> None:
    owner_one = uuid4()
    owner_two = uuid4()
    identity_one = uuid4()
    identity_two = uuid4()
    session_one = uuid4()
    session_two = uuid4()
    family = uuid4()
    family_two = uuid4()
    subject_one = f"subject-{uuid4()}"
    subject_two = f"subject-{uuid4()}"
    refresh_one = f"hash-{uuid4()}"
    refresh_two = f"hash-{uuid4()}"
    now = datetime.now(UTC)
    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, display_name, email, locale, timezone) VALUES "
                "(:one, 'Owner One', 'same@example.test', 'ko-KR', 'Asia/Seoul'), "
                "(:two, 'Owner Two', 'same@example.test', 'ko-KR', 'Asia/Seoul')"
            ),
            {"one": owner_one, "two": owner_two},
        )
        connection.execute(
            text(
                "INSERT INTO auth_identities "
                "(id, user_id, provider, provider_subject, provider_email, "
                "provider_email_verified) "
                "VALUES (:id1, :one, 'google', :subject1, 'same@example.test', true), "
                "(:id2, :two, 'google', :subject2, 'same@example.test', true)"
            ),
            {
                "id1": identity_one,
                "one": owner_one,
                "subject1": subject_one,
                "id2": identity_two,
                "two": owner_two,
                "subject2": subject_two,
            },
        )
        connection.execute(
            text(
                "INSERT INTO auth_sessions "
                "(id, user_id, auth_identity_id, refresh_token_hash, token_family_id, "
                "issued_at, expires_at) VALUES "
                "(:s1, :one, :i1, :hash1, :family, :now, :expires), "
                "(:s2, :two, :i2, :hash2, :family2, :now, :expires)"
            ),
            {
                "s1": session_one,
                "one": owner_one,
                "i1": identity_one,
                "hash1": refresh_one,
                "family": family,
                "s2": session_two,
                "two": owner_two,
                "i2": identity_two,
                "hash2": refresh_two,
                "family2": family_two,
                "now": now,
                "expires": now + timedelta(hours=1),
            },
        )
        privilege_sql = (
            Path(__file__).parents[3] / "infra" / "postgres" / "runtime_privileges.sql"
        ).read_text(encoding="utf-8")
        connection.execute(text(privilege_sql))

    with migrated_engine.begin() as connection:
        connection.execute(text("SET LOCAL ROLE epick_runtime"))
        assert connection.execute(text("SELECT count(*) FROM auth_identities")).scalar_one() == 0
        connection.execute(
            text("SELECT set_config('app.auth_provider_subject', :subject, true)"),
            {"subject": f"google:{subject_one}"},
        )
        rows = connection.execute(
            text("SELECT user_id, provider_email FROM auth_identities FOR UPDATE")
        ).all()
        assert rows == [(owner_one, "same@example.test")]
        connection.execute(
            text("SELECT set_config('app.auth_refresh_hash', :refresh_hash, true)"),
            {"refresh_hash": refresh_two},
        )
        sessions = connection.execute(
            text("SELECT id, token_family_id FROM auth_sessions FOR UPDATE")
        ).all()
        assert sessions == [(session_two, family_two)]


@pytest.mark.postgres
def test_auth_session_lineage_columns_support_rotation(migrated_engine) -> None:
    with migrated_engine.connect() as connection:
        columns = {
            row.column_name
            for row in connection.execute(
                text(
                    "SELECT column_name FROM information_schema.columns "
                    "WHERE table_schema='public' AND table_name='auth_sessions'"
                )
            )
        }
    assert {
        "refresh_token_hash",
        "token_family_id",
        "rotated_at",
        "revoked_at",
        "replaced_by_session_id",
    } <= columns


@pytest.mark.postgres
def test_refresh_rotation_inserts_replacement_before_linking_lineage(db_session) -> None:
    now = datetime.now(UTC)
    subject = f"rotation-subject-{uuid4()}"

    with db_session.begin():
        repository = IdentityRepository(db_session)
        identity = IdentityService(repository).get_or_create_google_identity(
            provider_subject=subject,
            display_name="Rotation User",
            provider_email="rotation@example.test",
            provider_email_verified=True,
            locale="ko-KR",
            timezone="Asia/Seoul",
            login_at=now,
        )
        service = AuthSessionService(
            repository,
            signing_key="integration-signing-key-with-at-least-32-bytes",
            refresh_pepper="integration-refresh-pepper-with-at-least-32-bytes",
            issuer="https://api.example.test",
            audience="epick-public-api",
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
            now=lambda: now,
        )
        issued = service.issue(identity)

    with db_session.begin():
        repository = IdentityRepository(db_session)
        service = AuthSessionService(
            repository,
            signing_key="integration-signing-key-with-at-least-32-bytes",
            refresh_pepper="integration-refresh-pepper-with-at-least-32-bytes",
            issuer="https://api.example.test",
            audience="epick-public-api",
            access_ttl_seconds=900,
            refresh_ttl_seconds=3600,
            now=lambda: now + timedelta(seconds=1),
        )
        service.rotate(issued.refresh_token)

    sessions = list(
        db_session.scalars(
            select(AuthSession)
            .where(AuthSession.user_id == identity.user_id)
            .order_by(AuthSession.issued_at)
        )
    )
    assert len(sessions) == 2
    assert sessions[0].replaced_by_session_id == sessions[1].id
    assert sessions[0].rotated_at == now + timedelta(seconds=1)
