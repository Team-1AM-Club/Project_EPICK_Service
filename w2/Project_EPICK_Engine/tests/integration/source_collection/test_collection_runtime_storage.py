"""Additive collection-runtime schema over an isolated approved PostgreSQL database."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from collections.abc import Callable, Iterator
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Barrier
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from alembic.config import Config
from alembic.script import ScriptDirectory
from scripts.verify_w2_w3_postgres_http import _collect_product_source
from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    Engine,
    create_engine,
    func,
    inspect,
    select,
    text,
    update,
)
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

import epick_engine.source_collection.commit_gate_store as commit_gate_store_module
import epick_engine.source_collection.persistence as persistence_module
import epick_engine.source_collection.source_runtime as source_runtime_module
from epick_engine.source_collection.collector import (
    StaticFetchResult,
    StaticResponseCandidate,
)
from epick_engine.source_collection.commit_gate_contracts import (
    CommitGateCommand,
    parse_commit_gate_command,
)
from epick_engine.source_collection.commit_gate_store import (
    CommitGateRejected,
    PrivateCommitGateAck,
    PrivateCommitStage,
    PrivateStagedOutbox,
    lock_private_command,
)
from epick_engine.source_collection.commit_gate_store import (
    apply_commit_gate as _apply_commit_gate,
)
from epick_engine.source_collection.contracts import (
    AccessClass,
    CollectionResult,
    CollectionStage,
    DateValue,
    ExtractionStatus,
    Locator,
    OfficialStatus,
    Permission,
    PostingSection,
    Representation,
    SourceEvent,
    SourceObservationSnapshot,
    SourceType,
)
from epick_engine.source_collection.parsing import extract_static_candidate
from epick_engine.source_collection.persistence import (
    Base,
    CollectionRuntimeAttempt,
    Company,
    Evidence,
    ExtractionRevision,
    OutboxEvent,
    ParserExecution,
    PreparedCollectionCommit,
    PreparedEvidence,
    PreparedExtractionRevision,
    PreparedParserExecution,
    PreparedSourceObservation,
    PreparedSourceVersion,
    PrivateDeletionOwnerState,
    Source,
    SourceObservation,
    SourcePolicyDecision,
    SourceVersion,
)
from epick_engine.source_collection.persistence import (
    commit_collection_candidate as _commit_collection_candidate,
)
from epick_engine.source_collection.persistence import (
    replay_staged_collection as _replay_staged_collection,
)
from epick_engine.source_collection.policy import (
    Representation as FetchRepresentation,
)
from epick_engine.source_collection.policy import (
    UntrustedDocument,
    ValidatedTarget,
)
from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionScope
from epick_engine.source_collection.private_scope import (
    PrivateGateAuthority,
    PrivateScopeRejected,
    PrivateTerminalCleanupAuthority,
    PrivateWriteAuthorityDecision,
    PrivateWriteScope,
)
from epick_engine.source_collection.source_runtime import (
    RuntimeAuthorizationError,
    handle_collection_dispatch,
)
from epick_engine.source_collection.source_runtime_input import (
    RuntimeSourceConfigFile,
    SourceRuntimeInputError,
    SqlAlchemyCollectionInputProvider,
)
from epick_engine.source_collection.source_runtime_store import (
    CollectionRuntimeBusy,
    CollectionRuntimeConflict,
    dispatch_digest,
    load_bound_collection_attempt,
)
from epick_engine.source_collection.source_runtime_store import (
    claim_collection_attempt as _claim_collection_attempt,
)
from epick_engine.source_collection.source_runtime_store import (
    release_collection_claim as _release_collection_claim,
)
from epick_engine.source_collection.source_runtime_store import (
    release_collection_reservation as _release_collection_reservation,
)
from epick_engine.source_collection.source_runtime_store import (
    renew_collection_claim as _renew_collection_claim,
)
from epick_engine.source_collection.source_runtime_store import (
    reserve_collection_attempt as _reserve_collection_attempt,
)
from epick_engine.source_collection.w1_lookup_client import W1LookupClientError
from epick_engine.source_collection.w1_private_authority_contracts import (
    CurrentWriteScopeLookupResponse,
    GateAuthorityResponse,
    PrivateWriteAuthorityResponse,
    TerminalCleanupAuthorityResponse,
    W1PrivateBinding,
)
from epick_engine.source_collection.w1_transport import (
    LookupResponse,
    W1Dispatch,
    parse_w1_dispatch,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
FIXTURES = Path(__file__).resolve().parents[2] / "fixtures" / "w1_private_contract"
NOW = datetime(2026, 9, 20, 12, tzinfo=UTC)


def _column_names(constraint: Any) -> tuple[str, ...]:
    return tuple(column.name for column in constraint.columns)


def test_collection_runtime_metadata_uses_durable_internal_ordering() -> None:
    sources = Base.metadata.tables["sources"]
    stages = Base.metadata.tables["private_commit_stages"]
    assert "collection_runtime_attempts" in Base.metadata.tables
    attempts = Base.metadata.tables["collection_runtime_attempts"]

    pointer_server_default = cast(Any, sources.c.pointer_update_mode.server_default)
    pointer_default = cast(Any, sources.c.pointer_update_mode.default)
    next_order_default = cast(Any, sources.c.next_observation_order.default)
    promoted_order_default = cast(Any, sources.c.last_promoted_observation_order.default)
    stage_server_default = cast(Any, stages.c.stage_kind.server_default)
    stage_default = cast(Any, stages.c.stage_kind.default)
    assert pointer_server_default is not None
    assert pointer_default is not None
    assert next_order_default is not None
    assert promoted_order_default is not None
    assert stage_server_default is not None
    assert stage_default is not None
    assert str(pointer_server_default.arg) == "'LEGACY_SAME_DB'"
    assert pointer_default.arg == "LEGACY_SAME_DB"
    assert isinstance(sources.c.next_observation_order.type, BigInteger)
    assert isinstance(sources.c.last_promoted_observation_order.type, BigInteger)
    assert next_order_default.arg == 0
    assert promoted_order_default.arg == 0
    assert isinstance(attempts.c.observation_order.type, BigInteger)
    assert isinstance(attempts.c.claim_expires_at.type, DateTime)
    assert attempts.c.claim_expires_at.type.timezone is True
    assert str(stage_server_default.arg) == "'PRIVATE_ONLY'"
    assert stage_default.arg == "PRIVATE_ONLY"
    assert {
        constraint.name
        for constraint in sources.constraints
        if isinstance(constraint.name, str) and constraint.name.startswith("ck_")
    } >= {
        "ck_sources_valid_pointer_update_mode",
        "ck_sources_valid_observation_order_counters",
    }
    assert {
        constraint.name
        for constraint in stages.constraints
        if isinstance(constraint.name, str) and constraint.name.startswith("ck_")
    } >= {"ck_private_commit_stages_valid_stage_kind"}

    assert {constraint.name for constraint in attempts.constraints} == {
        "pk_collection_runtime_attempts",
        "uq_collection_runtime_attempts_attempt_id",
        "uq_collection_runtime_attempts_source_observation_order",
        "fk_collection_runtime_attempts_source_company",
        "fk_collection_runtime_attempts_observation_same_source",
        "fk_collection_runtime_attempts_version_same_source",
        "ck_collection_runtime_attempts_positive_observation_order",
        "ck_collection_runtime_attempts_positive_policy_revision",
        "ck_collection_runtime_attempts_valid_dispatch_digest",
        "ck_collection_runtime_attempts_valid_state",
        "ck_collection_runtime_attempts_claim_fields_together",
        "ck_collection_runtime_attempts_claim_fields_reserved_only",
        "ck_collection_runtime_attempts_valid_private_scope",
    }
    assert all(len(str(constraint.name)) <= 63 for constraint in attempts.constraints)
    attempt_checks = {
        constraint.name: str(constraint.sqltext)
        for constraint in attempts.constraints
        if isinstance(constraint, CheckConstraint)
    }
    assert (
        "^[0-9a-f]{64}$" in attempt_checks["ck_collection_runtime_attempts_valid_dispatch_digest"]
    )
    assert all(
        state in attempt_checks["ck_collection_runtime_attempts_valid_state"]
        for state in ("RESERVED", "PERSISTED", "FINALIZED", "INVALIDATED")
    )
    assert (
        "claim_token IS NULL"
        in attempt_checks["ck_collection_runtime_attempts_claim_fields_together"]
    )
    assert (
        "state = 'RESERVED'"
        in attempt_checks["ck_collection_runtime_attempts_claim_fields_reserved_only"]
    )
    assert {(index.name, _column_names(index)) for index in attempts.indexes} == {
        ("ix_collection_runtime_attempts_owner_ref", ("owner_ref",)),
        ("ix_collection_runtime_attempts_job_id", ("job_id",)),
        (
            "ix_collection_runtime_attempts_owner_private_scope",
            ("owner_ref", "private_scope_kind", "project_id"),
        ),
    }


@pytest.mark.approved_postgres
def test_collection_runtime_migration_backfills_0007_and_matches_metadata(
    approved_postgres_url: URL,
) -> None:
    scripts = ScriptDirectory.from_config(Config(PROJECT_ROOT / "alembic.ini"))
    assert scripts.get_heads() == ["0013_deletion_ack_confirmed"]
    admin = create_engine(approved_postgres_url)
    schema = f"epick_w2_collection_runtime_{uuid4().hex}"
    command_id = uuid4()
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        scoped_url = approved_postgres_url.set(
            query={**approved_postgres_url.query, "options": f"-csearch_path={schema}"}
        )
        environment = {
            **os.environ,
            "EPICK_DATABASE_URL": scoped_url.render_as_string(hide_password=False),
        }

        def upgrade(target: str) -> None:
            completed = subprocess.run(
                [sys.executable, "-m", "alembic", "upgrade", target],
                cwd=PROJECT_ROOT,
                env=environment,
                capture_output=True,
                text=True,
                timeout=30,
            )
            assert completed.returncode == 0, f"isolated Alembic upgrade to {target} failed"

        upgrade("0007_restriction_receipt")
        with admin.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            connection.execute(
                text(
                    """
                    INSERT INTO private_commit_stages (
                        command_id, owner_ref, job_id, execution_fence,
                        owner_deletion_epoch, result_digest, operation_id,
                        operation_revision, max_purge_epoch, state, result_payload
                    ) VALUES (
                        :command_id, :owner_ref, :job_id, '1', '0', :result_digest,
                        NULL, '0', '0', 'STAGED', CAST(:result_payload AS jsonb)
                    )
                    """
                ),
                {
                    "command_id": command_id,
                    "owner_ref": uuid4(),
                    "job_id": uuid4(),
                    "result_digest": "sha256:" + "a" * 64,
                    "result_payload": "{}",
                },
            )

        upgrade("head")
        with admin.begin() as connection:
            connection.execute(text(f'SET LOCAL search_path TO "{schema}"'))
            inspector = inspect(connection)
            assert (
                connection.execute(text("SELECT version_num FROM alembic_version")).scalar_one()
                == "0013_deletion_ack_confirmed"
            )

            source_columns = {column["name"]: column for column in inspector.get_columns("sources")}
            assert source_columns["pointer_update_mode"]["default"] in {
                "'LEGACY_SAME_DB'::character varying",
                "'LEGACY_SAME_DB'::text",
            }
            assert source_columns["next_observation_order"]["default"] in {"0", "0::bigint"}
            assert source_columns["last_promoted_observation_order"]["default"] in {
                "0",
                "0::bigint",
            }
            assert isinstance(source_columns["next_observation_order"]["type"], BigInteger)
            assert isinstance(source_columns["last_promoted_observation_order"]["type"], BigInteger)
            assert {
                constraint["name"] for constraint in inspector.get_check_constraints("sources")
            } >= {
                "ck_sources_valid_pointer_update_mode",
                "ck_sources_valid_observation_order_counters",
            }

            assert "collection_runtime_attempts" in inspector.get_table_names()
            attempt_columns = {
                column["name"]: column
                for column in inspector.get_columns("collection_runtime_attempts")
            }
            assert isinstance(attempt_columns["observation_order"]["type"], BigInteger)
            assert inspector.get_pk_constraint("collection_runtime_attempts")[
                "constrained_columns"
            ] == ["command_id"]
            assert {
                (item["name"], tuple(item["column_names"]))
                for item in inspector.get_unique_constraints("collection_runtime_attempts")
            } == {
                ("uq_collection_runtime_attempts_attempt_id", ("attempt_id",)),
                (
                    "uq_collection_runtime_attempts_source_observation_order",
                    ("source_id", "observation_order"),
                ),
            }
            assert {
                (item["name"], tuple(item["column_names"]))
                for item in inspector.get_indexes("collection_runtime_attempts")
                if not item.get("duplicates_constraint")
            } == {
                ("ix_collection_runtime_attempts_owner_ref", ("owner_ref",)),
                ("ix_collection_runtime_attempts_job_id", ("job_id",)),
                (
                    "ix_collection_runtime_attempts_owner_private_scope",
                    ("owner_ref", "private_scope_kind", "project_id"),
                ),
            }
            assert {
                (
                    item["name"],
                    tuple(item["constrained_columns"]),
                    item["referred_table"],
                    tuple(item["referred_columns"]),
                )
                for item in inspector.get_foreign_keys("collection_runtime_attempts")
            } == {
                (
                    "fk_collection_runtime_attempts_source_company",
                    ("source_id", "company_id"),
                    "sources",
                    ("source_id", "company_id"),
                ),
                (
                    "fk_collection_runtime_attempts_observation_same_source",
                    ("observation_id", "source_id"),
                    "source_observations",
                    ("observation_id", "source_id"),
                ),
                (
                    "fk_collection_runtime_attempts_version_same_source",
                    ("source_version_id", "source_id"),
                    "source_versions",
                    ("source_version_id", "source_id"),
                ),
            }
            assert {
                constraint["name"]
                for constraint in inspector.get_check_constraints("collection_runtime_attempts")
            } == {
                constraint.name
                for constraint in Base.metadata.tables["collection_runtime_attempts"].constraints
                if isinstance(constraint.name, str) and constraint.name.startswith("ck_")
            }

            stage_columns = {
                column["name"]: column for column in inspector.get_columns("private_commit_stages")
            }
            assert stage_columns["stage_kind"]["nullable"] is False
            stage_kind_check = next(
                constraint
                for constraint in inspector.get_check_constraints("private_commit_stages")
                if constraint["name"] == "ck_private_commit_stages_valid_stage_kind"
            )
            assert "PRIVATE_ONLY" in stage_kind_check["sqltext"]
            assert "COLLECTION" in stage_kind_check["sqltext"]
            assert stage_kind_check["name"] in {
                constraint.name
                for constraint in Base.metadata.tables["private_commit_stages"].constraints
            }
            with Session(connection) as session:
                assert (
                    session.scalar(
                        select(PrivateCommitStage.stage_kind).where(
                            PrivateCommitStage.command_id == command_id
                        )
                    )
                    == "PRIVATE_ONLY"
                )

        with pytest.raises(RuntimeError, match="Destructive downgrade"):
            scripts.get_revision("0008_collection_runtime").module.downgrade()
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def runtime_database_engine(approved_postgres_url: URL) -> Iterator[Engine]:
    admin = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema = f"epick_w2_runtime_store_{uuid4().hex}"
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    engine = admin.execution_options(schema_translate_map={None: schema})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


@pytest.fixture
def runtime_session_factory(
    runtime_database_engine: Engine,
) -> sessionmaker[Session]:
    return sessionmaker(runtime_database_engine, autoflush=False, expire_on_commit=False)


def _seed_source(
    session_factory: sessionmaker[Session],
    *,
    company_id: UUID | None = None,
    source_id: UUID | None = None,
    policy_revision: int | None = 3,
) -> tuple[UUID, UUID]:
    company_id = company_id or uuid4()
    source_id = source_id or uuid4()
    with session_factory.begin() as session:
        session.add(
            Company(
                company_id=company_id,
                legal_name="Synthetic Runtime Company",
                aliases=[],
                official_domains=["runtime.test"],
                legal_identifiers={},
                identity_status="verified",
                identity_evidence=["synthetic:test"],
            )
        )
        session.add(
            Source(
                source_id=source_id,
                company_id=company_id,
                source_type="web",
                canonical_url=f"https://runtime.test/{source_id}",
                title="Runtime source",
            )
        )
        if policy_revision is not None:
            session.add(_policy_decision(source_id, policy_revision))
    return company_id, source_id


def _source_runtime_config(
    source_revisions: dict[UUID, int],
) -> RuntimeSourceConfigFile:
    return RuntimeSourceConfigFile.model_validate_json(
        json.dumps(
            {
                "schema_version": "w2.source-runtime-config.v1",
                "claim_lease_seconds": 120,
                "sources": {
                    str(source_id): {
                        "policy_revision": revision,
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
                    for source_id, revision in source_revisions.items()
                },
            }
        )
    )


def _policy_decision(
    source_id: UUID,
    revision: int,
    *,
    official_status: str = "verified",
) -> SourcePolicyDecision:
    return SourcePolicyDecision(
        policy_decision_id=uuid4(),
        source_id=source_id,
        revision=revision,
        official_status=official_status,
        access_class="public",
        collection_permission="allowed",
        excerpt_storage_permission="allowed",
        body_storage_permission="denied",
        redistribution_permission="unknown",
        evidence_refs=[f"synthetic:policy:{revision}"],
        checked_at=NOW,
        policy_version=f"policy-{revision}",
    )


def _dispatch(
    *,
    command_id: UUID,
    job_id: UUID,
    owner_ref: UUID,
    company_id: UUID,
    source_id: UUID,
    execution_fence: int = 1,
    owner_deletion_epoch: int = 0,
    decision_id: UUID | None = None,
    dispatch_kind: str = "core",
    project_ref: str | None = None,
) -> W1Dispatch:
    fixture = (
        "private-w2-command-dispatch.json"
        if dispatch_kind == "core"
        else "private-w2-direct-source-registration-dispatch.json"
    )
    raw = json.loads((FIXTURES / fixture).read_text(encoding="utf-8"))
    raw["message_id"] = str(command_id)
    raw["payload"].update(
        command_id=str(command_id),
        job_id=str(job_id),
        authenticated_owner_ref=str(owner_ref),
        company_id=str(company_id),
        source_id=str(source_id),
        execution_fence=str(execution_fence),
        owner_deletion_epoch=owner_deletion_epoch,
        project_ref=project_ref,
    )
    raw["lookup_request"].update(
        command_id=str(command_id),
        execution_fence=execution_fence,
        owner_deletion_epoch=owner_deletion_epoch,
    )
    if dispatch_kind == "core":
        raw["core_decision_pin"].update(
            company_id=str(company_id),
            source_id=str(source_id),
            decision_id=str(decision_id or uuid4()),
        )
    else:
        raw["direct_source_registration_pin"].update(
            company_id=str(company_id),
            source_id=str(source_id),
            registration_decision_id=str(decision_id or uuid4()),
        )
    return parse_w1_dispatch(raw)


_TEST_PRIVATE_SCOPES: dict[UUID, PrivateWriteScope] = {}
_TEST_PRIVATE_DISPATCH_DIGESTS: dict[UUID, str] = {}


def _trusted_scope(dispatch: W1Dispatch) -> PrivateWriteScope:
    command = dispatch.payload
    return PrivateWriteScope(
        PrivateWriteAuthorityDecision(
            owner_user_id=command.authenticated_owner_ref,
            owner_deletion_epoch=command.owner_deletion_epoch,
            scope=PrivateDeletionScope(kind="ACCOUNT", project_id=None),
            authority_ref="w1:test-runtime-authority",
            command_id=command.command_id,
            job_id=command.job_id,
        )
    )


def _scope_for_dispatch(
    dispatch: W1Dispatch,
    scope: PrivateDeletionScope,
    *,
    authority_ref: str = "w1:test-runtime-authority",
) -> PrivateWriteScope:
    command = dispatch.payload
    return PrivateWriteScope(
        PrivateWriteAuthorityDecision(
            owner_user_id=command.authenticated_owner_ref,
            owner_deletion_epoch=command.owner_deletion_epoch,
            scope=scope,
            authority_ref=authority_ref,
            command_id=command.command_id,
            job_id=command.job_id,
        )
    )


def _private_binding_payload(binding: W1PrivateBinding) -> dict[str, object]:
    return {
        "owner_user_id": binding.owner_user_id,
        "owner_deletion_epoch": binding.owner_deletion_epoch,
        "command_id": binding.command_id,
        "job_id": binding.job_id,
        "execution_fence": binding.execution_fence,
    }


def _terminal_cleanup_authority(
    binding: W1PrivateBinding,
    expected_scope: PrivateDeletionScope,
    *,
    cleanup_kind: str = "CLAIM_RELEASE",
    **overrides: object,
) -> PrivateTerminalCleanupAuthority:
    payload = {
        "schema_version": "w1.private.w2-terminal-cleanup.v1",
        **_private_binding_payload(binding),
        "scope": expected_scope.to_mapping(),
        "cleanup_kind": cleanup_kind,
        "authority_ref": "w1:test-terminal-cleanup",
        "allowed_effect": "OWNER_LOCKED_PRIVATE_CLEANUP_ONLY",
        **overrides,
    }
    return PrivateTerminalCleanupAuthority.from_w1_response(
        TerminalCleanupAuthorityResponse.model_validate(payload)
    )


class _PrivateAuthorityClient:
    def __init__(
        self,
        scope: PrivateDeletionScope,
        *,
        before_decision: Callable[[str], None] | None = None,
        write_scope: PrivateDeletionScope | None = None,
    ) -> None:
        self.scope = scope
        self.write_scope = write_scope
        self.before_decision = before_decision
        self.events: list[str] = []

    def lookup_current_scope(self, binding: W1PrivateBinding) -> CurrentWriteScopeLookupResponse:
        self.events.append("scope")
        if self.before_decision is not None:
            self.before_decision("scope")
        return CurrentWriteScopeLookupResponse.model_validate(
            {
                "schema_version": "w1.private.w2-current-write-scope-lookup.v1",
                **_private_binding_payload(binding),
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
        if self.before_decision is not None:
            self.before_decision("write")
        returned_scope = self.write_scope or scope
        return PrivateWriteAuthorityResponse.model_validate(
            {
                "schema_version": "w1.private.w2-write-authority.v1",
                **_private_binding_payload(binding),
                "scope": returned_scope.to_mapping(),
                "authority_ref": f"w1:test:{len(self.events)}",
            },
            strict=True,
        )


class _AvailableDispatchLookup:
    def __init__(self) -> None:
        self.dispatches: list[W1Dispatch] = []

    def lookup_dispatch(self, dispatch: W1Dispatch) -> LookupResponse:
        self.dispatches.append(dispatch)
        return LookupResponse(
            schema_version="w1.private.command-lookup.v1",
            command_id=dispatch.payload.command_id,
            status="AVAILABLE",
            reason_code=None,
            command=dispatch.payload,
        )


def _gate_authority(gate: CommitGateCommand) -> PrivateGateAuthority:
    response = GateAuthorityResponse.model_validate(
        {
            "schema_version": "w1.private.w2-gate-authority.v1",
            "owner_user_id": str(gate.authenticated_owner_ref),
            "owner_deletion_epoch": gate.owner_deletion_epoch,
            "command_id": str(gate.command_id),
            "job_id": str(gate.job_id),
            "execution_fence": gate.execution_fence,
            "scope": {"type": "ACCOUNT"},
            "operation_id": str(gate.operation_id),
            "operation_revision": gate.operation_revision,
            "action": gate.action,
            "phase": "APPLY",
            "result_digest": gate.result_digest,
            "purge_owner_deletion_epoch": gate.purge_owner_deletion_epoch,
            "authority_ref": "w1:test-runtime-gate-authority",
        }
    )
    return PrivateGateAuthority.from_w1_response(response)


def reserve_collection_attempt(session, dispatch, *args, **kwargs):
    scope = kwargs.setdefault("private_scope", _trusted_scope(dispatch))
    _TEST_PRIVATE_SCOPES[dispatch.payload.command_id] = scope
    _TEST_PRIVATE_DISPATCH_DIGESTS[dispatch.payload.command_id] = dispatch_digest(dispatch)
    return _reserve_collection_attempt(session, dispatch, *args, **kwargs)


def _scope_kwargs(command_id: UUID, kwargs: dict[str, Any]) -> None:
    kwargs.setdefault("private_scope", _TEST_PRIVATE_SCOPES.get(command_id))
    kwargs.setdefault(
        "expected_dispatch_digest",
        _TEST_PRIVATE_DISPATCH_DIGESTS.get(command_id),
    )


def claim_collection_attempt(session_factory, command_id, **kwargs):
    _scope_kwargs(command_id, kwargs)
    return _claim_collection_attempt(session_factory, command_id, **kwargs)


def renew_collection_claim(session_factory, command_id, **kwargs):
    _scope_kwargs(command_id, kwargs)
    return _renew_collection_claim(session_factory, command_id, **kwargs)


def release_collection_claim(session_factory, command_id, **kwargs):
    _scope_kwargs(command_id, kwargs)
    return _release_collection_claim(session_factory, command_id, **kwargs)


def commit_collection_candidate(session_factory, dispatch, *args, **kwargs):
    scope = kwargs.setdefault("private_scope", _trusted_scope(dispatch))
    _TEST_PRIVATE_SCOPES[dispatch.payload.command_id] = scope
    _TEST_PRIVATE_DISPATCH_DIGESTS[dispatch.payload.command_id] = dispatch_digest(dispatch)
    return _commit_collection_candidate(session_factory, dispatch, *args, **kwargs)


def replay_staged_collection(session_factory, dispatch, **kwargs):
    kwargs.setdefault("private_scope", _trusted_scope(dispatch))
    return _replay_staged_collection(session_factory, dispatch, **kwargs)


def apply_commit_gate(session, gate, **kwargs):
    kwargs.setdefault("private_gate_authority", _gate_authority(gate))
    return _apply_commit_gate(session, gate, **kwargs)


@pytest.mark.approved_postgres
@pytest.mark.parametrize("dispatch_kind", ["core", "direct"])
@pytest.mark.parametrize("scope_kind", ["ACCOUNT", "PROJECT"])
def test_first_reservation_requires_w1_scope_then_fresh_write_authority(
    runtime_session_factory: sessionmaker[Session],
    dispatch_kind: str,
    scope_kind: str,
) -> None:
    """Skipping either protected decision would persist an unbound first reservation."""

    company_id, source_id = _seed_source(runtime_session_factory)
    with runtime_session_factory.begin() as session:
        source = session.get(Source, source_id)
        assert source is not None
        source.source_type = SourceType.COMPANY_WEBSITE.value
    project_id = uuid4() if scope_kind == "PROJECT" else None
    scope = PrivateDeletionScope(kind=cast(Any, scope_kind), project_id=project_id)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
        dispatch_kind=dispatch_kind,
        project_ref=None if project_id is None else str(project_id),
    )
    observed_before_decision: list[tuple[str, int, int]] = []

    def observe_unmutated_database(decision: str) -> None:
        with runtime_session_factory() as session:
            source = session.get(Source, source_id)
            assert source is not None
            observed_before_decision.append(
                (
                    decision,
                    source.next_observation_order,
                    session.scalar(select(func.count()).select_from(CollectionRuntimeAttempt)),
                )
            )

    authority = _PrivateAuthorityClient(scope, before_decision=observe_unmutated_database)
    lookup = _AvailableDispatchLookup()
    attempt_id = uuid4()
    with pytest.raises(StopIteration):
        handle_collection_dispatch(
            dispatch,
            session_factory=runtime_session_factory,
            lookup_client=lookup,
            private_authority_client=authority,
            input_provider=SqlAlchemyCollectionInputProvider(
                runtime_session_factory,
                _source_runtime_config({source_id: 3}),
            ),
            collector_factory=lambda: pytest.fail("reservation boundary must stop before collect"),
            parser=extract_static_candidate,
            runtime_config=_source_runtime_config({source_id: 3}),
            clock=lambda: NOW,
            uuid_factory=_uuid_factory(attempt_id),
        )

    assert lookup.dispatches == [dispatch]
    assert authority.events == ["scope", "write"]
    assert observed_before_decision == [("scope", 0, 0), ("write", 0, 0)]
    with runtime_session_factory() as session:
        attempt = session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
        source = session.get(Source, source_id)
        assert attempt is not None and source is not None
        assert attempt.attempt_id == attempt_id
        assert attempt.dispatch_digest == dispatch_digest(dispatch)
        assert attempt.owner_ref == dispatch.payload.authenticated_owner_ref
        assert attempt.job_id == dispatch.payload.job_id
        assert attempt.private_scope_kind == scope_kind
        assert attempt.project_id == project_id
        assert source.next_observation_order == 1
        assert session.scalar(select(func.count()).select_from(CollectionRuntimeAttempt)) == 1


@pytest.mark.approved_postgres
def test_synthetic_w2_w3_probe_supplies_exact_account_authority(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    """The synthetic direct caller must cross the same first-binding authority boundary."""

    result = _collect_product_source(runtime_session_factory)

    assert set(result) == {
        "source_id",
        "source_version_id",
        "evidence_count",
        "outbox_event_id",
    }
    assert cast(int, result["evidence_count"]) > 0


@pytest.mark.approved_postgres
@pytest.mark.parametrize(
    ("scope_kind", "project_ref"),
    [
        ("ACCOUNT", "50000000-0000-4000-8000-000000000001"),
        ("PROJECT", None),
        ("PROJECT", "abcdefab-cdef-4abc-8def-abcdefabcdef".upper()),
        ("PROJECT", "{50000000-0000-4000-8000-000000000001}"),
    ],
)
def test_first_reservation_requires_w1_scope_then_fresh_write_authority_rejects_nonexact_ref(
    runtime_session_factory: sessionmaker[Session],
    scope_kind: str,
    project_ref: str | None,
) -> None:
    """Normalizing or inferring project_ref would bind a dispatch W1 did not issue."""

    company_id, source_id = _seed_source(runtime_session_factory)
    project_id = UUID("abcdefab-cdef-4abc-8def-abcdefabcdef") if scope_kind == "PROJECT" else None
    scope = PrivateDeletionScope(kind=cast(Any, scope_kind), project_id=project_id)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
        project_ref=project_ref,
    )
    authority = _PrivateAuthorityClient(scope)

    with pytest.raises(RuntimeAuthorizationError):
        handle_collection_dispatch(
            dispatch,
            session_factory=runtime_session_factory,
            lookup_client=_AvailableDispatchLookup(),
            private_authority_client=authority,
            input_provider=SqlAlchemyCollectionInputProvider(
                runtime_session_factory,
                _source_runtime_config({source_id: 3}),
            ),
            collector_factory=lambda: pytest.fail("invalid binding must not collect"),
            parser=extract_static_candidate,
            runtime_config=_source_runtime_config({source_id: 3}),
            clock=lambda: NOW,
            uuid_factory=uuid4,
        )

    assert authority.events == ["scope"]
    with runtime_session_factory() as session:
        source = session.get(Source, source_id)
        assert source is not None
        assert source.next_observation_order == 0
        assert session.scalar(select(func.count()).select_from(CollectionRuntimeAttempt)) == 0


@pytest.mark.approved_postgres
def test_revocation_between_claim_and_commit_prevents_public_and_private_writes(
    runtime_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A claim-time decision must not authorize renew, commit, or stale-digest takeover."""

    company_id, source_id = _seed_source(runtime_session_factory)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
        decision_id=UUID("51000000-0000-4000-8000-000000000011"),
    )
    scope = PrivateDeletionScope(kind="ACCOUNT", project_id=None)
    with runtime_session_factory.begin() as session:
        source = session.get(Source, source_id)
        assert source is not None
        source.source_type = SourceType.COMPANY_WEBSITE.value
        pointer_before = (
            source.latest_observation_id,
            source.current_source_version_id,
            source.first_collected_at,
            source.last_collected_at,
            source.last_promoted_observation_order,
        )

    class _RevokedAfterClaimAuthority(_PrivateAuthorityClient):
        def __init__(self) -> None:
            super().__init__(scope)
            self.successful_authority_refs: list[str] = []
            self.write_calls = 0

        def authorize_write(
            self,
            binding: W1PrivateBinding,
            requested_scope: PrivateDeletionScope,
        ) -> PrivateWriteAuthorityResponse:
            self.events.append("write")
            self.write_calls += 1
            if self.write_calls >= 3:
                raise W1LookupClientError("revoked after claim")
            authority_ref = f"w1:test:revocation:{self.write_calls}"
            self.successful_authority_refs.append(authority_ref)
            return PrivateWriteAuthorityResponse.model_validate(
                {
                    "schema_version": "w1.private.w2-write-authority.v1",
                    **_private_binding_payload(binding),
                    "scope": requested_scope.to_mapping(),
                    "authority_ref": authority_ref,
                },
                strict=True,
            )

    class _NoopHeartbeat:
        lost = False

        def __init__(self, *args: object, **kwargs: object) -> None:
            return None

        def start(self) -> None:
            return None

        def ensure_active(self) -> None:
            return None

        def stop_and_join(self) -> None:
            return None

    class _PreparedButUncommittedExecution:
        def __init__(self, *args: object, **kwargs: object) -> None:
            self.closed = False

        def run_once(self, context: object) -> object:
            enter_stage = cast(Any, context).enter_stage
            enter_stage(CollectionStage.POLICY, policy_revision=3)
            enter_stage(CollectionStage.FETCH, policy_revision=3)
            enter_stage(CollectionStage.PARSE, policy_revision=3)
            return object()

        def close(self) -> None:
            self.closed = True

    monkeypatch.setattr(source_runtime_module, "_ClaimHeartbeat", _NoopHeartbeat)
    monkeypatch.setattr(
        source_runtime_module,
        "StaticCollectionExecution",
        _PreparedButUncommittedExecution,
    )
    authority = _RevokedAfterClaimAuthority()
    attempt_id, claim_token, staged_message_id = uuid4(), uuid4(), uuid4()

    def reject_reused_commit(*args: object, **kwargs: object) -> object:
        pytest.fail("commit was reached with claim-time authority")

    store_operations = source_runtime_module.RuntimeStoreOperations(
        load_attempt=load_bound_collection_attempt,
        reserve_attempt=_reserve_collection_attempt,
        claim_attempt=_claim_collection_attempt,
        renew_claim=_renew_collection_claim,
        release_claim=_release_collection_claim,
        commit_candidate=reject_reused_commit,
        replay_candidate=_replay_staged_collection,
    )
    with pytest.raises(RuntimeAuthorizationError, match="private write authority"):
        handle_collection_dispatch(
            dispatch,
            session_factory=runtime_session_factory,
            lookup_client=_AvailableDispatchLookup(),
            private_authority_client=authority,
            input_provider=SqlAlchemyCollectionInputProvider(
                runtime_session_factory,
                _source_runtime_config({source_id: 3}),
            ),
            collector_factory=object,
            parser=extract_static_candidate,
            runtime_config=_source_runtime_config({source_id: 3}),
            clock=lambda: NOW,
            uuid_factory=_uuid_factory(attempt_id, claim_token, staged_message_id),
            store_operations=store_operations,
        )

    assert authority.events == ["scope", "write", "write", "write", "write"]
    assert authority.successful_authority_refs == [
        "w1:test:revocation:1",
        "w1:test:revocation:2",
    ]
    assert len(set(authority.successful_authority_refs)) == 2
    with runtime_session_factory.begin() as session:
        attempt = session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
        source = session.get(Source, source_id)
        assert attempt is not None and source is not None
        assert attempt.state == "RESERVED"
        assert attempt.claim_token == claim_token
        assert (
            source.latest_observation_id,
            source.current_source_version_id,
            source.first_collected_at,
            source.last_collected_at,
            source.last_promoted_observation_order,
        ) == pointer_before
        assert session.get(PrivateCommitStage, dispatch.payload.command_id) is None
        assert session.scalar(select(func.count()).select_from(PrivateStagedOutbox)) == 0
        assert session.scalar(select(func.count()).select_from(SourceObservation)) == 0
        assert session.scalar(select(func.count()).select_from(SourceVersion)) == 0
        attempt.claim_expires_at = NOW - timedelta(seconds=1)

    changed_dispatch = _dispatch(
        command_id=dispatch.payload.command_id,
        job_id=dispatch.payload.job_id,
        owner_ref=dispatch.payload.authenticated_owner_ref,
        company_id=company_id,
        source_id=source_id,
        decision_id=UUID("51000000-0000-4000-8000-000000000012"),
    )
    with pytest.raises(CollectionRuntimeConflict, match="dispatch"):
        _claim_collection_attempt(
            runtime_session_factory,
            dispatch.payload.command_id,
            claim_token=uuid4(),
            lease_seconds=30,
            expected_dispatch_digest=dispatch_digest(changed_dispatch),
            private_scope=_scope_for_dispatch(
                dispatch,
                scope,
                authority_ref="w1:test:revocation:takeover",
            ),
        )
    with runtime_session_factory() as session:
        attempt = session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
        assert attempt is not None
        assert attempt.dispatch_digest == dispatch_digest(dispatch)
        assert attempt.claim_token == claim_token
        assert attempt.claim_expires_at == NOW - timedelta(seconds=1)


