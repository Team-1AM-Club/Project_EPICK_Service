from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict


class CompanyResponse(BaseModel):
    """A read-only entry from the already identified Company catalog."""

    model_config = ConfigDict(extra="forbid")

    id: UUID
    display_name: str
    official_domain: str | None
    identification_status: str


class JobPostingListItemResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    title: str
    status: str
    role_display: str | None
    published_at: datetime | None
    source_version_ref: str
