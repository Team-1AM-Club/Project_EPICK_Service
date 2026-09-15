# ruff: noqa: E501

from __future__ import annotations

from uuid import UUID, uuid4

import pytest
from sqlalchemy import Engine, text
from sqlalchemy.exc import IntegrityError, OperationalError
from sqlalchemy.orm import Session

from app.models.identity import User
from app.services.application_workspace import ApplicationWorkspaceService
from app.services.recommendations import RecommendationService


@pytest.fixture(autouse=True)
def clean_claim_and_analysis_tables(migrated_engine: Engine) -> None:
    with migrated_engine.begin() as connection:
        connection.execute(text("TRUNCATE users CASCADE"))
        connection.execute(text("TRUNCATE companies CASCADE"))
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


def test_claim_evidence_is_company_scoped_and_source_history_is_append_only(
    migrated_engine: Engine,
) -> None:
    company_a_id = uuid4()
    company_b_id = uuid4()
    claim_version_id = uuid4()
    with migrated_engine.begin() as connection:
        _insert_company(connection, company_id=company_a_id, name="Company A")
        _insert_company(connection, company_id=company_b_id, name="Company B")
        _, source_version_a_id, evidence_a_id = _insert_source_with_evidence(
            connection, company_id=company_a_id, suffix="claim-a"
        )
        _, _, evidence_b_id = _insert_source_with_evidence(
            connection, company_id=company_b_id, suffix="claim-b"
        )
        claim_id = uuid4()
        connection.execute(
            text("INSERT INTO claims (id, company_id) VALUES (:id, :company_id)"),
            {"id": claim_id, "company_id": company_a_id},
        )
        connection.execute(
            text(
                "INSERT INTO claim_versions ("
                "id, claim_id, company_id, version_no, claim_type, subject_text, predicate, "
                "object_text, extractor_version, extracted_at"
                ") VALUES ("
                ":id, :claim_id, :company_id, 1, 'CULTURE', 'engineering', 'emphasizes', "
                "'Engineering quality is emphasized.', 'test-extractor-v1', now()"
                ")"
            ),
            {"id": claim_version_id, "claim_id": claim_id, "company_id": company_a_id},
        )
        connection.execute(
            text("UPDATE claims SET current_version_id = :version_id WHERE id = :claim_id"),
            {"version_id": claim_version_id, "claim_id": claim_id},
        )
        connection.execute(
            text(
                "INSERT INTO claim_evidence_links (id, claim_version_id, evidence_span_id, relation_type) "
                "VALUES (:id, :claim_version_id, :evidence_span_id, 'SUPPORTS')"
            ),
            {"id": uuid4(), "claim_version_id": claim_version_id, "evidence_span_id": evidence_a_id},
        )

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO claim_evidence_links (id, claim_version_id, evidence_span_id, relation_type) "
                        "VALUES (:id, :claim_version_id, :evidence_span_id, 'SUPPORTS')"
                    ),
                    {
                        "id": uuid4(),
                        "claim_version_id": claim_version_id,
                        "evidence_span_id": evidence_b_id,
                    },
                )
        finally:
            transaction.rollback()

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(OperationalError):
                connection.execute(
                    text("UPDATE source_versions SET content_hash = 'rewritten' WHERE id = :id"),
                    {"id": source_version_a_id},
                )
        finally:
            transaction.rollback()


