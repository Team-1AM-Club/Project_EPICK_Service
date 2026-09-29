"""One-shot HTTPS client for W1's protected private-authority decisions."""

from __future__ import annotations

import ssl
from uuid import UUID

from pydantic import BaseModel

from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionScope
from epick_engine.source_collection.w1_lookup_client import (
    DEFAULT_LOOKUP_TIMEOUT_SECONDS,
    LookupHTTPTransport,
    W1LookupClientError,
    _post_bounded_json,
    _require_verified_tls,
    _StdlibHTTPSLookupTransport,
    _validate_bearer,
    validate_lookup_endpoint,
)
from epick_engine.source_collection.w1_private_authority_contracts import (
    CleanupKind,
    CurrentWriteScopeLookupRequest,
    CurrentWriteScopeLookupResponse,
    GateAuthorityRequest,
    GateAuthorityResponse,
    GatePhase,
    GateScopeLookupRequest,
    GateScopeLookupResponse,
    PrivateWriteAuthorityRequest,
    PrivateWriteAuthorityResponse,
    TerminalCleanupAuthorityRequest,
    TerminalCleanupAuthorityResponse,
    W1GateBinding,
    W1PrivateBinding,
    validate_private_echo,
)
from epick_engine.source_collection.w1_transport import (
    W1WireContractError,
    _parse_wire,
)

CURRENT_SCOPE_TARGET = "/internal/v1/w2-private/current-write-scope-lookup"
WRITE_AUTHORITY_TARGET = "/internal/v1/w2-private/authority"
GATE_SCOPE_TARGET = "/internal/v1/w2-private/gate-scope-lookup"
GATE_AUTHORITY_TARGET = "/internal/v1/w2-private/gate-authority"
TERMINAL_CLEANUP_TARGET = "/internal/v1/w2-private/terminal-cleanup-authority"

_PROTECTED_TARGETS = frozenset(
    {
        CURRENT_SCOPE_TARGET,
        WRITE_AUTHORITY_TARGET,
        GATE_SCOPE_TARGET,
        GATE_AUTHORITY_TARGET,
        TERMINAL_CLEANUP_TARGET,
    }
)


def _binding_payload(binding: object) -> dict[str, object]:
    if not isinstance(binding, W1PrivateBinding):
        raise W1WireContractError("invalid W1 private authority binding")
    return {
        "owner_user_id": binding.owner_user_id,
        "owner_deletion_epoch": binding.owner_deletion_epoch,
        "command_id": binding.command_id,
        "job_id": binding.job_id,
        "execution_fence": binding.execution_fence,
    }


def _gate_payload(gate: object) -> dict[str, object]:
    if not isinstance(gate, W1GateBinding):
        raise W1WireContractError("invalid W1 private gate binding")
    payload = {
        **_binding_payload(gate.private),
        "operation_id": gate.operation_id,
        "operation_revision": gate.operation_revision,
        "action": gate.action,
        "result_digest": gate.result_digest,
    }
    if gate.purge_owner_deletion_epoch is not None:
        payload["purge_owner_deletion_epoch"] = gate.purge_owner_deletion_epoch
    return payload


def _scope_payload(scope: object) -> dict[str, str]:
    if not isinstance(scope, PrivateDeletionScope):
        raise W1WireContractError("invalid W1 private authority scope")
    if scope.kind == "ACCOUNT" and scope.project_id is None:
        return {"type": "ACCOUNT"}
    if scope.kind == "PROJECT" and isinstance(scope.project_id, UUID):
        return {"type": "PROJECT", "project_id": str(scope.project_id)}
    raise W1WireContractError("invalid W1 private authority scope")


def _request_model[M: BaseModel](model: type[M], payload: dict[str, object], *, label: str) -> M:
    try:
        return model.model_validate(payload, strict=True)
    except (TypeError, ValueError):
        raise W1WireContractError(f"invalid {label}") from None


