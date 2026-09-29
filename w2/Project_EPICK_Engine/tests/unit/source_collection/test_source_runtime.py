"""TDD contract for the lookup-gated POLICY collection runtime."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from typing import Any
from uuid import UUID

import pytest

import epick_engine.source_collection.source_runtime as source_runtime
from epick_engine.source_collection.contracts import CollectionStage
from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionScope
from epick_engine.source_collection.private_scope import (
    PrivateScopeRejected,
    PrivateTerminalCleanupAuthority,
    PrivateWriteScope,
)
from epick_engine.source_collection.source_runtime import (
    LookupGatedExecutionContext,
    RuntimeAuthorizationError,
)
from epick_engine.source_collection.source_runtime import (
    handle_collection_dispatch as _handle_collection_dispatch,
)
from epick_engine.source_collection.source_runtime_input import RuntimeSourceConfigFile
from epick_engine.source_collection.source_runtime_store import (
    CollectionRuntimeConflict,
    dispatch_digest,
)
from epick_engine.source_collection.w1_lookup_client import W1LookupClientError
from epick_engine.source_collection.w1_private_authority_contracts import (
    CurrentWriteScopeLookupResponse,
    PrivateWriteAuthorityResponse,
    TerminalCleanupAuthorityResponse,
    W1PrivateBinding,
)
from epick_engine.source_collection.w1_transport import (
    LookupResponse,
    W1Dispatch,
    W1WireContractError,
    parse_w1_dispatch,
)

FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "w1_private_contract"
NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)
ATTEMPT_ID = UUID("00000000-0000-4000-8000-000000000101")
CLAIM_TOKEN = UUID("00000000-0000-4000-8000-000000000102")
STAGED_MESSAGE_ID = UUID("00000000-0000-4000-8000-000000000103")


def _dispatch(*, resume_stage: str = "policy", policy_revision: int | None = None) -> W1Dispatch:
    raw = json.loads((FIXTURES / "private-w2-command-dispatch.json").read_text(encoding="utf-8"))
    raw["payload"]["resume_stage"] = resume_stage
    raw["payload"]["policy_revision"] = policy_revision
    return parse_w1_dispatch(raw)


def _binding_payload(binding: W1PrivateBinding) -> dict[str, object]:
    return {
        "owner_user_id": binding.owner_user_id,
        "owner_deletion_epoch": binding.owner_deletion_epoch,
        "command_id": binding.command_id,
        "job_id": binding.job_id,
        "execution_fence": binding.execution_fence,
    }


class _TrustedPrivateAuthorityClient:
    def __init__(
        self,
        dispatch: W1Dispatch,
        *,
        scope: PrivateDeletionScope | None = None,
    ) -> None:
        self.scope = scope or PrivateDeletionScope(kind="ACCOUNT", project_id=None)
        self.events: list[str] = []
        self._dispatch = dispatch

    def lookup_current_scope(self, binding: W1PrivateBinding) -> CurrentWriteScopeLookupResponse:
        self.events.append("scope")
        return CurrentWriteScopeLookupResponse.model_validate(
            {
                "schema_version": "w1.private.w2-current-write-scope-lookup.v1",
                **_binding_payload(binding),
                "scope": self.scope.to_mapping(),
            },
            strict=True,
        )

    def authorize_write(
        self,
        binding: W1PrivateBinding,
        scope: PrivateDeletionScope,
    ) -> PrivateWriteAuthorityResponse:
        self.events.append("write")
        return PrivateWriteAuthorityResponse.model_validate(
            {
                "schema_version": "w1.private.w2-write-authority.v1",
                **_binding_payload(binding),
                "scope": scope.to_mapping(),
                "authority_ref": "test:w1-authenticated",
            },
            strict=True,
        )


def handle_collection_dispatch(dispatch: W1Dispatch, **kwargs: Any) -> object:
    """Test adapter: inject deterministic protected W1 decisions."""

    return _handle_collection_dispatch(
        dispatch,
        private_authority_client=_TrustedPrivateAuthorityClient(dispatch),
        **kwargs,
    )


def _runtime_config(
    dispatch: W1Dispatch,
    *,
    policy_revision: int = 3,
) -> RuntimeSourceConfigFile:
    return RuntimeSourceConfigFile.model_validate_json(
        json.dumps(
            {
                "schema_version": "w2.source-runtime-config.v1",
                "claim_lease_seconds": 120,
                "sources": {
                    str(dispatch.payload.source_id): {
                        "policy_revision": policy_revision,
                        "robots_permission": "allowed",
                        "result_version": 1,
                        "language": "ko",
                        "redirect_robots_permissions": [],
                        "limits": {
                            "site_concurrency": 1,
                            "global_concurrency": 2,
                            "source_ttl_seconds": 300,
                            "max_response_bytes": 1_048_576,
                            "max_decompressed_bytes": 2_097_152,
                            "connect_timeout_seconds": 3.0,
                            "read_timeout_seconds": 5.0,
                            "max_redirects": 2,
                            "general_retry_limit": 0,
                            "retention_days": 7,
                        },
                    }
                },
            }
        )
    )


def _available(dispatch: W1Dispatch) -> LookupResponse:
    return LookupResponse(
        schema_version="w1.private.command-lookup.v1",
        command_id=dispatch.payload.command_id,
        status="AVAILABLE",
        reason_code=None,
        command=dispatch.payload,
    )


def _not_found(dispatch: W1Dispatch) -> LookupResponse:
    return LookupResponse(
        schema_version="w1.private.command-lookup.v1",
        command_id=dispatch.payload.command_id,
        status="NOT_FOUND",
        reason_code="COMMAND_NOT_FOUND",
        command=None,
    )


class _RecordingLookupClient:
    def __init__(self, response: LookupResponse) -> None:
        self._response = response
        self.dispatches: list[W1Dispatch] = []

    def lookup_dispatch(self, dispatch: W1Dispatch) -> LookupResponse:
        self.dispatches.append(dispatch)
        return self._response


class _UnexpectedCallable:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, *args: Any, **kwargs: Any) -> Any:
        self.calls += 1
        raise AssertionError("initial lookup failure must not create an execution resource")


class _UnexpectedInputProvider:
    def __init__(self) -> None:
        self.calls = 0

    def load(self, command: object) -> object:
        self.calls += 1
        raise AssertionError("initial lookup failure must not load approved source input")


class _Session:
    def begin(self) -> _Session:
        return self

    def __enter__(self) -> _Session:
        return self

    def __exit__(self, *args: object) -> bool:
        return False


class _SessionFactory:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self) -> _Session:
        self.calls += 1
        return _Session()


@dataclass(frozen=True)
class _RuntimeInput:
    policy_revision: int
    collection_strategy: str = "static"


class _RuntimeInputProvider:
    def __init__(self, value: _RuntimeInput, events: list[str]) -> None:
        self._value = value
        self._events = events
        self.commands: list[object] = []

    def load(self, command: object) -> _RuntimeInput:
        self.commands.append(command)
        self._events.append("provider")
        return self._value


def _heartbeat_type(
    events: list[str],
    *,
    lose_on_ensure: int | None = None,
) -> type[object]:
    class _Heartbeat:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self._ensures = 0
            self.lost = False
            events.append("heartbeat-create")

        def start(self) -> None:
            events.append("heartbeat-start")

        def ensure_active(self) -> None:
            self._ensures += 1
            events.append("heartbeat-ensure")
            if lose_on_ensure == self._ensures:
                self.lost = True
                raise RuntimeAuthorizationError("collection runtime claim is no longer active")

        def stop_and_join(self) -> None:
            events.append("heartbeat-stop-join")

    return _Heartbeat


def _execution_type(
    events: list[str],
    *,
    prepared: object,
    fail_run: bool = False,
    fail_close: bool = False,
) -> tuple[type[object], list[object]]:
    instances: list[object] = []

    class _Execution:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.closed = 0
            instances.append(self)
            events.append("execution-create")

        def run_once(self, context: LookupGatedExecutionContext) -> object:
            events.append("execution-run")
            context.enter_stage(CollectionStage.POLICY, policy_revision=3)
            context.enter_stage(CollectionStage.FETCH, policy_revision=3)
            if fail_run:
                raise TimeoutError("synthetic fetch timeout")
            context.enter_stage(CollectionStage.PARSE, policy_revision=3)
            return prepared

        def close(self) -> None:
            self.closed += 1
            events.append("execution-close")
            if fail_close:
                raise RuntimeError("synthetic close failure")

    return _Execution, instances


def _uuid_factory(*values: UUID) -> Any:
    iterator = iter(values)
    return lambda: next(iterator)


def _patch_runtime_happy_path(
    monkeypatch: pytest.MonkeyPatch,
    *,
    events: list[str],
    input_value: _RuntimeInput,
    heartbeat_type: type[object],
    execution_type: type[object],
    attempt_state: tuple[str, int, UUID] | None = None,
) -> dict[str, Any]:
    calls: dict[str, Any] = {
        "claim": [],
        "commit": [],
        "load": [],
        "release": [],
        "release_reservation": [],
        "replay": [],
        "renew": [],
        "reserve": [],
    }

    def load(*args: object, **kwargs: object) -> object | None:
        calls["load"].append((args, kwargs))
        if attempt_state is None:
            return None
        return SimpleNamespace(
            state=attempt_state[0],
            effective_policy_revision=attempt_state[1],
            attempt_id=attempt_state[2],
            private_scope_kind="ACCOUNT",
            project_id=None,
        )

    def reserve(*args: object, **kwargs: object) -> object:
        calls["reserve"].append((args, kwargs))
        events.append("reserve")
        return SimpleNamespace(
            attempt_id=ATTEMPT_ID,
            effective_policy_revision=input_value.policy_revision,
        )

    def claim(*args: object, **kwargs: object) -> object:
        calls["claim"].append((args, kwargs))
        events.append("claim")
        return SimpleNamespace(attempt_id=ATTEMPT_ID)

    def renew(*args: object, **kwargs: object) -> None:
        calls["renew"].append((args, kwargs))
        events.append("renew")

    def release(*args: object, **kwargs: object) -> None:
        calls["release"].append((args, kwargs))
        events.append("release")

    def release_reservation(*args: object, **kwargs: object) -> None:
        calls["release_reservation"].append((args, kwargs))
        events.append("release-reservation")

    def commit(*args: object, **kwargs: object) -> object:
        calls["commit"].append((args, kwargs))
        events.append("commit")
        return "staged-proposal"

    def replay(*args: object, **kwargs: object) -> object:
        calls["replay"].append((args, kwargs))
        events.append("replay")
        return "replayed-proposal"

    operations = source_runtime.RuntimeStoreOperations(
        load_attempt=load,
        reserve_attempt=reserve,
        claim_attempt=claim,
        renew_claim=renew,
        release_claim=release,
        release_reservation=release_reservation,
        commit_candidate=commit,
        replay_candidate=replay,
    )
    calls["operations"] = operations

    monkeypatch.setattr(source_runtime, "StaticCollectionInput", _RuntimeInput)
    monkeypatch.setattr(source_runtime, "StaticCollectionExecution", execution_type)
    monkeypatch.setattr(source_runtime, "_ClaimHeartbeat", heartbeat_type)
    return calls


class _SequencedLookupClient:
    def __init__(
        self,
        *,
        unavailable_call: int | None = None,
    ) -> None:
        self._unavailable_call = unavailable_call
        self.dispatches: list[W1Dispatch] = []

    def lookup_dispatch(self, dispatch: W1Dispatch) -> LookupResponse:
        self.dispatches.append(dispatch)
        if self._unavailable_call == len(self.dispatches):
            return _not_found(dispatch)
        return _available(dispatch)


def test_each_collection_transaction_has_a_distinct_w1_decision(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Reusing one authority_ref would let revocation miss later DB transactions."""

    dispatch = _dispatch()
    expected_digest = dispatch_digest(dispatch)
    real_heartbeat_type = source_runtime._ClaimHeartbeat

    class _DistinctAuthorityClient(_TrustedPrivateAuthorityClient):
        def __init__(self, received: W1Dispatch) -> None:
            super().__init__(received)
            self.authority_refs: list[str] = []

        def authorize_write(
            self,
            binding: W1PrivateBinding,
            scope: PrivateDeletionScope,
        ) -> PrivateWriteAuthorityResponse:
            self.events.append("write")
            authority_ref = f"test:w1-decision:{len(self.authority_refs) + 1}"
            self.authority_refs.append(authority_ref)
            return PrivateWriteAuthorityResponse.model_validate(
                {
                    "schema_version": "w1.private.w2-write-authority.v1",
                    **_binding_payload(binding),
                    "scope": scope.to_mapping(),
                    "authority_ref": authority_ref,
                },
                strict=True,
            )

    class _OneRenewalHeartbeat:
        lost = False

        def __init__(
            self,
            session_factory: object,
            command_id: UUID,
            claim_token: UUID,
            **kwargs: Any,
        ) -> None:
            self._session_factory = session_factory
            self._command_id = command_id
            self._claim_token = claim_token
            self._lease_seconds = kwargs["lease_seconds"]
            self._renew_claim = kwargs["renew_claim"]
            self._authority_provider = kwargs.get("authority_provider")
            self._legacy_scope = kwargs.get("private_scope")
            self._expected_dispatch_digest = kwargs.get("expected_dispatch_digest")

        def start(self) -> None:
            scope = (
                self._authority_provider()
                if self._authority_provider is not None
                else self._legacy_scope
            )
            renewal_kwargs: dict[str, object] = {
                "claim_token": self._claim_token,
                "lease_seconds": self._lease_seconds,
                "private_scope": scope,
            }
            if self._expected_dispatch_digest is not None:
                renewal_kwargs["expected_dispatch_digest"] = self._expected_dispatch_digest
            self._renew_claim(
                self._session_factory,
                self._command_id,
                **renewal_kwargs,
            )

        def ensure_active(self) -> None:
            return None

        def stop_and_join(self) -> None:
            return None

    authority = _DistinctAuthorityClient(dispatch)
    operation_refs: list[tuple[str, str]] = []

    def record_operations(
        name: str, calls: list[tuple[tuple[object, ...], dict[str, Any]]]
    ) -> None:
        for _args, kwargs in calls:
            scope = kwargs["private_scope"]
            assert isinstance(scope, PrivateWriteScope)
            operation_refs.append((name, scope.authority_ref))

    success_events: list[str] = []
    success_input = _RuntimeInput(policy_revision=3)
    success_execution, _instances = _execution_type(success_events, prepared="prepared")
    success_calls = _patch_runtime_happy_path(
        monkeypatch,
        events=success_events,
        input_value=success_input,
        heartbeat_type=_OneRenewalHeartbeat,
        execution_type=success_execution,
    )
    assert (
        _handle_collection_dispatch(
            dispatch,
            session_factory=_SessionFactory(),
            lookup_client=_SequencedLookupClient(),
            private_authority_client=authority,
            input_provider=_RuntimeInputProvider(success_input, success_events),
            collector_factory=lambda: object(),
            parser=lambda candidate: candidate,
            runtime_config=_runtime_config(dispatch),
            clock=lambda: NOW,
            uuid_factory=_uuid_factory(CLAIM_TOKEN, STAGED_MESSAGE_ID),
            store_operations=success_calls["operations"],
        )
        == "staged-proposal"
    )
    record_operations("reservation", success_calls["reserve"])
    record_operations("claim", success_calls["claim"])
    record_operations("renew", success_calls["renew"])
    record_operations("candidate-commit", success_calls["commit"])

    replay_events: list[str] = []
    replay_input = _RuntimeInput(policy_revision=3)
    replay_execution, _instances = _execution_type(replay_events, prepared="unused")
    replay_calls = _patch_runtime_happy_path(
        monkeypatch,
        events=replay_events,
        input_value=replay_input,
        heartbeat_type=_OneRenewalHeartbeat,
        execution_type=replay_execution,
        attempt_state=("PERSISTED", 3, ATTEMPT_ID),
    )
    assert (
        _handle_collection_dispatch(
            dispatch,
            session_factory=_SessionFactory(),
            lookup_client=_SequencedLookupClient(),
            private_authority_client=authority,
            input_provider=_RuntimeInputProvider(replay_input, replay_events),
            collector_factory=lambda: object(),
            parser=lambda candidate: candidate,
            runtime_config=_runtime_config(dispatch),
            clock=lambda: NOW,
            uuid_factory=_uuid_factory(),
            store_operations=replay_calls["operations"],
        )
        == "replayed-proposal"
    )
    record_operations("replay", replay_calls["replay"])

    failure_events: list[str] = []
    failure_input = _RuntimeInput(policy_revision=3)
    failure_execution, _instances = _execution_type(
        failure_events,
        prepared="unused",
        fail_run=True,
    )
    failure_calls = _patch_runtime_happy_path(
        monkeypatch,
        events=failure_events,
        input_value=failure_input,
        heartbeat_type=_OneRenewalHeartbeat,
        execution_type=failure_execution,
    )
    with pytest.raises(TimeoutError, match="synthetic fetch timeout"):
        _handle_collection_dispatch(
            dispatch,
            session_factory=_SessionFactory(),
            lookup_client=_SequencedLookupClient(),
            private_authority_client=authority,
            input_provider=_RuntimeInputProvider(failure_input, failure_events),
            collector_factory=lambda: object(),
            parser=lambda candidate: candidate,
            runtime_config=_runtime_config(dispatch),
            clock=lambda: NOW,
            uuid_factory=_uuid_factory(CLAIM_TOKEN),
            store_operations=failure_calls["operations"],
        )
    record_operations("reservation", failure_calls["reserve"])
    record_operations("claim", failure_calls["claim"])
    record_operations("heartbeat-renew", failure_calls["renew"])
    record_operations("normal-release", failure_calls["release"])

    thread_renewed = Event()
    thread_renewals: list[tuple[tuple[object, ...], dict[str, Any]]] = []

    def record_thread_renewal(*args: object, **kwargs: Any) -> None:
        thread_renewals.append((args, kwargs))
        thread_renewed.set()
        raise RuntimeError("stop deterministic heartbeat after one renewal")

    binding = W1PrivateBinding.from_collection(dispatch.payload)
    scope = PrivateDeletionScope(kind="ACCOUNT", project_id=None)
    real_heartbeat = real_heartbeat_type(
        _SessionFactory(),
        dispatch.payload.command_id,
        CLAIM_TOKEN,
        lease_seconds=120,
        interval_seconds=0.001,
        renew_claim=record_thread_renewal,
        authority_provider=lambda: source_runtime._authorize_write(binding, scope, authority),
        expected_dispatch_digest=expected_digest,
    )
    real_heartbeat.start()
    assert thread_renewed.wait(timeout=1.0)
    real_heartbeat.stop_and_join()
    assert real_heartbeat.lost is True
    record_operations("thread-heartbeat-renew", thread_renewals)

    labels = [name for name, _ref in operation_refs]
    assert labels == [
        "reservation",
        "claim",
        "renew",
        "renew",
        "candidate-commit",
        "replay",
        "reservation",
        "claim",
        "heartbeat-renew",
        "normal-release",
        "thread-heartbeat-renew",
    ]
    refs = [ref for _name, ref in operation_refs]
    assert len(refs) == len(set(refs)) == len(authority.authority_refs)
    for calls in (
        success_calls["claim"],
        success_calls["renew"],
        failure_calls["claim"],
        failure_calls["renew"],
        failure_calls["release"],
        thread_renewals,
    ):
        assert all(kwargs["expected_dispatch_digest"] == expected_digest for _args, kwargs in calls)


