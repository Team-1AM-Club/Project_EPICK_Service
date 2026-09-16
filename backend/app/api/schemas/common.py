from __future__ import annotations

from typing import Generic, TypeVar

from pydantic import BaseModel, ConfigDict, Field

ItemT = TypeVar("ItemT")


class ApiMeta(BaseModel):
    """Optional transport metadata; domain responses remain unwrapped."""

    model_config = ConfigDict(extra="forbid")

    correlation_id: str


class CursorListResponse(BaseModel, Generic[ItemT]):
    """The sole public list envelope for cursor-paginated v1 endpoints."""

    model_config = ConfigDict(extra="forbid")

    items: list[ItemT] = Field(default_factory=list)
    next_cursor: str | None = None


class ErrorFieldResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    field: str
    reason: str


class ApiErrorBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    code: str
    message_ko: str
    retryable: bool = False
    actions: list[str] = Field(default_factory=list)
    correlation_id: str
    fields: list[ErrorFieldResponse] = Field(default_factory=list)


class ApiErrorResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    error: ApiErrorBody
