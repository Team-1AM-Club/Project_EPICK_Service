from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from app.core.config import settings
from app.db.base import Base
from app.models import (  # noqa: F401
    application_workspace,
    deletion,
    experience,
    identity,
    job_postings,
    jobs,
    knowledge,
    lifecycle_operations,
    organizations,
    privacy_controls,
    projection,
    question_analysis,
    recommendations,
    sources,
    w2_commit_operations,
)

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata

LEGACY_AUTOGENERATE_TABLES = frozenset(
    {
        "question_analysis_job_requirement_group_links",
        "question_analysis_job_requirement_links",
    }
)


def include_object(
    object_: object,
    name: str | None,
    type_: str,
    reflected: bool,
    compare_to: object | None,
) -> bool:
    """Limit autogenerate to the ORM-owned table/column/type inventory.

    This service keeps FK, unique, partial-index, RLS, trigger, and policy
    contracts in hand-authored PostgreSQL migrations and verifies them through
    integration tests.  Comparing their generated physical names to the
    intentionally slimmer ORM metadata produces false upgrade operations.
    """

    if type_ == "table" and reflected and name in LEGACY_AUTOGENERATE_TABLES:
        return False
    if type_ in {"foreign_key_constraint", "index", "unique_constraint"}:
        return False
    return True


def run_migrations_offline() -> None:
    url = (
        config.get_main_option("sqlalchemy.url")
        or settings.migration_database_url
        or settings.database_url
    )
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        include_object=include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    configuration = config.get_section(config.config_ini_section, {})
    if not configuration.get("sqlalchemy.url"):
        configuration["sqlalchemy.url"] = settings.migration_database_url or settings.database_url

    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            include_object=include_object,
        )

        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
