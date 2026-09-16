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
