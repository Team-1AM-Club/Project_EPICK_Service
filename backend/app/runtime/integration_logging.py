"""Allowlisted log fields only; private payloads never enter this API."""

from __future__ import annotations

import re

from app.runtime.integration_context import IntegrationHop

_SAFE_LABEL = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")


def safe_integration_log(
    hop: IntegrationHop,
    *,
    event: str,
    status: str,
) -> dict[str, str]:
    if not _SAFE_LABEL.fullmatch(event) or not _SAFE_LABEL.fullmatch(status):
        raise ValueError("integration log event/status must be safe labels")
    return {
        "correlation_id": str(hop.context.correlation_id),
        "run_id": str(hop.context.run_id),
        "hop": hop.hop,
        "event": event,
        "status": status,
    }
