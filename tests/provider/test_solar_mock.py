import pytest

from w3_knowledge.adapters.solar import (
    ProviderRateLimited,
    ProviderResponseError,
    SolarExtractionAdapter,
)
from w3_knowledge.config import RuntimeConfiguration
from tests.support.factories import source_with_text


class _Completion:
    def __init__(self, content: str) -> None:
        self.choices = [
            type("Choice", (), {"message": type("Message", (), {"content": content})()})()
        ]


class _Client:
    def __init__(self, content: str | Exception) -> None:
        self.content = content
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs: object) -> _Completion:
        assert "tools" not in kwargs
        if isinstance(self.content, Exception):
            raise self.content
        return _Completion(self.content)


def _adapter(content: str | Exception) -> SolarExtractionAdapter:
    config = RuntimeConfiguration(
        model_id="mock-model",
        provider_base_url="https://mock.invalid",
        api_key="not-logged",
        allow_provider_poc=True,
    )
    return SolarExtractionAdapter(config, client_factory=lambda **kwargs: _Client(content))


def test_mock_rejects_unknown_fields_and_invalid_evidence() -> None:
    bad = '{"claims": [], "requirements": [], "unexpected": true}'
    with pytest.raises(ProviderResponseError):
        _adapter(bad).extract_claims(source=source_with_text())


def test_mock_uses_strict_schema_without_tools() -> None:
    assert (
        _adapter('{"claims": [], "requirements": []}').extract_claims(source=source_with_text())
        == ()
    )


def test_429_is_returned_without_sdk_retry() -> None:
    error = type("RateError", (Exception,), {"status_code": 429, "response": None})()
    with pytest.raises(ProviderRateLimited):
        _adapter(error).extract_claims(source=source_with_text())
