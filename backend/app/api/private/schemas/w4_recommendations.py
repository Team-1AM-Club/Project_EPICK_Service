from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class PrivateModel(BaseModel):
    # HTTP JSON carries UUIDs and timestamps as strings.  Keep the wire shape
    # closed while allowing Pydantic's JSON-to-domain parsing.
    model_config = ConfigDict(extra="forbid")


class AcquireRequest(PrivateModel):
    schema_version: Literal["w1.private.w4.recommendation.acquire.v1"]
    owner_user_id: UUID
    run_id: UUID


class AcquireResponse(PrivateModel):
    schema_version: Literal["w1.private.w4.recommendation.binding.v1"] = (
        "w1.private.w4.recommendation.binding.v1"
    )
    completed: bool = False
    binding: dict[str, object] | None


class LeaseRequest(PrivateModel):
    schema_version: Literal["w1.private.w4.recommendation.lease.v1"]
    run_id: UUID
    lease_token: UUID


class ContextResponse(PrivateModel):
    schema_version: Literal["w1.private.w4.recommendation.context.v1"] = (
        "w1.private.w4.recommendation.context.v1"
    )
    context: dict[str, object]


class AuthorizeRequest(LeaseRequest):
    action: Literal["PROCESS", "SEND_TO_PROVIDER", "RETURN_TO_CALLER"]
    user_id: UUID
    project_id: UUID
    context_version: str = Field(min_length=1, max_length=200)


class AuthorizeResponse(PrivateModel):
    schema_version: Literal["w1.private.w4.recommendation.authorization.v1"] = (
        "w1.private.w4.recommendation.authorization.v1"
    )
    allowed: bool


class PublishRequest(LeaseRequest):
    publication: dict[str, object]


class PublishResponse(PrivateModel):
    schema_version: Literal["w1.private.w4.recommendation.publication-result.v1"] = (
        "w1.private.w4.recommendation.publication-result.v1"
    )
    published: bool


class FailRequest(LeaseRequest):
    code: str = Field(min_length=1, max_length=64)


class FailResponse(PrivateModel):
    schema_version: Literal["w1.private.w4.recommendation.failure-result.v1"] = (
        "w1.private.w4.recommendation.failure-result.v1"
    )
    failed: bool