@pytest.mark.approved_postgres
@pytest.mark.parametrize(
    "changed",
    [
        "uppercase_ref",
        "noncanonical_ref",
        "owner",
        "job",
        "fence",
        "epoch",
        "missing_ref",
        "pin_digest",
        "scope",
        "unknown_scope",
    ],
)
def test_replayed_dispatch_cannot_change_original_scope_or_digest(
    runtime_session_factory: sessionmaker[Session],
    changed: str,
) -> None:
    """Replay must neither rewrite its first binding nor consume a second order."""

    company_id, source_id = _seed_source(runtime_session_factory)
    command_id, job_id, owner_ref = uuid4(), uuid4(), uuid4()
    project_id = UUID("abcdefab-cdef-4abc-8def-abcdefabcdef")
    decision_id = UUID("51000000-0000-4000-8000-000000000001")
    original = _dispatch(
        command_id=command_id,
        job_id=job_id,
        owner_ref=owner_ref,
        company_id=company_id,
        source_id=source_id,
        project_ref=str(project_id),
        decision_id=decision_id,
    )
    project_scope = PrivateDeletionScope(kind="PROJECT", project_id=project_id)
    with runtime_session_factory.begin() as session:
        source = session.get(Source, source_id)
        assert source is not None
        source.source_type = SourceType.COMPANY_WEBSITE.value
        session.flush()
        _reserve_collection_attempt(
            session,
            original,
            effective_policy_revision=3,
            now=NOW,
            uuid_factory=_uuid_factory(uuid4()),
            private_scope=_scope_for_dispatch(original, project_scope),
        )
    if changed == "unknown_scope":
        with runtime_session_factory.begin() as session:
            attempt = session.get(CollectionRuntimeAttempt, command_id)
            assert attempt is not None
            attempt.private_scope_kind = "UNKNOWN"
            attempt.project_id = None

    changed_project_ref: str | None = str(project_id)
    if changed == "uppercase_ref":
        changed_project_ref = str(project_id).upper()
    elif changed == "noncanonical_ref":
        changed_project_ref = "{" + str(project_id) + "}"
    elif changed == "missing_ref":
        changed_project_ref = None
    replay = _dispatch(
        command_id=command_id,
        job_id=uuid4() if changed == "job" else job_id,
        owner_ref=uuid4() if changed == "owner" else owner_ref,
        company_id=company_id,
        source_id=source_id,
        execution_fence=2 if changed == "fence" else 1,
        owner_deletion_epoch=1 if changed == "epoch" else 0,
        project_ref=changed_project_ref,
        decision_id=(
            UUID("51000000-0000-4000-8000-000000000002") if changed == "pin_digest" else decision_id
        ),
    )
    authority = _PrivateAuthorityClient(
        project_scope,
        write_scope=(
            PrivateDeletionScope(kind="ACCOUNT", project_id=None) if changed == "scope" else None
        ),
    )

    with pytest.raises(
        (RuntimeAuthorizationError, CollectionRuntimeConflict, PrivateScopeRejected)
    ):
        handle_collection_dispatch(
            replay,
            session_factory=runtime_session_factory,
            lookup_client=_AvailableDispatchLookup(),
            private_authority_client=authority,
            input_provider=SqlAlchemyCollectionInputProvider(
                runtime_session_factory,
                _source_runtime_config({source_id: 3}),
            ),
            collector_factory=lambda: pytest.fail("conflicting replay must not collect"),
            parser=extract_static_candidate,
            runtime_config=_source_runtime_config({source_id: 3}),
            clock=lambda: NOW,
            uuid_factory=_uuid_factory(),
        )

    assert authority.events == (["write"] if changed == "scope" else [])
    with runtime_session_factory() as session:
        attempt = session.get(CollectionRuntimeAttempt, command_id)
        source = session.get(Source, source_id)
        assert attempt is not None and source is not None
        assert attempt.dispatch_digest == dispatch_digest(original)
        assert attempt.owner_ref == owner_ref
        assert attempt.job_id == job_id
        assert attempt.private_scope_kind == (
            "UNKNOWN" if changed == "unknown_scope" else "PROJECT"
        )
        assert attempt.project_id == (None if changed == "unknown_scope" else project_id)
        assert source.next_observation_order == 1
        assert session.scalar(select(func.count()).select_from(CollectionRuntimeAttempt)) == 1


