from __future__ import annotations

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.api.errors import AuthenticationRequiredError
from app.models.identity import AuthIdentity, AuthSession, User
from app.services.auth_sessions import AuthSessionService


class FakeAuthRepository:
    def __init__(self, user: User, identity: AuthIdentity) -> None:
        self.user = user
        self.identity = identity
        self.sessions: list[AuthSession] = []
        self.flushed_session_ids = []

    def add_auth_session(self, auth_session: AuthSession) -> None:
        self.sessions.append(auth_session)

    def flush_auth_session(self, auth_session: AuthSession) -> None:
        assert all(
            item.replaced_by_session_id is None
            for item in self.sessions
            if item.id != auth_session.id
        )
        self.flushed_session_ids.append(auth_session.id)

    def find_refresh_session_for_update(self, refresh_hash: str) -> AuthSession | None:
        return next(
            (item for item in self.sessions if item.refresh_token_hash == refresh_hash), None
        )

    def list_token_family_for_update(self, family_id):
        return [item for item in self.sessions if item.token_family_id == family_id]

    def get_user(self, user_id):
        return self.user if self.user.id == user_id else None

    def get_auth_session(self, session_id, user_id):
        return next(
            (item for item in self.sessions if item.id == session_id and item.user_id == user_id),
            None,
        )


def _fixture(now: datetime):
    user = User(
        id=uuid4(),
        display_name="Synthetic User",
        email="synthetic@example.test",
        locale="ko-KR",
        timezone="Asia/Seoul",
        account_status="ACTIVE",
    )
    identity = AuthIdentity(
        id=uuid4(),
        user_id=user.id,
        provider="google",
        provider_subject="subject-1",
        provider_email=user.email,
        provider_email_verified=True,
    )
    repository = FakeAuthRepository(user, identity)
    service = AuthSessionService(
        repository,
        signing_key="access-signing-key-with-at-least-32-bytes",
        refresh_pepper="refresh-pepper-with-at-least-32-bytes",
        issuer="https://api.example.test",
        audience="epick-public-api",
        access_ttl_seconds=900,
        refresh_ttl_seconds=3600,
        now=lambda: now,
    )
    return service, repository, identity


def test_issue_hashes_refresh_and_rotation_reuses_family() -> None:
    now = datetime(2026, 9, 20, tzinfo=UTC)
    service, repository, identity = _fixture(now)
    issued = service.issue(identity)
    first = repository.sessions[0]

    assert issued.refresh_token not in first.refresh_token_hash
    rotated = service.rotate(issued.refresh_token)
    second = repository.sessions[1]
    assert first.rotated_at == now
    assert first.replaced_by_session_id == second.id
    assert repository.flushed_session_ids == [second.id]
    assert second.token_family_id == first.token_family_id
    assert rotated.refresh_token != issued.refresh_token


def test_refresh_replay_revokes_the_entire_family() -> None:
    now = datetime(2026, 9, 20, tzinfo=UTC)
    service, repository, identity = _fixture(now)
    issued = service.issue(identity)
    service.rotate(issued.refresh_token)

    with pytest.raises(AuthenticationRequiredError):
        service.rotate(issued.refresh_token)
    assert all(item.revoked_at == now for item in repository.sessions)
    assert all(item.revoke_reason == "REUSE_DETECTED" for item in repository.sessions)


def test_expiry_logout_and_deleting_account_fail_closed() -> None:
    now = datetime(2026, 9, 20, tzinfo=UTC)
    service, repository, identity = _fixture(now)
    issued = service.issue(identity)
    repository.sessions[0].expires_at = now - timedelta(seconds=1)
    with pytest.raises(AuthenticationRequiredError):
        service.rotate(issued.refresh_token)

    repository.sessions[0].expires_at = now + timedelta(hours=1)
    service.logout(issued.refresh_token)
    assert repository.sessions[0].revoke_reason == "LOGOUT"
    with pytest.raises(AuthenticationRequiredError):
        service.rotate(issued.refresh_token)

    service2, repository2, identity2 = _fixture(now)
    issued2 = service2.issue(identity2)
    repository2.user.account_status = "DELETION_PENDING"
    with pytest.raises(AuthenticationRequiredError):
        service2.rotate(issued2.refresh_token)


def test_access_token_requires_current_active_session() -> None:
    now = datetime(2026, 9, 20, tzinfo=UTC)
    service, repository, identity = _fixture(now)
    issued = service.issue(identity)
    principal = service.verify_access_token(issued.access_token)
    assert principal.owner_user_id == repository.user.id

    repository.sessions[0].revoked_at = now
    with pytest.raises(AuthenticationRequiredError):
        service.verify_access_token(issued.access_token)
