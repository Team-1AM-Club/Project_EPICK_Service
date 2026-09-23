from __future__ import annotations

import pytest

from app import main as api_main
from app.core.config import Settings


def test_staging_worker_settings_do_not_require_api_auth_secrets() -> None:
    settings = Settings(
        _env_file=None,
        app_env="staging",
        google_client_id=None,
        google_client_secret=None,
        epick_auth_signing_key=None,
        epick_refresh_token_pepper=None,
    )

    assert settings.app_env == "staging"
    assert settings.google_client_secret is None


def test_staging_api_rejects_missing_auth_secrets(monkeypatch: pytest.MonkeyPatch) -> None:
    staging_settings = Settings(_env_file=None).model_copy(update={"app_env": "staging"})
    monkeypatch.setattr(api_main, "settings", staging_settings)

    with pytest.raises(ValueError, match="OIDC and EPICK auth secrets"):
        api_main.create_app()


def test_staging_api_rejects_http_auth_urls(monkeypatch: pytest.MonkeyPatch) -> None:
    staging_settings = Settings(
        _env_file=None,
        google_client_id="client-id",
        google_client_secret="client-secret",
        epick_auth_signing_key="signing-key",
        epick_refresh_token_pepper="refresh-pepper",
    ).model_copy(update={"app_env": "staging"})
    monkeypatch.setattr(api_main, "settings", staging_settings)

    with pytest.raises(ValueError, match="Authentication URLs must use HTTPS"):
        api_main.create_app()


def test_staging_api_rejects_insecure_auth_cookie(monkeypatch: pytest.MonkeyPatch) -> None:
    staging_settings = Settings(
        _env_file=None,
        google_client_id="client-id",
        google_client_secret="client-secret",
        epick_auth_signing_key="signing-key",
        epick_refresh_token_pepper="refresh-pepper",
        google_oidc_redirect_uri="https://api.example.test/api/v1/auth/google/callback",
        epick_auth_issuer="https://api.example.test",
        epick_frontend_url="https://app.example.test",
        epick_allowed_frontend_origins=["https://app.example.test"],
        epick_auth_cookie_secure=False,
    ).model_copy(update={"app_env": "staging"})
    monkeypatch.setattr(api_main, "settings", staging_settings)

    with pytest.raises(ValueError, match="Authentication cookies must be Secure"):
        api_main.create_app()


def test_staging_api_starts_with_valid_auth_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    staging_settings = Settings(
        _env_file=None,
        google_client_id="client-id",
        google_client_secret="client-secret",
        epick_auth_signing_key="signing-key",
        epick_refresh_token_pepper="refresh-pepper",
        google_oidc_redirect_uri="https://api.example.test/api/v1/auth/google/callback",
        epick_auth_issuer="https://api.example.test",
        epick_frontend_url="https://app.example.test",
        epick_allowed_frontend_origins=["https://app.example.test"],
        epick_auth_cookie_secure=True,
    ).model_copy(update={"app_env": "staging"})
    monkeypatch.setattr(api_main, "settings", staging_settings)

    assert api_main.create_app().title == "EPICK Service API"