def test_cancelled_claim_uses_cleanup_not_current_write() -> None:
    """A semantic current-write denial may release only the exact claimed row."""

    dispatch = _dispatch()
    binding = W1PrivateBinding.from_collection(dispatch.payload)
    scope = PrivateDeletionScope(kind="ACCOUNT", project_id=None)
    events: list[str] = []
    released: list[dict[str, Any]] = []

    class _CancelledAuthorityClient:
        def authorize_write(
            self,
            received_binding: W1PrivateBinding,
            received_scope: PrivateDeletionScope,
        ) -> PrivateWriteAuthorityResponse:
            assert received_binding == binding
            assert received_scope == scope
            events.append("write-denied")
            raise W1LookupClientError("HTTP_403")

        def authorize_terminal_cleanup(
            self,
            received_binding: W1PrivateBinding,
            received_scope: PrivateDeletionScope,
            cleanup_kind: str,
        ) -> TerminalCleanupAuthorityResponse:
            assert received_binding == binding
            assert received_scope == scope
            assert cleanup_kind == "CLAIM_RELEASE"
            events.append("cleanup")
            return TerminalCleanupAuthorityResponse.model_validate(
                {
                    "schema_version": "w1.private.w2-terminal-cleanup.v1",
                    **_binding_payload(binding),
                    "scope": scope.to_mapping(),
                    "cleanup_kind": cleanup_kind,
                    "authority_ref": "test:w1-terminal-cleanup",
                    "allowed_effect": "OWNER_LOCKED_PRIVATE_CLEANUP_ONLY",
                },
                strict=True,
            )

    def release_claim(*args: object, **kwargs: Any) -> object:
        events.append("release")
        released.append(kwargs)
        return object()

    source_runtime._release_after_clean_failure(
        _SessionFactory(),
        binding.command_id,
        CLAIM_TOKEN,
        binding=binding,
        scope=scope,
        authority_client=_CancelledAuthorityClient(),
        expected_dispatch_digest=dispatch_digest(dispatch),
        store_operations=source_runtime.RuntimeStoreOperations(release_claim=release_claim),
    )

    assert events == ["write-denied", "cleanup", "release"]
    assert len(released) == 1
    cleanup = released[0]["private_scope"]
    assert isinstance(cleanup, PrivateTerminalCleanupAuthority)
    assert cleanup.cleanup_kind == "CLAIM_RELEASE"
    assert released[0]["private_binding"] == binding


