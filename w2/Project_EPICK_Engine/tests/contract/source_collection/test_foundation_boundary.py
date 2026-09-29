"""Foundation boundary checkpoints for the source-collection core.

These tests deliberately exercise only the reusable W2 boundary primitives.  W1
owns application registration, authentication, dispatch, and Analytics policy;
the test-local app below must not be mistaken for a production route.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable, Coroutine
from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from alembic import command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from fastapi import FastAPI, Request
from jsonschema import Draft202012Validator, FormatChecker
from pydantic import BaseModel, ValidationError
from sqlalchemy import MetaData, Table, create_engine, insert, inspect, select, text
from sqlalchemy.engine import URL
from sqlalchemy.exc import IntegrityError
from starlette.responses import Response

from epick_engine.source_collection.api import (
    ApiErrorCode,
    ApiErrorEnvelope,
    ApiProblem,
    AuthenticatedPrincipal,
    install_api_exception_handlers,
    make_configured_dependency,
    make_principal_dependency,
    require_owned_resource,
)
from epick_engine.source_collection.contracts import (
    CollectionCommand,
    CollectionResult,
    SourceEnvelope,
    SourceEvent,
)

ENGINE_ROOT = Path(__file__).resolve().parents[3]
CONTRACT_DIR = ENGINE_ROOT.parent / "specs" / "001-official-source-collection" / "contracts"
MIGRATION_HEAD = "0013_deletion_ack_confirmed"


def _load_json(name: str) -> dict[str, Any]:
    with (CONTRACT_DIR / name).open(encoding="utf-8") as stream:
        value: dict[str, Any] = json.load(stream)
    return value


def test_ct15_contract_mentions_t067_schema_and_forward_head() -> None:
    document = (ENGINE_ROOT / "contracts/w2-private/ct15-runtime.md").read_text(encoding="utf-8")
    normalized_document = " ".join(document.split())

    assert "0013_deletion_ack_confirmed" in normalized_document
    assert "W1_LOOKUP_ENDPOINT" in normalized_document
    assert "W1_LOOKUP_BEARER" in normalized_document
    assert "W1_LOOKUP_CA_FILE" in normalized_document
    assert "metadata-only preflight does not grant runtime authority" in normalized_document
    assert "private-deletion-command.schema.json" in normalized_document
    assert "private-deletion-ack.schema.json" in normalized_document
    assert "Historical local verification snapshots" in normalized_document
    assert "applies Alembic through `0009_private_deletion_receipt`" in normalized_document
    assert "W2 local T067 payload, consumer, and migration work is complete" in normalized_document
    assert "joint T067 end-to-end validation remain incomplete" in normalized_document
    assert "does not establish current READY" in normalized_document


def test_ct15_compose_forwards_w1_authority_with_read_only_ca_mount() -> None:
    compose = (ENGINE_ROOT / "compose.ct15.yaml").read_text(encoding="utf-8")

    assert (
        "W1_LOOKUP_ENDPOINT: ${W1_LOOKUP_ENDPOINT:?W1 supplies the authenticated lookup endpoint}"
        in compose
    )
    assert (
        "W1_LOOKUP_BEARER: ${W1_LOOKUP_BEARER:?Supply through the approved secret channel}"
        in compose
    )
    assert "W1_LOOKUP_CA_FILE: /run/epick/source-runtime/w1-ca.pem" in compose
    assert (
        """    volumes:
      - type: bind
        source: ${W1_LOOKUP_CA_HOST_FILE:?Supply the W1 CA certificate file}
        target: /run/epick/source-runtime/w1-ca.pem
        read_only: true
