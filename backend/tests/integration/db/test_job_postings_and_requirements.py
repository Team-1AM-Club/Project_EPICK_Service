from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError


@pytest.fixture(autouse=True)
def clean_job_posting_tables(migrated_engine: Engine) -> None:
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


def _insert_source_with_version(
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
            ":id, :company_id, 'JOB_POSTING', :canonical_url, :canonical_url_hash, "
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
            ":id, :source_version_id, 'Python is required.', 'LINE_RANGE', '1-1', 0, "
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
            "id, posting_id, company_id, source_id, version_no, source_version_id, title"
            ") VALUES ("
            ":id, :posting_id, :company_id, :source_id, 1, :source_version_id, "
            "'Backend Engineer'"
            ")"
        ),
        {
            "id": posting_version_id,
            "posting_id": posting_id,
            "company_id": company_id,
            "source_id": source_id,
            "source_version_id": source_version_id,
        },
    )
    connection.execute(
        text("UPDATE job_postings SET current_version_id = :version_id WHERE id = :posting_id"),
        {"version_id": posting_version_id, "posting_id": posting_id},
    )
    return posting_id, posting_version_id


def test_job_posting_versions_and_requirements_keep_source_evidence_scope(
    migrated_engine: Engine,
) -> None:
    company_a_id = uuid4()
    company_b_id = uuid4()
    with migrated_engine.begin() as connection:
        _insert_company(connection, company_id=company_a_id, name="Company A")
        _insert_company(connection, company_id=company_b_id, name="Company B")
        source_a_id, source_version_a_id, evidence_span_a_id = _insert_source_with_version(
            connection, company_id=company_a_id, suffix="a"
        )
        source_b_id, source_version_b_id, _ = _insert_source_with_version(
            connection, company_id=company_b_id, suffix="b"
        )
        posting_id, posting_version_id = _insert_job_posting(
            connection,
            company_id=company_a_id,
            source_id=source_a_id,
            source_version_id=source_version_a_id,
        )
        skill_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO canonical_skills (id, canonical_name) "
                "VALUES (:id, 'Python')"
            ),
            {"id": skill_id},
        )
        group_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO requirement_groups ("
                "id, job_posting_version_id, group_order, operator, same_experience_required"
                ") VALUES (:id, :posting_version_id, 0, 'AND', true)"
            ),
            {"id": group_id, "posting_version_id": posting_version_id},
        )
        requirement_id = uuid4()
        connection.execute(
            text(
                "INSERT INTO requirements ("
                "id, group_id, evidence_span_id, category, necessity, source_text, normalized_text"
                ") VALUES ("
                ":id, :group_id, :evidence_span_id, 'SKILL', 'REQUIRED', "
                "'Python is required.', 'Python experience'"
                ")"
            ),
            {
                "id": requirement_id,
                "group_id": group_id,
                "evidence_span_id": evidence_span_a_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO requirement_skills ("
                "requirement_id, canonical_skill_id, raw_term, relation_type"
                ") VALUES (:requirement_id, :skill_id, 'Python', 'REQUIRES')"
            ),
            {"requirement_id": requirement_id, "skill_id": skill_id},
        )
        current_version_id = connection.execute(
            text("SELECT current_version_id FROM job_postings WHERE id = :posting_id"),
            {"posting_id": posting_id},
        ).scalar_one()

    assert current_version_id == posting_version_id

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO job_postings (id, company_id, source_id) "
                        "VALUES (:id, :company_id, :source_id)"
                    ),
                    {"id": uuid4(), "company_id": company_a_id, "source_id": source_b_id},
                )
        finally:
            transaction.rollback()

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO job_posting_versions ("
                        "id, posting_id, company_id, source_id, version_no, "
                        "source_version_id, title"
                        ") VALUES ("
                        ":id, :posting_id, :company_id, :source_id, 2, :source_version_id, "
                        "'Invalid source scope'"
                        ")"
                    ),
                    {
                        "id": uuid4(),
                        "posting_id": posting_id,
                        "company_id": company_a_id,
                        "source_id": source_b_id,
                        "source_version_id": source_version_b_id,
                    },
                )
        finally:
            transaction.rollback()


