"""Add stable JobPosting identities and normalized posting sections.

Revision ID: 0002_job_posting
Revises: 0001_source_collection_core

Deployment requires fully draining and stopping legacy writers, waiting for
their open write transactions to finish, upgrading, deploying the new
dual-write application, checking JSON/normalized consistency, and only then
resuming traffic. This one-shot backfill is not safe as an online rolling
upgrade while legacy-only writers remain active.
"""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002_job_posting"
down_revision: str | None = "0001_source_collection_core"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "job_postings",
        sa.Column("job_posting_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("company_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("source_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["source_id", "company_id"],
            ["sources.source_id", "sources.company_id"],
            name="fk_job_postings_source_company",
        ),
        sa.PrimaryKeyConstraint("job_posting_id", name="pk_job_postings"),
        sa.UniqueConstraint("source_id", name="uq_job_postings_source_id"),
    )

    op.create_table(
        "posting_sections",
        sa.Column(
            "extraction_revision_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("section_key", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("heading_raw", sa.Text(), nullable=True),
        sa.Column("text_raw", sa.Text(), nullable=False),
        sa.Column("section_order", sa.Integer(), nullable=False),
        sa.Column("relation_text", sa.Text(), nullable=True),
        sa.CheckConstraint(
            "heading_raw IS NULL OR length(heading_raw) > 0",
            name="ck_posting_sections_nonempty_heading_raw",
        ),
        sa.CheckConstraint(
            "kind IN ('title', 'role', 'organization', 'duties', 'required', "
            "'preferred', 'general', 'location', 'employment_type', 'published', "
            "'deadline')",
            name="ck_posting_sections_valid_kind",
        ),
        sa.CheckConstraint(
            "length(section_key) > 0",
            name="ck_posting_sections_nonempty_section_key",
        ),
        sa.CheckConstraint(
            "length(text_raw) > 0",
            name="ck_posting_sections_nonempty_text_raw",
        ),
        sa.CheckConstraint(
            "section_order >= 0",
            name="ck_posting_sections_nonnegative_section_order",
        ),
        sa.CheckConstraint(
            "relation_text IS NULL OR length(relation_text) > 0",
            name="ck_posting_sections_nonempty_relation_text",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_revision_id"],
            ["extraction_revisions.extraction_revision_id"],
            name="fk_posting_sections_extraction_revision_id",
        ),
        sa.PrimaryKeyConstraint(
            "extraction_revision_id",
            "section_key",
            name="pk_posting_sections",
        ),
        sa.UniqueConstraint(
            "extraction_revision_id",
            "section_order",
            name="uq_posting_sections_revision_order",
        ),
    )

    op.create_table(
        "posting_section_evidence",
        sa.Column(
            "extraction_revision_id",
            postgresql.UUID(as_uuid=True),
            nullable=False,
        ),
        sa.Column("evidence_id", postgresql.UUID(as_uuid=True), nullable=False),
        sa.Column("section_key", sa.Text(), nullable=False),
        sa.Column("evidence_order", sa.Integer(), nullable=False),
        sa.CheckConstraint(
            "evidence_order >= 0",
            name="ck_posting_section_evidence_nonnegative_evidence_order",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_revision_id", "evidence_id"],
            [
                "extraction_revision_evidence.extraction_revision_id",
                "extraction_revision_evidence.evidence_id",
            ],
            name="fk_posting_section_evidence_revision_evidence",
        ),
        sa.ForeignKeyConstraint(
            ["extraction_revision_id", "section_key"],
            ["posting_sections.extraction_revision_id", "posting_sections.section_key"],
            name="fk_posting_section_evidence_section",
        ),
        sa.PrimaryKeyConstraint(
            "extraction_revision_id",
            "evidence_id",
            "section_key",
            name="pk_posting_section_evidence",
        ),
        sa.UniqueConstraint(
            "extraction_revision_id",
            "section_key",
            "evidence_order",
            name="uq_posting_section_evidence_section_order",
        ),
    )

    op.execute(
        sa.text(
            """
            DO $migration$
            BEGIN
                IF EXISTS (
                    SELECT 1
                    FROM extraction_revisions
                    WHERE jsonb_typeof(posting_sections) IS DISTINCT FROM 'array'
                ) THEN
                    RAISE EXCEPTION
                        'legacy extraction_revisions.posting_sections must be a JSON array';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM extraction_revisions AS revision
                    CROSS JOIN LATERAL jsonb_array_elements(revision.posting_sections)
                        AS section(section_value)
                    WHERE jsonb_typeof(section.section_value) IS DISTINCT FROM 'object'
                       OR NOT (
                           section.section_value ?& ARRAY[
                               'section_key', 'kind', 'heading_raw', 'text_raw',
                               'evidence_ids', 'order', 'relation_text'
                           ]
                       )
                       OR section.section_value - ARRAY[
                           'section_key', 'kind', 'heading_raw', 'text_raw',
                           'evidence_ids', 'order', 'relation_text'
                       ] <> '{}'::jsonb
                       OR jsonb_typeof(section.section_value -> 'section_key')
                           IS DISTINCT FROM 'string'
                       OR length(section.section_value ->> 'section_key') = 0
                       OR jsonb_typeof(section.section_value -> 'kind')
                           IS DISTINCT FROM 'string'
                       OR section.section_value ->> 'kind' NOT IN (
                           'title', 'role', 'organization', 'duties', 'required',
                           'preferred', 'general', 'location', 'employment_type',
                           'published', 'deadline'
                       )
                       OR jsonb_typeof(section.section_value -> 'heading_raw')
                           NOT IN ('string', 'null')
                       OR (
                           jsonb_typeof(section.section_value -> 'heading_raw') = 'string'
                           AND length(section.section_value ->> 'heading_raw') = 0
                       )
                       OR jsonb_typeof(section.section_value -> 'text_raw')
                           IS DISTINCT FROM 'string'
                       OR length(section.section_value ->> 'text_raw') = 0
                       OR jsonb_typeof(section.section_value -> 'evidence_ids')
                           IS DISTINCT FROM 'array'
                       OR jsonb_array_length(section.section_value -> 'evidence_ids') = 0
                       OR jsonb_typeof(section.section_value -> 'order')
                           IS DISTINCT FROM 'number'
                       OR section.section_value ->> 'order' !~ '^[0-9]+$'
                       OR jsonb_typeof(section.section_value -> 'relation_text')
                           NOT IN ('string', 'null')
                       OR (
                           jsonb_typeof(section.section_value -> 'relation_text') = 'string'
                           AND length(section.section_value ->> 'relation_text') = 0
                       )
                ) THEN
                    RAISE EXCEPTION 'legacy posting section payload is malformed';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM extraction_revisions AS revision
                    CROSS JOIN LATERAL jsonb_array_elements(revision.posting_sections)
                        WITH ORDINALITY AS section(section_value, payload_order)
                    WHERE (section.section_value ->> 'order')::integer
                        <> section.payload_order - 1
                ) THEN
                    RAISE EXCEPTION
                        'legacy posting section order must be contiguous and match payload order';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM extraction_revisions AS revision
                    CROSS JOIN LATERAL jsonb_array_elements(revision.posting_sections)
                        AS section(section_value)
                    GROUP BY
                        revision.extraction_revision_id,
                        section.section_value ->> 'section_key'
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION 'legacy posting section keys must be unique';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM extraction_revisions AS revision
                    CROSS JOIN LATERAL jsonb_array_elements(revision.posting_sections)
                        AS section(section_value)
                    CROSS JOIN LATERAL jsonb_array_elements(section.section_value -> 'evidence_ids')
                        AS evidence(evidence_value)
                    WHERE jsonb_typeof(evidence.evidence_value) IS DISTINCT FROM 'string'
                ) THEN
                    RAISE EXCEPTION 'legacy posting section evidence ids must be UUID strings';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM extraction_revisions AS revision
                    CROSS JOIN LATERAL jsonb_array_elements(revision.posting_sections)
                        AS section(section_value)
                    CROSS JOIN LATERAL jsonb_array_elements_text(
                        section.section_value -> 'evidence_ids'
                    ) AS evidence(evidence_id)
                    GROUP BY
                        revision.extraction_revision_id,
                        section.section_value ->> 'section_key',
                        evidence.evidence_id
                    HAVING count(*) > 1
                ) THEN
                    RAISE EXCEPTION 'legacy posting section evidence ids must be unique';
                END IF;

                IF EXISTS (
                    SELECT 1
                    FROM extraction_revisions AS revision
                    CROSS JOIN LATERAL jsonb_array_elements(revision.posting_sections)
                        AS section(section_value)
                    CROSS JOIN LATERAL jsonb_array_elements_text(
                        section.section_value -> 'evidence_ids'
                    ) AS evidence(evidence_id)
                    LEFT JOIN extraction_revision_evidence AS revision_evidence
                        ON revision_evidence.extraction_revision_id =
                            revision.extraction_revision_id
                       AND revision_evidence.evidence_id = evidence.evidence_id::uuid
                    WHERE revision_evidence.evidence_id IS NULL
                ) THEN
                    RAISE EXCEPTION
                        'legacy posting section evidence must belong to its extraction revision';
                END IF;
            END
            $migration$;
            """
        )
    )

    op.execute(
        sa.text(
            """
            INSERT INTO job_postings (job_posting_id, company_id, source_id)
            SELECT source_id, company_id, source_id
            FROM sources
            WHERE source_type = 'job_posting'
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO posting_sections (
                extraction_revision_id,
                section_key,
                kind,
                heading_raw,
                text_raw,
                section_order,
                relation_text
            )
            SELECT
                revision.extraction_revision_id,
                section.section_value ->> 'section_key',
                section.section_value ->> 'kind',
                section.section_value ->> 'heading_raw',
                section.section_value ->> 'text_raw',
                (section.section_value ->> 'order')::integer,
                section.section_value ->> 'relation_text'
            FROM extraction_revisions AS revision
            CROSS JOIN LATERAL jsonb_array_elements(revision.posting_sections)
                AS section(section_value)
            """
        )
    )
    op.execute(
        sa.text(
            """
            INSERT INTO posting_section_evidence (
                extraction_revision_id,
                evidence_id,
                section_key,
                evidence_order
            )
            SELECT
                revision.extraction_revision_id,
                evidence.evidence_id::uuid,
                section.section_value ->> 'section_key',
                evidence.evidence_order - 1
            FROM extraction_revisions AS revision
            CROSS JOIN LATERAL jsonb_array_elements(revision.posting_sections)
                AS section(section_value)
            CROSS JOIN LATERAL jsonb_array_elements_text(
                section.section_value -> 'evidence_ids'
            ) WITH ORDINALITY AS evidence(evidence_id, evidence_order)
            """
        )
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive downgrade is intentionally unsupported; use a forward corrective migration."
    )