@pytest.mark.parametrize("changed_cleanup_binding", [False, True])
def test_cancelled_reservation_uses_cleanup_before_claim(
    monkeypatch: pytest.MonkeyPatch,
    changed_cleanup_binding: bool,
) -> None:
    """A post-reservation semantic denial may tombstone only that exact row."""

    dispatch = _dispatch()
    binding = W1PrivateBinding.from_collection(dispatch.payload)
    events: list[str] = []
    input_value = _RuntimeInput(policy_revision=3)
    execution_type, _instances = _execution_type(events, prepared="prepared")
    calls = _patch_runtime_happy_path(
        monkeypatch,
        events=events,
        input_value=input_value,
        heartbeat_type=_heartbeat_type(events),
        execution_type=execution_type,
    )

    class _CancelledAfterReservationAuthority(_TrustedPrivateAuthorityClient):
        def __init__(self, received: W1Dispatch) -> None:
            super().__init__(received)
            self.write_calls = 0

        def authorize_write(
            self,
            received_binding: W1PrivateBinding,
            received_scope: PrivateDeletionScope,
        ) -> PrivateWriteAuthorityResponse:
            self.write_calls += 1
            if self.write_calls == 2:
                assert received_binding == binding
                self.events.append("write-denied")
                raise W1LookupClientError("HTTP_403")
            return super().authorize_write(received_binding, received_scope)

        def authorize_terminal_cleanup(
            self,
            received_binding: W1PrivateBinding,
            received_scope: PrivateDeletionScope,
            cleanup_kind: str,
        ) -> TerminalCleanupAuthorityResponse:
            assert received_binding == binding
            assert received_scope == self.scope
            assert cleanup_kind == "RESERVATION_RELEASE"
            self.events.append("cleanup")
            response_binding = _binding_payload(binding)
            if changed_cleanup_binding:
                response_binding["job_id"] = UUID("99999999-9999-4999-8999-999999999999")
            return TerminalCleanupAuthorityResponse.model_validate(
                {
                    "schema_version": "w1.private.w2-terminal-cleanup.v1",
                    **response_binding,
                    "scope": self.scope.to_mapping(),
                    "cleanup_kind": cleanup_kind,
                    "authority_ref": "test:w1-reservation-cleanup",
                    "allowed_effect": "OWNER_LOCKED_PRIVATE_CLEANUP_ONLY",
                },
                strict=True,
            )

    authority = _CancelledAfterReservationAuthority(dispatch)
    with pytest.raises(RuntimeAuthorizationError, match="semantically denied"):
        _handle_collection_dispatch(
            dispatch,
            session_factory=_SessionFactory(),
            lookup_client=_SequencedLookupClient(),
            private_authority_client=authority,
            input_provider=_RuntimeInputProvider(input_value, events),
            collector_factory=_UnexpectedCallable(),
            parser=_UnexpectedCallable(),
            runtime_config=_runtime_config(dispatch),
            clock=lambda: NOW,
            uuid_factory=_uuid_factory(ATTEMPT_ID, CLAIM_TOKEN),
            store_operations=calls["operations"],
        )

    assert authority.events == ["scope", "write", "write-denied", "cleanup"]
    assert len(calls["reserve"]) == 1
    assert calls["claim"] == []
    if changed_cleanup_binding:
        assert calls["release_reservation"] == []
        return
    assert len(calls["release_reservation"]) == 1
    release_kwargs = calls["release_reservation"][0][1]
    cleanup = release_kwargs["private_scope"]
    assert isinstance(cleanup, PrivateTerminalCleanupAuthority)
    assert cleanup.cleanup_kind == "RESERVATION_RELEASE"
    assert release_kwargs["private_binding"] == binding
    assert release_kwargs["expected_dispatch_digest"] == dispatch_digest(dispatch)


