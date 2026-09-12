"""실행 모드와 공급자 사용의 명시적 방어선."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .errors import ErrorCode, SafeError
from .models import EvaluationMode, Purpose


class RuntimeConfiguration(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    model_id: str | None = None
    schema_version: str = "0.2.0-draft"
    provider_base_url: str | None = None
    api_key: str | None = Field(default=None, repr=False)
    allow_provider_poc: bool = False

    @model_validator(mode="after")
    def _model_schema_pair(self) -> RuntimeConfiguration:
        if not self.schema_version:
            raise ValueError("schema_version이 필요합니다.")
        return self


def live_execution_error(
    context_mode: EvaluationMode, purpose: Purpose, config: RuntimeConfiguration
) -> SafeError | None:
    if context_mode != EvaluationMode.LIVE:
        return None
    if purpose not in {Purpose.PROVIDER_POC, Purpose.PRODUCTION_STRUCTURE}:
        return SafeError(
            code=ErrorCode.LIVE_CONFIGURATION_REQUIRED,
            message="LIVE 모드는 허용된 목적이 필요합니다.",
        )
    if (
        not config.allow_provider_poc
        or not config.model_id
        or not config.provider_base_url
        or not config.api_key
    ):
        return SafeError(
            code=ErrorCode.LIVE_CONFIGURATION_REQUIRED,
            message="LIVE 실행 설정 또는 허용이 없습니다.",
        )
    return None