class W1PrivateAuthorityClient:
    """Issue exactly one authenticated POST for each private-authority request."""

    __slots__ = ("_bearer", "_endpoint", "_ssl_context", "_transport")

    def __init__(
        self,
        *,
        endpoint: str,
        bearer: str,
        ssl_context: ssl.SSLContext,
        transport: LookupHTTPTransport | None = None,
    ) -> None:
        self._endpoint = validate_lookup_endpoint(endpoint)
        self._bearer = _validate_bearer(bearer)
        self._ssl_context = _require_verified_tls(ssl_context)
        self._transport = _StdlibHTTPSLookupTransport() if transport is None else transport

    def __repr__(self) -> str:
        return "W1PrivateAuthorityClient(endpoint=<redacted>, bearer=<redacted>)"

    def _post[M: BaseModel, R: BaseModel](
        self,
        *,
        target: str,
        request: M,
        response_model: type[R],
        response_label: str,
    ) -> R:
        if target not in _PROTECTED_TARGETS:
            raise W1LookupClientError("INVALID_TARGET")
        try:
            request_payload = request.model_dump(
                mode="json",
                warnings="error",
                exclude_none=True,
            )
        except (TypeError, ValueError):
            raise W1WireContractError("invalid W1 private authority request") from None
        http_status, decoded = _post_bounded_json(
            endpoint=self._endpoint,
            bearer=self._bearer,
            ssl_context=self._ssl_context,
            transport=self._transport,
            target=target,
            request_payload=request_payload,
            timeout=DEFAULT_LOOKUP_TIMEOUT_SECONDS,
        )
        if http_status != 200:
            raise W1LookupClientError(f"HTTP_{http_status}")
        parsed = _parse_wire(decoded, response_model, label=response_label)
        validate_private_echo(request, parsed)
        return parsed

    def lookup_current_scope(self, binding: W1PrivateBinding) -> CurrentWriteScopeLookupResponse:
        request = _request_model(
            CurrentWriteScopeLookupRequest,
            {
                "schema_version": "w1.private.w2-current-write-scope-lookup.v1",
                **_binding_payload(binding),
            },
            label="W1 current-write scope lookup request",
        )
        return self._post(
            target=CURRENT_SCOPE_TARGET,
            request=request,
            response_model=CurrentWriteScopeLookupResponse,
            response_label="W1 current-write scope lookup response",
        )

    def authorize_write(
        self,
        binding: W1PrivateBinding,
        scope: PrivateDeletionScope,
    ) -> PrivateWriteAuthorityResponse:
        request = _request_model(
            PrivateWriteAuthorityRequest,
            {
                "schema_version": "w1.private.w2-write-authority.v1",
                **_binding_payload(binding),
                "scope": _scope_payload(scope),
            },
            label="W1 private write authority request",
        )
        return self._post(
            target=WRITE_AUTHORITY_TARGET,
            request=request,
            response_model=PrivateWriteAuthorityResponse,
            response_label="W1 private write authority response",
        )

    def lookup_gate_scope(
        self,
        gate: W1GateBinding,
        phase: GatePhase,
    ) -> GateScopeLookupResponse:
        request = _request_model(
            GateScopeLookupRequest,
            {
                "schema_version": "w1.private.w2-gate-scope-lookup.v1",
                **_gate_payload(gate),
                "phase": phase,
            },
            label="W1 gate scope lookup request",
        )
        return self._post(
            target=GATE_SCOPE_TARGET,
            request=request,
            response_model=GateScopeLookupResponse,
            response_label="W1 gate scope lookup response",
        )

    def authorize_gate(
        self,
        gate: W1GateBinding,
        phase: GatePhase,
        scope: PrivateDeletionScope,
    ) -> GateAuthorityResponse:
        request = _request_model(
            GateAuthorityRequest,
            {
                "schema_version": "w1.private.w2-gate-authority.v1",
                **_gate_payload(gate),
                "scope": _scope_payload(scope),
                "phase": phase,
            },
            label="W1 gate authority request",
        )
        return self._post(
            target=GATE_AUTHORITY_TARGET,
            request=request,
            response_model=GateAuthorityResponse,
            response_label="W1 gate authority response",
        )

    def authorize_terminal_cleanup(
        self,
        binding: W1PrivateBinding,
        scope: PrivateDeletionScope,
        cleanup_kind: CleanupKind,
    ) -> TerminalCleanupAuthorityResponse:
        request = _request_model(
            TerminalCleanupAuthorityRequest,
            {
                "schema_version": "w1.private.w2-terminal-cleanup.v1",
                **_binding_payload(binding),
                "scope": _scope_payload(scope),
                "cleanup_kind": cleanup_kind,
            },
            label="W1 terminal cleanup authority request",
        )
        return self._post(
            target=TERMINAL_CLEANUP_TARGET,
            request=request,
            response_model=TerminalCleanupAuthorityResponse,
            response_label="W1 terminal cleanup authority response",
        )
