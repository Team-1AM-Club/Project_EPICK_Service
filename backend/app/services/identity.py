from __future__ import annotations

from datetime import datetime
from uuid import UUID, uuid4

from app.db.session import set_local_owner_context
from app.models.identity import AuthIdentity, User
from app.repo.identity import IdentityRepository


class IdentityService:
    def __init__(self, repository: IdentityRepository) -> None:
        self.repository = repository

    def get_or_create_google_identity(
        self,
        *,
        provider_subject: str,
        display_name: str,
        provider_email: str | None,
        provider_email_verified: bool,
        locale: str,
        timezone: str,
        login_at: datetime,
    ) -> AuthIdentity:
        self.repository.lock_provider_subject(provider="google", provider_subject=provider_subject)
        identity = self.repository.find_provider_identity_for_update(
            provider="google", provider_subject=provider_subject
        )
        if identity is not None:
            user = self.repository.get_user(identity.user_id)
            if user is not None:
                user.display_name = display_name
                if provider_email_verified:
                    user.email = provider_email
            self.repository.touch_identity(
                identity,
                provider_email=provider_email,
                provider_email_verified=provider_email_verified,
                last_login_at=login_at,
            )
            return identity

        owner_user_id = uuid4()
        set_local_owner_context(self.repository.session, owner_user_id)
        identity = self.register_provider_identity(
            owner_user_id=owner_user_id,
            display_name=display_name,
            provider="google",
            provider_subject=provider_subject,
            provider_email=provider_email,
            provider_email_verified=provider_email_verified,
            locale=locale,
            timezone=timezone,
        )
        identity.last_login_at = login_at
        return identity

    def register_provider_identity(
        self,
        *,
        owner_user_id: UUID | None = None,
        display_name: str,
        provider: str,
        provider_subject: str,
        provider_email: str | None,
        provider_email_verified: bool,
        locale: str,
        timezone: str,
    ) -> AuthIdentity:
        """Create a new account identity without email-based account merging."""
        user = User(
            id=owner_user_id or uuid4(),
            display_name=display_name,
            email=provider_email if provider_email_verified else None,
            locale=locale,
            timezone=timezone,
        )
        identity = AuthIdentity(
            id=uuid4(),
            user_id=user.id,
            user=user,
            provider=provider,
            provider_subject=provider_subject,
            provider_email=provider_email,
            provider_email_verified=provider_email_verified,
        )
        self.repository.add_user(user)
        self.repository.add_identity(identity)
        return identity
