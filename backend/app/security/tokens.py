from __future__ import annotations

import base64
import hashlib
import hmac
import json
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from authlib.jose import JoseError, jwt

from app.api.errors import AuthenticationRequiredError


def hash_secret(value: str, pepper: str) -> str:
    return hmac.new(pepper.encode(), value.encode(), hashlib.sha256).hexdigest()


def encode_signed_payload(payload: Mapping[str, Any], secret: str) -> str:
    encoded = _b64url(json.dumps(payload, separators=(",", ":"), sort_keys=True).encode())
    signature = _b64url(hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest())
    return f"{encoded}.{signature}"


def decode_signed_payload(value: str, secret: str) -> dict[str, Any]:
    try:
        encoded, supplied_signature = value.split(".", 1)
        expected_signature = _b64url(
            hmac.new(secret.encode(), encoded.encode(), hashlib.sha256).digest()
        )
        if not hmac.compare_digest(supplied_signature, expected_signature):
            raise ValueError("invalid signature")
        payload = json.loads(_b64url_decode(encoded))
        if not isinstance(payload, dict):
            raise ValueError("invalid payload")
        return payload
    except (ValueError, TypeError, json.JSONDecodeError) as error:
        raise AuthenticationRequiredError() from error


def encode_access_token(*, claims: Mapping[str, Any], signing_key: str) -> str:
    token = jwt.encode({"alg": "HS256", "typ": "JWT"}, dict(claims), signing_key)
    return token.decode() if isinstance(token, bytes) else token


def decode_access_token(
    token: str,
    *,
    signing_key: str,
    issuer: str,
    audience: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    try:
        claims = jwt.decode(
            token,
            signing_key,
            claims_options={
                "iss": {"essential": True, "value": issuer},
                "aud": {"essential": True, "value": audience},
                "sub": {"essential": True},
                "sid": {"essential": True},
                "exp": {"essential": True},
            },
        )
        claims.validate(now=int((now or datetime.now(UTC)).timestamp()), leeway=5)
        return dict(claims)
    except (JoseError, ValueError, TypeError, KeyError) as error:
        raise AuthenticationRequiredError() from error


def _b64url(value: bytes) -> str:
    return base64.urlsafe_b64encode(value).rstrip(b"=").decode()


def _b64url_decode(value: str) -> bytes:
    padding = "=" * (-len(value) % 4)
    return base64.urlsafe_b64decode(f"{value}{padding}")
