from __future__ import annotations

from types import SimpleNamespace
from uuid import uuid4

from fastapi.testclient import TestClient

from app.api.schemas.auth import LOCAL_REFRESH_COOKIE, OIDC_TRANSACTION_COOKIE
from app.api.v1 import auth as auth_routes
from app.services.auth_sessions import RefreshReplayDetected
from app.services.google_oidc import AuthorizationRequest, GoogleIdentityClaims, OidcTransaction


class FakeOidcService:
    def create_authorization_request(self, return_to: str) -> AuthorizationRequest:
        assert return_to.startswith("/")
        return AuthorizationRequest(
            authorization_url="https://accounts.google.test/auth?state=synthetic-state",
            cookie_value="signed-transaction",
            state="synthetic-state",
        )

    def consume_transaction(self, cookie_value: str, state: str) -> OidcTransaction:
        assert cookie_value == "signed-transaction"
        assert state == "synthetic-state-long-enough"
        return OidcTransaction(nonce="nonce", code_verifier="verifier", return_to="/projects")

    def exchange_code(self, *, code: str, transaction: OidcTransaction) -> GoogleIdentityClaims:
        assert code == "google-code"
        assert transaction.nonce == "nonce"
        return GoogleIdentityClaims(
            subject="google-subject",
            display_name="Synthetic User",
            email="synthetic@example.test",
            email_verified=True,
            locale="ko-KR",
        )

    def now(self):
        from datetime import UTC, datetime

        return datetime(2026, 9, 20, tzinfo=UTC)


class FakeAuthSessionService:
    logged_out: str | None = None

    def issue(self, identity, **_kwargs):
        assert identity.user_id
        return SimpleNamespace(
            refresh_token="refresh-issued",
            access_token="access-issued",
            access_expires_in=900,
        )

    def rotate(self, raw_refresh: str):
        assert raw_refresh == "refresh-original"
        return SimpleNamespace(
            refresh_token="refresh-rotated",
            access_token="access-rotated",
            access_expires_in=900,
        )

    def logout(self, raw_refresh: str | None) -> None:
        self.logged_out = raw_refresh


class FakeIdentityService:
    def __init__(self, _repository) -> None:
        pass

    def get_or_create_google_identity(self, **_kwargs):
        return SimpleNamespace(id=uuid4(), user_id=uuid4())


class FakeSession:
    def __init__(self) -> None:
        self.commits = 0

    def commit(self) -> None:
        self.commits += 1


def test_auth_openapi_contains_additive_routes(api_app) -> None:
    paths = api_app.openapi()["paths"]
    assert "/api/v1/auth/google/start" in paths
    assert "/api/v1/auth/google/callback" in paths
    assert "/api/v1/auth/refresh" in paths
    assert "/api/v1/auth/logout" in paths


def test_start_callback_refresh_and_logout_cookie_contract(api_app, monkeypatch) -> None:
    oidc = FakeOidcService()
    auth_service = FakeAuthSessionService()
    api_app.dependency_overrides[auth_routes.get_google_oidc_service] = lambda: oidc
    fake_session = FakeSession()
    api_app.dependency_overrides[auth_routes.get_auth_database_session] = lambda: fake_session
    monkeypatch.setattr(auth_routes, "IdentityRepository", lambda session: session)
    monkeypatch.setattr(auth_routes, "IdentityService", FakeIdentityService)
    monkeypatch.setattr(auth_routes, "build_auth_session_service", lambda _repo: auth_service)
    monkeypatch.setattr(
        auth_routes.settings,
        "epick_allowed_frontend_origins",
        ["http://localhost:3001"],
    )

    with TestClient(api_app, follow_redirects=False) as client:
        started = client.get("/api/v1/auth/google/start?return_to=/projects")
        assert started.status_code == 302
        assert started.headers["location"].startswith("https://accounts.google.test/auth")
        assert OIDC_TRANSACTION_COOKIE in started.headers["set-cookie"]
        assert "HttpOnly" in started.headers["set-cookie"]
        assert started.headers["cache-control"] == "no-store"

        client.cookies.set(
            OIDC_TRANSACTION_COOKIE,
            "signed-transaction",
            path="/api/v1/auth/google",
        )
        callback = client.get(
            "/api/v1/auth/google/callback",
            params={"code": "google-code", "state": "synthetic-state-long-enough"},
        )
        assert callback.status_code == 302
        assert callback.headers["location"] == "http://localhost:3001/projects"
        assert LOCAL_REFRESH_COOKIE in callback.headers["set-cookie"]
        assert "HttpOnly" in callback.headers["set-cookie"]

        client.cookies.set(LOCAL_REFRESH_COOKIE, "refresh-original", path="/api/v1/auth")
        refreshed = client.post("/api/v1/auth/refresh", headers={"Origin": "http://localhost:3001"})
        assert refreshed.status_code == 200
        assert refreshed.json() == {
            "access_token": "access-rotated",
            "token_type": "Bearer",
            "expires_in": 900,
        }
        assert refreshed.headers["cache-control"] == "no-store"
        assert "refresh-rotated" in refreshed.headers["set-cookie"]

        client.cookies.set(LOCAL_REFRESH_COOKIE, "refresh-original", path="/api/v1/auth")
        logged_out = client.post("/api/v1/auth/logout", headers={"Origin": "http://localhost:3001"})
        assert logged_out.status_code == 204
        assert auth_service.logged_out == "refresh-original"
        assert "Max-Age=0" in logged_out.headers["set-cookie"]


def test_refresh_rejects_missing_cookie_and_cross_origin(api_app, monkeypatch) -> None:
    api_app.dependency_overrides[auth_routes.get_auth_database_session] = lambda: object()
    monkeypatch.setattr(
        auth_routes.settings,
        "epick_allowed_frontend_origins",
        ["http://localhost:3001"],
    )
    with TestClient(api_app) as client:
        cross_origin = client.post(
            "/api/v1/auth/refresh", headers={"Origin": "https://evil.example"}
        )
        missing = client.post("/api/v1/auth/refresh", headers={"Origin": "http://localhost:3001"})
    assert cross_origin.status_code == 403
    assert cross_origin.json()["error"]["code"] == "ORIGIN_NOT_ALLOWED"
    assert missing.status_code == 401
    assert missing.json()["error"]["code"] == "AUTHENTICATION_REQUIRED"


def test_refresh_replay_commits_family_revocation_before_401(api_app, monkeypatch) -> None:
    class ReplayService:
        def rotate(self, _raw_refresh: str):
            raise RefreshReplayDetected()

    fake_session = FakeSession()
    api_app.dependency_overrides[auth_routes.get_auth_database_session] = lambda: fake_session
    monkeypatch.setattr(auth_routes, "IdentityRepository", lambda session: session)
    monkeypatch.setattr(auth_routes, "build_auth_session_service", lambda _repo: ReplayService())
    monkeypatch.setattr(
        auth_routes.settings,
        "epick_allowed_frontend_origins",
        ["http://localhost:3001"],
    )
    with TestClient(api_app) as client:
        client.cookies.set(LOCAL_REFRESH_COOKIE, "replayed", path="/api/v1/auth")
        response = client.post("/api/v1/auth/refresh", headers={"Origin": "http://localhost:3001"})
    assert response.status_code == 401
    assert fake_session.commits == 1