@pytest.mark.parametrize(
    "failure",
    [
        W1LookupClientError("HTTP_503"),
        W1LookupClientError("TRANSPORT_FAILURE"),
        W1WireContractError("malformed response"),
    ],
)
def test_reservation_cleanup_is_not_fallback_for_uncertain_error(
    monkeypatch: pytest.MonkeyPatch,
    failure: Exception,
) -> None:
    dispatch = _dispatch()
    events: list[str] = []
    input_value = _RuntimeInput(policy_revision=3)
    execution_type, _instances = _execution_type(events, prepared="prepared")
    calls = _patch_runtime_happy_path(
        monkeypatch,
        events=events,
        input_value=input_value,
        heartbeat_type=_heartbeat_type(events),
        execution_type=execution_type,
    )

    class _UncertainAfterReservationAuthority(_TrustedPrivateAuthorityClient):
        def __init__(self, received: W1Dispatch) -> None:
            super().__init__(received)
            self.write_calls = 0
            self.cleanup_calls = 0

        def authorize_write(
            self,
            received_binding: W1PrivateBinding,
            received_scope: PrivateDeletionScope,
        ) -> PrivateWriteAuthorityResponse:
            self.write_calls += 1
            if self.write_calls == 2:
                raise failure
            return super().authorize_write(received_binding, received_scope)

        def authorize_terminal_cleanup(self, *args: object) -> TerminalCleanupAuthorityResponse:
            self.cleanup_calls += 1
            raise AssertionError("uncertain write failure must not request terminal cleanup")

    authority = _UncertainAfterReservationAuthority(dispatch)
    with pytest.raises(RuntimeAuthorizationError, match="private write authority"):
        _handle_collection_dispatch(
            dispatch,
            session_factory=_SessionFactory(),
            lookup_client=_SequencedLookupClient(),
            private_authority_client=authority,
            input_provider=_RuntimeInputProvider(input_value, events),
            collector_factory=_UnexpectedCallable(),
            parser=_UnexpectedCallable(),
            runtime_config=_runtime_config(dispatch),
            clock=lambda: NOW,
            uuid_factory=_uuid_factory(ATTEMPT_ID, CLAIM_TOKEN),
            store_operations=calls["operations"],
        )

    assert len(calls["reserve"]) == 1
    assert calls["claim"] == []
    assert calls["release_reservation"] == []
    assert authority.cleanup_calls == 0


