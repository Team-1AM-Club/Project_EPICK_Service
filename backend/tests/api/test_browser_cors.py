from __future__ import annotations

from fastapi.testclient import TestClient

from app.core.config import settings
from app.main import create_app


def test_browser_cors_allows_only_explicit_credentialed_origin(monkeypatch) -> None:
    monkeypatch.setattr(settings, "epick_allowed_frontend_origins", ["https://app.example.test"])
    with TestClient(create_app()) as client:
        allowed = client.options(
            "/api/v1/auth/refresh",
            headers={
                "Origin": "https://app.example.test",
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        denied = client.options(
            "/api/v1/auth/refresh",
            headers={
                "Origin": "https://evil.example.test",
                "Access-Control-Request-Method": "POST",
            },
        )

    assert allowed.status_code == 200
    assert allowed.headers["access-control-allow-origin"] == "https://app.example.test"
    assert allowed.headers["access-control-allow-credentials"] == "true"
    assert "authorization" in allowed.headers["access-control-allow-headers"].lower()
    assert denied.status_code == 400
    assert "access-control-allow-origin" not in denied.headers


def test_cookie_endpoints_validate_origin_or_referer(monkeypatch) -> None:
    monkeypatch.setattr(settings, "epick_allowed_frontend_origins", ["https://app.example.test"])
    with TestClient(create_app()) as client:
        allowed_referer = client.post(
            "/api/v1/auth/refresh",
            headers={"Referer": "https://app.example.test/projects/one"},
        )
        missing_source = client.post("/api/v1/auth/refresh")
        wrong_referer = client.post(
            "/api/v1/auth/refresh",
            headers={"Referer": "https://evil.example.test/projects/one"},
        )

    assert allowed_referer.status_code == 401
    assert allowed_referer.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"
    assert missing_source.status_code == 403
    assert wrong_referer.status_code == 403