"""
        in compose
    )


EXAMPLES_BY_NAME = {
    example["name"]: example["value"] for example in _load_json("examples.json")["examples"]
}
SCHEMA_VALIDATOR = Draft202012Validator(
    _load_json("source-collection.schema.json"),
    format_checker=FormatChecker(),
)


def _runtime_model(value: dict[str, Any]) -> type[BaseModel]:
    if "event_type" in value:
        return SourceEvent
    if "authenticated_owner_ref" in value:
        return CollectionCommand
    if "completion_kind" in value:
        return CollectionResult
    return SourceEnvelope


def _request(
    *,
    raw_body: bytes = b"",
    raw_url: str = "https://synthetic.example.test/",
    token: str = "synthetic-token",
) -> Request:
    async def receive() -> dict[str, object]:
        return {"type": "http.request", "body": raw_body, "more_body": False}

    return Request(
        {
            "type": "http",
            "http_version": "1.1",
            "method": "POST",
            "scheme": "https",
            "path": "/api/v1/synthetic",
            "raw_path": b"/api/v1/synthetic",
            "query_string": f"source_url={raw_url}&token={token}".encode(),
            "headers": [(b"authorization", f"Bearer {token}".encode())],
            "client": ("test-client", 1),
            "server": ("test-server", 443),
        },
        receive=receive,
    )


def _render_exception(
    app: FastAPI,
    exception: Exception,
    request: Request | None = None,
) -> Response:
    handler_type = next(
        candidate for candidate in type(exception).__mro__ if candidate in app.exception_handlers
    )
    handler = cast(
        Callable[[Request, Exception], Coroutine[Any, Any, Response]],
        app.exception_handlers[handler_type],
    )
    coroutine = handler(request or _request(), exception)
    try:
        coroutine.send(None)
    except StopIteration as completed:
        return cast(Response, completed.value)
    finally:
        coroutine.close()
    raise AssertionError("common exception handler unexpectedly suspended")


def _error_payload(response: Response, *, status: int, code: ApiErrorCode) -> dict[str, Any]:
    assert response.status_code == status
    payload = json.loads(response.body)
    envelope = ApiErrorEnvelope.model_validate(payload)
    assert envelope.error.code is code
    return payload["error"]


@pytest.mark.parametrize(
    "example_name",
    [
        "source-envelope",
        "initial-policy-command",
        "core-failure-and-rate-limit",
        "source.observation.changed",
    ],
)
def test_representative_stored_contracts_survive_schema_and_runtime_boundary(
    example_name: str,
) -> None:
    value = deepcopy(EXAMPLES_BY_NAME[example_name])

    assert list(SCHEMA_VALIDATOR.iter_errors(value)) == []
    serialized = _runtime_model(value).model_validate(value).model_dump(mode="json")
    assert list(SCHEMA_VALIDATOR.iter_errors(serialized)) == []


def test_runtime_enforces_cross_field_command_invariant_beyond_json_schema() -> None:
    command_value = deepcopy(EXAMPLES_BY_NAME["initial-policy-command"])
    command_value["core_source_decision"]["analysis_input_version"] += 1

    # Draft 2020-12 cannot express equality between these sibling values.
    assert list(SCHEMA_VALIDATOR.iter_errors(command_value)) == []
    with pytest.raises(ValidationError, match="analysis_input_version"):
        CollectionCommand.model_validate(command_value)


@pytest.mark.approved_postgres
def test_alembic_foundation_schema_has_head_constraints_and_owner_scope(
    approved_postgres_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    schema_name = f"t016_{uuid4().hex}"
    schema_url = approved_postgres_url.update_query_dict(
        {"options": f"-csearch_path={schema_name}"}
    )
    config = Config(str(ENGINE_ROOT / "alembic.ini"))
    script = ScriptDirectory.from_config(config)
    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)

    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))

    monkeypatch.setenv("EPICK_DATABASE_URL", schema_url.render_as_string(hide_password=False))
    try:
        command.upgrade(config, "head")

        with admin_engine.begin() as connection:
            connection.execute(text(f'SET search_path TO "{schema_name}"'))
            assert MigrationContext.configure(connection).get_current_revision() == MIGRATION_HEAD
            assert script.get_current_head() == MIGRATION_HEAD

            inspector = inspect(connection)
            tables = set(inspector.get_table_names(schema=schema_name))
            assert {
                "companies",
                "sources",
                "source_versions",
                "evidence",
                "job_postings",
                "posting_sections",
                "posting_section_evidence",
                "collection_attempts",
                "request_deduplications",
                "outbox_events",
            } <= tables

            source_constraints = {
                constraint["name"]
                for constraint in inspector.get_unique_constraints("sources", schema=schema_name)
            }
            dedup_constraints = {
                constraint["name"]
                for constraint in inspector.get_unique_constraints(
                    "request_deduplications", schema=schema_name
                )
            }
            outbox_foreign_keys = {
                foreign_key["name"]
                for foreign_key in inspector.get_foreign_keys("outbox_events", schema=schema_name)
            }
            job_posting_foreign_keys = {
                foreign_key["name"]
                for foreign_key in inspector.get_foreign_keys("job_postings", schema=schema_name)
            }
            section_evidence_foreign_keys = {
                foreign_key["name"]
                for foreign_key in inspector.get_foreign_keys(
                    "posting_section_evidence", schema=schema_name
                )
            }
            assert "uq_sources_company_url" in source_constraints
            assert "uq_request_deduplications_owner_operation_key" in dedup_constraints
            assert "fk_outbox_events_aggregate_id" in outbox_foreign_keys
            assert "fk_job_postings_source_company" in job_posting_foreign_keys
            assert section_evidence_foreign_keys == {
                "fk_posting_section_evidence_revision_evidence",
                "fk_posting_section_evidence_section",
            }

            deduplications = Table(
                "request_deduplications",
                MetaData(),
                schema=schema_name,
                autoload_with=connection,
            )
            owner_a = uuid4()
            owner_b = uuid4()
            shared_key = "synthetic-shared-key"
            for owner, resource_ref in (
                (owner_a, "synthetic://owner-a/request"),
                (owner_b, "synthetic://owner-b/request"),
            ):
                connection.execute(
                    insert(deduplications).values(
                        request_deduplication_id=uuid4(),
                        owner_user_id=owner,
                        operation="collection.refresh",
                        idempotency_key=shared_key,
                        request_hash="a" * 64,
                        accepted_resource_ref=resource_ref,
                        input_version=1,
                        created_at=datetime(2026, 9, 10, tzinfo=UTC),
                    )
                )

            with pytest.raises(IntegrityError):
                with connection.begin_nested():
                    connection.execute(
                        insert(deduplications).values(
                            request_deduplication_id=uuid4(),
                            owner_user_id=owner_a,
                            operation="collection.refresh",
                            idempotency_key=shared_key,
                            request_hash="b" * 64,
                            accepted_resource_ref="synthetic://duplicate/request",
                            input_version=1,
                            created_at=datetime(2026, 9, 10, tzinfo=UTC),
                        )
                    )

            owner_a_rows = (
                connection.execute(
                    select(deduplications.c.accepted_resource_ref).where(
                        deduplications.c.owner_user_id == owner_a,
                        deduplications.c.operation == "collection.refresh",
                        deduplications.c.idempotency_key == shared_key,
                    )
                )
                .scalars()
                .all()
            )
            owner_b_rows = (
                connection.execute(
                    select(deduplications.c.accepted_resource_ref).where(
                        deduplications.c.owner_user_id == owner_b,
                        deduplications.c.operation == "collection.refresh",
                        deduplications.c.idempotency_key == shared_key,
                    )
                )
                .scalars()
                .all()
            )
            assert owner_a_rows == ["synthetic://owner-a/request"]
            assert owner_b_rows == ["synthetic://owner-b/request"]
    finally:
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def test_boundary_fails_closed_and_hides_cross_owner_resources() -> None:
    app = FastAPI()
    install_api_exception_handlers(app)

    with pytest.raises(ApiProblem) as unauthenticated:
        make_principal_dependency(lambda _request: None)(_request())
    unauthenticated_payload = _error_payload(
        _render_exception(app, unauthenticated.value),
        status=401,
        code=ApiErrorCode.AUTHENTICATION_REQUIRED,
    )

    owner_id = uuid4()
    foreign_owner_id = uuid4()
    foreign_resource_id = uuid4()
    missing_resource_id = uuid4()
    foreign_resource = {"resource_id": foreign_resource_id}

    def load(*, resource_id: UUID, owner_user_id: UUID) -> object | None:
        if resource_id == foreign_resource_id and owner_user_id == foreign_owner_id:
            return foreign_resource
        return None

    assert (
        require_owned_resource(
            load,
            resource_id=foreign_resource_id,
            principal=AuthenticatedPrincipal(foreign_owner_id),
        )
        is foreign_resource
    )
    principal = AuthenticatedPrincipal(owner_id)
    hidden_errors: list[dict[str, Any]] = []
    for resource_id in (foreign_resource_id, missing_resource_id):
        with pytest.raises(ApiProblem) as hidden:
            require_owned_resource(load, resource_id=resource_id, principal=principal)
        hidden_errors.append(
            _error_payload(
                _render_exception(app, hidden.value),
                status=404,
                code=ApiErrorCode.RESOURCE_NOT_FOUND,
            )
        )

    assert unauthenticated_payload["code"] == ApiErrorCode.AUTHENTICATION_REQUIRED
    for payload in hidden_errors:
        payload["correlation_id"] = "<server-generated>"
    assert hidden_errors[0] == hidden_errors[1]


def test_missing_owner_supplied_dependencies_render_sanitized_503() -> None:
    app = FastAPI()
    install_api_exception_handlers(app)

    with pytest.raises(ApiProblem) as missing_authenticator:
        make_principal_dependency(None)(_request())
    with pytest.raises(ApiProblem) as missing_dispatch:
        make_configured_dependency(None)()

    for problem in (missing_authenticator.value, missing_dispatch.value):
        response = _render_exception(app, problem)
        _error_payload(response, status=503, code=ApiErrorCode.DEPENDENCY_UNAVAILABLE)


def test_unexpected_error_envelope_and_logs_do_not_disclose_raw_request_data(
    caplog: pytest.LogCaptureFixture,
) -> None:
    raw_body = "raw-body-do-not-disclose"
    raw_url = "https://synthetic.example.test/private?reference=raw-url-do-not-disclose"
    raw_token = "token-do-not-disclose"
    app = FastAPI()
    install_api_exception_handlers(app)
    caplog.set_level(logging.DEBUG)

    response = _render_exception(
        app,
        RuntimeError(f"{raw_body} {raw_url} {raw_token}"),
        _request(raw_body=raw_body.encode(), raw_url=raw_url, token=raw_token),
    )

    _error_payload(response, status=503, code=ApiErrorCode.DEPENDENCY_UNAVAILABLE)
    disclosed = (response.body.decode(), caplog.text)
    for sentinel in (raw_body, raw_url, raw_token):
        assert all(sentinel not in value for value in disclosed)


def test_analytics_off_does_not_block_a_trusted_core_boundary_dependency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EPICK_ANALYTICS_ENABLED", "0")

    def trusted_core_port() -> str:
        return "core-ready"

    resolved_core_port = make_configured_dependency(trusted_core_port)()
    assert resolved_core_port() == "core-ready"
