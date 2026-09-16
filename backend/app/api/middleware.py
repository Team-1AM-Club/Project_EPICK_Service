from __future__ import annotations

from collections.abc import Awaitable, Callable
from uuid import UUID, uuid4

from fastapi import Request, Response

REQUEST_ID_HEADER = "X-Request-ID"
_CORRELATION_ID_STATE_KEY = "correlation_id"


def get_correlation_id(request: Request) -> str:
    correlation_id = getattr(request.state, _CORRELATION_ID_STATE_KEY, None)
    if isinstance(correlation_id, str):
        return correlation_id
    return str(uuid4())


async def correlation_id_middleware(
    request: Request,
    call_next: Callable[[Request], Awaitable[Response]],
) -> Response:
    """Attach one UUID correlation ID to every response without trusting arbitrary input."""

    supplied = request.headers.get(REQUEST_ID_HEADER)
    try:
        correlation_id = str(UUID(supplied)) if supplied is not None else str(uuid4())
    except ValueError:
        correlation_id = str(uuid4())

    setattr(request.state, _CORRELATION_ID_STATE_KEY, correlation_id)
    response = await call_next(request)
    response.headers[REQUEST_ID_HEADER] = correlation_id
    return response
