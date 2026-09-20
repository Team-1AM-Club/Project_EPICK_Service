"""Local model adapter contract. This module performs no network or key access."""

import json
import re
from typing import Protocol


class LLMError(RuntimeError):
    def __init__(self, code: str, stage: str):
        self.code = code
        self.stage = stage
        # Errors must not include original text or provider responses.
        super().__init__(f"{code}: {stage}")


class JsonClient(Protocol):
    """Supplied by the caller; tests inject a stub, not an actual model."""

    provider: str
    model: str
    simulated: bool

    def complete_json(self, *, stage: str, system_prompt: str, payload: dict) -> dict: ...


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _invalid_constant(_value):
    raise ValueError("invalid JSON constant")


def parse_json_object(text: str, stage: str, *, allow_fence: bool = True) -> dict:
    if not isinstance(text, str):
        raise LLMError("LLM_INVALID_JSON", stage)
    text = text.strip()
    fence = re.fullmatch(r"```(?:json)?\s*\n(.*?)\n```", text, re.DOTALL)
    if fence and allow_fence:
        text = fence.group(1)
    try:
        value = json.loads(text, object_pairs_hook=_unique_object, parse_constant=_invalid_constant)
        # Reject escaped lone surrogates before later UTF-8 request/response encoding.
        json.dumps(value, ensure_ascii=False).encode("utf-8")
    except (ValueError, RecursionError):
        raise LLMError("LLM_INVALID_JSON", stage) from None
    if not isinstance(value, dict):
        raise LLMError("LLM_INVALID_JSON", stage)
    return value
