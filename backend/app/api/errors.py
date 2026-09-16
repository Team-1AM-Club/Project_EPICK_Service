from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field


@dataclass(frozen=True)
class ApiFieldError:
    field: str
    reason: str


@dataclass(frozen=True)
class ApiProblem(Exception):
    """A safe, public error that can be rendered as the v1 error envelope."""

    status_code: int
    code: str
    message_ko: str
    retryable: bool = False
    actions: Sequence[str] = field(default_factory=tuple)
    fields: Sequence[ApiFieldError] = field(default_factory=tuple)


class AuthenticationRequiredError(ApiProblem):
    def __init__(self) -> None:
        super().__init__(
            status_code=401,
            code="AUTHENTICATION_REQUIRED",
            message_ko="인증이 필요하거나 인증 정보를 확인할 수 없습니다.",
        )


class ResourceNotFoundError(ApiProblem):
    def __init__(self) -> None:
        super().__init__(
            status_code=404,
            code="RESOURCE_NOT_FOUND",
            message_ko="요청한 리소스를 찾을 수 없습니다.",
        )


class InvalidInputError(ApiProblem):
    def __init__(
        self,
        *,
        fields: Sequence[ApiFieldError] = (),
        message_ko: str = "요청 값을 확인해 주세요.",
    ) -> None:
        super().__init__(
            status_code=422,
            code="INVALID_INPUT",
            message_ko=message_ko,
            fields=fields,
        )


class IdempotencyConflictError(ApiProblem):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="IDEMPOTENCY_CONFLICT",
            message_ko="같은 멱등성 키가 다른 요청에 이미 사용되었습니다.",
        )


class VersionConflictApiError(ApiProblem):
    def __init__(self, *, expected_version: int, actual_version: int) -> None:
        super().__init__(
            status_code=412,
            code="VERSION_CONFLICT",
            message_ko="리소스 버전이 변경되었습니다. 최신 내용을 확인한 후 다시 시도해 주세요.",
            retryable=True,
            fields=(
                ApiFieldError(
                    field="If-Match",
                    reason=f"EXPECTED_{expected_version}_ACTUAL_{actual_version}",
                ),
            ),
        )


class CompletionRequirementsNotMetError(ApiProblem):
    def __init__(self, *, fields: Sequence[ApiFieldError]) -> None:
        super().__init__(
            status_code=422,
            code="COMPLETION_REQUIREMENTS_NOT_MET",
            message_ko="완료에 필요한 입력 항목을 확인해 주세요.",
            fields=fields,
        )


class ActionNotAllowedError(ApiProblem):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="ACTION_NOT_ALLOWED",
            message_ko="현재 상태에서는 요청한 작업을 수행할 수 없습니다.",
        )


class ActiveReferenceExistsError(ApiProblem):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="ACTIVE_REFERENCE_EXISTS",
            message_ko="현재 선택 또는 실행 결과가 연결되어 있어 처리할 수 없습니다.",
        )


class StaleInputError(ApiProblem):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="STALE_INPUT",
            message_ko=(
                "추천 입력 또는 후보 결과가 변경되었습니다. "
                "최신 내용을 확인한 후 다시 시도해 주세요."
            ),
            retryable=True,
        )


class StaleJobActionError(ApiProblem):
    def __init__(self) -> None:
        super().__init__(
            status_code=409,
            code="STALE_INPUT",
            message_ko=(
                "Job의 입력 또는 결과 기준이 변경되었습니다. "
                "최신 상태를 확인한 후 다시 시도해 주세요."
            ),
            retryable=True,
        )


class ExecutionPolicyUnconfiguredError(ApiProblem):
    def __init__(self) -> None:
        super().__init__(
            status_code=503,
            code="EXECUTION_POLICY_UNCONFIGURED",
            message_ko="현재 실행 정책이 구성되지 않았습니다.",
            retryable=True,
        )
