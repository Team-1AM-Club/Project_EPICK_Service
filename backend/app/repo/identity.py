from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.session import set_local_owner_context
from app.models.identity import AuthIdentity, AuthSession, IdempotencyRecord, User


class IdentityRepository:
    def __init__(self, session: Session) -> None:
        self.session = session

    def add_user(self, user: User) -> None:
        self.session.add(user)

    def add_identity(self, identity: AuthIdentity) -> None:
        self.session.add(identity)

    def lock_provider_subject(self, *, provider: str, provider_subject: str) -> None:
        self.session.execute(
            text("SELECT pg_advisory_xact_lock(hashtextextended(:value, 0))"),
            {"value": f"{provider}:{provider_subject}"},
        )

    def find_provider_identity_for_update(
        self, *, provider: str, provider_subject: str
    ) -> AuthIdentity | None:
        self.session.execute(
            text("SELECT set_config('app.auth_provider_subject', :value, true)"),
            {"value": f"{provider}:{provider_subject}"},
        )
        identity = self.session.scalar(
            select(AuthIdentity)
            .where(
                AuthIdentity.provider == provider,
                AuthIdentity.provider_subject == provider_subject,
            )
            .with_for_update()
        )
        if identity is not None:
            set_local_owner_context(self.session, identity.user_id)
        return identity

    def get_user(self, user_id: UUID) -> User | None:
        return self.session.scalar(select(User).where(User.id == user_id))

    def add_auth_session(self, auth_session: AuthSession) -> None:
        self.session.add(auth_session)

    def flush_auth_session(self, auth_session: AuthSession) -> None:
        self.session.flush((auth_session,))

    def find_refresh_session_for_update(self, refresh_hash: str) -> AuthSession | None:
        self.session.execute(
            text("SELECT set_config('app.auth_refresh_hash', :value, true)"),
            {"value": refresh_hash},
        )
        auth_session = self.session.scalar(
            select(AuthSession)
            .where(AuthSession.refresh_token_hash == refresh_hash)
            .with_for_update()
        )
        if auth_session is not None:
            set_local_owner_context(self.session, auth_session.user_id)
        return auth_session

    def list_token_family_for_update(self, family_id: UUID) -> list[AuthSession]:
        return list(
            self.session.scalars(
                select(AuthSession)
                .where(AuthSession.token_family_id == family_id)
                .with_for_update()
            )
        )

    def get_auth_session(self, session_id: UUID, user_id: UUID) -> AuthSession | None:
        set_local_owner_context(self.session, user_id)
        return self.session.scalar(
            select(AuthSession).where(
                AuthSession.id == session_id,
                AuthSession.user_id == user_id,
            )
        )

    def touch_identity(
        self,
        identity: AuthIdentity,
        *,
        provider_email: str | None,
        provider_email_verified: bool,
        last_login_at: datetime,
    ) -> None:
        identity.provider_email = provider_email
        identity.provider_email_verified = provider_email_verified
        identity.last_login_at = last_login_at

    def find_idempotency_record(
        self, owner_user_id: UUID, method: str, path_scope: str, idempotency_key: str
    ) -> IdempotencyRecord | None:
        statement = (
            select(IdempotencyRecord)
            .where(
                IdempotencyRecord.owner_user_id == owner_user_id,
                IdempotencyRecord.method == method,
                IdempotencyRecord.path_scope == path_scope,
                IdempotencyRecord.idempotency_key == idempotency_key,
            )
            .with_for_update()
        )
        return self.session.scalar(statement)

    def add_idempotency_record(self, record: IdempotencyRecord) -> None:
        self.session.add(record)
