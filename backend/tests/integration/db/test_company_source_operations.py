from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models.identity import User
from app.services.experience import ExperienceService


@pytest.fixture(autouse=True)
def clean_company_source_operation_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))
        connection.execute(text("TRUNCATE canonical_skills CASCADE"))
    yield


def _insert_company(connection, *, company_id: UUID, name: str) -> None:
    connection.execute(
        text(
            "INSERT INTO companies (id, legal_name, display_name) "
            "VALUES (:id, :legal_name, :display_name)"
        ),
        {"id": company_id, "legal_name": name, "display_name": name},
    )


def _insert_source_with_evidence(
    connection, *, company_id: UUID, suffix: str
) -> tuple[UUID, UUID, UUID]:
    source_id = uuid4()
    source_version_id = uuid4()
    evidence_span_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO sources ("
            "id, company_id, source_type, canonical_url, canonical_url_hash, "
            "url_normalization_version, policy_version, policy_checked_at"
            ") VALUES ("
            ":id, :company_id, 'CAREERS', :canonical_url, :canonical_url_hash, "
            "'v1', 'policy-v1', now()"
            ")"
        ),
        {
            "id": source_id,
            "company_id": company_id,
            "canonical_url": f"https://careers.example.test/{suffix}",
            "canonical_url_hash": f"url-hash-{suffix}",
        },
    )
    connection.execute(
        text(
            "INSERT INTO source_versions ("
            "id, source_id, company_id, version_no, collected_at, content_hash, "
            "parser_version, content_normalization_version, extraction_status, "
            "access_policy_at_collection, storage_policy_at_collection, "
            "reuse_policy_at_collection, policy_version_at_collection"
            ") VALUES ("
            ":id, :source_id, :company_id, 1, now(), :content_hash, "
            "'parser-v1', 'normalization-v1', 'SUCCEEDED', 'ALLOWED', "
            "'FULL_CONTENT_ALLOWED', 'CROSS_USER_ALLOWED', 'policy-v1'"
            ")"
        ),
        {
            "id": source_version_id,
            "source_id": source_id,
            "company_id": company_id,
            "content_hash": f"content-hash-{suffix}",
        },
    )
    connection.execute(
        text(
            "INSERT INTO evidence_spans ("
            "id, source_version_id, excerpt, locator_type, locator, chunk_order, excerpt_hash"
            ") VALUES ("
            ":id, :source_version_id, 'Official evidence', 'LINE_RANGE', '1-1', 0, "
            ":excerpt_hash"
            ")"
        ),
        {
            "id": evidence_span_id,
            "source_version_id": source_version_id,
            "excerpt_hash": f"excerpt-hash-{suffix}",
        },
    )
    return source_id, source_version_id, evidence_span_id


def _insert_job_posting(
    connection,
    *,
    company_id: UUID,
    source_id: UUID,
    source_version_id: UUID,
    org_unit_version_id: UUID | None = None,
    role_version_id: UUID | None = None,
) -> tuple[UUID, UUID]:
    posting_id = uuid4()
    posting_version_id = uuid4()
    connection.execute(
        text(
            "INSERT INTO job_postings (id, company_id, source_id, status) "
            "VALUES (:id, :company_id, :source_id, 'OPEN')"
        ),
        {"id": posting_id, "company_id": company_id, "source_id": source_id},
    )
    connection.execute(
        text(
            "INSERT INTO job_posting_versions ("
            "id, posting_id, company_id, source_id, version_no, source_version_id, title, "
            "org_unit_version_id, role_version_id"
            ") VALUES ("
            ":id, :posting_id, :company_id, :source_id, 1, :source_version_id, "
            "'Backend Engineer', :org_unit_version_id, :role_version_id"
            ")"
        ),
        {
            "id": posting_version_id,
            "posting_id": posting_id,
            "company_id": company_id,
            "source_id": source_id,
            "source_version_id": source_version_id,
            "org_unit_version_id": org_unit_version_id,
            "role_version_id": role_version_id,
        },
    )
    connection.execute(
        text("UPDATE job_postings SET current_version_id = :version_id WHERE id = :posting_id"),
        {"version_id": posting_version_id, "posting_id": posting_id},
    )
    return posting_id, posting_version_id


