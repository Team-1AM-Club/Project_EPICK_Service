from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, model_validator

NotificationSeverity = Literal["INFO", "WARNING", "CRITICAL"]


class NotificationResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    project_id: UUID | None
    type: str
    severity: NotificationSeverity
    title: str
    message: str
    action_url: str | None
    read: bool
    archived: bool
    created_at: datetime


class NotificationUpdateRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    read: bool | None = None
    archived: bool | None = None

    @model_validator(mode="after")
    def require_state_change(self) -> NotificationUpdateRequest:
        if self.read is None and self.archived is None:
            raise ValueError("one notification state is required")
        if self.read is False or self.archived is False:
            raise ValueError("notification state can only advance")
        return self
