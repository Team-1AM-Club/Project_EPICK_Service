from __future__ import annotations

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlsplit

import pytest

from app.api.errors import AuthenticationRequiredError, InvalidInputError
from app.services.google_oidc import GoogleOidcService, safe_return_path


def _service(now: datetime) -> GoogleOidcService:
    return GoogleOidcService(
        client_id="google-client",
        client_secret="google-secret",
        redirect_uri="https://api.example.test/api/v1/auth/google/callback",
        authorization_endpoint="https://accounts.google.test/auth",
        discovery_url="https://accounts.google.test/.well-known/openid-configuration",
        transaction_signing_key="transaction-signing-key-with-32-bytes",
        transaction_ttl_seconds=600,
        now=lambda: now,
    )


def test_authorization_transaction_binds_state_nonce_pkce_and_return_path() -> None:
    now = datetime(2026, 9, 20, tzinfo=UTC)
    service = _service(now)

    request = service.create_authorization_request("/projects/one?tab=resume")
    query = parse_qs(urlsplit(request.authorization_url).query)

    assert query["client_id"] == ["google-client"]
    assert query["redirect_uri"] == ["https://api.example.test/api/v1/auth/google/callback"]
    assert query["response_type"] == ["code"]
    assert query["scope"] == ["openid email profile"]
    assert query["code_challenge_method"] == ["S256"]
    assert query["nonce"][0]
    assert query["state"][0]
    transaction = service.consume_transaction(request.cookie_value, query["state"][0])
    assert transaction.return_to == "/projects/one?tab=resume"
    assert transaction.code_verifier
    assert transaction.nonce == query["nonce"][0]


def test_transaction_rejects_wrong_state_tamper_and_expiry() -> None:
    now = datetime(2026, 9, 20, tzinfo=UTC)
    service = _service(now)
    request = service.create_authorization_request("/")

    with pytest.raises(AuthenticationRequiredError):
        service.consume_transaction(request.cookie_value, "wrong-state-value-which-is-long-enough")
    with pytest.raises(AuthenticationRequiredError):
        service.consume_transaction(f"{request.cookie_value}tampered", request.state)

    expired = _service(now + timedelta(minutes=11))
    with pytest.raises(AuthenticationRequiredError):
        expired.consume_transaction(request.cookie_value, request.state)


@pytest.mark.parametrize(
    "value", ["https://evil.test/", "//evil.test/path", "callback", "/api/v1/auth/logout"]
)
def test_return_path_rejects_external_and_reserved_paths(value: str) -> None:
    with pytest.raises(InvalidInputError):
        safe_return_path(value)


def test_claim_validation_rejects_issuer_audience_expiry_nonce_and_missing_subject() -> None:
    now = datetime(2026, 9, 20, tzinfo=UTC)
    service = _service(now)
    valid = {
        "iss": "https://accounts.google.com",
        "aud": "google-client",
        "exp": int((now + timedelta(minutes=5)).timestamp()),
        "sub": "google-subject",
        "nonce": "expected-nonce",
        "email": "user@example.test",
        "email_verified": True,
    }
    assert service.validate_claims(valid, nonce="expected-nonce").subject == "google-subject"

    for changed in (
        {"iss": "https://evil.test"},
        {"aud": "other-client"},
        {"exp": int((now - timedelta(seconds=1)).timestamp())},
        {"nonce": "wrong"},
        {"sub": ""},
    ):
        with pytest.raises(AuthenticationRequiredError):
            service.validate_claims({**valid, **changed}, nonce="expected-nonce")