def _insert_org_and_role(
    connection,
    *,
    company_id: UUID,
    evidence_span_id: UUID,
    suffix: str,
) -> tuple[UUID, UUID, UUID, UUID]:
    org_unit_id = uuid4()
    org_unit_version_id = uuid4()
    role_id = uuid4()
    role_version_id = uuid4()
    connection.execute(
        text("INSERT INTO org_units (id, company_id) VALUES (:id, :company_id)"),
        {"id": org_unit_id, "company_id": company_id},
    )
    connection.execute(
        text(
            "INSERT INTO org_unit_versions ("
            "id, org_unit_id, company_id, version_no, name, normalized_name, evidence_span_id"
            ") VALUES ("
            ":id, :org_unit_id, :company_id, 1, :name, :normalized_name, :evidence_span_id"
            ")"
        ),
        {
            "id": org_unit_version_id,
            "org_unit_id": org_unit_id,
            "company_id": company_id,
            "name": f"Platform {suffix}",
            "normalized_name": f"platform-{suffix}",
            "evidence_span_id": evidence_span_id,
        },
    )
    connection.execute(
        text("UPDATE org_units SET current_version_id = :version_id WHERE id = :id"),
        {"id": org_unit_id, "version_id": org_unit_version_id},
    )
    connection.execute(
        text("INSERT INTO roles (id, company_id) VALUES (:id, :company_id)"),
        {"id": role_id, "company_id": company_id},
    )
    connection.execute(
        text(
            "INSERT INTO role_versions ("
            "id, role_id, company_id, version_no, name, normalized_name, "
            "org_unit_version_id, evidence_span_id"
            ") VALUES ("
            ":id, :role_id, :company_id, 1, :name, :normalized_name, "
            ":org_unit_version_id, :evidence_span_id"
            ")"
        ),
        {
            "id": role_version_id,
            "role_id": role_id,
            "company_id": company_id,
            "name": f"Backend Engineer {suffix}",
            "normalized_name": f"backend-engineer-{suffix}",
            "org_unit_version_id": org_unit_version_id,
            "evidence_span_id": evidence_span_id,
        },
    )
    connection.execute(
        text("UPDATE roles SET current_version_id = :version_id WHERE id = :id"),
        {"id": role_id, "version_id": role_version_id},
    )
    return org_unit_id, org_unit_version_id, role_id, role_version_id


def test_company_relations_and_collection_attempts_keep_source_scope(
    migrated_engine: Engine,
) -> None:
    company_a_id = uuid4()
    company_b_id = uuid4()
    with migrated_engine.begin() as connection:
        _insert_company(connection, company_id=company_a_id, name="Company A")
        _insert_company(connection, company_id=company_b_id, name="Company B")
        source_a_id, source_version_a_id, evidence_span_a_id = _insert_source_with_evidence(
            connection, company_id=company_a_id, suffix="a"
        )
        _, source_version_b_id, _ = _insert_source_with_evidence(
            connection, company_id=company_b_id, suffix="b"
        )
        connection.execute(
            text(
                "INSERT INTO company_relations ("
                "id, from_company_id, to_company_id, relation_type, evidence_span_id"
                ") VALUES ("
                ":id, :from_company_id, :to_company_id, 'SUBSIDIARY_OF', :evidence_span_id"
                ")"
            ),
            {
                "id": uuid4(),
                "from_company_id": company_a_id,
                "to_company_id": company_b_id,
                "evidence_span_id": evidence_span_a_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO source_collection_attempts ("
                "id, source_id, idempotency_key, attempt_no, access_result, storage_result, "
                "parse_result, policy_version, started_at, source_version_id, "
                "command_schema_version, result_completeness"
                ") VALUES ("
                ":id, :source_id, 'source-a-v1', 1, 'ALLOWED', 'STORED_FULL', "
                "'SUCCEEDED', 'policy-v1', now(), :source_version_id, '1.0', 'complete'"
                ")"
            ),
            {"id": uuid4(), "source_id": source_a_id, "source_version_id": source_version_a_id},
        )

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO company_relations ("
                        "id, from_company_id, to_company_id, relation_type, evidence_span_id"
                        ") VALUES ("
                        ":id, :company_id, :company_id, 'PARENT_OF', :evidence_span_id"
                        ")"
                    ),
                    {
                        "id": uuid4(),
                        "company_id": company_a_id,
                        "evidence_span_id": evidence_span_a_id,
                    },
                )
        finally:
            transaction.rollback()

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO source_collection_attempts ("
                        "id, source_id, idempotency_key, attempt_no, access_result, "
                        "storage_result, "
                        "parse_result, policy_version, started_at, source_version_id, "
                        "command_schema_version"
                        ") VALUES ("
                        ":id, :source_id, 'source-a-v2', 1, 'ALLOWED', 'STORED_FULL', "
                        "'SUCCEEDED', 'policy-v1', now(), :source_version_id, '1.0'"
                        ")"
                    ),
                    {
                        "id": uuid4(),
                        "source_id": source_a_id,
                        "source_version_id": source_version_b_id,
                    },
                )
        finally:
            transaction.rollback()