@pytest.mark.parametrize(
    ("failure", "release_fails"),
    [
        (W1LookupClientError("HTTP_503"), False),
        (W1LookupClientError("TIMEOUT"), False),
        (W1WireContractError("malformed response"), False),
        (None, True),
    ],
)
def test_terminal_cleanup_is_not_fallback_for_uncertain_or_local_failure(
    failure: Exception | None,
    release_fails: bool,
) -> None:
    dispatch = _dispatch()
    binding = W1PrivateBinding.from_collection(dispatch.payload)
    scope = PrivateDeletionScope(kind="ACCOUNT", project_id=None)
    cleanup_calls: list[str] = []

    class _AuthorityClient(_TrustedPrivateAuthorityClient):
        def authorize_write(
            self,
            received_binding: W1PrivateBinding,
            received_scope: PrivateDeletionScope,
        ) -> PrivateWriteAuthorityResponse:
            if failure is not None:
                raise failure
            return super().authorize_write(received_binding, received_scope)

        def authorize_terminal_cleanup(self, *args: object) -> TerminalCleanupAuthorityResponse:
            cleanup_calls.append("cleanup")
            raise AssertionError("terminal cleanup must not follow an uncertain or local failure")

    def release_claim(*args: object, **kwargs: Any) -> object:
        if release_fails:
            raise PrivateScopeRejected("local binding mismatch")
        return object()

    source_runtime._release_after_clean_failure(
        _SessionFactory(),
        binding.command_id,
        CLAIM_TOKEN,
        binding=binding,
        scope=scope,
        authority_client=_AuthorityClient(dispatch),
        expected_dispatch_digest=dispatch_digest(dispatch),
        store_operations=source_runtime.RuntimeStoreOperations(release_claim=release_claim),
    )

    assert cleanup_calls == []


