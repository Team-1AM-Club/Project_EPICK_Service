from __future__ import annotations

import base64
import hashlib
import hmac
import logging
import secrets
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode, urlsplit

import httpx
from authlib.jose import JoseError, jwt

from app.api.errors import AuthenticationRequiredError, InvalidInputError
from app.security.tokens import decode_signed_payload, encode_signed_payload, hash_secret

GOOGLE_ISSUERS = {"accounts.google.com", "https://accounts.google.com"}
RESERVED_AUTH_PATHS = {
    "/api/v1/auth/google/start",
    "/api/v1/auth/google/callback",
    "/api/v1/auth/refresh",
    "/api/v1/auth/logout",
}
logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AuthorizationRequest:
    authorization_url: str
    cookie_value: str
    state: str


@dataclass(frozen=True)
class OidcTransaction:
    nonce: str
    code_verifier: str
    return_to: str


@dataclass(frozen=True)
class GoogleIdentityClaims:
    subject: str
    display_name: str
    email: str | None
    email_verified: bool
    locale: str


class GoogleOidcService:
    def __init__(
        self,
        *,
        client_id: str,
        client_secret: str,
        redirect_uri: str,
        authorization_endpoint: str,
        discovery_url: str,
        transaction_signing_key: str,
        transaction_ttl_seconds: int,
        now: Callable[[], datetime] | None = None,
        http_client_factory: Callable[[], httpx.Client] | None = None,
    ) -> None:
        self.client_id = client_id
        self.client_secret = client_secret
        self.redirect_uri = redirect_uri
        self.authorization_endpoint = authorization_endpoint
        self.discovery_url = discovery_url
        self.transaction_signing_key = transaction_signing_key
        self.transaction_ttl_seconds = transaction_ttl_seconds
        self.now = now or (lambda: datetime.now(UTC))
        self.http_client_factory = http_client_factory or (
            lambda: httpx.Client(timeout=10, follow_redirects=False)
        )

    def create_authorization_request(self, return_to: str) -> AuthorizationRequest:
        safe_path = safe_return_path(return_to)
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        verifier = secrets.token_urlsafe(64)
        challenge = _base64url(hashlib.sha256(verifier.encode()).digest())
        issued_at = int(self.now().timestamp())
        cookie_value = encode_signed_payload(
            {
                "state_hash": hash_secret(state, self.transaction_signing_key),
                "nonce": nonce,
                "code_verifier": verifier,
                "return_to": safe_path,
                "iat": issued_at,
                "exp": issued_at + self.transaction_ttl_seconds,
            },
            self.transaction_signing_key,
        )
        query = urlencode(
            {
                "client_id": self.client_id,
                "redirect_uri": self.redirect_uri,
                "response_type": "code",
                "scope": "openid email profile",
                "state": state,
                "nonce": nonce,
                "code_challenge": challenge,
                "code_challenge_method": "S256",
                "prompt": "select_account",
            }
        )
        return AuthorizationRequest(
            authorization_url=f"{self.authorization_endpoint}?{query}",
            cookie_value=cookie_value,
            state=state,
        )

    def consume_transaction(self, cookie_value: str, state: str) -> OidcTransaction:
        payload = decode_signed_payload(cookie_value, self.transaction_signing_key)
        now = int(self.now().timestamp())
        expected_state_hash = payload.get("state_hash")
        actual_state_hash = hash_secret(state, self.transaction_signing_key)
        if (
            not isinstance(expected_state_hash, str)
            or not hmac.compare_digest(expected_state_hash, actual_state_hash)
            or not isinstance(payload.get("exp"), int)
            or now >= payload["exp"]
            or not isinstance(payload.get("nonce"), str)
            or not isinstance(payload.get("code_verifier"), str)
            or not isinstance(payload.get("return_to"), str)
        ):
            raise AuthenticationRequiredError()
        return OidcTransaction(
            nonce=payload["nonce"],
            code_verifier=payload["code_verifier"],
            return_to=safe_return_path(payload["return_to"]),
        )

    def exchange_code(self, *, code: str, transaction: OidcTransaction) -> GoogleIdentityClaims:
        try:
            with self.http_client_factory() as client:
                discovery_response = client.get(self.discovery_url)
                discovery_response.raise_for_status()
                discovery = discovery_response.json()
                token_response = client.post(
                    str(discovery["token_endpoint"]),
                    data={
                        "code": code,
                        "client_id": self.client_id,
                        "client_secret": self.client_secret,
                        "redirect_uri": self.redirect_uri,
                        "grant_type": "authorization_code",
                        "code_verifier": transaction.code_verifier,
                    },
                    headers={"Accept": "application/json"},
                )
                token_response.raise_for_status()
                id_token = token_response.json()["id_token"]
                jwks_response = client.get(str(discovery["jwks_uri"]))
                jwks_response.raise_for_status()
                claims = jwt.decode(
                    id_token,
                    jwks_response.json(),
                    claims_options={
                        "iss": {"essential": True, "values": list(GOOGLE_ISSUERS)},
                        "aud": {"essential": True, "value": self.client_id},
                        "exp": {"essential": True},
                        "sub": {"essential": True},
                    },
                )
                claims.validate(now=int(self.now().timestamp()), leeway=30)
                return self.validate_claims(dict(claims), nonce=transaction.nonce)
        except httpx.HTTPStatusError as error:
            logger.warning(
                "google_oidc_exchange_failed stage=http_response method=%s "
                "host=%s path=%s status=%s",
                error.request.method,
                error.request.url.host,
                error.request.url.path,
                error.response.status_code,
            )
            raise AuthenticationRequiredError() from error
        except httpx.HTTPError as error:
            request = error.request
            logger.warning(
                "google_oidc_exchange_failed stage=http_transport method=%s "
                "host=%s path=%s error_type=%s",
                request.method,
                request.url.host,
                request.url.path,
                type(error).__name__,
            )
            raise AuthenticationRequiredError() from error
        except (JoseError, KeyError, TypeError, ValueError) as error:
            logger.warning(
                "google_oidc_exchange_failed stage=token_validation error_type=%s",
                type(error).__name__,
            )
            raise AuthenticationRequiredError() from error

    def validate_claims(self, claims: Mapping[str, Any], *, nonce: str) -> GoogleIdentityClaims:
        issuer = claims.get("iss")
        audience = claims.get("aud")
        expires_at = claims.get("exp")
        subject = claims.get("sub")
        token_nonce = claims.get("nonce")
        audience_values = audience if isinstance(audience, list) else [audience]
        if (
            issuer not in GOOGLE_ISSUERS
            or self.client_id not in audience_values
            or not isinstance(expires_at, int)
            or expires_at <= int(self.now().timestamp())
            or not isinstance(subject, str)
            or not subject
            or not isinstance(token_nonce, str)
            or not hmac.compare_digest(token_nonce, nonce)
        ):
            raise AuthenticationRequiredError()
        email = claims.get("email") if isinstance(claims.get("email"), str) else None
        name = claims.get("name") if isinstance(claims.get("name"), str) else None
        locale = claims.get("locale") if isinstance(claims.get("locale"), str) else "ko-KR"
        return GoogleIdentityClaims(
            subject=subject,
            display_name=name or email or "EPICK 사용자",
            email=email,
            email_verified=claims.get("email_verified") is True,
            locale=locale,
        )


def safe_return_path(value: str) -> str:
    if not value.startswith("/") or value.startswith("//"):
        raise InvalidInputError(message_ko="로그인 후 이동 경로가 올바르지 않습니다.")
    parsed = urlsplit(value)
    if parsed.scheme or parsed.netloc or parsed.path in RESERVED_AUTH_PATHS:
        raise InvalidInputError(message_ko="로그인 후 이동 경로가 올바르지 않습니다.")
    return value


def _base64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()
