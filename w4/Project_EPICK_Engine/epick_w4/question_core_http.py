"""Authenticated, uncached W1 CT-12 context lookup on the private Compose network."""

import re
from dataclasses import dataclass, field
from http.client import HTTPException
from typing import Annotated, Literal
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from pydantic import Field, ValidationError

from .c01_contract import UUIDText
from .question_core_contract import QuestionCoreError, canonical_json, parse_json, require
from .question_core_producer import JobContext

BASE_URL = "http://w1-w4-question-core-context:8081"
RESOLVE_PATH = "/internal/v1/w4/question-core-contexts/resolve"
SCHEMA_VERSION = "w1.private.w4-question-core-context.v1"
MAX_RESPONSE_BYTES = 16 * 1024


class ContextResponse(JobContext):
    schema_version: Literal["w1.private.w4-question-core-context.v1"]
    context_key: UUIDText
    authorization_revision: Annotated[str, Field(pattern=r"^sha256:[0-9a-f]{64}$")]


@dataclass(frozen=True)
class W1HttpSettings:
    bearer: str = field(repr=False)
    base_url: str = BASE_URL
    principal: str = "w4"
    timeout_seconds: float = 5.0

    def validate(self):
        require(
            self.base_url == BASE_URL
            and self.principal == "w4"
            and isinstance(self.bearer, str)
            and 1 <= len(self.bearer) <= 4096
            and all(33 <= ord(c) <= 126 for c in self.bearer)
            and type(self.timeout_seconds) in {int, float}
            and 0 < self.timeout_seconds <= 10,
            "CORE_CONTEXT_CONFIG_INVALID",
        )


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class W1HttpContexts:
    def __init__(self, settings: W1HttpSettings, *, opener=None):
        settings.validate()
        self.settings = settings
        # Ignore host proxy configuration; credentials stay at the fixed private origin.
        self._opener = opener or build_opener(ProxyHandler({}), _NoRedirect())
        self._auth_failed = False

    def load(self, context_key: str) -> JobContext:
        require(not self._auth_failed, "CORE_CONTEXT_AUTH_FAILED")
        require(
            isinstance(context_key, str)
            and re.fullmatch(
                r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}",
                context_key,
            ),
            "CORE_CONTEXT_REQUEST_INVALID",
        )
        request = Request(
            self.settings.base_url + RESOLVE_PATH,
            data=canonical_json({"schema_version": SCHEMA_VERSION, "context_key": context_key}),
            headers={
                "Content-Type": "application/json",
                "Accept": "application/json",
                "Authorization": "Bearer " + self.settings.bearer,
                "X-EPICK-Service-Principal": self.settings.principal,
            },
            method="POST",
        )
        try:
            with self._opener.open(request, timeout=self.settings.timeout_seconds) as response:
                if response.status != 200:
                    self._reject_status(response.status)
                require(
                    response.headers.get_content_type() == "application/json",
                    "CORE_CONTEXT_RESPONSE_INVALID",
                )
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            # Never echo or parse an upstream error body, URL, or bearer.
            try:
                self._reject_status(error.code)
            finally:
                error.close()
        except (URLError, TimeoutError, OSError, HTTPException):
            raise QuestionCoreError("CORE_CURRENTNESS_UNAVAILABLE") from None
        try:
            value = parse_json(raw, maximum=MAX_RESPONSE_BYTES)
            response = ContextResponse.model_validate_json(canonical_json(value))
            require(response.context_key == context_key, "CORE_CONTEXT_KEY_MISMATCH")
            return JobContext.model_validate(response.model_dump(exclude={"schema_version"}))
        except (QuestionCoreError, ValidationError) as error:
            if isinstance(error, QuestionCoreError) and error.code == "CORE_CONTEXT_KEY_MISMATCH":
                raise
            raise QuestionCoreError("CORE_CONTEXT_RESPONSE_INVALID") from None

    def _reject_status(self, status):
        if status in {401, 403}:
            # A new adapter/process with replaced configuration is required.
            self._auth_failed = True
            code = "CORE_CONTEXT_AUTH_FAILED"
        else:
            code = {
                404: "CORE_CONTEXT_NOT_FOUND",
                422: "CORE_CONTEXT_REQUEST_INVALID",
                503: "CORE_CURRENTNESS_UNAVAILABLE",
            }.get(status, "CORE_CONTEXT_RESPONSE_INVALID")
        raise QuestionCoreError(code)