def test_organization_references_are_limited_to_the_posting_company(
    migrated_engine: Engine,
) -> None:
    company_a_id = uuid4()
    company_b_id = uuid4()
    with migrated_engine.begin() as connection:
        _insert_company(connection, company_id=company_a_id, name="Company A")
        _insert_company(connection, company_id=company_b_id, name="Company B")
        source_a_id, source_version_a_id, evidence_span_a_id = _insert_source_with_evidence(
            connection, company_id=company_a_id, suffix="org-a"
        )
        _, _, evidence_span_b_id = _insert_source_with_evidence(
            connection, company_id=company_b_id, suffix="org-b"
        )
        _, org_version_a_id, _, role_version_a_id = _insert_org_and_role(
            connection,
            company_id=company_a_id,
            evidence_span_id=evidence_span_a_id,
            suffix="a",
        )
        _, org_version_b_id, _, _ = _insert_org_and_role(
            connection,
            company_id=company_b_id,
            evidence_span_id=evidence_span_b_id,
            suffix="b",
        )
        posting_id, posting_version_id = _insert_job_posting(
            connection,
            company_id=company_a_id,
            source_id=source_a_id,
            source_version_id=source_version_a_id,
            org_unit_version_id=org_version_a_id,
            role_version_id=role_version_a_id,
        )
        group_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO requirement_groups ("
                "id, job_posting_version_id, group_order, operator"
                ") VALUES (:id, :posting_version_id, 0, 'AND')"
            ),
            {"id": group_id, "posting_version_id": posting_version_id},
        )
        requirement_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO requirements ("
                "id, group_id, evidence_span_id, category, necessity, source_text, "
                "org_unit_version_id"
                ") VALUES ("
                ":id, :group_id, :evidence_span_id, 'ROLE', 'REQUIRED', "
                "'Platform organization experience', :org_unit_version_id"
                ")"
            ),
            {
                "id": requirement_id,
                "group_id": group_id,
                "evidence_span_id": evidence_span_a_id,
                "org_unit_version_id": org_version_a_id,
            },
        )

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO job_posting_versions ("
                        "id, posting_id, company_id, source_id, version_no, source_version_id, "
                        "title, org_unit_version_id"
                        ") VALUES ("
                        ":id, :posting_id, :company_id, :source_id, 2, :source_version_id, "
                        "'Backend Engineer v2', :org_unit_version_id"
                        ")"
                    ),
                    {
                        "id": uuid4(),
                        "posting_id": posting_id,
                        "company_id": company_a_id,
                        "source_id": source_a_id,
                        "source_version_id": source_version_a_id,
                        "org_unit_version_id": org_version_b_id,
                    },
                )
        finally:
            transaction.rollback()

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO requirements ("
                        "id, group_id, evidence_span_id, category, necessity, source_text, "
                        "org_unit_version_id"
                        ") VALUES ("
                        ":id, :group_id, :evidence_span_id, 'ROLE', 'REQUIRED', "
                        "'Foreign organization experience', :org_unit_version_id"
                        ")"
                    ),
                    {
                        "id": uuid4(),
                        "group_id": group_id,
                        "evidence_span_id": evidence_span_a_id,
                        "org_unit_version_id": org_version_b_id,
                    },
                )
        finally:
            transaction.rollback()

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(OperationalError):
                connection.execute(
                    text("UPDATE job_posting_versions SET title = title WHERE id = :id"),
                    {"id": posting_version_id},
                )
        finally:
            transaction.rollback()

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(OperationalError):
                connection.execute(
                    text("UPDATE requirements SET source_text = source_text WHERE id = :id"),
                    {"id": requirement_id},
                )
        finally:
            transaction.rollback()


