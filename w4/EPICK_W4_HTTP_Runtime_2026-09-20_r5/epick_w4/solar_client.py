"""Bounded Upstage transport for the approved synthetic fixtures only."""

import json
from http.client import HTTPException
import os
import re
import socket
from urllib.error import HTTPError, URLError
from urllib.request import HTTPRedirectHandler, Request, build_opener

from .llm_contract import LLMError, parse_json_object
from .synthetic_policy import check_sample_payload

API_URL = "https://api.upstage.ai/v1/chat/completions"
DEFAULT_MODEL = "solar-pro4"
MAX_RESPONSE_BYTES = 1_000_000
MAX_REQUEST_BYTES = 250_000


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        # Do not forward Authorization or source text to a redirected destination.
        return None


class SolarClient:
    provider = "upstage"
    content_logging_enabled = False
    simulated = False

    def __init__(self, api_key: str, *, model: str = DEFAULT_MODEL, timeout: int = 60):
        if not isinstance(api_key, str) or not api_key.strip():
            raise LLMError("LLM_API_KEY_MISSING", "configuration")
        if not api_key.isascii() or any(char.isspace() or ord(char) < 32 for char in api_key):
            raise LLMError("LLM_API_KEY_INVALID", "configuration")
        if not isinstance(model, str) or re.fullmatch(r"solar-[a-zA-Z0-9._-]{1,80}", model) is None:
            raise LLMError("LLM_MODEL_INVALID", "configuration")
        if type(timeout) is not int or not 1 <= timeout <= 120:
            raise LLMError("LLM_TIMEOUT_INVALID", "configuration")
        self._api_key = api_key
        self.model = model
        self.timeout = timeout
        self._opener = build_opener(_NoRedirect())

    @classmethod
    def from_env(cls):
        return cls(os.environ.get("UPSTAGE_API_KEY", ""),
                   model=os.environ.get("W4_LLM_MODEL", DEFAULT_MODEL))

    @property
    def generation_config(self):
        return {"temperature": 0, "reasoning_effort": "none", "max_tokens": 6000,
                "stream": False}

    def complete_json(self, *, stage: str, system_prompt: str, payload: dict) -> dict:
        check_sample_payload(stage, system_prompt, payload)
        body = json.dumps({
            "model": self.model,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
            ],
            **self.generation_config,
        }, ensure_ascii=False).encode("utf-8")
        if len(body) > MAX_REQUEST_BYTES:
            raise LLMError("LLM_INPUT_LIMIT_EXCEEDED", stage)
        request = Request(API_URL, data=body, method="POST", headers={
            "Authorization": f"Bearer {self._api_key}",
            "Content-Type": "application/json", "Accept": "application/json",
        })
        try:
            with self._opener.open(request, timeout=self.timeout) as response:
                raw = response.read(MAX_RESPONSE_BYTES + 1)
        except HTTPError as error:
            status = error.code
            error.close()
            code = {401: "LLM_AUTH_FAILED", 403: "LLM_ACCESS_DENIED",
                    429: "LLM_RATE_LIMITED"}.get(status, "LLM_HTTP_ERROR")
            if 300 <= status < 400:
                code = "LLM_REDIRECT_REJECTED"
            elif 500 <= status < 600:
                code = "LLM_PROVIDER_UNAVAILABLE"
            raise LLMError(code, stage) from None
        except URLError as error:
            code = "LLM_TIMEOUT" if isinstance(error.reason, (TimeoutError, socket.timeout)) else "LLM_NETWORK_ERROR"
            raise LLMError(code, stage) from None
        except TimeoutError:
            raise LLMError("LLM_TIMEOUT", stage) from None
        except (OSError, HTTPException):
            raise LLMError("LLM_NETWORK_ERROR", stage) from None
        if len(raw) > MAX_RESPONSE_BYTES:
            raise LLMError("LLM_RESPONSE_LIMIT_EXCEEDED", stage)
        try:
            envelope = parse_json_object(raw.decode("utf-8"), stage)
        except (UnicodeError, LLMError):
            raise LLMError("LLM_INVALID_RESPONSE", stage) from None
        choices = envelope.get("choices")
        if not isinstance(choices, list) or len(choices) != 1 or not isinstance(choices[0], dict):
            raise LLMError("LLM_INVALID_RESPONSE", stage)
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise LLMError("LLM_OUTPUT_TRUNCATED", stage)
        message = choice.get("message")
        if choice.get("finish_reason") != "stop" or not isinstance(message, dict):
            raise LLMError("LLM_INVALID_RESPONSE", stage)
        if message.get("refusal") or message.get("tool_calls") or message.get("function_call"):
            raise LLMError("LLM_RESPONSE_REJECTED", stage)
        # Ignore provider reasoning fields; return only the validated JSON content.
        return parse_json_object(message.get("content"), stage)
