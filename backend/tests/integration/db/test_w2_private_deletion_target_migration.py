from __future__ import annotations

from sqlalchemy import Engine, text


def test_w2_private_deletion_is_a_mandatory_store_target(migrated_engine: Engine) -> None:
    with migrated_engine.connect() as connection:
        store_constraint = connection.scalar(
            text(
                "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                "WHERE conrelid = 'public.deletion_targets'::regclass "
                "AND conname LIKE '%store_type_allowed'"
            )
        )
        completion_function = connection.scalar(
            text(
                "SELECT pg_get_functiondef('validate_deletion_request_completion()'::regprocedure)"
            )
        )
        schema_version_width = connection.scalar(
            text(
                "SELECT character_maximum_length FROM information_schema.columns "
                "WHERE table_schema = 'public' AND table_name = 'outbox_messages' "
                "AND column_name = 'schema_version'"
            )
        )
    assert store_constraint is not None
    assert "W2_SOURCE_RUNTIME" in store_constraint
    assert completion_function is not None
    assert "<> 7" in completion_function
    assert schema_version_width is not None and schema_version_width >= 34