@pytest.mark.parametrize("failure", ["wrong-echo", "http-503", "timeout"])
def test_first_reservation_authority_failure_has_zero_writes_and_no_scope_probe(
    monkeypatch: pytest.MonkeyPatch,
    failure: str,
) -> None:
    """Accepting an untrusted/failed scope lookup would permit a reservation write."""

    dispatch = _dispatch()
    original = deepcopy(dispatch.model_dump(mode="python"))
    events: list[str] = []
    input_value = _RuntimeInput(policy_revision=3)
    execution_type, _instances = _execution_type(events, prepared="prepared")
    calls = _patch_runtime_happy_path(
        monkeypatch,
        events=events,
        input_value=input_value,
        heartbeat_type=_heartbeat_type(events),
        execution_type=execution_type,
    )
    cancelled = Event()

    class _FailingPrivateAuthorityClient(_TrustedPrivateAuthorityClient):
        def __init__(self, received: W1Dispatch) -> None:
            super().__init__(received)
            self.bindings: list[W1PrivateBinding] = []

        def lookup_current_scope(
            self,
            binding: W1PrivateBinding,
        ) -> CurrentWriteScopeLookupResponse:
            self.events.append("scope")
            self.bindings.append(binding)
            if failure == "http-503":
                cancelled.set()
                raise W1LookupClientError("HTTP_503")
            if failure == "timeout":
                cancelled.set()
                raise W1LookupClientError("TRANSPORT_FAILURE")
            payload = _binding_payload(binding)
            payload["owner_user_id"] = UUID("99999999-9999-4999-8999-999999999999")
            return CurrentWriteScopeLookupResponse.model_validate(
                {
                    "schema_version": "w1.private.w2-current-write-scope-lookup.v1",
                    **payload,
                    "scope": {"type": "ACCOUNT"},
                },
                strict=True,
            )

    authority = _FailingPrivateAuthorityClient(dispatch)
    session_factory = _SessionFactory()
    input_provider = _RuntimeInputProvider(input_value, events)
    collector_factory = _UnexpectedCallable()
    parser = _UnexpectedCallable()
    uuid_factory = _UnexpectedCallable()
    with pytest.raises(RuntimeAuthorizationError):
        _handle_collection_dispatch(
            dispatch,
            session_factory=session_factory,
            lookup_client=_SequencedLookupClient(),
            private_authority_client=authority,
            input_provider=input_provider,
            collector_factory=collector_factory,
            parser=parser,
            runtime_config=_runtime_config(dispatch),
            clock=lambda: NOW,
            uuid_factory=uuid_factory,
            store_operations=calls["operations"],
        )

    assert authority.events == ["scope"]
    assert authority.bindings == [W1PrivateBinding.from_collection(dispatch.payload)]
    assert cancelled.is_set() is (failure in {"http-503", "timeout"})
    assert session_factory.calls == 1
    assert len(calls["load"]) == 1
    assert calls["reserve"] == []
    assert input_provider.commands == []
    assert collector_factory.calls == parser.calls == uuid_factory.calls == 0
    assert events == []
    assert dispatch.model_dump(mode="python") == original


def test_initial_unavailable_lookup_has_no_db_or_execution_side_effects() -> None:
    """Removing the initial lookup guard must fail before any DB/provider/collector action."""

    dispatch = _dispatch()
    lookup = _RecordingLookupClient(_not_found(dispatch))
    session_factory = _UnexpectedCallable()
    input_provider = _UnexpectedInputProvider()
    collector_factory = _UnexpectedCallable()
    parser = _UnexpectedCallable()

    with pytest.raises(RuntimeAuthorizationError):
        handle_collection_dispatch(
            dispatch,
            session_factory=session_factory,
            lookup_client=lookup,
            input_provider=input_provider,
            collector_factory=collector_factory,
            parser=parser,
            runtime_config=_runtime_config(dispatch),
            clock=_UnexpectedCallable(),
            uuid_factory=_UnexpectedCallable(),
        )

    assert lookup.dispatches == [dispatch]
    assert session_factory.calls == 0
    assert input_provider.calls == 0
    assert collector_factory.calls == 0
    assert parser.calls == 0


def test_non_policy_wire_command_is_rejected_before_lookup_or_db_work() -> None:
    """Changing the initial POLICY/null command gate must leave no authorization side effect."""

    dispatch = _dispatch(resume_stage="fetch", policy_revision=3)
    lookup = _RecordingLookupClient(_available(dispatch))
    session_factory = _UnexpectedCallable()

    with pytest.raises(RuntimeAuthorizationError):
        handle_collection_dispatch(
            dispatch,
            session_factory=session_factory,
            lookup_client=lookup,
            input_provider=_UnexpectedInputProvider(),
            collector_factory=_UnexpectedCallable(),
            parser=_UnexpectedCallable(),
            runtime_config=_runtime_config(dispatch),
            clock=_UnexpectedCallable(),
            uuid_factory=_UnexpectedCallable(),
        )

    assert lookup.dispatches == []
    assert session_factory.calls == 0


def test_context_derives_effective_policy_without_mutating_wire_dispatch() -> None:
    """Assigning the provider revision to the received W1 command would corrupt replay identity."""

    dispatch = _dispatch()
    original_payload = deepcopy(dispatch.payload.model_dump(mode="python"))
    lookup = _RecordingLookupClient(_available(dispatch))
    context = LookupGatedExecutionContext(
        dispatch=dispatch,
        lookup_client=lookup,
        _effective_command=dispatch.payload,
    )

    response = context.enter_stage(CollectionStage.FETCH, policy_revision=3)

    assert response.status == "AVAILABLE"
    assert lookup.dispatches == [dispatch]
    assert dispatch.payload.model_dump(mode="python") == original_payload
    assert dispatch.payload.policy_revision is None
    assert context.command is not dispatch.payload
    assert context.command.resume_stage is CollectionStage.FETCH
    assert context.command.policy_revision == 3


