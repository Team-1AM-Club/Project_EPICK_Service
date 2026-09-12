"""원문, 비밀 설정, SDK payload를 담지 않는 오류 계약."""

from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class ErrorCode(StrEnum):
    POLICY_DENIED = "POLICY_DENIED"
    POLICY_UNKNOWN = "POLICY_UNKNOWN"
    INPUT_CONTRACT_INVALID = "INPUT_CONTRACT_INVALID"
    RAW_TEXT_MISSING = "RAW_TEXT_MISSING"
    VERSION_MISSING = "VERSION_MISSING"
    EVIDENCE_UNVERIFIABLE = "EVIDENCE_UNVERIFIABLE"
    PROVIDER_RATE_LIMITED = "PROVIDER_RATE_LIMITED"
    PROVIDER_RESPONSE_INVALID = "PROVIDER_RESPONSE_INVALID"
    LIVE_CONFIGURATION_REQUIRED = "LIVE_CONFIGURATION_REQUIRED"


class SafeError(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    code: ErrorCode
    message: str
    retry_after_seconds: int | None = None
    source_id: str | None = None