def test_project_and_job_inputs_accept_only_their_scoped_p1_resources(
    migrated_engine: Engine,
) -> None:
    owner_id = uuid4()
    company_a_id = uuid4()
    company_b_id = uuid4()
    project_a_id = uuid4()
    project_b_id = uuid4()
    project_version_a_id = uuid4()
    project_version_b_id = uuid4()
    job_id = uuid4()

    with migrated_engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, display_name, locale, timezone) "
                "VALUES (:id, 'Owner', 'ko-KR', 'Asia/Seoul')"
            ),
            {"id": owner_id},
        )
        _insert_company(connection, company_id=company_a_id, name="Company A")
        _insert_company(connection, company_id=company_b_id, name="Company B")
        source_a_id, source_version_a_id, _ = _insert_source_with_version(
            connection, company_id=company_a_id, suffix="project-a"
        )
        posting_id, posting_version_id = _insert_job_posting(
            connection,
            company_id=company_a_id,
            source_id=source_a_id,
            source_version_id=source_version_a_id,
        )
        for project_id, project_version_id, company_id in (
            (project_a_id, project_version_a_id, company_a_id),
            (project_b_id, project_version_b_id, company_b_id),
        ):
            connection.execute(
                text(
                    "INSERT INTO application_projects (id, owner_user_id) "
                    "VALUES (:id, :owner_user_id)"
                ),
                {"id": project_id, "owner_user_id": owner_id},
            )
            connection.execute(
                text(
                    "INSERT INTO application_project_versions ("
                    "id, project_id, owner_user_id, version_no, company_id, title, role_name"
                    ") VALUES ("
                    ":id, :project_id, :owner_user_id, 1, :company_id, 'Application', "
                    "'Backend Engineer'"
                    ")"
                ),
                {
                    "id": project_version_id,
                    "project_id": project_id,
                    "owner_user_id": owner_id,
                    "company_id": company_id,
                },
            )
        connection.execute(
            text(
                "UPDATE application_project_versions SET job_posting_id = :posting_id "
                "WHERE id = :project_version_id"
            ),
            {"posting_id": posting_id, "project_version_id": project_version_a_id},
        )
        connection.execute(
            text(
                "INSERT INTO jobs (id, owner_user_id, job_type, owner_deletion_epoch) "
                "VALUES (:id, :owner_user_id, 'SOURCE_ANALYSIS', 0)"
            ),
            {"id": job_id, "owner_user_id": owner_id},
        )
        connection.execute(
            text(
                "INSERT INTO job_input_refs (id, job_id, owner_user_id, source_version_id) "
                "VALUES (:id, :job_id, :owner_user_id, :source_version_id)"
            ),
            {
                "id": uuid4(),
                "job_id": job_id,
                "owner_user_id": owner_id,
                "source_version_id": source_version_a_id,
            },
        )

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "UPDATE application_project_versions SET job_posting_id = :posting_id "
                        "WHERE id = :project_version_id"
                    ),
                    {"posting_id": posting_id, "project_version_id": project_version_b_id},
                )
        finally:
            transaction.rollback()

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO job_input_refs ("
                        "id, job_id, owner_user_id, source_version_id, job_posting_version_id"
                        ") VALUES ("
                        ":id, :job_id, :owner_user_id, :source_version_id, "
                        ":job_posting_version_id"
                        ")"
                    ),
                    {
                        "id": uuid4(),
                        "job_id": job_id,
                        "owner_user_id": owner_id,
                        "source_version_id": source_version_a_id,
                        "job_posting_version_id": posting_version_id,
                    },
                )
        finally:
            transaction.rollback()
