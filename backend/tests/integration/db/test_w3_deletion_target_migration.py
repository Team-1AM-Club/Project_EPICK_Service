from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest
from sqlalchemy import Engine, text

pytestmark = pytest.mark.postgres

BACKEND_ROOT = Path(__file__).parents[3]
MIGRATION_PATH = (
    BACKEND_ROOT / "migrations" / "versions" / "035_w3_core_runtime_deletion_target.py"
)
SOURCE_PRIVATE_MIGRATION_PATH = (
    BACKEND_ROOT / "migrations" / "versions" / "037_w3_source_private_outbox.py"
)
DELETER_RLS_MIGRATION_PATH = (
    BACKEND_ROOT / "migrations" / "versions" / "038_w3_retention_deleter_rls.py"
)


def test_deletion_target_constraint_adds_w3_without_removing_existing_stores(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.connect() as connection:
        definition = connection.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'public.deletion_targets'::regclass "
                "AND conname LIKE '%store_type_allowed'"
            )
        )

    assert definition is not None
    for store_type in (
        "POSTGRESQL",
        "NEO4J",
        "VECTOR",
        "CACHE",
        "CHECKPOINT",
        "W3_CORE_RUNTIME",
    ):
        assert store_type in definition


def test_w3_deletion_target_migration_is_forward_only() -> None:
    assert MIGRATION_PATH.exists(), "revision 035 must publish the additive W3 target migration"
    spec = importlib.util.spec_from_file_location("w3_deletion_target_migration", MIGRATION_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "035_w3_deletion_target"
    assert module.down_revision == "034_w3_authority_currentness_rls"
    with pytest.raises(RuntimeError, match="forward-only"):
        module.downgrade()


def test_source_retirement_is_an_explicit_private_outbox_shape(
    migrated_engine: Engine,
) -> None:
    with migrated_engine.connect() as connection:
        definition = connection.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'public.outbox_messages'::regclass "
                "AND contype = 'c' "
                "AND pg_get_constraintdef(oid) LIKE "
                "'%w1.private.w3.source-retirement.v1%'"
            )
        )

    assert definition is not None
    assert "w1.private.w3.source-retirement.v1" in definition
    assert "W3_SOURCE_RETIREMENT" in definition


def test_source_private_outbox_migration_is_forward_only() -> None:
    assert SOURCE_PRIVATE_MIGRATION_PATH.exists()
    spec = importlib.util.spec_from_file_location(
        "w3_source_private_outbox_migration", SOURCE_PRIVATE_MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "037_w3_source_private_outbox"
    assert module.down_revision == "036_w3_deletion_completion"
    with pytest.raises(RuntimeError, match="forward-only"):
        module.downgrade()


def test_w3_retention_deleter_has_only_required_operational_rls(
    migrated_engine: Engine,
) -> None:
    expected = {
        ("users", "users_deleter_operational_read_policy", "SELECT"),
        ("users", "users_deleter_operational_lock_policy", "UPDATE"),
        (
            "deletion_requests",
            "deletion_requests_deleter_operational_policy",
            "ALL",
        ),
        (
            "deletion_targets",
            "deletion_targets_deleter_operational_policy",
            "ALL",
        ),
        (
            "outbox_messages",
            "outbox_messages_w3_retention_deleter_policy",
            "ALL",
        ),
    }
    with migrated_engine.connect() as connection:
        rows = set(
            connection.execute(
                text(
                    "SELECT tablename, policyname, cmd FROM pg_policies "
                    "WHERE schemaname = 'public' "
                    "AND 'epick_deleter' = ANY(roles)"
                )
            ).tuples()
        )
        outbox_qual = connection.scalar(
            text(
                "SELECT qual FROM pg_policies WHERE schemaname = 'public' "
                "AND tablename = 'outbox_messages' "
                "AND policyname = 'outbox_messages_w3_retention_deleter_policy'"
            )
        )

    assert expected <= rows
    assert outbox_qual is not None
    assert "w1.private.w3.owner-deletion.v1" in outbox_qual
    assert "w1.private.w3.source-retirement.v1" in outbox_qual


def test_w3_retention_deleter_rls_migration_is_forward_only() -> None:
    assert DELETER_RLS_MIGRATION_PATH.exists()
    spec = importlib.util.spec_from_file_location(
        "w3_retention_deleter_rls_migration", DELETER_RLS_MIGRATION_PATH
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)

    assert module.revision == "038_w3_retention_deleter_rls"
    assert module.down_revision == "037_w3_source_private_outbox"
    with pytest.raises(RuntimeError, match="forward-only"):
        module.downgrade()