def test_question_analysis_and_snapshot_links_freeze_the_same_owner_project_and_company(
    db_session: Session, migrated_engine: Engine
) -> None:
    owner = User(display_name="Analysis owner", locale="ko-KR", timezone="Asia/Seoul")
    db_session.add(owner)
    db_session.flush()
    workspace_service = ApplicationWorkspaceService(db_session)
    company_a = workspace_service.create_company(legal_name="Company A", display_name="Company A")
    company_b_id = uuid4()
    first_project = workspace_service.create_project(
        owner_user_id=owner.id,
        company_id=company_a.id,
        title="First project",
        role_name="Backend engineer",
    )
    first_question = workspace_service.create_question(
        owner_user_id=owner.id,
        project_id=first_project.id,
        display_order=0,
        prompt="Explain the strongest backend experience.",
        source="USER",
    )
    second_project = workspace_service.create_project(
        owner_user_id=owner.id,
        company_id=company_a.id,
        title="Second project",
        role_name="Platform engineer",
    )
    second_question = workspace_service.create_question(
        owner_user_id=owner.id,
        project_id=second_project.id,
        display_order=0,
        prompt="Explain platform experience.",
        source="USER",
    )
    snapshot = RecommendationService(db_session).create_snapshot(
        owner_user_id=owner.id,
        project_id=first_project.id,
    )
    db_session.commit()

    first_analysis_id = uuid4()
    second_analysis_id = uuid4()
    with migrated_engine.begin() as connection:
        _insert_company(connection, company_id=company_b_id, name="Company B")
        source_a_id, source_version_a_id, _ = _insert_source_with_evidence(
            connection, company_id=company_a.id, suffix="snapshot-a"
        )
        source_b_id, source_version_b_id, _ = _insert_source_with_evidence(
            connection, company_id=company_b_id, suffix="snapshot-b"
        )
        for analysis_id, project, question in (
            (first_analysis_id, first_project, first_question),
            (second_analysis_id, second_project, second_question),
        ):
            connection.execute(
                text(
                    "INSERT INTO question_analyses ("
                    "id, owner_user_id, project_id, project_version_id, company_id, "
                    "question_id, question_version_id, analysis_input_version, "
                    "analysis_revision, result_status"
                    ") VALUES ("
                    ":id, :owner_user_id, :project_id, :project_version_id, :company_id, "
                    ":question_id, :question_version_id, '1.0', 1, 'SUCCEEDED'"
                    ")"
                ),
                {
                    "id": analysis_id,
                    "owner_user_id": owner.id,
                    "project_id": project.id,
                    "project_version_id": project.current_version_id,
                    "company_id": company_a.id,
                    "question_id": question.id,
                    "question_version_id": question.current_version_id,
                },
            )
        connection.execute(
            text(
                "INSERT INTO snapshot_question_analyses ("
                "snapshot_id, owner_user_id, project_id, project_version_id, question_analysis_id"
                ") VALUES ("
                ":snapshot_id, :owner_user_id, :project_id, :project_version_id, :analysis_id"
                ")"
            ),
            {
                "snapshot_id": snapshot.id,
                "owner_user_id": owner.id,
                "project_id": first_project.id,
                "project_version_id": first_project.current_version_id,
                "analysis_id": first_analysis_id,
            },
        )
        connection.execute(
            text(
                "INSERT INTO snapshot_source_versions ("
                "snapshot_id, owner_user_id, source_id, source_version_id, company_id"
                ") VALUES ("
                ":snapshot_id, :owner_user_id, :source_id, :source_version_id, :company_id"
                ")"
            ),
            {
                "snapshot_id": snapshot.id,
                "owner_user_id": owner.id,
                "source_id": source_a_id,
                "source_version_id": source_version_a_id,
                "company_id": company_a.id,
            },
        )

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO snapshot_question_analyses ("
                        "snapshot_id, owner_user_id, project_id, project_version_id, question_analysis_id"
                        ") VALUES ("
                        ":snapshot_id, :owner_user_id, :project_id, :project_version_id, :analysis_id"
                        ")"
                    ),
                    {
                        "snapshot_id": snapshot.id,
                        "owner_user_id": owner.id,
                        "project_id": first_project.id,
                        "project_version_id": first_project.current_version_id,
                        "analysis_id": second_analysis_id,
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
                        "INSERT INTO snapshot_source_versions ("
                        "snapshot_id, owner_user_id, source_id, source_version_id, company_id"
                        ") VALUES ("
                        ":snapshot_id, :owner_user_id, :source_id, :source_version_id, :company_id"
                        ")"
                    ),
                    {
                        "snapshot_id": snapshot.id,
                        "owner_user_id": owner.id,
                        "source_id": source_b_id,
                        "source_version_id": source_version_b_id,
                        "company_id": company_b_id,
                    },
                )
        finally:
            transaction.rollback()


def test_private_claim_analysis_tables_keep_owner_rls_and_model_execution_requires_one_parent(
    migrated_engine: Engine,
) -> None:
    private_table_names = (
        "project_interpretations",
        "project_interpretation_versions",
        "project_interpretation_evidence_links",
        "project_interpretation_decisions",
        "user_interpretation_decisions",
        "question_analyses",
        "question_analysis_intents",
        "question_analysis_requirement_groups",
        "question_analysis_requirements",
        "snapshot_activity_versions",
        "snapshot_source_versions",
        "snapshot_question_analyses",
        "model_executions",
    )
    with migrated_engine.connect() as connection:
        rows = (
            connection.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity "
                    "FROM pg_class WHERE relname = ANY(:table_names)"
                ),
                {"table_names": list(private_table_names)},
            )
            .mappings()
            .all()
        )

    assert {row["relname"] for row in rows} == set(private_table_names)
    assert all(row["relrowsecurity"] and row["relforcerowsecurity"] for row in rows)

    with migrated_engine.connect() as connection:
        transaction = connection.begin()
        try:
            with pytest.raises(IntegrityError):
                connection.execute(
                    text(
                        "INSERT INTO model_executions ("
                        "id, owner_user_id, execution_kind, model_provider, model_identifier, "
                        "input_fingerprint, result_status, started_at"
                        ") VALUES ("
                        ":id, :owner_user_id, 'QUESTION_ANALYSIS', 'test', 'test-v1', "
                        "'fingerprint', 'FAILED', now()"
                        ")"
                    ),
                    {"id": uuid4(), "owner_user_id": uuid4()},
                )
        finally:
            transaction.rollback()