def test_successful_persist_stops_heartbeat_before_exact_claim_candidate_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Moving commit before heartbeat join could race the PERSISTED claim-clear transition."""

    dispatch = _dispatch()
    events: list[str] = []
    input_value = _RuntimeInput(policy_revision=3)
    execution_type, instances = _execution_type(events, prepared="prepared")
    calls = _patch_runtime_happy_path(
        monkeypatch,
        events=events,
        input_value=input_value,
        heartbeat_type=_heartbeat_type(events),
        execution_type=execution_type,
    )
    lookup = _SequencedLookupClient()
    session_factory = _SessionFactory()
    input_provider = _RuntimeInputProvider(input_value, events)

    proposal = handle_collection_dispatch(
        dispatch,
        session_factory=session_factory,
        lookup_client=lookup,
        input_provider=input_provider,
        collector_factory=lambda: object(),
        parser=lambda candidate: candidate,
        runtime_config=_runtime_config(dispatch),
        clock=lambda: NOW,
        uuid_factory=_uuid_factory(CLAIM_TOKEN, STAGED_MESSAGE_ID),
        store_operations=calls["operations"],
    )

    assert proposal == "staged-proposal"
    assert lookup.dispatches == [dispatch] * 5
    assert input_provider.commands == [dispatch.payload]
    assert len(calls["reserve"]) == len(calls["claim"]) == len(calls["renew"]) == 1
    assert calls["release"] == []
    assert len(calls["commit"]) == 1
    commit_args, commit_kwargs = calls["commit"][0]
    assert commit_args[1].payload.policy_revision is None
    assert commit_args[2].resume_stage is CollectionStage.PERSIST
    assert commit_args[2].policy_revision == 3
    assert commit_args[3] == "prepared"
    assert commit_kwargs["claim_token"] == CLAIM_TOKEN
    assert commit_kwargs["staged_message_id"] == STAGED_MESSAGE_ID
    assert instances[0].closed == 1
    assert events.index("renew") < events.index("heartbeat-stop-join")
    assert events.index("heartbeat-stop-join") < events.index("execution-close")
    assert events.index("execution-close") < events.index("commit")
    assert "deliver" not in events


def test_rendered_strategy_uses_only_its_explicit_approved_factory(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    dispatch = _dispatch()
    events: list[str] = []
    input_value = _RuntimeInput(policy_revision=3, collection_strategy="rendered")
    execution_type, _instances = _execution_type(events, prepared="rendered-prepared")
    calls = _patch_runtime_happy_path(
        monkeypatch,
        events=events,
        input_value=input_value,
        heartbeat_type=_heartbeat_type(events),
        execution_type=execution_type,
    )
    selected: list[object] = []

    def rendered_factory(value: object) -> object:
        selected.append(value)
        return object()

    proposal = handle_collection_dispatch(
        dispatch,
        session_factory=_SessionFactory(),
        lookup_client=_SequencedLookupClient(),
        input_provider=_RuntimeInputProvider(input_value, events),
        collector_factory=lambda: pytest.fail("static collector must not run"),
        rendered_collector_factory=rendered_factory,
        parser=lambda candidate: candidate,
        runtime_config=_runtime_config(dispatch),
        clock=lambda: NOW,
        uuid_factory=_uuid_factory(CLAIM_TOKEN, STAGED_MESSAGE_ID),
        store_operations=calls["operations"],
    )

    assert proposal == "staged-proposal"
    assert selected == [input_value]


@pytest.mark.parametrize(
    ("failed_stage", "unavailable_call"),
    [
        (CollectionStage.FETCH, 3),
        (CollectionStage.PARSE, 4),
        (CollectionStage.PERSIST, 5),
    ],
)
def test_unavailable_execution_stage_never_commits_candidate_and_releases_clean_claim(
    monkeypatch: pytest.MonkeyPatch,
    failed_stage: CollectionStage,
    unavailable_call: int,
) -> None:
    """Removing a fresh lookup at FETCH/PARSE/PERSIST would permit stale candidate writes."""

    dispatch = _dispatch()
    events: list[str] = []
    input_value = _RuntimeInput(policy_revision=3)
    execution_type, instances = _execution_type(events, prepared="prepared")
    calls = _patch_runtime_happy_path(
        monkeypatch,
        events=events,
        input_value=input_value,
        heartbeat_type=_heartbeat_type(events),
        execution_type=execution_type,
    )
    lookup = _SequencedLookupClient(unavailable_call=unavailable_call)

    with pytest.raises(RuntimeAuthorizationError):
        handle_collection_dispatch(
            dispatch,
            session_factory=_SessionFactory(),
            lookup_client=lookup,
            input_provider=_RuntimeInputProvider(input_value, events),
            collector_factory=lambda: object(),
            parser=lambda candidate: candidate,
            runtime_config=_runtime_config(dispatch),
            clock=lambda: NOW,
            uuid_factory=_uuid_factory(CLAIM_TOKEN),
            store_operations=calls["operations"],
        )

    assert len(lookup.dispatches) == unavailable_call
    assert calls["commit"] == []
    assert len(calls["release"]) == 1
    assert calls["release"][0][1]["claim_token"] == CLAIM_TOKEN
    assert instances[0].closed == 1
    assert events.index("heartbeat-stop-join") < events.index("execution-close")
    assert events.index("execution-close") < events.index("release")
    assert failed_stage in {CollectionStage.FETCH, CollectionStage.PARSE, CollectionStage.PERSIST}


def test_lost_heartbeat_claim_closes_work_but_never_releases_or_commits_stale_token(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A lost lease must cancel local work and leave takeover to DB-clock expiry."""

    dispatch = _dispatch()
    events: list[str] = []
    input_value = _RuntimeInput(policy_revision=3)
    execution_type, instances = _execution_type(events, prepared="prepared")
    calls = _patch_runtime_happy_path(
        monkeypatch,
        events=events,
        input_value=input_value,
        heartbeat_type=_heartbeat_type(events, lose_on_ensure=2),
        execution_type=execution_type,
    )

    with pytest.raises(RuntimeAuthorizationError, match="claim is no longer active"):
        handle_collection_dispatch(
            dispatch,
            session_factory=_SessionFactory(),
            lookup_client=_SequencedLookupClient(),
            input_provider=_RuntimeInputProvider(input_value, events),
            collector_factory=lambda: object(),
            parser=lambda candidate: candidate,
            runtime_config=_runtime_config(dispatch),
            clock=lambda: NOW,
            uuid_factory=_uuid_factory(CLAIM_TOKEN),
            store_operations=calls["operations"],
        )

    assert calls["commit"] == []
    assert calls["release"] == []
    assert instances[0].closed == 1
    assert "heartbeat-stop-join" in events


