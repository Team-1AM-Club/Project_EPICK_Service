from __future__ import annotations

from app.models.identity import AuthIdentity, User
from app.repo.identity import IdentityRepository


class IdentityService:
    def __init__(self, repository: IdentityRepository) -> None:
        self.repository = repository

    def register_provider_identity(
        self,
        *,
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
            display_name=display_name,
            email=provider_email if provider_email_verified else None,
            locale=locale,
            timezone=timezone,
        )
        identity = AuthIdentity(
            user=user,
            provider=provider,
            provider_subject=provider_subject,
            provider_email=provider_email,
            provider_email_verified=provider_email_verified,
        )
        self.repository.add_user(user)
        self.repository.add_identity(identity)
        return identity
