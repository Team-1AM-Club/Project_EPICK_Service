from __future__ import annotations

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

DeletionRequestStatus = Literal[
    "AWAITING_CONFIRMATION",
    "RUNNING",
    "PARTIALLY_COMPLETED",
    "FAILED_RETRYABLE",
    "COMPLETED",
    "EXPIRED",
]
DeletionTargetStatus = Literal["QUEUED", "DISPATCHED", "ACKNOWLEDGED", "FAILED_RETRYABLE"]
DeletionStore = Literal["POSTGRESQL", "NEO4J", "VECTOR", "CACHE", "CHECKPOINT"]


class AccountDeletionPreviewResponse(BaseModel):
    """The opaque confirmation secret is returned once and never persisted in API replay data."""

    model_config = ConfigDict(extra="forbid")

    deletion_request_id: UUID
    target_type: Literal["ACCOUNT"]
    scope: Literal["ALL_PRIVATE_DATA"]
    preview_token: str
    expires_at: datetime


class AccountDeletionConfirmRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    deletion_request_id: UUID
    preview_token: Annotated[str, Field(min_length=1, max_length=4096)]
    confirmation: Literal["DELETE"]


class AccountDeletionRetryRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    target_id: UUID


class AccountDeletionTargetResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    store: DeletionStore
    status: DeletionTargetStatus


class AccountDeletionResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: UUID
    target_type: Literal["ACCOUNT"]
    scope: Literal["ALL_PRIVATE_DATA"]
    status: DeletionRequestStatus
    targets: list[AccountDeletionTargetResponse] = Field(default_factory=list)
    requested_at: datetime
    completed_at: datetime | None