def test_uncertain_close_after_precommit_failure_does_not_eagerly_release_claim(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Releasing after a failed close could let another worker overlap a live collector."""

    dispatch = _dispatch()
    events: list[str] = []
    input_value = _RuntimeInput(policy_revision=3)
    execution_type, instances = _execution_type(
        events,
        prepared="prepared",
        fail_run=True,
        fail_close=True,
    )
    calls = _patch_runtime_happy_path(
        monkeypatch,
        events=events,
        input_value=input_value,
        heartbeat_type=_heartbeat_type(events),
        execution_type=execution_type,
    )

    with pytest.raises(TimeoutError, match="synthetic fetch timeout"):
        handle_collection_dispatch(
            dispatch,
            session_factory=_SessionFactory(),
            lookup_client=_SequencedLookupClient(),
            input_provider=_RuntimeInputProvider(input_value, events),
            collector_factory=lambda: object(),
            parser=lambda candidate: candidate,
            runtime_config=_runtime_config(dispatch),
            clock=lambda: NOW,
            uuid_factory=_uuid_factory(CLAIM_TOKEN),
            store_operations=calls["operations"],
        )

    assert calls["commit"] == []
    assert calls["release"] == []
    assert instances[0].closed == 1
    assert "heartbeat-stop-join" in events


def test_heartbeat_start_failure_releases_exact_claim_without_attempting_join_or_commit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An exception before the main try block must not strand a healthy just-acquired claim."""

    dispatch = _dispatch()
    events: list[str] = []
    input_value = _RuntimeInput(policy_revision=3)
    execution_type, _instances = _execution_type(events, prepared="prepared")

    class _StartFailsHeartbeat:
        lost = False

        def __init__(self, *args: object, **kwargs: object) -> None:
            events.append("heartbeat-create")

        def start(self) -> None:
            events.append("heartbeat-start")
            raise RuntimeError("synthetic heartbeat start failure")

        def ensure_active(self) -> None:
            raise AssertionError("failed heartbeat must not enter execution")

        def stop_and_join(self) -> None:
            events.append("heartbeat-stop-join")

    calls = _patch_runtime_happy_path(
        monkeypatch,
        events=events,
        input_value=input_value,
        heartbeat_type=_StartFailsHeartbeat,
        execution_type=execution_type,
    )

    with pytest.raises(RuntimeError, match="synthetic heartbeat start failure"):
        handle_collection_dispatch(
            dispatch,
            session_factory=_SessionFactory(),
            lookup_client=_SequencedLookupClient(),
            input_provider=_RuntimeInputProvider(input_value, events),
            collector_factory=lambda: object(),
            parser=lambda candidate: candidate,
            runtime_config=_runtime_config(dispatch),
            clock=lambda: NOW,
            uuid_factory=_uuid_factory(CLAIM_TOKEN),
            store_operations=calls["operations"],
        )

    assert calls["commit"] == []
    assert len(calls["release"]) == 1
    assert calls["release"][0][1]["claim_token"] == CLAIM_TOKEN
    assert "heartbeat-stop-join" not in events


def test_reserved_attempt_policy_advance_blocks_claim_and_collector_before_writes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Using a stale RESERVED revision after a policy writer update would bypass Source locking."""

    dispatch = _dispatch()
    events: list[str] = []
    input_value = _RuntimeInput(policy_revision=3)
    execution_type, _instances = _execution_type(events, prepared="prepared")
    calls = _patch_runtime_happy_path(
        monkeypatch,
        events=events,
        input_value=input_value,
        heartbeat_type=_heartbeat_type(events),
        execution_type=execution_type,
        attempt_state=("RESERVED", 3, ATTEMPT_ID),
    )
    original_operations = calls["operations"]

    def stale_locked_reservation(*args: object, **kwargs: object) -> object:
        calls["reserve"].append((args, kwargs))
        raise CollectionRuntimeConflict("current policy revision changed under Source lock")

    calls["operations"] = replace(
        original_operations,
        reserve_attempt=stale_locked_reservation,
    )
    collector_factory = _UnexpectedCallable()

    with pytest.raises(CollectionRuntimeConflict, match="policy revision changed"):
        handle_collection_dispatch(
            dispatch,
            session_factory=_SessionFactory(),
            lookup_client=_SequencedLookupClient(),
            input_provider=_RuntimeInputProvider(input_value, events),
            collector_factory=collector_factory,
            parser=lambda candidate: candidate,
            runtime_config=_runtime_config(dispatch),
            clock=lambda: NOW,
            uuid_factory=_uuid_factory(CLAIM_TOKEN),
            store_operations=calls["operations"],
        )

    assert len(calls["reserve"]) == 1
    assert calls["claim"] == []
    assert calls["commit"] == []
    assert calls["release"] == []
    assert collector_factory.calls == 0