def test_public_history_records_reject_updates_and_deletes(migrated_engine: Engine) -> None:
    company_a_id = uuid4()
    company_b_id = uuid4()
    with migrated_engine.begin() as connection:
        _insert_company(connection, company_id=company_a_id, name="Company A")
        _insert_company(connection, company_id=company_b_id, name="Company B")
        source_a_id, source_version_a_id, evidence_a_id = _insert_source_with_evidence(
            connection, company_id=company_a_id, suffix="immutability-a"
        )
        _, source_version_b_id, _ = _insert_source_with_evidence(
            connection, company_id=company_b_id, suffix="immutability-b"
        )
        collection_attempt_id = uuid4()
        source_relation_id = uuid4()
        company_relation_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO source_collection_attempts ("
                "id, source_id, idempotency_key, attempt_no, access_result, storage_result, "
                "parse_result, policy_version, started_at, source_version_id, "
                "command_schema_version, result_completeness"
                ") VALUES ("
                ":id, :source_id, 'immutable-attempt', 1, 'ALLOWED', 'STORED_FULL', "
                "'SUCCEEDED', 'policy-v1', now(), :source_version_id, '1.0', 'complete'"
                ")"
            ),
            {
                "id": collection_attempt_id,
                "source_id": source_a_id,
                "source_version_id": source_version_a_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO source_relations ("
                "id, from_source_version_id, to_source_version_id, relation_type"
                ") VALUES ("
                ":id, :from_source_version_id, :to_source_version_id, 'RELATED'"
                ")"
            ),
            {
                "id": source_relation_id,
                "from_source_version_id": source_version_a_id,
                "to_source_version_id": source_version_b_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO company_relations ("
                "id, from_company_id, to_company_id, relation_type, evidence_span_id"
                ") VALUES ("
                ":id, :from_company_id, :to_company_id, 'RELATED_TO', :evidence_span_id"
                ")"
            ),
            {
                "id": company_relation_id,
                "from_company_id": company_a_id,
                "to_company_id": company_b_id,
                "evidence_span_id": evidence_a_id,
            },
        )
        _, org_version_id, _, role_version_id = _insert_org_and_role(
            connection,
            company_id=company_a_id,
            evidence_span_id=evidence_a_id,
            suffix="immutability",
        )
        _, posting_version_id = _insert_job_posting(
            connection,
            company_id=company_a_id,
            source_id=source_a_id,
            source_version_id=source_version_a_id,
            org_unit_version_id=org_version_id,
            role_version_id=role_version_id,
        )
        requirement_group_id = uuid4()
        requirement_id = uuid4()
        canonical_skill_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO requirement_groups ("
                "id, job_posting_version_id, group_order, operator"
                ") VALUES (:id, :job_posting_version_id, 0, 'AND')"
            ),
            {"id": requirement_group_id, "job_posting_version_id": posting_version_id},
        )
        connection.execute(
            text(
                "INSERT INTO requirements ("
                "id, group_id, evidence_span_id, category, necessity, source_text"
                ") VALUES ("
                ":id, :group_id, :evidence_span_id, 'SKILL', 'REQUIRED', 'Python'"
                ")"
            ),
            {
                "id": requirement_id,
                "group_id": requirement_group_id,
                "evidence_span_id": evidence_a_id,
            },
        )
        connection.execute(
            text("INSERT INTO canonical_skills (id, canonical_name) VALUES (:id, 'Python')"),
            {"id": canonical_skill_id},
        )
        connection.execute(
            text(
                "INSERT INTO requirement_skills ("
                "requirement_id, canonical_skill_id, raw_term, relation_type"
                ") VALUES ("
                ":requirement_id, :canonical_skill_id, 'Python', 'REQUIRES'"
                ")"
            ),
            {"requirement_id": requirement_id, "canonical_skill_id": canonical_skill_id},
        )

    immutable_rows = (
        ("source_versions", "id = :id", {"id": source_version_a_id}, "content_hash = content_hash"),
        ("evidence_spans", "id = :id", {"id": evidence_a_id}, "excerpt = excerpt"),
        (
            "source_collection_attempts",
            "id = :id",
            {"id": collection_attempt_id},
            "parse_result = parse_result",
        ),
        (
            "source_relations",
            "id = :id",
            {"id": source_relation_id},
            "relation_type = relation_type",
        ),
        (
            "company_relations",
            "id = :id",
            {"id": company_relation_id},
            "relation_type = relation_type",
        ),
        ("org_unit_versions", "id = :id", {"id": org_version_id}, "name = name"),
        ("role_versions", "id = :id", {"id": role_version_id}, "name = name"),
        ("job_posting_versions", "id = :id", {"id": posting_version_id}, "title = title"),
        ("requirement_groups", "id = :id", {"id": requirement_group_id}, "operator = operator"),
        ("requirements", "id = :id", {"id": requirement_id}, "source_text = source_text"),
        (
            "requirement_skills",
            "requirement_id = :requirement_id AND canonical_skill_id = :canonical_skill_id",
            {"requirement_id": requirement_id, "canonical_skill_id": canonical_skill_id},
            "relation_type = relation_type",
        ),
    )

    for table_name, predicate, parameters, assignment in immutable_rows:
        for statement in (
            f"UPDATE {table_name} SET {assignment} WHERE {predicate}",
            f"DELETE FROM {table_name} WHERE {predicate}",
        ):
            with migrated_engine.connect() as connection:
                transaction = connection.begin()
                try:
                    with pytest.raises(OperationalError):
                        connection.execute(text(statement), parameters)
                finally:
                    transaction.rollback()


