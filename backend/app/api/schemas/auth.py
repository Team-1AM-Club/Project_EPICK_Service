from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

OIDC_TRANSACTION_COOKIE = "epick-oidc-transaction"
LOCAL_REFRESH_COOKIE = "epick-refresh"
SECURE_REFRESH_COOKIE = "__Host-epick-refresh"


class AccessTokenResponse(BaseModel):
    model_config = ConfigDict(extra="forbid")

    access_token: str = Field(min_length=1)
    token_type: Literal["Bearer"] = "Bearer"
    expires_in: int = Field(ge=1)
