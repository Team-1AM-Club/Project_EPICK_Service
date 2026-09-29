"""PostgreSQL coverage for private-scope attribution and write fences."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

import pytest
import sqlalchemy as sa
from alembic import command as alembic_command
from alembic.config import Config
from alembic.migration import MigrationContext
from alembic.script import ScriptDirectory
from sqlalchemy import Engine, create_engine, inspect, text
from sqlalchemy.engine import URL
from sqlalchemy.orm import Session, sessionmaker

from epick_engine.source_collection.persistence import (
    Base,
    PrivateDeletionOwnerState,
    PrivateDeletionProjectTombstone,
)
from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionScope
from epick_engine.source_collection.private_scope import (
    PrivateScopeRejected,
    PrivateWriteAuthorityDecision,
    PrivateWriteScope,
    ScopeUnclassified,
    lock_private_write_scope,
)

PROJECT_ROOT = Path(__file__).resolve().parents[3]
OWNER_ID = UUID("00000000-0000-4000-8000-000000007101")
PROJECT_ID = UUID("00000000-0000-4000-8000-000000007102")
OTHER_PROJECT_ID = UUID("00000000-0000-4000-8000-000000007103")
COMMAND_ID = UUID("00000000-0000-4000-8000-000000007104")
JOB_ID = UUID("00000000-0000-4000-8000-000000007105")
SIGNED_64_MAX = 9_223_372_036_854_775_807


@contextmanager
def _migration_schema(
    approved_postgres_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> Iterator[tuple[Engine, Config, str]]:
    schema_name = f"epick_private_scope_migration_{uuid4().hex}"
    schema_url = approved_postgres_url.update_query_dict(
        {"options": f"-csearch_path={schema_name}"}
    )
    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema_engine = create_engine(schema_url, pool_pre_ping=True)
    config = Config(str(PROJECT_ROOT / "alembic.ini"))
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    monkeypatch.setenv(
        "EPICK_DATABASE_URL",
        schema_url.render_as_string(hide_password=False),
    )
    try:
        yield schema_engine, config, schema_name
    finally:
        schema_engine.dispose()
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA IF EXISTS "{schema_name}" CASCADE'))
        admin_engine.dispose()


def _seed_0009_private_rows(engine: Engine) -> UUID:
    deletion_id = uuid4()
    values = {
        "owner_id": OWNER_ID,
        "project_id": PROJECT_ID,
        "command_id": COMMAND_ID,
        "job_id": JOB_ID,
        "deletion_id": deletion_id,
        "company_id": uuid4(),
        "source_id": uuid4(),
        "attempt_id": uuid4(),
        "dedup_id": uuid4(),
    }
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO companies (company_id, legal_name, aliases, official_domains, "
                "legal_identifiers, identity_status, identity_evidence) VALUES "
                "(:company_id, 'Synthetic', '{}', '{}', '{}'::jsonb, 'VERIFIED', '{}')"
            ),
            values,
        )
        connection.execute(
            text(
                "INSERT INTO sources (source_id, company_id, source_type, canonical_url) "
                "VALUES (:source_id, :company_id, 'CAREERS', 'https://scope.test/source')"
            ),
            values,
        )
        connection.execute(
            text(
                "INSERT INTO collection_attempts (attempt_id, owner_user_id, job_id, "
                "project_id, command_id, input_version, target_ref, purpose_ref, "
                "core_source_decision, resume_stage, policy_revision, result_version, "
                "execution_fence, owner_deletion_epoch, result_refs, failures, "
                "required_actions) VALUES (:attempt_id, :owner_id, :job_id, :project_id, "
                ":command_id, 1, 'target', 'purpose', '{}'::jsonb, 'policy', NULL, 1, "
                "'fence', 0, '[]'::jsonb, '[]'::jsonb, '[]'::jsonb)"
            ),
            values,
        )
        connection.execute(
            text(
                "INSERT INTO request_deduplications (request_deduplication_id, "
                "owner_user_id, operation, idempotency_key, request_hash, "
                "accepted_resource_ref, input_version, created_at) VALUES "
                "(:dedup_id, :owner_id, 'collect', 'key', 'hash', 'resource', 1, now())"
            ),
            values,
        )
        connection.execute(
            text(
                "INSERT INTO collection_runtime_attempts (command_id, attempt_id, "
                "dispatch_digest, owner_ref, job_id, source_id, company_id, "
                "observation_order, effective_policy_revision, state, created_at, updated_at) "
                "VALUES (:command_id, :attempt_id, :digest, :owner_id, :job_id, :source_id, "
                ":company_id, 1, 1, 'RESERVED', now(), now())"
            ),
            {**values, "digest": "a" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO private_commit_stages (command_id, owner_ref, job_id, "
                "execution_fence, owner_deletion_epoch, result_digest, operation_revision, "
                "max_purge_epoch, state, result_payload) VALUES (:command_id, :owner_id, "
                ":job_id, '0', '0', :digest, '1', '0', 'STAGED', '{}'::jsonb)"
            ),
            {**values, "digest": "sha256:" + "b" * 64},
        )
        connection.execute(
            text(
                "INSERT INTO private_deletion_owner_states (owner_user_id, latest_epoch) "
                "VALUES (:owner_id, 1)"
            ),
            values,
        )
        connection.execute(
            text(
                "INSERT INTO private_deletion_receipts (deletion_id, owner_user_id, "
                "deletion_epoch, command_digest, outcome, created_at) VALUES "
                "(:deletion_id, :owner_id, 1, :digest, 'APPLIED', now())"
            ),
            {**values, "digest": "c" * 64},
        )
    return deletion_id


@pytest.mark.approved_postgres
def test_0010_migrates_private_inventory_and_backfills_legacy_rows(
    approved_postgres_url: URL,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    with _migration_schema(approved_postgres_url, monkeypatch) as (
        engine,
        config,
        schema_name,
    ):
        alembic_command.upgrade(config, "0009_private_deletion_receipt")
        deletion_id = _seed_0009_private_rows(engine)

        alembic_command.upgrade(config, "0010_private_deletion_scope_v2")

        with engine.begin() as connection:
            inspector = inspect(connection)
            owner_columns = {
                "collection_attempts": "owner_user_id",
                "request_deduplications": "owner_user_id",
                "collection_runtime_attempts": "owner_ref",
                "private_commit_stages": "owner_ref",
            }
            for table_name, owner_column in owner_columns.items():
                columns = {
                    column["name"]: column
                    for column in inspector.get_columns(table_name, schema=schema_name)
                }
                assert columns["private_scope_kind"]["nullable"] is False
                assert columns["project_id"]["nullable"] is True
                assert columns["private_scope_kind"]["default"] is not None
                constraints = {
                    constraint["name"]: constraint["sqltext"]
                    for constraint in inspector.get_check_constraints(
                        table_name,
                        schema=schema_name,
                    )
                }
                assert f"ck_{table_name}_valid_private_scope" in constraints
                indexes = {
                    index["name"]: index
                    for index in inspector.get_indexes(table_name, schema=schema_name)
                }
                assert indexes[f"ix_{table_name}_owner_private_scope"]["column_names"] == [
                    owner_column,
                    "private_scope_kind",
                    "project_id",
                ]

            attempt_columns = {
                column["name"]: column
                for column in inspector.get_columns(
                    "collection_attempts",
                    schema=schema_name,
                )
            }
            assert isinstance(attempt_columns["owner_deletion_epoch"]["type"], sa.BigInteger)

            owner_state_columns = {
                column["name"]: column
                for column in inspector.get_columns(
                    "private_deletion_owner_states",
                    schema=schema_name,
                )
            }
            assert owner_state_columns["account_deleted"]["nullable"] is False
            tombstone_pk = inspector.get_pk_constraint(
                "private_deletion_project_tombstones",
                schema=schema_name,
            )
            assert tombstone_pk["constrained_columns"] == ["owner_user_id", "project_id"]

            legacy_kinds = {
                table_name: connection.scalar(
                    text(f"SELECT private_scope_kind FROM {table_name} LIMIT 1")
                )
                for table_name in owner_columns
            }
            assert legacy_kinds == dict.fromkeys(owner_columns, "UNKNOWN")
            assert (
                connection.scalar(
                    text(
                        "SELECT contract_version FROM private_deletion_receipts "
                        "WHERE deletion_id = :deletion_id"
                    ),
                    {"deletion_id": deletion_id},
                )
                == "w2.private-deletion.v1"
            )
            assert (
                MigrationContext.configure(connection).get_current_revision()
                == "0010_private_deletion_scope_v2"
            )
            assert ScriptDirectory.from_config(config).get_heads() == [
                "0013_deletion_ack_confirmed"
            ]


@pytest.fixture
def database_engine(approved_postgres_url: URL) -> Iterator[Engine]:
    admin_engine = create_engine(approved_postgres_url, pool_pre_ping=True)
    schema_name = f"epick_private_scope_{uuid4().hex}"
    with admin_engine.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema_name}"'))
    engine = admin_engine.execution_options(schema_translate_map={None: schema_name})
    Base.metadata.create_all(engine)
    try:
        yield engine
    finally:
        Base.metadata.drop_all(engine)
        with admin_engine.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema_name}" CASCADE'))
        admin_engine.dispose()


@pytest.fixture
def session_factory(database_engine: Engine) -> sessionmaker[Session]:
    return sessionmaker(database_engine, expire_on_commit=False)


def _proof(
    *,
    epoch: int = 0,
    kind: Literal["ACCOUNT", "PROJECT"] = "ACCOUNT",
    project_id: UUID | None = None,
) -> PrivateWriteScope:
    return PrivateWriteScope(
        PrivateWriteAuthorityDecision(
            owner_user_id=OWNER_ID,
            owner_deletion_epoch=epoch,
            scope=PrivateDeletionScope(kind=kind, project_id=project_id),
            authority_ref="w1:test-authority",
            command_id=COMMAND_ID,
            job_id=JOB_ID,
        )
    )


def test_private_write_scope_consumes_v2_scope_and_checks_exact_binding() -> None:
    proof = PrivateWriteScope(
        PrivateWriteAuthorityDecision(
            owner_user_id=OWNER_ID,
            owner_deletion_epoch=0,
            scope=PrivateDeletionScope(kind="PROJECT", project_id=PROJECT_ID),
            authority_ref="w1:test-authority",
            command_id=COMMAND_ID,
            job_id=JOB_ID,
        )
    )

    proof.assert_bound_to(
        owner_user_id=OWNER_ID,
        owner_deletion_epoch=0,
        command_id=COMMAND_ID,
        job_id=JOB_ID,
    )
    with pytest.raises(PrivateScopeRejected, match="owner"):
        proof.assert_bound_to(
            owner_user_id=uuid4(),
            owner_deletion_epoch=0,
            command_id=COMMAND_ID,
            job_id=JOB_ID,
        )
    with pytest.raises(PrivateScopeRejected, match="command"):
        proof.assert_bound_to(
            owner_user_id=OWNER_ID,
            owner_deletion_epoch=0,
            command_id=uuid4(),
            job_id=JOB_ID,
        )
    with pytest.raises(PrivateScopeRejected, match="job"):
        proof.assert_bound_to(
            owner_user_id=OWNER_ID,
            owner_deletion_epoch=0,
            command_id=COMMAND_ID,
            job_id=uuid4(),
        )
    with pytest.raises(PrivateScopeRejected, match="command"):
        proof.assert_bound_to(
            owner_user_id=OWNER_ID,
            owner_deletion_epoch=0,
            job_id=JOB_ID,
        )
    with pytest.raises(PrivateScopeRejected, match="job"):
        proof.assert_bound_to(
            owner_user_id=OWNER_ID,
            owner_deletion_epoch=0,
            command_id=COMMAND_ID,
        )


@pytest.mark.parametrize("target_epoch", [False, -1, SIGNED_64_MAX + 1])
def test_private_write_scope_rejects_invalid_target_epoch(target_epoch: object) -> None:
    proof = _proof()

    with pytest.raises(PrivateScopeRejected, match="outside signed 64-bit"):
        proof.assert_bound_to(
            owner_user_id=OWNER_ID,
            owner_deletion_epoch=target_epoch,  # type: ignore[arg-type]
            command_id=COMMAND_ID,
            job_id=JOB_ID,
        )


def test_loose_scope_and_authority_string_cannot_construct_write_proof() -> None:
    with pytest.raises(TypeError):
        PrivateWriteScope(
            owner_user_id=OWNER_ID,
            owner_deletion_epoch=0,
            kind="PROJECT",
            project_id=PROJECT_ID,
            authority_ref="unverified:caller-string",
            command_id=COMMAND_ID,
            job_id=JOB_ID,
        )
    assert not hasattr(PrivateWriteScope, "from_scope")
    with pytest.raises(PrivateScopeRejected, match="authority decision"):
        PrivateWriteScope(  # type: ignore[arg-type]
            PrivateDeletionScope(kind="PROJECT", project_id=PROJECT_ID)
        )


@pytest.mark.parametrize(
    ("overrides", "match"),
    [
        ({"authority_ref": "   "}, "authority"),
        ({"owner_deletion_epoch": -1}, "epoch"),
        ({"owner_deletion_epoch": SIGNED_64_MAX + 1}, "epoch"),
        ({"owner_deletion_epoch": True}, "epoch"),
        ({"scope": object()}, "validated v2 scope"),
        ({"command_id": "not-a-uuid"}, "command"),
        ({"job_id": "not-a-uuid"}, "job"),
    ],
)
def test_private_write_scope_rejects_untrusted_shapes(
    overrides: dict[str, object],
    match: str,
) -> None:
    values: dict[str, object] = {
        "owner_user_id": OWNER_ID,
        "owner_deletion_epoch": 0,
        "scope": PrivateDeletionScope(kind="ACCOUNT", project_id=None),
        "authority_ref": "w1:test-authority",
        "command_id": COMMAND_ID,
        "job_id": JOB_ID,
    }
    values.update(overrides)

    with pytest.raises(PrivateScopeRejected, match=match):
        PrivateWriteAuthorityDecision(**values)  # type: ignore[arg-type]


def test_scope_unclassified_is_a_private_scope_rejection() -> None:
    assert issubclass(ScopeUnclassified, PrivateScopeRejected)


@pytest.mark.approved_postgres
def test_lock_private_write_scope_creates_and_locks_current_zero_epoch(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        lock_private_write_scope(session, _proof())

    with session_factory() as session:
        owner_state = session.get(PrivateDeletionOwnerState, OWNER_ID)
        assert owner_state is not None
        assert owner_state.latest_epoch == 0
        assert owner_state.account_deleted is False


@pytest.mark.approved_postgres
@pytest.mark.parametrize("proof_epoch", [4, 6])
def test_lock_private_write_scope_rejects_stale_and_future_epochs(
    session_factory: sessionmaker[Session],
    proof_epoch: int,
) -> None:
    with session_factory.begin() as session:
        session.add(
            PrivateDeletionOwnerState(
                owner_user_id=OWNER_ID,
                latest_epoch=5,
                account_deleted=False,
            )
        )

    with session_factory.begin() as session:
        with pytest.raises(PrivateScopeRejected, match="current epoch"):
            lock_private_write_scope(session, _proof(epoch=proof_epoch))


@pytest.mark.approved_postgres
def test_lock_private_write_scope_rejects_account_tombstone(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        session.add(
            PrivateDeletionOwnerState(
                owner_user_id=OWNER_ID,
                latest_epoch=3,
                account_deleted=True,
            )
        )

    with session_factory.begin() as session:
        with pytest.raises(PrivateScopeRejected, match="account tombstone"):
            lock_private_write_scope(session, _proof(epoch=3))


@pytest.mark.approved_postgres
def test_lock_private_write_scope_rejects_only_the_tombstoned_project(
    session_factory: sessionmaker[Session],
) -> None:
    with session_factory.begin() as session:
        session.add(
            PrivateDeletionOwnerState(
                owner_user_id=OWNER_ID,
                latest_epoch=3,
                account_deleted=False,
            )
        )
        session.add(
            PrivateDeletionProjectTombstone(
                owner_user_id=OWNER_ID,
                project_id=PROJECT_ID,
                deletion_epoch=3,
            )
        )

    with session_factory.begin() as session:
        with pytest.raises(PrivateScopeRejected, match="project tombstone"):
            lock_private_write_scope(
                session,
                _proof(epoch=3, kind="PROJECT", project_id=PROJECT_ID),
            )

    with session_factory.begin() as session:
        lock_private_write_scope(
            session,
            _proof(epoch=3, kind="PROJECT", project_id=OTHER_PROJECT_ID),
        )