def test_external_source_provenance_requires_a_source_version(
    db_session: Session, migrated_engine: Engine
) -> None:
    company_id = uuid4()
    with migrated_engine.begin() as connection:
        _insert_company(connection, company_id=company_id, name="Company")
        _, source_version_id, _ = _insert_source_with_evidence(
            connection, company_id=company_id, suffix="provenance"
        )

    owner = User(display_name="Provenance owner", locale="ko-KR", timezone="Asia/Seoul")
    db_session.add(owner)
    db_session.flush()
    activity = ExperienceService(db_session).create_activity(
        owner_user_id=owner.id,
        title="Source-backed activity",
    )
    db_session.execute(
        text(
            "INSERT INTO experience_field_provenance ("
            "id, owner_user_id, activity_version_id, field_name, origin, source_version_id"
            ") VALUES ("
            ":id, :owner_user_id, :activity_version_id, 'title', 'EXTERNAL_SOURCE', "
            ":source_version_id"
            ")"
        ),
        {
            "id": uuid4(),
            "owner_user_id": owner.id,
            "activity_version_id": activity.current_version_id,
            "source_version_id": source_version_id,
        },
    )
    db_session.commit()

    with pytest.raises(IntegrityError):
        db_session.execute(
            text(
                "INSERT INTO experience_field_provenance ("
                "id, owner_user_id, activity_version_id, field_name, origin"
                ") VALUES ("
                ":id, :owner_user_id, :activity_version_id, 'role_text', 'EXTERNAL_SOURCE'"
                ")"
            ),
            {
                "id": uuid4(),
                "owner_user_id": owner.id,
                "activity_version_id": activity.current_version_id,
            },
        )
    db_session.rollback()
