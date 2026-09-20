"""Approved W3 private runtime retention policy and deadline calculations."""

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


POLICY_REVISION = "w3.retention/1.1"


class RetentionPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    revision: Literal["w3.retention/1.1"] = POLICY_REVISION
    handoff_body_seconds: Annotated[int, Field(strict=True, ge=1)] = 1_209_600
    private_body_max_seconds: Annotated[int, Field(strict=True, ge=1)] = 2_592_000
    terminal_metadata_seconds: Annotated[int, Field(strict=True, ge=1)] = 7_776_000
    owner_tombstone_seconds: Annotated[int, Field(strict=True, ge=1)] = 31_536_000
    retired_counter_seconds: Annotated[int, Field(strict=True, ge=1)] = 7_776_000
    backup_seconds: Annotated[int, Field(strict=True, ge=1)] = 2_592_000

    @model_validator(mode="after")
    def handoff_window_fits_absolute_window(self):
        if self.handoff_body_seconds > self.private_body_max_seconds:
            raise ValueError("HANDOFF_RETENTION_EXCEEDS_ABSOLUTE_MAX")
        return self

    def body_deadline(self, created_at: float, published_at: float | None) -> float:
        absolute = created_at + self.private_body_max_seconds
        if published_at is None:
            return absolute
        return min(absolute, published_at + self.handoff_body_seconds)


DEFAULT_RETENTION_POLICY = RetentionPolicy()
