"""Private W1→W3 lifecycle commands and metadata-only W3 receipts."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, TypeAdapter, model_validator


class OwnerDeletionCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["1.0"]
    command_id: UUID
    owner_id: UUID
    owner_deletion_epoch: Annotated[int, Field(strict=True, ge=1)]
    target_type: Literal["W3_CORE_RUNTIME"]
    target_ref: UUID


class SourceRetirementCommand(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["w1.private.w3-source-retirement/1.0"]
    command_id: UUID
    operation: Literal["RETIRE_SOURCE"]
    company_id: UUID
    source_id: UUID
    retired_at: AwareDatetime
    target_type: Literal["W3_CORE_RUNTIME"]
    target_ref: UUID


LifecycleCommand = OwnerDeletionCommand | SourceRetirementCommand


class LifecycleReceipt(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    schema_version: Literal["w3.private.w1-lifecycle-receipt/1.0"]
    message_type: Literal["w3.private.w1.lifecycle-receipt"]
    receipt_id: UUID
    occurred_at: AwareDatetime
    visibility_scope: Literal["PRIVATE"]
    producer: Literal["w3"]
    command_id: UUID
    target_ref: UUID
    operation: Literal["DELETE_OWNER", "RETIRE_SOURCE"]
    outcome: Literal["APPLIED", "DUPLICATE", "STALE"]
    affected_count: Annotated[int, Field(strict=True, ge=0)]
    applied_epoch: Annotated[int, Field(strict=True, ge=0)] | None
    effective_at: AwareDatetime | None

    @model_validator(mode="after")
    def operation_fields_match(self):
        if self.operation == "DELETE_OWNER":
            if self.applied_epoch is None or self.effective_at is not None:
                raise ValueError("OWNER_RECEIPT_FIELDS_INVALID")
        elif self.applied_epoch is not None or self.effective_at is None:
            raise ValueError("SOURCE_RECEIPT_FIELDS_INVALID")
        return self


@dataclass(frozen=True, slots=True)
class LifecycleApplyResult:
    receipt: LifecycleReceipt
    duplicate_delivery: bool
    receipt_state: str


def parse_lifecycle_command(body: str) -> LifecycleCommand:
    if not isinstance(body, str) or not body or len(body.encode("utf-8")) > 65_536:
        raise ValueError("LIFECYCLE_COMMAND_SIZE_INVALID")
    try:
        value = json.loads(body)
    except (UnicodeError, json.JSONDecodeError) as error:
        raise ValueError("LIFECYCLE_COMMAND_JSON_INVALID") from error
    if not isinstance(value, dict):
        raise ValueError("LIFECYCLE_COMMAND_OBJECT_REQUIRED")
    if value.get("target_type") != "W3_CORE_RUNTIME":
        raise ValueError("LIFECYCLE_TARGET_INVALID")
    model = (
        SourceRetirementCommand
        if value.get("operation") == "RETIRE_SOURCE"
        else OwnerDeletionCommand
    )
    try:
        return model.model_validate(value)
    except Exception as error:
        raise ValueError("LIFECYCLE_COMMAND_SCHEMA_INVALID") from error


def canonical_command(command: LifecycleCommand) -> str:
    command = TypeAdapter(LifecycleCommand).validate_python(command)
    return json.dumps(
        command.model_dump(mode="json"),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )


def command_digest(command: LifecycleCommand) -> str:
    return hashlib.sha256(canonical_command(command).encode()).hexdigest()


def utc_from_timestamp(value: float) -> datetime:
    from datetime import UTC

    return datetime.fromtimestamp(value, UTC)
