from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy.orm import Session

from app.api.idempotency import canonical_request_hash
from app.models.jobs import Job
from app.models.sources import Source
from app.repo.application_workspace import ApplicationWorkspaceRepository
from app.repo.source_collections import SourceCollectionRepository
from app.services.direct_source_registration import DirectSourceRegistrationService
from app.services.source_url_policy import CanonicalSourceUrl, canonicalize_official_url


class SourceCollectionError(ValueError):
    pass


class SourceCollectionNotFoundError(SourceCollectionError):
    pass


class SourceCollectionCompanyUnavailableError(SourceCollectionError):
    pass


@dataclass(frozen=True)
class SourceCollectionAcceptance:
    source: Source
    job: Job
    canonical: CanonicalSourceUrl
    replayed: bool


class SourceCollectionService:
    """Create the Source and owner Job inside the caller's request transaction."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def accept(
        self,
        *,
        owner_user_id: UUID,
        project_id: UUID,
        official_url: str,
        purpose: str,
        idempotency_key: str,
    ) -> SourceCollectionAcceptance:
        workspace = ApplicationWorkspaceRepository(self.session)
        project = workspace.get_project_for_update(
            project_id=project_id, owner_user_id=owner_user_id
        )
        if project is None:
            raise SourceCollectionNotFoundError("PROJECT_NOT_FOUND")
        version = workspace.get_current_project_version(
            project=project, owner_user_id=owner_user_id
        )
        if version is None:
            raise SourceCollectionNotFoundError("PROJECT_VERSION_NOT_FOUND")
        company = workspace.get_company(company_id=version.company_id)
        if (
            company is None
            or company.identification_status != "VERIFIED"
            or not company.official_domain
        ):
            raise SourceCollectionCompanyUnavailableError("COMPANY_NOT_VERIFIED")

        canonical = canonicalize_official_url(
            official_url, official_domain=company.official_domain
        )
        source, _created = SourceCollectionRepository(self.session).resolve_or_create(
            company_id=company.id,
            source_type=purpose,
            canonical=canonical,
            checked_at=datetime.now(UTC),
        )
        request_hash = canonical_request_hash(
            {
                "project_id": str(project.id),
                "project_version_id": str(version.id),
                "source_type": "OFFICIAL_URL",
                "official_url": canonical.url,
                "purpose": purpose,
            }
        )
        registration = DirectSourceRegistrationService(self.session).accept(
            owner_user_id=owner_user_id,
            company_id=company.id,
            source_id=source.id,
            idempotency_key=idempotency_key,
            request_hash=request_hash,
            project_id=project.id,
            project_version_id=version.id,
            path_scope=f"/api/v1/application-projects/{project.id}/source-collections",
        )
        return SourceCollectionAcceptance(
            source=source,
            job=registration.job,
            canonical=canonical,
            replayed=registration.replayed,
        )