@pytest.mark.approved_postgres
def test_runtime_reservation_and_claim_lifecycle_require_current_trusted_scope(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    with runtime_session_factory.begin() as session:
        with pytest.raises(PrivateScopeRejected, match="scope"):
            _reserve_collection_attempt(
                session,
                dispatch,
                effective_policy_revision=3,
                now=NOW,
                uuid_factory=_uuid_factory(uuid4()),
            )
        attempt = reserve_collection_attempt(
            session,
            dispatch,
            effective_policy_revision=3,
            now=NOW,
            uuid_factory=_uuid_factory(uuid4()),
        )
        assert attempt.private_scope_kind == "ACCOUNT"
        assert attempt.project_id is None

    command_id = dispatch.payload.command_id
    with pytest.raises(PrivateScopeRejected, match="scope"):
        _claim_collection_attempt(
            runtime_session_factory,
            command_id,
            claim_token=uuid4(),
            lease_seconds=30,
            expected_dispatch_digest=dispatch_digest(dispatch),
        )
    claim_token = uuid4()
    claim_collection_attempt(
        runtime_session_factory,
        command_id,
        claim_token=claim_token,
        lease_seconds=30,
    )
    renew_collection_claim(
        runtime_session_factory,
        command_id,
        claim_token=claim_token,
        lease_seconds=30,
    )
    release_collection_claim(
        runtime_session_factory,
        command_id,
        claim_token=claim_token,
    )

    stale = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    with runtime_session_factory.begin() as session:
        session.add(
            PrivateDeletionOwnerState(
                owner_user_id=stale.payload.authenticated_owner_ref,
                latest_epoch=1,
                account_deleted=False,
            )
        )
    with runtime_session_factory.begin() as session:
        with pytest.raises(PrivateScopeRejected, match="current epoch"):
            _reserve_collection_attempt(
                session,
                stale,
                effective_policy_revision=3,
                now=NOW,
                uuid_factory=_uuid_factory(uuid4()),
                private_scope=_trusted_scope(stale),
            )


def _uuid_factory(*values: UUID) -> Callable[[], UUID]:
    iterator = iter(values)
    return lambda: next(iterator)


def _unknown_date() -> DateValue:
    return DateValue(
        status="unknown",
        raw_text=None,
        value=None,
        precision=None,
        timezone=None,
    )


def _candidate_complete_prepared(
    command: Any,
    source: Source,
    policy: SourcePolicyDecision,
    *,
    attempt_id: UUID,
    aggregate_revision: int,
) -> PreparedCollectionCommit:
    source_version_id = uuid4()
    evidence_id = uuid4()
    extraction_revision_id = uuid4()
    observation = SourceObservationSnapshot(
        observation_id=uuid4(),
        source_id=source.source_id,
        source_version_id=source_version_id,
        policy_decision_id=policy.policy_decision_id,
        observed_at=NOW,
        access_class="public",
        acquisition_status="AVAILABLE",
        http_status=200,
        checked_url=source.canonical_url,
        error_code=None,
        representation="static_html",
    )
    result = CollectionResult.model_validate(
        {
            "schema_version": "w2.collection.v1",
            "command_id": command.command_id,
            "job_id": command.job_id,
            "source_id": command.source_id,
            "input_version": command.input_version,
            "result_version": 1,
            "policy_revision": command.policy_revision,
            "successful_source_refs": [
                {
                    "source_id": source.source_id,
                    "source_version_id": source_version_id,
                    "extraction_revision_id": extraction_revision_id,
                }
            ],
            "failures": [],
            "completion_kind": "complete",
            "resume_stage": None,
            "checkpoint_ref": None,
            "required_actions": [],
            "retry_not_before": None,
            "message_ko": "candidate collection complete",
        }
    )
    return PreparedCollectionCommit(
        attempt_id=attempt_id,
        result=result,
        finalized_at=NOW,
        source_version=PreparedSourceVersion(
            source_version_id=source_version_id,
            source_id=source.source_id,
            company_id=source.company_id,
            title=source.title,
            source_type=SourceType.JOB_POSTING,
            canonical_url=source.canonical_url,
            content_hash="c" * 64,
            hash_profile_version="html-v1",
            representation=Representation.STATIC_HTML,
            first_parser_version="candidate-parser-v1",
            collected_at=NOW,
            published_at=_unknown_date(),
            valid_from=_unknown_date(),
            valid_to=_unknown_date(),
            language="ko",
            policy_decision_id=policy.policy_decision_id,
        ),
        evidence=(
            PreparedEvidence(
                evidence_id=evidence_id,
                source_version_id=source_version_id,
                evidence_key="required:0",
                section_title="Required",
                text_excerpt="Candidate evidence",
                locator=Locator(
                    kind="css",
                    value="#requirements",
                    normalization_version=None,
                    start=None,
                    end=None,
                ),
                chunk_order=0,
                origin_kind="direct",
            ),
        ),
        extraction_revision=PreparedExtractionRevision(
            extraction_revision_id=extraction_revision_id,
            source_version_id=source_version_id,
            parser_version="candidate-parser-v1",
            output_hash="c" * 64,
            created_at=NOW,
            extraction_status=ExtractionStatus.COMPLETE,
            posting_sections=(
                PostingSection(
                    section_key="required",
                    kind="required",
                    heading_raw="Required",
                    text_raw="Candidate evidence",
                    evidence_ids=[evidence_id],
                    order=0,
                    relation_text=None,
                ),
            ),
            date_values={"published": _unknown_date()},
            limitations=(),
            evidence_ids=(evidence_id,),
        ),
        parser_execution=PreparedParserExecution(
            parser_execution_id=uuid4(),
            source_id=source.source_id,
            source_version_id=source_version_id,
            content_hash="c" * 64,
            parser_version="candidate-parser-v1",
            output_hash="c" * 64,
            extraction_revision_id=extraction_revision_id,
            status="succeeded",
            executed_at=NOW,
        ),
        observation=PreparedSourceObservation(snapshot=observation),
        events=(
            SourceEvent(
                event_id=uuid4(),
                event_type="source.observation.changed",
                schema_version="w2.source.v1",
                aggregate_id=source.source_id,
                aggregate_revision=aggregate_revision,
                occurred_at=NOW,
                payload=observation,
            ),
        ),
    )


def _runtime_attempt(
    session_factory: sessionmaker[Session],
    dispatch: W1Dispatch,
    *,
    effective_policy_revision: int = 3,
) -> CollectionRuntimeAttempt:
    with session_factory.begin() as session:
        return reserve_collection_attempt(
            session,
            dispatch,
            effective_policy_revision=effective_policy_revision,
            now=NOW,
            uuid_factory=_uuid_factory(uuid4()),
        )


def _claimed_candidate_inputs(
    session_factory: sessionmaker[Session],
) -> tuple[W1Dispatch, Any, PreparedCollectionCommit, UUID]:
    company_id, source_id = _seed_source(session_factory)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    effective_command = dispatch.payload.model_copy(update={"policy_revision": 3})
    with session_factory.begin() as session:
        source = session.get(Source, source_id)
        policy = session.scalar(
            select(SourcePolicyDecision).where(
                SourcePolicyDecision.source_id == source_id,
                SourcePolicyDecision.revision == 3,
            )
        )
        assert source is not None and policy is not None
        source.source_type = SourceType.JOB_POSTING.value
    reserved = _runtime_attempt(session_factory, dispatch, effective_policy_revision=3)
    claim_token = uuid4()
    claim_collection_attempt(
        session_factory,
        dispatch.payload.command_id,
        claim_token=claim_token,
        lease_seconds=30,
    )
    prepared = _candidate_complete_prepared(
        effective_command,
        source,
        policy,
        attempt_id=reserved.attempt_id,
        aggregate_revision=987,
    )
    return dispatch, effective_command, prepared, claim_token


def _gate(
    dispatch: W1Dispatch,
    action: str,
    *,
    result_digest: str | None = None,
    operation_id: UUID | None = None,
    operation_revision: int = 1,
) -> CommitGateCommand:
    raw = json.loads(
        (FIXTURES / f"private-w2-commit-gate-{action.lower()}.json").read_text(encoding="utf-8")
    )
    command = dispatch.payload
    raw.update(
        message_id=str(uuid4()),
        operation_id=str(operation_id or uuid4()),
        operation_revision=operation_revision,
        command_id=str(command.command_id),
        job_id=str(command.job_id),
        authenticated_owner_ref=str(command.authenticated_owner_ref),
        execution_fence=int(command.execution_fence),
        owner_deletion_epoch=command.owner_deletion_epoch,
        result_digest=result_digest or "sha256:" + "a" * 64,
    )
    if action == "PURGE":
        raw["purge_owner_deletion_epoch"] = command.owner_deletion_epoch + 1
    return parse_commit_gate_command(raw)


def _assert_attempt_row_is_unlocked(
    session_factory: sessionmaker[Session], command_id: UUID
) -> None:
    with session_factory.begin() as session:
        assert (
            session.scalar(
                select(CollectionRuntimeAttempt)
                .where(CollectionRuntimeAttempt.command_id == command_id)
                .with_for_update(nowait=True)
            )
            is not None
        )


@pytest.mark.approved_postgres
def test_reservation_binds_digest_replays_without_consuming_order_and_orders_two_owners(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    first_dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    second_dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    first_attempt_id, unused_replay_id, second_attempt_id = uuid4(), uuid4(), uuid4()
    ids = _uuid_factory(first_attempt_id, unused_replay_id, second_attempt_id)

    with runtime_session_factory.begin() as session:
        first = reserve_collection_attempt(
            session,
            first_dispatch,
            effective_policy_revision=3,
            now=NOW,
            uuid_factory=ids,
        )
        replay = reserve_collection_attempt(
            session,
            first_dispatch,
            effective_policy_revision=3,
            now=NOW,
            uuid_factory=ids,
        )
        second = reserve_collection_attempt(
            session,
            second_dispatch,
            effective_policy_revision=3,
            now=NOW,
            uuid_factory=ids,
        )

        assert first.attempt_id == first_attempt_id
        assert replay.command_id == first.command_id
        assert replay.observation_order == first.observation_order == 1
        assert second.attempt_id == unused_replay_id
        assert second.attempt_id != second_attempt_id
        assert second.observation_order == 2
        assert first.dispatch_digest == dispatch_digest(first_dispatch)

    with runtime_session_factory() as session:
        source = session.get(Source, source_id)
        assert source is not None
        assert source.pointer_update_mode == "FINALIZE_GATE"
        assert source.next_observation_order == 2
        assert session.scalar(select(func.count()).select_from(CollectionRuntimeAttempt)) == 2


@pytest.mark.approved_postgres
@pytest.mark.parametrize("changed", ["pin", "company", "source", "fence", "epoch", "job"])
def test_reservation_rejects_changed_immutable_dispatch_without_consuming_order(
    runtime_session_factory: sessionmaker[Session], changed: str
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    command_id, job_id, owner_ref = uuid4(), uuid4(), uuid4()
    dispatch = _dispatch(
        command_id=command_id,
        job_id=job_id,
        owner_ref=owner_ref,
        company_id=company_id,
        source_id=source_id,
        decision_id=UUID("31000000-0000-4000-8000-000000000001"),
    )
    _runtime_attempt(runtime_session_factory, dispatch)
    changed_dispatch = _dispatch(
        command_id=command_id,
        job_id=uuid4() if changed == "job" else job_id,
        owner_ref=owner_ref,
        company_id=uuid4() if changed == "company" else company_id,
        source_id=uuid4() if changed == "source" else source_id,
        execution_fence=2 if changed == "fence" else 1,
        owner_deletion_epoch=1 if changed == "epoch" else 0,
        decision_id=(
            UUID("31000000-0000-4000-8000-000000000002")
            if changed == "pin"
            else UUID("31000000-0000-4000-8000-000000000001")
        ),
    )

    with runtime_session_factory.begin() as session:
        with pytest.raises((CollectionRuntimeConflict, PrivateScopeRejected)):
            reserve_collection_attempt(
                session,
                changed_dispatch,
                effective_policy_revision=3,
                now=NOW,
                uuid_factory=_uuid_factory(uuid4()),
            )

    with runtime_session_factory() as session:
        source = session.get(Source, source_id)
        assert source is not None
        assert source.next_observation_order == 1


@pytest.mark.approved_postgres
def test_reservation_rejects_changed_effective_policy_revision_without_consuming_order(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    _runtime_attempt(runtime_session_factory, dispatch, effective_policy_revision=3)

    with runtime_session_factory.begin() as session:
        with pytest.raises(CollectionRuntimeConflict):
            reserve_collection_attempt(
                session,
                dispatch,
                effective_policy_revision=4,
                now=NOW,
                uuid_factory=_uuid_factory(uuid4()),
            )

    with runtime_session_factory() as session:
        source = session.get(Source, source_id)
        assert source is not None
        assert source.next_observation_order == 1


@pytest.mark.approved_postgres
def test_reservation_rechecks_locked_source_policy_revision_before_switching_mode_or_order(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    with runtime_session_factory.begin() as session:
        session.add(_policy_decision(source_id, 4))

    with runtime_session_factory.begin() as session:
        with pytest.raises(CollectionRuntimeConflict):
            reserve_collection_attempt(
                session,
                dispatch,
                effective_policy_revision=3,
                now=NOW,
                uuid_factory=_uuid_factory(uuid4()),
            )

    with runtime_session_factory() as session:
        source = session.get(Source, source_id)
        assert source is not None
        assert source.pointer_update_mode == "LEGACY_SAME_DB"
        assert source.next_observation_order == 0
        assert session.scalar(select(func.count()).select_from(CollectionRuntimeAttempt)) == 0


@pytest.mark.approved_postgres
@pytest.mark.parametrize("revision", [0, -1])
def test_reservation_rejects_non_positive_policy_revision_before_counter_increment(
    runtime_session_factory: sessionmaker[Session], revision: int
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )

    with runtime_session_factory.begin() as session:
        with pytest.raises(CollectionRuntimeConflict):
            reserve_collection_attempt(
                session,
                dispatch,
                effective_policy_revision=revision,
                now=NOW,
                uuid_factory=_uuid_factory(uuid4()),
            )

    with runtime_session_factory() as session:
        source = session.get(Source, source_id)
        assert source is not None
        assert source.next_observation_order == 0


@pytest.mark.approved_postgres
def test_reservation_rejects_missing_or_wrong_company_source_without_consuming_order(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    other_company_id, other_source_id = _seed_source(runtime_session_factory)
    missing_dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=uuid4(),
    )
    mismatch_dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=other_source_id,
    )

    for dispatch in (missing_dispatch, mismatch_dispatch):
        with runtime_session_factory.begin() as session:
            with pytest.raises(CollectionRuntimeConflict):
                reserve_collection_attempt(
                    session,
                    dispatch,
                    effective_policy_revision=3,
                    now=NOW,
                    uuid_factory=_uuid_factory(uuid4()),
                )

    with runtime_session_factory() as session:
        source = session.get(Source, source_id)
        assert source is not None
        assert source.next_observation_order == 0
        other = session.get(Source, other_source_id)
        assert other is not None
        assert other.company_id == other_company_id
        assert other.next_observation_order == 0


@pytest.mark.approved_postgres
def test_reservation_fails_if_source_was_deleted_after_dispatch_validation(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory, policy_revision=None)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    with runtime_session_factory.begin() as session:
        source = session.get(Source, source_id)
        assert source is not None
        session.delete(source)

    with runtime_session_factory.begin() as session:
        with pytest.raises(CollectionRuntimeConflict):
            reserve_collection_attempt(
                session,
                dispatch,
                effective_policy_revision=3,
                now=NOW,
                uuid_factory=_uuid_factory(uuid4()),
            )

    with runtime_session_factory() as session:
        assert session.get(CollectionRuntimeAttempt, dispatch.payload.command_id) is None


@pytest.mark.approved_postgres
def test_bound_load_ignores_newer_current_policy_but_reservation_rejects_terminal_state(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    _runtime_attempt(runtime_session_factory, dispatch, effective_policy_revision=3)
    with runtime_session_factory.begin() as session:
        session.add(
            SourcePolicyDecision(
                policy_decision_id=uuid4(),
                source_id=source_id,
                revision=4,
                official_status="verified",
                access_class="public",
                collection_permission="allowed",
                excerpt_storage_permission="allowed",
                body_storage_permission="allowed",
                redistribution_permission="denied",
                evidence_refs=["synthetic:policy:4"],
                checked_at=NOW,
                policy_version="test-v4",
            )
        )
    with runtime_session_factory.begin() as session:
        attempt = session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
        assert attempt is not None
        attempt.state = "PERSISTED"

    with runtime_session_factory() as session:
        loaded = load_bound_collection_attempt(session, dispatch)
        assert loaded is not None
        assert loaded.effective_policy_revision == 3
        assert loaded.state == "PERSISTED"

    with runtime_session_factory.begin() as session:
        with pytest.raises(CollectionRuntimeConflict):
            reserve_collection_attempt(
                session,
                dispatch,
                effective_policy_revision=3,
                now=NOW,
                uuid_factory=_uuid_factory(uuid4()),
            )


@pytest.mark.approved_postgres
@pytest.mark.parametrize("action", ["ABORT", "PURGE"])
def test_gate_first_collection_tombstone_rejects_reservation_without_consuming_order(
    runtime_session_factory: sessionmaker[Session], action: str
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    with runtime_session_factory.begin() as session:
        lock_private_command(session, dispatch.payload.command_id)
        apply_commit_gate(
            session,
            _gate(dispatch, action),
            ack_message_id=uuid4(),
            occurred_at=NOW,
            missing_stage_kind="COLLECTION",
        )

    with runtime_session_factory.begin() as session:
        stage = session.get(PrivateCommitStage, dispatch.payload.command_id)
        assert stage is not None
        assert stage.stage_kind == "COLLECTION"
        assert stage.state == ("ABORTED" if action == "ABORT" else "PURGED")
        with pytest.raises(CollectionRuntimeConflict):
            reserve_collection_attempt(
                session,
                dispatch,
                effective_policy_revision=3,
                now=NOW,
                uuid_factory=_uuid_factory(uuid4()),
            )

    with runtime_session_factory() as session:
        source = session.get(Source, source_id)
        assert source is not None
        assert source.next_observation_order == 0
        assert session.get(CollectionRuntimeAttempt, dispatch.payload.command_id) is None


@pytest.mark.approved_postgres
def test_two_sessions_serialize_source_orders_for_different_commands(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    dispatches = tuple(
        _dispatch(
            command_id=uuid4(),
            job_id=uuid4(),
            owner_ref=uuid4(),
            company_id=company_id,
            source_id=source_id,
        )
        for _ in range(2)
    )
    barrier = Barrier(2)

    def reserve(dispatch: W1Dispatch) -> int:
        with runtime_session_factory.begin() as session:
            barrier.wait(timeout=5)
            attempt = reserve_collection_attempt(
                session,
                dispatch,
                effective_policy_revision=3,
                now=NOW,
                uuid_factory=_uuid_factory(uuid4()),
            )
            return attempt.observation_order

    with ThreadPoolExecutor(max_workers=2) as pool:
        orders = tuple(pool.map(reserve, dispatches))

    assert set(orders) == {1, 2}
    with runtime_session_factory() as session:
        source = session.get(Source, source_id)
        assert source is not None
        assert source.next_observation_order == 2


@pytest.mark.approved_postgres
def test_claim_uses_db_clock_is_busy_until_expiry_then_allows_takeover_renew_and_release(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    _runtime_attempt(runtime_session_factory, dispatch)
    command_id = dispatch.payload.command_id
    first_token, second_token = uuid4(), uuid4()
    with runtime_session_factory() as session:
        before = session.scalar(select(func.clock_timestamp()))
    claimed = claim_collection_attempt(
        runtime_session_factory,
        command_id,
        claim_token=first_token,
        lease_seconds=30,
    )
    with runtime_session_factory() as session:
        after = session.scalar(select(func.clock_timestamp()))
    assert before is not None and after is not None
    assert claimed.claim_token == first_token
    assert claimed.claim_expires_at is not None
    assert before.timestamp() + 30 <= claimed.claim_expires_at.timestamp()
    assert claimed.claim_expires_at.timestamp() <= after.timestamp() + 30
    _assert_attempt_row_is_unlocked(runtime_session_factory, command_id)

    with pytest.raises(CollectionRuntimeBusy):
        claim_collection_attempt(
            runtime_session_factory,
            command_id,
            claim_token=second_token,
            lease_seconds=30,
        )
    with runtime_session_factory() as session:
        busy_row = session.get(CollectionRuntimeAttempt, command_id)
        assert busy_row is not None
        assert busy_row.claim_token == first_token
    _assert_attempt_row_is_unlocked(runtime_session_factory, command_id)

    with runtime_session_factory.begin() as session:
        session.execute(
            update(CollectionRuntimeAttempt)
            .where(CollectionRuntimeAttempt.command_id == command_id)
            .values(claim_expires_at=func.clock_timestamp() - text("interval '1 second'"))
        )
    takeover = claim_collection_attempt(
        runtime_session_factory,
        command_id,
        claim_token=second_token,
        lease_seconds=30,
    )
    assert takeover.claim_token == second_token

    with pytest.raises(CollectionRuntimeConflict):
        renew_collection_claim(
            runtime_session_factory,
            command_id,
            claim_token=first_token,
            lease_seconds=30,
        )
    with pytest.raises(CollectionRuntimeConflict):
        release_collection_claim(
            runtime_session_factory,
            command_id,
            claim_token=first_token,
        )

    renewed = renew_collection_claim(
        runtime_session_factory,
        command_id,
        claim_token=second_token,
        lease_seconds=60,
    )
    renewed_again = renew_collection_claim(
        runtime_session_factory,
        command_id,
        claim_token=second_token,
        lease_seconds=60,
    )
    assert renewed.claim_token == renewed_again.claim_token == second_token
    assert renewed.claim_expires_at is not None
    assert renewed_again.claim_expires_at is not None
    assert renewed_again.claim_expires_at >= renewed.claim_expires_at
    _assert_attempt_row_is_unlocked(runtime_session_factory, command_id)

    released = release_collection_claim(
        runtime_session_factory,
        command_id,
        claim_token=second_token,
    )
    assert released.claim_token is None
    assert released.claim_expires_at is None
    _assert_attempt_row_is_unlocked(runtime_session_factory, command_id)


@pytest.mark.approved_postgres
@pytest.mark.parametrize(
    ("overrides", "cleanup_kind"),
    [
        ({"owner_user_id": uuid4()}, "CLAIM_RELEASE"),
        ({"job_id": uuid4()}, "CLAIM_RELEASE"),
        ({"execution_fence": 2}, "CLAIM_RELEASE"),
        ({"owner_deletion_epoch": 1}, "CLAIM_RELEASE"),
        ({"scope": {"type": "PROJECT", "project_id": str(uuid4())}}, "CLAIM_RELEASE"),
        ({}, "RESERVATION_RELEASE"),
    ],
)
def test_cancelled_claim_uses_cleanup_rejects_changed_binding_and_absent_row(
    runtime_session_factory: sessionmaker[Session],
    overrides: dict[str, object],
    cleanup_kind: str,
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    _runtime_attempt(runtime_session_factory, dispatch)
    command_id = dispatch.payload.command_id
    claim_token = uuid4()
    claim_collection_attempt(
        runtime_session_factory,
        command_id,
        claim_token=claim_token,
        lease_seconds=30,
    )
    binding = W1PrivateBinding.from_collection(dispatch.payload)
    scope = PrivateDeletionScope(kind="ACCOUNT", project_id=None)
    authority = _terminal_cleanup_authority(
        binding,
        scope,
        cleanup_kind=cleanup_kind,
        **overrides,
    )
    with runtime_session_factory.begin() as session:
        owner_state = session.get(PrivateDeletionOwnerState, binding.owner_user_id)
        assert owner_state is not None
        owner_state.latest_epoch = binding.owner_deletion_epoch + 1
        owner_state.account_deleted = True

    with pytest.raises((CollectionRuntimeConflict, PrivateScopeRejected)):
        _release_collection_claim(
            runtime_session_factory,
            command_id,
            claim_token=claim_token,
            expected_dispatch_digest=dispatch_digest(dispatch),
            private_scope=authority,
            private_binding=binding,
        )

    with runtime_session_factory() as session:
        attempt = session.get(CollectionRuntimeAttempt, command_id)
        assert attempt is not None
        assert attempt.claim_token == claim_token

    valid_authority = _terminal_cleanup_authority(binding, scope)
    released = _release_collection_claim(
        runtime_session_factory,
        command_id,
        claim_token=claim_token,
        expected_dispatch_digest=dispatch_digest(dispatch),
        private_scope=valid_authority,
        private_binding=binding,
    )
    assert released.claim_token is None
    assert released.claim_expires_at is None

    with runtime_session_factory.begin() as session:
        attempt = session.get(CollectionRuntimeAttempt, command_id)
        assert attempt is not None
        session.delete(attempt)
    with pytest.raises(CollectionRuntimeConflict):
        _release_collection_claim(
            runtime_session_factory,
            command_id,
            claim_token=claim_token,
            expected_dispatch_digest=dispatch_digest(dispatch),
            private_scope=valid_authority,
            private_binding=binding,
        )


@pytest.mark.approved_postgres
def test_cancelled_reservation_uses_exact_terminal_cleanup(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    _runtime_attempt(runtime_session_factory, dispatch)
    binding = W1PrivateBinding.from_collection(dispatch.payload)
    scope = PrivateDeletionScope(kind="ACCOUNT", project_id=None)
    with runtime_session_factory.begin() as session:
        owner_state = session.get(PrivateDeletionOwnerState, binding.owner_user_id)
        assert owner_state is not None
        owner_state.latest_epoch = binding.owner_deletion_epoch + 1
        owner_state.account_deleted = True

    released = _release_collection_reservation(
        runtime_session_factory,
        binding.command_id,
        expected_dispatch_digest=dispatch_digest(dispatch),
        private_scope=_terminal_cleanup_authority(
            binding,
            scope,
            cleanup_kind="RESERVATION_RELEASE",
        ),
        private_binding=binding,
    )

    assert released.state == "INVALIDATED"
    assert released.claim_token is None
    assert released.claim_expires_at is None
    with runtime_session_factory() as session:
        attempt = session.get(CollectionRuntimeAttempt, binding.command_id)
        source = session.get(Source, source_id)
        assert attempt is not None and source is not None
        assert attempt.state == "INVALIDATED"
        assert source.next_observation_order == 1


@pytest.mark.approved_postgres
@pytest.mark.parametrize(
    "changed",
    ["owner", "job", "fence", "epoch", "scope", "kind", "digest", "absent", "claimed"],
)
def test_reservation_cleanup_rejects_changed_binding_absent_or_claimed_row(
    runtime_session_factory: sessionmaker[Session],
    changed: str,
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    _runtime_attempt(runtime_session_factory, dispatch)
    binding = W1PrivateBinding.from_collection(dispatch.payload)
    scope = PrivateDeletionScope(kind="ACCOUNT", project_id=None)
    claim_token = uuid4()
    if changed == "claimed":
        claim_collection_attempt(
            runtime_session_factory,
            binding.command_id,
            claim_token=claim_token,
            lease_seconds=30,
        )
    with runtime_session_factory.begin() as session:
        owner_state = session.get(PrivateDeletionOwnerState, binding.owner_user_id)
        assert owner_state is not None
        owner_state.latest_epoch = binding.owner_deletion_epoch + 1
        owner_state.account_deleted = True
        if changed == "absent":
            attempt = session.get(CollectionRuntimeAttempt, binding.command_id)
            assert attempt is not None
            session.delete(attempt)

    overrides: dict[str, object] = {}
    if changed == "owner":
        overrides["owner_user_id"] = uuid4()
    elif changed == "job":
        overrides["job_id"] = uuid4()
    elif changed == "fence":
        overrides["execution_fence"] = binding.execution_fence + 1
    elif changed == "epoch":
        overrides["owner_deletion_epoch"] = binding.owner_deletion_epoch + 1
    elif changed == "scope":
        overrides["scope"] = {"type": "PROJECT", "project_id": str(uuid4())}
    cleanup_kind = "CLAIM_RELEASE" if changed == "kind" else "RESERVATION_RELEASE"
    expected_digest = "f" * 64 if changed == "digest" else dispatch_digest(dispatch)
    authority = _terminal_cleanup_authority(
        binding,
        scope,
        cleanup_kind=cleanup_kind,
        **overrides,
    )

    with pytest.raises((CollectionRuntimeConflict, PrivateScopeRejected)):
        _release_collection_reservation(
            runtime_session_factory,
            binding.command_id,
            expected_dispatch_digest=expected_digest,
            private_scope=authority,
            private_binding=binding,
        )

    with runtime_session_factory() as session:
        attempt = session.get(CollectionRuntimeAttempt, binding.command_id)
        if changed == "absent":
            assert attempt is None
        else:
            assert attempt is not None
            assert attempt.state == "RESERVED"
            assert attempt.claim_token == (claim_token if changed == "claimed" else None)


@pytest.mark.approved_postgres
@pytest.mark.parametrize("lease_seconds", [0, -1, True])
def test_claim_and_renew_reject_invalid_lease(
    runtime_session_factory: sessionmaker[Session], lease_seconds: int
) -> None:
    command_id = uuid4()
    for operation in (claim_collection_attempt, renew_collection_claim):
        with pytest.raises(CollectionRuntimeConflict):
            operation(
                runtime_session_factory,
                command_id,
                claim_token=uuid4(),
                lease_seconds=lease_seconds,
            )


@pytest.mark.approved_postgres
def test_claim_lifecycle_rejects_invalid_state_expired_or_wrong_token(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    _runtime_attempt(runtime_session_factory, dispatch)
    command_id = dispatch.payload.command_id
    token = uuid4()

    with pytest.raises(CollectionRuntimeConflict):
        claim_collection_attempt(
            runtime_session_factory,
            command_id,
            claim_token="not-a-token",  # type: ignore[arg-type]
            lease_seconds=30,
        )
    with pytest.raises(CollectionRuntimeConflict):
        release_collection_claim(runtime_session_factory, command_id, claim_token=token)

    claim_collection_attempt(
        runtime_session_factory,
        command_id,
        claim_token=token,
        lease_seconds=30,
    )
    wrong_token = uuid4()
    with pytest.raises(CollectionRuntimeConflict):
        renew_collection_claim(
            runtime_session_factory,
            command_id,
            claim_token=wrong_token,
            lease_seconds=30,
        )
    with pytest.raises(CollectionRuntimeConflict):
        release_collection_claim(runtime_session_factory, command_id, claim_token=wrong_token)

    with runtime_session_factory.begin() as session:
        session.execute(
            update(CollectionRuntimeAttempt)
            .where(CollectionRuntimeAttempt.command_id == command_id)
            .values(claim_expires_at=func.clock_timestamp() - text("interval '1 second'"))
        )
    with pytest.raises(CollectionRuntimeConflict):
        renew_collection_claim(
            runtime_session_factory,
            command_id,
            claim_token=token,
            lease_seconds=30,
        )
    with pytest.raises(CollectionRuntimeConflict):
        release_collection_claim(runtime_session_factory, command_id, claim_token=token)

    with runtime_session_factory.begin() as session:
        attempt = session.get(CollectionRuntimeAttempt, command_id)
        assert attempt is not None
        attempt.claim_token = None
        attempt.claim_expires_at = None
        attempt.state = "PERSISTED"
    with pytest.raises(CollectionRuntimeConflict):
        claim_collection_attempt(
            runtime_session_factory,
            command_id,
            claim_token=uuid4(),
            lease_seconds=30,
        )
    with pytest.raises(CollectionRuntimeConflict):
        renew_collection_claim(
            runtime_session_factory,
            command_id,
            claim_token=uuid4(),
            lease_seconds=30,
        )
    with pytest.raises(CollectionRuntimeConflict):
        release_collection_claim(
            runtime_session_factory,
            command_id,
            claim_token=uuid4(),
        )


@pytest.mark.approved_postgres
def test_collection_input_provider_loads_latest_policy_and_next_outbox_revision(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    latest_policy = _policy_decision(source_id, 4)
    with runtime_session_factory.begin() as session:
        source = session.get(Source, source_id)
        assert source is not None
        source.source_type = SourceType.COMPANY_WEBSITE.value
        source.canonical_url = "https://runtime.test/careers"
        source.title = "Runtime careers"
        session.add(_policy_decision(source_id, 1, official_status="unverified"))
        session.add(latest_policy)
        for revision in (2, 8):
            session.add(
                OutboxEvent(
                    event_id=uuid4(),
                    aggregate_id=source_id,
                    aggregate_revision=revision,
                    event_type="source.observed",
                    schema_version="w2.source-event.v1",
                    payload={"revision": revision},
                    occurred_at=NOW,
                    delivery_state="pending",
                )
            )

    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    provider = SqlAlchemyCollectionInputProvider(
        runtime_session_factory,
        _source_runtime_config({source_id: 4}),
    )

    loaded = provider.load(dispatch.payload)

    assert loaded.source_id == source_id
    assert loaded.company_id == company_id
    assert loaded.source_url == "https://runtime.test/careers"
    assert loaded.title == "Runtime careers"
    assert loaded.source_type is SourceType.COMPANY_WEBSITE
    assert loaded.policy.policy_decision_id == latest_policy.policy_decision_id
    assert loaded.policy.official_status is OfficialStatus.VERIFIED
    assert loaded.policy.access_class is AccessClass.PUBLIC
    assert loaded.policy.collection_permission is Permission.ALLOWED
    assert loaded.policy.excerpt_storage_permission is Permission.ALLOWED
    assert loaded.policy.body_storage_permission is Permission.DENIED
    assert loaded.policy.redistribution_permission is Permission.UNKNOWN
    assert loaded.policy_revision == 4
    assert loaded.robots_permission is Permission.ALLOWED
    assert loaded.limits.general_retry_limit == 0
    assert loaded.result_version == 1
    assert loaded.aggregate_revision == 9
    assert loaded.language == "ko"
    assert loaded.redirect_robots_permissions == ()


@pytest.mark.approved_postgres
def test_collection_input_provider_fails_closed_for_stale_or_unregistered_rows(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    no_policy_company_id, no_policy_source_id = _seed_source(
        runtime_session_factory, policy_revision=None
    )
    unverified_company_id, unverified_source_id = _seed_source(
        runtime_session_factory, policy_revision=None
    )
    missing_source_id = uuid4()
    with runtime_session_factory.begin() as session:
        for registered_source_id in (source_id, no_policy_source_id, unverified_source_id):
            source = session.get(Source, registered_source_id)
            assert source is not None
            source.source_type = SourceType.COMPANY_WEBSITE.value
        session.add(_policy_decision(source_id, 4))
        session.add(_policy_decision(unverified_source_id, 3))
        company = session.get(Company, unverified_company_id)
        assert company is not None
        company.identity_status = "unverified"

    config = _source_runtime_config(
        {
            source_id: 3,
            no_policy_source_id: 3,
            unverified_source_id: 3,
            missing_source_id: 3,
        }
    )
    provider = SqlAlchemyCollectionInputProvider(runtime_session_factory, config)

    stale_command = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    ).payload
    with pytest.raises(SourceRuntimeInputError, match="policy revision"):
        provider.load(stale_command)

    no_policy_command = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=no_policy_company_id,
        source_id=no_policy_source_id,
    ).payload
    with pytest.raises(SourceRuntimeInputError, match="policy"):
        provider.load(no_policy_command)

    unverified_command = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=unverified_company_id,
        source_id=unverified_source_id,
    ).payload
    with pytest.raises(SourceRuntimeInputError, match="verified"):
        provider.load(unverified_command)

    missing_source_command = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=missing_source_id,
    ).payload
    with pytest.raises(SourceRuntimeInputError, match="registered Source"):
        provider.load(missing_source_command)

    wrong_company_command = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=uuid4(),
        source_id=source_id,
    ).payload
    with pytest.raises(SourceRuntimeInputError, match="Source/Company"):
        provider.load(wrong_company_command)


@pytest.mark.approved_postgres
def test_candidate_commit_persists_atomic_stage_without_pointer_mutation_and_clears_claim(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    company_id, source_id = _seed_source(runtime_session_factory)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    effective_command = dispatch.payload.model_copy(update={"policy_revision": 3})
    with runtime_session_factory.begin() as session:
        source = session.get(Source, source_id)
        assert source is not None
        source.source_type = SourceType.JOB_POSTING.value
        policy = session.scalar(
            select(SourcePolicyDecision).where(
                SourcePolicyDecision.source_id == source_id,
                SourcePolicyDecision.revision == 3,
            )
        )
        assert policy is not None

    reserved = _runtime_attempt(
        runtime_session_factory,
        dispatch,
        effective_policy_revision=3,
    )
    claim_token = uuid4()
    claim_collection_attempt(
        runtime_session_factory,
        dispatch.payload.command_id,
        claim_token=claim_token,
        lease_seconds=30,
    )
    with runtime_session_factory() as session:
        source = session.get(Source, source_id)
        policy = session.scalar(
            select(SourcePolicyDecision).where(
                SourcePolicyDecision.source_id == source_id,
                SourcePolicyDecision.revision == 3,
            )
        )
        assert source is not None and policy is not None
        pointer_before = (
            source.latest_observation_id,
            source.current_source_version_id,
            source.first_collected_at,
            source.last_collected_at,
            source.last_promoted_observation_order,
            source.next_observation_order,
        )
        prepared = _candidate_complete_prepared(
            effective_command,
            source,
            policy,
            attempt_id=reserved.attempt_id,
            aggregate_revision=987,
        )

    staged_message_id = uuid4()
    proposal = commit_collection_candidate(
        runtime_session_factory,
        dispatch,
        effective_command,
        prepared,
        claim_token=claim_token,
        staged_message_id=staged_message_id,
        occurred_at=NOW,
    )

    assert proposal.message_id == staged_message_id
    assert proposal.command.model_dump(mode="json") == dispatch.payload.model_dump(mode="json")
    assert proposal.command.policy_revision is None
    assert proposal.result.policy_revision == 3

    with runtime_session_factory() as session:
        source = session.get(Source, source_id)
        attempt = session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
        stage = session.get(PrivateCommitStage, dispatch.payload.command_id)
        staged_outbox = session.get(PrivateStagedOutbox, staged_message_id)
        event = session.scalar(
            select(OutboxEvent).where(OutboxEvent.event_id == prepared.events[0].event_id)
        )
        assert source is not None and attempt is not None
        assert (
            source.latest_observation_id,
            source.current_source_version_id,
            source.first_collected_at,
            source.last_collected_at,
            source.last_promoted_observation_order,
            source.next_observation_order,
        ) == pointer_before
        assert attempt.state == "PERSISTED"
        assert attempt.claim_token is None
        assert attempt.claim_expires_at is None
        assert attempt.source_version_id == prepared.source_version.source_version_id
        assert attempt.observation_id == prepared.observation.snapshot.observation_id
        assert session.scalar(select(func.count()).select_from(SourceVersion)) == 1
        assert session.scalar(select(func.count()).select_from(Evidence)) == 1
        assert session.scalar(select(func.count()).select_from(ExtractionRevision)) == 1
        assert session.scalar(select(func.count()).select_from(ParserExecution)) == 1
        assert session.scalar(select(func.count()).select_from(SourceObservation)) == 1
        assert stage is not None
        assert stage.stage_kind == "COLLECTION"
        assert staged_outbox is not None
        assert staged_outbox.command_id == dispatch.payload.command_id
        assert staged_outbox.delivered_at is None
        assert event is not None
        assert event.aggregate_revision == 1


@pytest.mark.approved_postgres
def test_candidate_commit_rejects_historical_unknown_runtime_scope_without_writes(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    dispatch, effective_command, prepared, claim_token = _claimed_candidate_inputs(
        runtime_session_factory
    )
    with runtime_session_factory.begin() as session:
        attempt = session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
        assert attempt is not None
        attempt.private_scope_kind = "UNKNOWN"
        attempt.project_id = None

    with pytest.raises(PrivateScopeRejected, match="runtime private scope"):
        commit_collection_candidate(
            runtime_session_factory,
            dispatch,
            effective_command,
            prepared,
            claim_token=claim_token,
            staged_message_id=uuid4(),
            occurred_at=NOW,
        )

    with runtime_session_factory() as session:
        attempt = session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
        assert attempt is not None
        assert attempt.state == "RESERVED"
        assert attempt.private_scope_kind == "UNKNOWN"
        assert session.get(PrivateCommitStage, dispatch.payload.command_id) is None


@pytest.mark.approved_postgres
def test_terminal_gate_before_collection_replay_prevents_revival_or_public_writes(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    dispatch, effective_command, prepared, claim_token = _claimed_candidate_inputs(
        runtime_session_factory
    )
    proposal = commit_collection_candidate(
        runtime_session_factory,
        dispatch,
        effective_command,
        prepared,
        claim_token=claim_token,
        staged_message_id=uuid4(),
        occurred_at=NOW,
    )
    with runtime_session_factory.begin() as session:
        apply_commit_gate(
            session,
            _gate(dispatch, "ABORT", result_digest=str(proposal.result_digest)),
            ack_message_id=uuid4(),
            occurred_at=NOW,
        )
    with runtime_session_factory() as session:
        stage_before = session.get(PrivateCommitStage, dispatch.payload.command_id)
        public_rows_before = session.scalar(select(func.count()).select_from(OutboxEvent))
        assert stage_before is not None
        assert stage_before.state == "ABORTED"
        assert stage_before.result_payload is None

    with pytest.raises(CollectionRuntimeConflict):
        replay_staged_collection(runtime_session_factory, dispatch)

    with runtime_session_factory() as session:
        stage_after = session.get(PrivateCommitStage, dispatch.payload.command_id)
        assert stage_after is not None
        assert stage_after.state == "ABORTED"
        assert stage_after.result_payload is None
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == public_rows_before


@pytest.mark.approved_postgres
@pytest.mark.parametrize("claim_case", ["wrong", "expired", "stale"])
def test_candidate_commit_rejects_non_active_claim_without_public_or_private_writes(
    runtime_session_factory: sessionmaker[Session],
    claim_case: str,
) -> None:
    dispatch, effective_command, prepared, original_claim_token = _claimed_candidate_inputs(
        runtime_session_factory
    )
    supplied_claim_token = original_claim_token
    expected_claim_token = original_claim_token
    if claim_case in {"expired", "stale"}:
        with runtime_session_factory.begin() as session:
            session.execute(
                update(CollectionRuntimeAttempt)
                .where(CollectionRuntimeAttempt.command_id == dispatch.payload.command_id)
                .values(claim_expires_at=func.clock_timestamp() - text("interval '1 second'"))
            )
    if claim_case == "wrong":
        supplied_claim_token = uuid4()
    elif claim_case == "stale":
        expected_claim_token = uuid4()
        claim_collection_attempt(
            runtime_session_factory,
            dispatch.payload.command_id,
            claim_token=expected_claim_token,
            lease_seconds=30,
        )

    with runtime_session_factory() as session:
        source = session.get(Source, dispatch.payload.source_id)
        assert source is not None
        pointer_before = (
            source.latest_observation_id,
            source.current_source_version_id,
            source.last_promoted_observation_order,
            source.next_observation_order,
        )

    with pytest.raises(CollectionRuntimeConflict, match="claim token"):
        commit_collection_candidate(
            runtime_session_factory,
            dispatch,
            effective_command,
            prepared,
            claim_token=supplied_claim_token,
            staged_message_id=uuid4(),
            occurred_at=NOW,
        )

    with runtime_session_factory() as session:
        source = session.get(Source, dispatch.payload.source_id)
        attempt = session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
        assert source is not None and attempt is not None
        assert (
            source.latest_observation_id,
            source.current_source_version_id,
            source.last_promoted_observation_order,
            source.next_observation_order,
        ) == pointer_before
        assert attempt.state == "RESERVED"
        assert attempt.claim_token == expected_claim_token
        assert session.scalar(select(func.count()).select_from(SourceVersion)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
        assert session.get(PrivateCommitStage, dispatch.payload.command_id) is None


@pytest.mark.approved_postgres
@pytest.mark.parametrize("finalized", [False, True])
def test_collection_replay_returns_exact_staged_identity_without_public_revision_consumption(
    runtime_session_factory: sessionmaker[Session],
    finalized: bool,
) -> None:
    dispatch, effective_command, prepared, claim_token = _claimed_candidate_inputs(
        runtime_session_factory
    )
    proposal = commit_collection_candidate(
        runtime_session_factory,
        dispatch,
        effective_command,
        prepared,
        claim_token=claim_token,
        staged_message_id=uuid4(),
        occurred_at=NOW,
    )
    if finalized:
        operation_id = uuid4()
        with runtime_session_factory.begin() as session:
            apply_commit_gate(
                session,
                _gate(
                    dispatch,
                    "PREPARE",
                    result_digest=str(proposal.result_digest),
                    operation_id=operation_id,
                    operation_revision=1,
                ),
                ack_message_id=uuid4(),
                occurred_at=NOW,
            )
            apply_commit_gate(
                session,
                _gate(
                    dispatch,
                    "FINALIZE",
                    result_digest=str(proposal.result_digest),
                    operation_id=operation_id,
                    operation_revision=2,
                ),
                ack_message_id=uuid4(),
                occurred_at=NOW,
            )
            attempt = session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
            assert attempt is not None
            attempt.state = "FINALIZED"

    with runtime_session_factory.begin() as session:
        session.execute(
            update(PrivateStagedOutbox)
            .where(PrivateStagedOutbox.message_id == proposal.message_id)
            .values(delivered_at=NOW)
        )
    with runtime_session_factory() as session:
        events_before = tuple(
            session.execute(
                select(OutboxEvent.event_id, OutboxEvent.aggregate_revision).order_by(
                    OutboxEvent.event_id
                )
            ).all()
        )
        source = session.get(Source, dispatch.payload.source_id)
        assert source is not None
        next_order_before = source.next_observation_order

    replayed = replay_staged_collection(runtime_session_factory, dispatch)

    assert replayed.model_dump(mode="json") == proposal.model_dump(mode="json")
    assert replayed.message_id == proposal.message_id
    with runtime_session_factory() as session:
        delivery = session.get(PrivateStagedOutbox, proposal.message_id)
        source = session.get(Source, dispatch.payload.source_id)
        events_after = tuple(
            session.execute(
                select(OutboxEvent.event_id, OutboxEvent.aggregate_revision).order_by(
                    OutboxEvent.event_id
                )
            ).all()
        )
        assert delivery is not None
        assert delivery.delivered_at is None
        assert source is not None
        assert source.next_observation_order == next_order_before
        assert events_after == events_before


@pytest.mark.approved_postgres
def test_collection_replay_rejects_changed_persisted_ack_wire(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    dispatch, effective_command, prepared, claim_token = _claimed_candidate_inputs(
        runtime_session_factory
    )
    proposal = commit_collection_candidate(
        runtime_session_factory,
        dispatch,
        effective_command,
        prepared,
        claim_token=claim_token,
        staged_message_id=uuid4(),
        occurred_at=NOW,
    )
    with runtime_session_factory.begin() as session:
        apply_commit_gate(
            session,
            _gate(
                dispatch,
                "PREPARE",
                result_digest=str(proposal.result_digest),
                operation_id=uuid4(),
                operation_revision=1,
            ),
            ack_message_id=uuid4(),
            occurred_at=NOW,
        )
        ack = session.scalar(select(PrivateCommitGateAck))
        assert ack is not None
        ack.payload = {**ack.payload, "outcome": "DUPLICATE"}

    with pytest.raises(CommitGateRejected, match="invalid persisted private commit-gate ACK"):
        replay_staged_collection(runtime_session_factory, dispatch)


@pytest.mark.approved_postgres
@pytest.mark.parametrize("mismatched_row", ["runtime", "stage"])
def test_collection_replay_rejects_other_project_persisted_scope(
    runtime_session_factory: sessionmaker[Session],
    mismatched_row: str,
) -> None:
    dispatch, effective_command, prepared, claim_token = _claimed_candidate_inputs(
        runtime_session_factory
    )
    commit_collection_candidate(
        runtime_session_factory,
        dispatch,
        effective_command,
        prepared,
        claim_token=claim_token,
        staged_message_id=uuid4(),
        occurred_at=NOW,
    )
    other_project_id = UUID("00000000-0000-4000-8000-000000000299")
    with runtime_session_factory.begin() as session:
        row = (
            session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
            if mismatched_row == "runtime"
            else session.get(PrivateCommitStage, dispatch.payload.command_id)
        )
        assert row is not None
        row.private_scope_kind = "PROJECT"
        row.project_id = other_project_id

    with pytest.raises(PrivateScopeRejected):
        replay_staged_collection(runtime_session_factory, dispatch)

    with runtime_session_factory() as session:
        row = (
            session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
            if mismatched_row == "runtime"
            else session.get(PrivateCommitStage, dispatch.payload.command_id)
        )
        assert row is not None
        assert row.private_scope_kind == "PROJECT"
        assert row.project_id == other_project_id


@pytest.mark.approved_postgres
@pytest.mark.parametrize("fault_point", ["public", "proposal", "private_flush"])
def test_candidate_commit_rolls_back_public_private_and_claim_on_injected_fault(
    runtime_session_factory: sessionmaker[Session],
    monkeypatch: pytest.MonkeyPatch,
    fault_point: str,
) -> None:
    dispatch, effective_command, prepared, claim_token = _claimed_candidate_inputs(
        runtime_session_factory
    )
    if fault_point == "public":
        original_public = persistence_module.persist_canonical_public_commit

        def fail_after_public_flush(session: Session, *args: Any, **kwargs: Any) -> Any:
            original_public(session, *args, **kwargs)
            session.flush()
            raise RuntimeError("injected public persistence fault")

        monkeypatch.setattr(
            persistence_module,
            "persist_canonical_public_commit",
            fail_after_public_flush,
        )
    elif fault_point == "proposal":
        original_build = commit_gate_store_module.build_staged_result

        def fail_after_proposal_build(*args: Any, **kwargs: Any) -> Any:
            original_build(*args, **kwargs)
            raise RuntimeError("injected staged proposal fault")

        monkeypatch.setattr(
            commit_gate_store_module,
            "build_staged_result",
            fail_after_proposal_build,
        )
    else:
        original_flush = commit_gate_store_module._flush_private_storage

        def fail_after_private_flush(session: Session) -> None:
            original_flush(session)
            raise RuntimeError("injected private stage flush fault")

        monkeypatch.setattr(
            commit_gate_store_module,
            "_flush_private_storage",
            fail_after_private_flush,
        )

    with pytest.raises(RuntimeError, match="injected"):
        commit_collection_candidate(
            runtime_session_factory,
            dispatch,
            effective_command,
            prepared,
            claim_token=claim_token,
            staged_message_id=uuid4(),
            occurred_at=NOW,
        )

    with runtime_session_factory() as session:
        attempt = session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
        assert attempt is not None
        assert attempt.state == "RESERVED"
        assert attempt.claim_token == claim_token
        assert attempt.claim_expires_at is not None
        assert session.scalar(select(func.count()).select_from(SourceVersion)) == 0
        assert session.scalar(select(func.count()).select_from(Evidence)) == 0
        assert session.scalar(select(func.count()).select_from(ExtractionRevision)) == 0
        assert session.scalar(select(func.count()).select_from(SourceObservation)) == 0
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
        assert session.get(PrivateCommitStage, dispatch.payload.command_id) is None


@pytest.mark.approved_postgres
@pytest.mark.parametrize(
    ("corruption", "error_type"),
    [
        ("missing_delivery", CommitGateRejected),
        ("corrupt_delivery", CommitGateRejected),
        ("invalidated_attempt", CollectionRuntimeConflict),
    ],
)
def test_collection_replay_rejects_missing_corrupt_or_invalidated_durable_state(
    runtime_session_factory: sessionmaker[Session],
    corruption: str,
    error_type: type[Exception],
) -> None:
    dispatch, effective_command, prepared, claim_token = _claimed_candidate_inputs(
        runtime_session_factory
    )
    proposal = commit_collection_candidate(
        runtime_session_factory,
        dispatch,
        effective_command,
        prepared,
        claim_token=claim_token,
        staged_message_id=uuid4(),
        occurred_at=NOW,
    )
    with runtime_session_factory.begin() as session:
        if corruption == "missing_delivery":
            delivery = session.get(PrivateStagedOutbox, proposal.message_id)
            assert delivery is not None
            session.delete(delivery)
        elif corruption == "corrupt_delivery":
            session.execute(
                update(PrivateStagedOutbox)
                .where(PrivateStagedOutbox.message_id == proposal.message_id)
                .values(payload={"invalid": "proposal"})
            )
        else:
            attempt = session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
            assert attempt is not None
            attempt.state = "INVALIDATED"

    with pytest.raises(error_type):
        replay_staged_collection(runtime_session_factory, dispatch)


@pytest.mark.approved_postgres
def test_replay_then_terminal_transition_blocks_every_later_collection_replay(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    dispatch, effective_command, prepared, claim_token = _claimed_candidate_inputs(
        runtime_session_factory
    )
    proposal = commit_collection_candidate(
        runtime_session_factory,
        dispatch,
        effective_command,
        prepared,
        claim_token=claim_token,
        staged_message_id=uuid4(),
        occurred_at=NOW,
    )
    assert (
        replay_staged_collection(runtime_session_factory, dispatch).message_id
        == proposal.message_id
    )
    with runtime_session_factory.begin() as session:
        apply_commit_gate(
            session,
            _gate(dispatch, "PURGE", result_digest=str(proposal.result_digest)),
            ack_message_id=uuid4(),
            occurred_at=NOW,
        )
    with pytest.raises(CollectionRuntimeConflict):
        replay_staged_collection(runtime_session_factory, dispatch)


@pytest.mark.approved_postgres
def test_candidate_commit_rejects_legacy_pointer_mode_before_public_or_private_write(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    dispatch, effective_command, prepared, claim_token = _claimed_candidate_inputs(
        runtime_session_factory
    )
    with runtime_session_factory.begin() as session:
        source = session.get(Source, dispatch.payload.source_id)
        assert source is not None
        source.pointer_update_mode = "LEGACY_SAME_DB"

    with pytest.raises(CollectionRuntimeConflict, match="FINALIZE_GATE"):
        commit_collection_candidate(
            runtime_session_factory,
            dispatch,
            effective_command,
            prepared,
            claim_token=claim_token,
            staged_message_id=uuid4(),
            occurred_at=NOW,
        )

    with runtime_session_factory() as session:
        attempt = session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
        assert attempt is not None
        assert attempt.state == "RESERVED"
        assert attempt.claim_token == claim_token
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 0
        assert session.get(PrivateCommitStage, dispatch.payload.command_id) is None


@pytest.mark.approved_postgres
def test_terminal_receipt_contradicting_restored_active_stage_fails_closed_on_replay(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    dispatch, effective_command, prepared, claim_token = _claimed_candidate_inputs(
        runtime_session_factory
    )
    proposal = commit_collection_candidate(
        runtime_session_factory,
        dispatch,
        effective_command,
        prepared,
        claim_token=claim_token,
        staged_message_id=uuid4(),
        occurred_at=NOW,
    )
    with runtime_session_factory() as session:
        stage = session.get(PrivateCommitStage, dispatch.payload.command_id)
        delivery = session.get(PrivateStagedOutbox, proposal.message_id)
        assert stage is not None and delivery is not None
        durable_result = deepcopy(stage.result_payload)
        durable_delivery = deepcopy(delivery.payload)

    with runtime_session_factory.begin() as session:
        apply_commit_gate(
            session,
            _gate(dispatch, "ABORT", result_digest=str(proposal.result_digest)),
            ack_message_id=uuid4(),
            occurred_at=NOW,
        )
        stage = session.get(PrivateCommitStage, dispatch.payload.command_id)
        delivery = session.get(PrivateStagedOutbox, proposal.message_id)
        assert stage is not None and delivery is not None
        stage.state = "STAGED"
        stage.result_payload = durable_result
        delivery.payload = durable_delivery
        delivery.delivered_at = NOW

    with pytest.raises(CollectionRuntimeConflict, match="terminal"):
        replay_staged_collection(runtime_session_factory, dispatch)


@pytest.mark.approved_postgres
def test_candidate_public_event_uses_locked_db_max_revision_not_hint_or_observation_order(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    dispatch, effective_command, prepared, claim_token = _claimed_candidate_inputs(
        runtime_session_factory
    )
    with runtime_session_factory.begin() as session:
        session.add(
            OutboxEvent(
                event_id=uuid4(),
                aggregate_id=dispatch.payload.source_id,
                aggregate_revision=41,
                event_type="source.observed",
                schema_version="w2.source-event.v1",
                payload={"existing": True},
                occurred_at=NOW,
                delivery_state="pending",
            )
        )
    commit_collection_candidate(
        runtime_session_factory,
        dispatch,
        effective_command,
        prepared,
        claim_token=claim_token,
        staged_message_id=uuid4(),
        occurred_at=NOW,
    )

    with runtime_session_factory() as session:
        attempt = session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
        event = session.get(OutboxEvent, prepared.events[0].event_id)
        assert attempt is not None and event is not None
        assert attempt.observation_order == 1
        assert prepared.events[0].aggregate_revision == 987
        assert event.aggregate_revision == 42


@pytest.mark.approved_postgres
def test_full_runtime_handler_persists_candidate_after_fresh_stages_without_delivery(
    runtime_session_factory: sessionmaker[Session],
) -> None:
    """The runtime's terminal PERSIST action must be durable but must not relay the stage."""

    company_id, source_id = _seed_source(runtime_session_factory, policy_revision=3)
    dispatch = _dispatch(
        command_id=uuid4(),
        job_id=uuid4(),
        owner_ref=uuid4(),
        company_id=company_id,
        source_id=source_id,
    )
    source_url = f"https://runtime.test/{source_id}"
    with runtime_session_factory.begin() as session:
        source = session.get(Source, source_id)
        assert source is not None
        source.source_type = SourceType.COMPANY_WEBSITE.value
        source.canonical_url = source_url
        source.title = "Runtime careers"
    document = "<html><body><main><h2>Requirements</h2><p>Python</p></main></body></html>"

    class _AvailableLookup:
        def __init__(self) -> None:
            self.dispatches: list[W1Dispatch] = []

        def lookup_dispatch(self, received: W1Dispatch) -> LookupResponse:
            self.dispatches.append(received)
            return LookupResponse(
                schema_version="w1.private.command-lookup.v1",
                command_id=received.payload.command_id,
                status="AVAILABLE",
                reason_code=None,
                command=received.payload,
            )

    class _Collector:
        def __init__(self) -> None:
            self.fetches = 0
            self.closed = 0

        def fetch(self, request: object, *, is_cancelled: Callable[[], bool]) -> StaticFetchResult:
            self.fetches += 1
            assert is_cancelled() is False
            return StaticFetchResult(
                command_id=dispatch.payload.command_id,
                candidate=StaticResponseCandidate(
                    final_target=ValidatedTarget(
                        url=source_url,
                        hostname="runtime.test",
                        port=443,
                        resolved_addresses=frozenset({"198.51.100.10"}),
                    ),
                    representation=FetchRepresentation.HTML,
                    document=UntrustedDocument(text=document),
                    http_status=200,
                    raw_size=len(document.encode("utf-8")),
                    decompressed_size=len(document.encode("utf-8")),
                ),
                failure_code=None,
            )

        def close(self) -> None:
            self.closed += 1

    lookup = _AvailableLookup()
    collector = _Collector()
    proposal = handle_collection_dispatch(
        dispatch,
        session_factory=runtime_session_factory,
        lookup_client=lookup,
        input_provider=SqlAlchemyCollectionInputProvider(
            runtime_session_factory,
            _source_runtime_config({source_id: 3}),
        ),
        collector_factory=lambda: collector,
        parser=extract_static_candidate,
        runtime_config=_source_runtime_config({source_id: 3}),
        clock=lambda: NOW,
        uuid_factory=uuid4,
        private_authority_client=_PrivateAuthorityClient(
            PrivateDeletionScope(kind="ACCOUNT", project_id=None)
        ),
    )

    assert lookup.dispatches == [dispatch] * 5
    assert collector.fetches == 1
    assert collector.closed == 1
    assert proposal.command.model_dump(mode="json") == dispatch.payload.model_dump(mode="json")
    assert proposal.command.policy_revision is None

    with runtime_session_factory() as session:
        attempt = session.get(CollectionRuntimeAttempt, dispatch.payload.command_id)
        stage = session.get(PrivateCommitStage, dispatch.payload.command_id)
        delivery = session.get(PrivateStagedOutbox, proposal.message_id)
        source = session.get(Source, source_id)
        assert (
            attempt is not None
            and stage is not None
            and delivery is not None
            and source is not None
        )
        assert attempt.state == "PERSISTED"
        assert attempt.claim_token is None
        assert attempt.claim_expires_at is None
        assert stage.stage_kind == "COLLECTION"
        assert delivery.delivered_at is None
        assert source.current_source_version_id is None
        assert source.latest_observation_id is None
        assert session.scalar(select(func.count()).select_from(SourceVersion)) == 1
        assert session.scalar(select(func.count()).select_from(SourceObservation)) == 1
        assert session.scalar(select(func.count()).select_from(OutboxEvent)) == 1
