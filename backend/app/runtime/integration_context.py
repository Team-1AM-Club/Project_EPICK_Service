"""Validated, owner-free correlation between asynchronous integration hops."""

from __future__ import annotations

import re
from dataclasses import dataclass
from uuid import UUID

_HOP_NAME = re.compile(r"^[a-z][a-z0-9-]{0,63}$")


@dataclass(frozen=True, slots=True)
class IntegrationContext:
    run_id: UUID
    job_id: UUID
    execution_fence: int
    owner_deletion_epoch: int
    analysis_input_version: str
    idempotency_key: str
    correlation_id: UUID
    source_id: UUID | None = None
    analysis_request_id: UUID | None = None
    recommendation_run_id: UUID | None = None

    def __post_init__(self) -> None:
        for value in (
            self.run_id,
            self.job_id,
            self.correlation_id,
            self.source_id,
            self.analysis_request_id,
            self.recommendation_run_id,
        ):
            if value is not None and not isinstance(value, UUID):
                raise ValueError("integration identifiers must be UUID values")
        if self.execution_fence < 1:
            raise ValueError("execution fence must be positive")
        if self.owner_deletion_epoch < 0:
            raise ValueError("owner deletion epoch must be non-negative")
        if not self.analysis_input_version or len(self.analysis_input_version) > 512:
            raise ValueError("analysis input version must be non-empty and bounded")
        if not self.idempotency_key or len(self.idempotency_key) > 255:
            raise ValueError("idempotency key must be non-empty and bounded")

    def for_hop(self, hop: str) -> IntegrationHop:
        if not _HOP_NAME.fullmatch(hop):
            raise ValueError("integration hop name is invalid")
        return IntegrationHop(context=self, hop=hop)


@dataclass(frozen=True, slots=True)
class IntegrationHop:
    context: IntegrationContext
    hop: str

    def __post_init__(self) -> None:
        if not _HOP_NAME.fullmatch(self.hop):
            raise ValueError("integration hop name is invalid")

    @property
    def currentness(self) -> tuple[int, int, str]:
        context = self.context
        return (
            context.execution_fence,
            context.owner_deletion_epoch,
            context.analysis_input_version,
        )
