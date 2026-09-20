from __future__ import annotations

import secrets
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from app.api.errors import AuthenticationRequiredError
from app.models.identity import AuthIdentity, AuthSession
from app.security.tokens import decode_access_token, encode_access_token, hash_secret


@dataclass(frozen=True)
class IssuedSession:
    refresh_token: str
    access_token: str
    access_expires_in: int


@dataclass(frozen=True)
class VerifiedAccessSession:
    issuer: str
    subject: str
    owner_user_id: UUID
    session_id: UUID


class RefreshReplayDetected(AuthenticationRequiredError):
    """Signals that family revocation must commit before returning 401."""


class AuthSessionService:
    def __init__(
        self,
        repository,
        *,
        signing_key: str,
        refresh_pepper: str,
        issuer: str,
        audience: str,
        access_ttl_seconds: int,
        refresh_ttl_seconds: int,
        now: Callable[[], datetime] | None = None,
    ) -> None:
        self.repository = repository
        self.signing_key = signing_key
        self.refresh_pepper = refresh_pepper
        self.issuer = issuer
        self.audience = audience
        self.access_ttl_seconds = access_ttl_seconds
        self.refresh_ttl_seconds = refresh_ttl_seconds
        self.now = now or (lambda: datetime.now(UTC))

    def issue(
        self,
        identity: AuthIdentity,
        *,
        created_ip_hash: str | None = None,
        user_agent_summary: str | None = None,
        token_family_id: UUID | None = None,
    ) -> IssuedSession:
        now = self.now()
        raw_refresh = secrets.token_urlsafe(48)
        auth_session = AuthSession(
            id=uuid4(),
            user_id=identity.user_id,
            auth_identity_id=identity.id,
            refresh_token_hash=hash_secret(raw_refresh, self.refresh_pepper),
            token_family_id=token_family_id or uuid4(),
            issued_at=now,
            expires_at=now + timedelta(seconds=self.refresh_ttl_seconds),
            created_ip_hash=created_ip_hash,
            user_agent_summary=(user_agent_summary or "")[:512] or None,
        )
        self.repository.add_auth_session(auth_session)
        return self._issued(auth_session, raw_refresh)

    def rotate(self, raw_refresh: str) -> IssuedSession:
        now = self.now()
        refresh_hash = hash_secret(raw_refresh, self.refresh_pepper)
        current = self.repository.find_refresh_session_for_update(refresh_hash)
        if current is None:
            raise AuthenticationRequiredError()
        if current.rotated_at is not None or current.replaced_by_session_id is not None:
            self._revoke_family(current.token_family_id, "REUSE_DETECTED")
            raise RefreshReplayDetected()
        user = self.repository.get_user(current.user_id)
        if (
            current.revoked_at is not None
            or current.expires_at <= now
            or user is None
            or user.account_status != "ACTIVE"
        ):
            raise AuthenticationRequiredError()
        raw_next = secrets.token_urlsafe(48)
        replacement = AuthSession(
            id=uuid4(),
            user_id=current.user_id,
            auth_identity_id=current.auth_identity_id,
            refresh_token_hash=hash_secret(raw_next, self.refresh_pepper),
            token_family_id=current.token_family_id,
            issued_at=now,
            expires_at=now + timedelta(seconds=self.refresh_ttl_seconds),
            created_ip_hash=current.created_ip_hash,
            user_agent_summary=current.user_agent_summary,
        )
        # The lineage FK points to the replacement row. Persist that row first;
        # otherwise SQLAlchemy may flush the existing-session UPDATE before the
        # replacement INSERT because the model intentionally has no self-relation.
        self.repository.add_auth_session(replacement)
        self.repository.flush_auth_session(replacement)
        current.last_used_at = now
        current.rotated_at = now
        current.replaced_by_session_id = replacement.id
        return self._issued(replacement, raw_next)

    def logout(self, raw_refresh: str | None) -> None:
        if not raw_refresh:
            return
        current = self.repository.find_refresh_session_for_update(
            hash_secret(raw_refresh, self.refresh_pepper)
        )
        if current is not None and current.revoked_at is None:
            current.revoked_at = self.now()
            current.revoke_reason = "LOGOUT"

    def verify_access_token(self, token: str) -> VerifiedAccessSession:
        now = self.now()
        claims = decode_access_token(
            token,
            signing_key=self.signing_key,
            issuer=self.issuer,
            audience=self.audience,
            now=now,
        )
        try:
            user_id = UUID(str(claims["sub"]))
            session_id = UUID(str(claims["sid"]))
        except (KeyError, TypeError, ValueError) as error:
            raise AuthenticationRequiredError() from error
        auth_session = self.repository.get_auth_session(session_id, user_id)
        user = self.repository.get_user(user_id)
        if (
            auth_session is None
            or auth_session.revoked_at is not None
            or auth_session.rotated_at is not None
            or auth_session.expires_at <= now
            or user is None
            or user.account_status != "ACTIVE"
        ):
            raise AuthenticationRequiredError()
        return VerifiedAccessSession(
            issuer=self.issuer,
            subject=str(user_id),
            owner_user_id=user_id,
            session_id=session_id,
        )

    def _issued(self, auth_session: AuthSession, raw_refresh: str) -> IssuedSession:
        now = self.now()
        access_token = encode_access_token(
            claims={
                "iss": self.issuer,
                "aud": self.audience,
                "sub": str(auth_session.user_id),
                "sid": str(auth_session.id),
                "iat": int(now.timestamp()),
                "exp": int((now + timedelta(seconds=self.access_ttl_seconds)).timestamp()),
                "jti": str(uuid4()),
            },
            signing_key=self.signing_key,
        )
        return IssuedSession(
            refresh_token=raw_refresh,
            access_token=access_token,
            access_expires_in=self.access_ttl_seconds,
        )

    def _revoke_family(self, family_id: UUID, reason: str) -> None:
        now = self.now()
        for auth_session in self.repository.list_token_family_for_update(family_id):
            if auth_session.revoked_at is None:
                auth_session.revoked_at = now
                auth_session.revoke_reason = reason
