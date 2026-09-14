from __future__ import annotations

from pathlib import Path
from uuid import UUID, uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.db.session import SessionLocal, engine, set_local_owner_context

RUNTIME_ROLE_SQL = Path(__file__).parent / "sql" / "runtime_roles.sql"
RUNTIME_ROLE_TEMPLATE_SQL = (
    Path(__file__).parents[3] / "infra" / "postgres" / "runtime_roles.sql"
)
TEST_TABLE = "stage0_private_rows"
RUNTIME_ROLE = "epick_runtime"
OWNER_CONTEXT_EXPRESSION = "NULLIF(current_setting('app.current_user_id', true), '')::uuid"


@pytest.fixture(autouse=True)
def private_test_table() -> None:
    role_sql = RUNTIME_ROLE_SQL.read_text(encoding="utf-8")
    with engine.begin() as connection:
        connection.execute(text(role_sql))
        connection.execute(text(f"DROP TABLE IF EXISTS {TEST_TABLE}"))
        connection.execute(
            text(
                f"CREATE TABLE {TEST_TABLE} ("
                "id uuid PRIMARY KEY, "
                "owner_user_id uuid NOT NULL, "
                "label text NOT NULL"
                ")"
            )
        )
        connection.execute(text(f"ALTER TABLE {TEST_TABLE} ENABLE ROW LEVEL SECURITY"))
        connection.execute(text(f"ALTER TABLE {TEST_TABLE} FORCE ROW LEVEL SECURITY"))
        grant_statement = f"GRANT SELECT, INSERT, UPDATE, DELETE ON {TEST_TABLE} TO {RUNTIME_ROLE}"
        connection.execute(text(grant_statement))
        connection.execute(
            text(
                f"CREATE POLICY {TEST_TABLE}_owner_policy ON {TEST_TABLE} "
                f"USING (owner_user_id = {OWNER_CONTEXT_EXPRESSION}) "
                f"WITH CHECK (owner_user_id = {OWNER_CONTEXT_EXPRESSION})"
            )
        )

    yield

    with engine.begin() as connection:
        connection.execute(text(f"DROP TABLE IF EXISTS {TEST_TABLE}"))


def _runtime_transaction(owner_user_id: UUID):
    session = SessionLocal()
    transaction = session.begin()
    transaction.__enter__()
    session.execute(text(f"SET LOCAL ROLE {RUNTIME_ROLE}"))
    set_local_owner_context(session, owner_user_id)
    return session, transaction


def test_owner_context_is_limited_to_one_transaction_and_cannot_leak_from_pool() -> None:
    owner_a = uuid4()
    owner_b = uuid4()

    session_a, transaction_a = _runtime_transaction(owner_a)
    try:
        session_a.execute(
            text(
                f"INSERT INTO {TEST_TABLE} (id, owner_user_id, label) "
                "VALUES (:id, :owner_user_id, :label)"
            ),
            {"id": uuid4(), "owner_user_id": owner_a, "label": "A"},
        )
        transaction_a.commit()
    finally:
        session_a.close()

    session_b, transaction_b = _runtime_transaction(owner_b)
    try:
        selected_rows = session_b.execute(text(f"SELECT owner_user_id FROM {TEST_TABLE}"))
        visible_to_b = selected_rows.scalars().all()
        assert visible_to_b == []
        transaction_b.commit()
    finally:
        session_b.close()

    session_without_context = SessionLocal()
    transaction_without_context = session_without_context.begin()
    transaction_without_context.__enter__()
    try:
        session_without_context.execute(text(f"SET LOCAL ROLE {RUNTIME_ROLE}"))
        visible_without_context = (
            session_without_context.execute(text(f"SELECT owner_user_id FROM {TEST_TABLE}"))
            .scalars()
            .all()
        )
        assert visible_without_context == []
        transaction_without_context.commit()
    finally:
        session_without_context.close()


def test_malformed_owner_context_is_rejected_before_querying_private_rows() -> None:
    with SessionLocal.begin() as session:
        session.execute(text(f"SET LOCAL ROLE {RUNTIME_ROLE}"))

        with pytest.raises(ValueError, match="valid UUID"):
            set_local_owner_context(session, "not-a-uuid")


def test_runtime_role_groups_cannot_bypass_rls_or_create_schema_objects(
    migrated_engine,
) -> None:
    role_template = RUNTIME_ROLE_TEMPLATE_SQL.read_text(encoding="utf-8")
    with migrated_engine.begin() as connection:
        connection.execute(text(role_template))
        roles = (
            connection.execute(
                text(
                    "SELECT rolname, rolsuper, rolbypassrls, rolcanlogin "
                    "FROM pg_roles "
                    "WHERE rolname = ANY(:role_names)"
                ),
                {
                    "role_names": [
                        "epick_migrator",
                        "epick_runtime",
                        "epick_worker",
                        "epick_deleter",
                    ]
                },
            )
            .mappings()
            .all()
        )
        can_runtime_create = connection.execute(
            text("SELECT has_schema_privilege('epick_runtime', 'public', 'CREATE')")
        ).scalar_one()

    assert {role["rolname"] for role in roles} == {
        "epick_migrator",
        "epick_runtime",
        "epick_worker",
        "epick_deleter",
    }
    assert all(not role["rolsuper"] and not role["rolbypassrls"] for role in roles)
    assert all(not role["rolcanlogin"] for role in roles)
    assert can_runtime_create is False

    try:
        with migrated_engine.connect() as connection:
            with connection.begin():
                connection.execute(text(f"SET LOCAL ROLE {RUNTIME_ROLE}"))
                with pytest.raises(DBAPIError):
                    with connection.begin_nested():
                        connection.execute(text("CREATE TABLE pg0_runtime_ddl_probe (id uuid)"))
    finally:
        with migrated_engine.begin() as connection:
            connection.execute(text("DROP TABLE IF EXISTS pg0_runtime_ddl_probe"))
