-- Execute after `alembic upgrade head` as the migration-table owner.
--
-- Group-role mapping:
--   epick_runtime: interactive API process
--   epick_worker:  queue worker, outbox relay, and projection delivery
--   epick_deleter: deletion orchestration process
--
-- The script is intentionally deny-by-default for future tables. Every schema
-- migration that introduces a table consumed by one of these processes must
-- extend the relevant list below and this script must be re-applied in the
-- same release. Do not grant schema CREATE to runtime groups.

REVOKE ALL PRIVILEGES ON ALL TABLES IN SCHEMA public
    FROM epick_runtime, epick_worker, epick_lookup, epick_w3_authority, epick_w4_context, epick_deleter;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public
    FROM epick_runtime, epick_worker, epick_lookup, epick_w3_authority, epick_w4_context, epick_deleter;

-- Interactive API: owner-facing records and user commands. Canonical company,
-- Source, Claim, and indexing data remain read-only to the API.
GRANT SELECT, INSERT, UPDATE ON TABLE
    users,
    auth_identities,
    auth_sessions,
    idempotency_records,
    activities,
    episodes,
    activity_versions,
    episode_versions,
    episode_version_skills,
    experience_field_provenance,
    application_projects,
    application_project_versions,
    project_questions,
    question_versions,
    jobs,
    job_required_actions,
    job_commands,
    w2_commit_operations,
    outbox_messages,
    experience_exclusions,
    snapshot_exclusions,
    project_snapshots,
    snapshot_episode_versions,
    recommendation_runs,
    recommendation_execution_bindings,
    recommendation_execution_episodes,
    material_selection_sets,
    material_selection_items,
    inference_decisions,
    experience_duplicate_decisions,
    project_interpretation_decisions,
    user_interpretation_decisions,
    user_settings,
    recommendation_preferences,
    project_recommendation_preferences,
    snapshot_recommendation_preferences,
    consents,
    retention_preferences,
    feedback,
    analytics_events,
    notifications,
    deletion_requests,
    deletion_targets
TO epick_runtime;

GRANT SELECT ON TABLE
    companies,
    company_aliases,
    company_identifiers,
    company_interests,
    sources,
    source_versions,
    evidence_spans,
    source_relations,
    company_relations,
    org_units,
    org_unit_versions,
    roles,
    role_versions,
    canonical_skills,
    skill_aliases,
    job_postings,
    job_posting_versions,
    requirement_groups,
    requirements,
    requirement_skills,
    claims,
    claim_versions,
    claim_evidence_links,
    claim_relations,
    interpretations,
    interpretation_versions,
    interpretation_evidence_links,
    project_interpretations,
    project_interpretation_versions,
    project_interpretation_evidence_links,
    question_intent_types,
    question_analyses,
    question_analysis_intents,
    question_analysis_requirement_groups,
    question_analysis_requirements,
    snapshot_activity_versions,
    snapshot_source_versions,
    snapshot_question_analyses,
    recommendation_candidates,
    inference_suggestions,
    inference_suggestion_sources,
    experience_duplicate_suggestions
TO epick_runtime;

-- Worker-owned writes: execution ledger, Source ingestion, immutable
-- derivations, and projections. The worker can read the inputs it needs, but
-- does not receive interactive identity/settings mutation permissions.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO epick_worker;
-- PostgreSQL requires UPDATE privilege on at least one column when SELECT uses
-- a row-locking clause.  W1 locks the owner row while validating account status
-- and deletion_epoch, but never mutates identity state.  Limit the capability
-- to the non-authoritative timestamp column instead of granting table UPDATE.
GRANT UPDATE (updated_at) ON TABLE users TO epick_worker;
-- W4 acceptance locks the mutable Project/Question rows and their immutable
-- version rows before its final currentness check. PostgreSQL requires an
-- UPDATE-capable column for SELECT ... FOR UPDATE even though W1 never mutates
-- these rows on this path. Keep that capability on non-authoritative audit
-- timestamps; current bindings, status, company, prompt, and IDs remain denied.
GRANT UPDATE (updated_at) ON TABLE application_projects TO epick_worker;
GRANT UPDATE (created_at) ON TABLE application_project_versions TO epick_worker;
GRANT UPDATE (updated_at) ON TABLE project_questions TO epick_worker;
GRANT UPDATE (created_at) ON TABLE question_versions TO epick_worker;
GRANT SELECT, INSERT, UPDATE ON TABLE
    jobs,
    job_input_refs,
    job_required_actions,
    job_commands,
    w2_commit_operations,
    w2_staged_results,
    outbox_messages,
    owner_execution_slots,
    job_execution_leases,
    job_checkpoints,
    notifications,
    company_aliases,
    company_identifiers,
    company_interests,
    sources,
    source_versions,
    evidence_spans,
    source_relations,
    job_source_links,
    analysis_source_decisions,
    company_relations,
    org_units,
    org_unit_versions,
    roles,
    role_versions,
    source_collection_attempts,
    canonical_skills,
    skill_aliases,
    job_postings,
    job_posting_versions,
    requirement_groups,
    requirements,
    requirement_skills,
    claims,
    claim_versions,
    claim_evidence_links,
    claim_relations,
    interpretations,
    interpretation_versions,
    interpretation_evidence_links,
    project_interpretations,
    project_interpretation_versions,
    project_interpretation_evidence_links,
    question_analyses,
    question_analysis_intents,
    question_analysis_requirement_groups,
    question_analysis_requirements,
    snapshot_activity_versions,
    snapshot_source_versions,
    snapshot_question_analyses,
    model_executions,
    project_snapshots,
    snapshot_episode_versions,
    recommendation_runs,
    recommendation_execution_bindings,
    recommendation_execution_episodes,
    recommendation_publications,
    recommendation_source_dependencies,
    recommendation_candidates,
    material_selection_sets,
    material_selection_items,
    inference_suggestions,
    inference_suggestion_sources,
    experience_duplicate_suggestions,
    experience_merge_records,
    projection_sync_states
TO epick_worker;

-- The recommendation outbox relay locks the referenced run before publishing
-- its W4 dispatch, and the private W4 adapter later finalizes that same run.
-- The original owner-only policy hides the row when no interactive
-- app.current_user_id is set, so grant the operational worker an explicit
-- all-row policy. Keep this idempotent because this manifest is deliberately
-- re-applied after forward-only migrations in existing environments.
DO $$
BEGIN
    IF NOT EXISTS (
        SELECT 1
        FROM pg_policies
        WHERE schemaname = 'public'
          AND tablename = 'recommendation_runs'
          AND policyname = 'recommendation_runs_worker_execution_policy'
    ) THEN
        CREATE POLICY recommendation_runs_worker_execution_policy
        ON recommendation_runs
        FOR ALL TO epick_worker
        USING (true)
        WITH CHECK (true);
    END IF;
END
$$;

-- Inbound delivery receipts are append-first. Existing workers may finalize
-- only the outcome column after a durable transaction; digest/provenance are
-- immutable. Core Decision bindings are append-only audit pins.  Revision 028
-- adds W4 producer/scope/question/origin columns to that same table; the
-- existing table-level SELECT+INSERT grant is intentionally sufficient and
-- does not grant UPDATE or DELETE on any W3/W4 identity field.
GRANT SELECT, INSERT ON TABLE inbox_receipts TO epick_worker;
GRANT UPDATE (outcome_code) ON TABLE inbox_receipts TO epick_worker;
GRANT SELECT, INSERT ON TABLE job_core_decision_bindings TO epick_worker;

-- W1 issues opaque synthetic CT-12 context handles; W4 has no direct database
-- connection and can only resolve a handle through its separately authenticated adapter.
GRANT SELECT, INSERT, UPDATE ON TABLE w4_question_core_contexts TO epick_worker;

-- The interactive API reads the current owner-scoped binding only when an
-- explicit user retry creates the next execution fence. Owner RLS still
-- requires app.current_user_id and no mutation privilege is granted.
GRANT SELECT ON TABLE job_core_decision_bindings TO epick_runtime;

-- The lookup adapter has a separate, read-only login.  Its route is protected by
-- a W2 service principal and still verifies command/fence/epoch before emitting
-- a semantic result; the interactive API process never inherits this role.
GRANT SELECT (id, deletion_epoch, account_status)
ON TABLE users
TO epick_lookup;

GRANT SELECT (
    id,
    owner_user_id,
    status,
    execution_fence,
    owner_deletion_epoch,
    project_id,
    analysis_input_version,
    active_lease_id
)
ON TABLE jobs
TO epick_lookup;

GRANT SELECT (
    id,
    job_id,
    owner_user_id,
    command_type,
    command_schema_version,
    command_sequence,
    execution_fence,
    owner_deletion_epoch,
    analysis_input_version,
    analysis_source_decision_id,
    payload,
    status,
    created_at,
    consumed_at
)
ON TABLE job_commands
TO epick_lookup;

-- QUESTION_MATCHING decisions deliberately retain a null company pin.  The lookup adapter may
-- resolve the W2 company only through these current, owner-scoped relation columns.  This is not
-- a general table grant: no prompt, URL, content, mutation, or unrelated projection is exposed.
GRANT SELECT (id, owner_user_id, current_version_id)
ON TABLE application_projects
TO epick_lookup;

GRANT SELECT (id, project_id, owner_user_id, company_id)
ON TABLE application_project_versions
TO epick_lookup;

GRANT SELECT (id, owner_user_id, project_id, current_version_id, status)
ON TABLE project_questions
TO epick_lookup;

GRANT SELECT (id, question_id, project_id, owner_user_id)
ON TABLE question_versions
TO epick_lookup;

GRANT SELECT (id, job_id, owner_user_id, source_id, command_id, purpose_ref, analysis_input_version)
ON TABLE job_source_links
TO epick_lookup;

GRANT SELECT (id, company_id)
ON TABLE sources
TO epick_lookup;

GRANT SELECT (
    id,
    decision_scope,
    company_id,
    question_version_id,
    source_id,
    analysis_input_version,
    decision_version,
    decision_code,
    decision_owner,
    reason_code
)
ON TABLE analysis_source_decisions
TO epick_lookup;

GRANT SELECT (
    id,
    job_id,
    owner_user_id,
    owner_deletion_epoch,
    analysis_source_decision_id,
    origin_producer,
    origin_message_id,
    decision_scope,
    question_version_id,
    source_id,
    analysis_input_version,
    decision_version,
    decision_code
)
ON TABLE job_core_decision_bindings
TO epick_lookup;

-- W3 Authority has its own read-only login. It can derive only the current
-- Authorization projection and cannot read identity attributes, Source URLs,
-- command payloads, content, or any mutable column.
GRANT SELECT (id, account_status, deletion_epoch)
ON TABLE users
TO epick_w3_authority;

GRANT SELECT (id, owner_user_id, status, owner_deletion_epoch, analysis_input_version)
ON TABLE jobs
TO epick_w3_authority;

GRANT SELECT (job_id, owner_user_id, source_id, analysis_input_version)
ON TABLE job_source_links
TO epick_w3_authority;

GRANT SELECT (id, company_id)
ON TABLE sources
TO epick_w3_authority;

-- The W4 context adapter uses its own read-only login.  These projections are the minimal
-- data needed to calculate an opaque authorization revision and fail closed before W4 sends.
-- They intentionally exclude prompt text, company identifiers, source URLs/content and all
-- mutation privileges.  `w4_question_core_contexts.context_key` is W1-issued and the adapter
-- uses it as the only caller-supplied database reference.
GRANT SELECT (
    context_key,
    job_id,
    owner_user_id,
    question_version_id,
    source_id,
    analysis_input_version,
    execution_fence,
    owner_deletion_epoch,
    data_kind,
    expires_at,
    revoked_at,
    created_at
)
ON TABLE w4_question_core_contexts
TO epick_w4_context;

GRANT SELECT (id, account_status, deletion_epoch, deleted_at)
ON TABLE users
TO epick_w4_context;

GRANT SELECT (
    id,
    owner_user_id,
    project_id,
    status,
    execution_fence,
    owner_deletion_epoch,
    analysis_input_version,
    active_lease_id
)
ON TABLE jobs
TO epick_w4_context;

GRANT SELECT (id, owner_user_id, current_version_id)
ON TABLE application_projects
TO epick_w4_context;

GRANT SELECT (id, project_id, owner_user_id, company_id)
ON TABLE application_project_versions
TO epick_w4_context;

GRANT SELECT (id, owner_user_id, project_id, current_version_id, status)
ON TABLE project_questions
TO epick_w4_context;

GRANT SELECT (id, question_id, project_id, owner_user_id)
ON TABLE question_versions
TO epick_w4_context;

GRANT SELECT (id, job_id, owner_user_id, source_id, analysis_input_version)
ON TABLE job_source_links
TO epick_w4_context;

GRANT SELECT (id, company_id)
ON TABLE sources
TO epick_w4_context;

GRANT SELECT (
    id,
    job_id,
    owner_user_id,
    action_code,
    action_status,
    expected_input_version,
    resolved_at
)
ON TABLE job_required_actions
TO epick_w4_context;

GRANT SELECT (
    job_id,
    source_id,
    origin_producer,
    decision_scope,
    question_version_id,
    analysis_input_version,
    decision_version
)
ON TABLE job_core_decision_bindings
TO epick_w4_context;

-- The deleter advances deletion state and may remove owner-scoped data. It is
-- deliberately denied mutation of canonical Source/knowledge tables.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO epick_deleter;
GRANT SELECT, INSERT, UPDATE ON TABLE
    deletion_requests,
    deletion_targets,
    jobs,
    job_required_actions,
    job_commands,
    w2_commit_operations,
    w2_staged_results,
    outbox_messages,
    owner_execution_slots,
    job_execution_leases,
    job_checkpoints,
    notifications
TO epick_deleter;

GRANT DELETE ON TABLE
    users,
    auth_identities,
    auth_sessions,
    idempotency_records,
    activities,
    episodes,
    activity_versions,
    episode_versions,
    episode_version_skills,
    experience_field_provenance,
    application_projects,
    application_project_versions,
    project_questions,
    question_versions,
    jobs,
    job_input_refs,
    job_required_actions,
    job_commands,
    project_snapshots,
    snapshot_episode_versions,
    snapshot_activity_versions,
    snapshot_source_versions,
    snapshot_question_analyses,
    recommendation_runs,
    recommendation_candidates,
    material_selection_sets,
    material_selection_items,
    experience_exclusions,
    snapshot_exclusions,
    inference_suggestions,
    inference_suggestion_sources,
    inference_decisions,
    experience_duplicate_suggestions,
    experience_duplicate_decisions,
    experience_merge_records,
    project_interpretations,
    project_interpretation_versions,
    project_interpretation_evidence_links,
    project_interpretation_decisions,
    user_interpretation_decisions,
    question_analyses,
    question_analysis_intents,
    question_analysis_requirement_groups,
    question_analysis_requirements,
    model_executions,
    user_settings,
    recommendation_preferences,
    project_recommendation_preferences,
    snapshot_recommendation_preferences,
    consents,
    retention_preferences,
    feedback,
    analytics_events,
    notifications
TO epick_deleter;

GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public
    TO epick_runtime, epick_worker, epick_deleter;

-- These default-privilege revocations apply only to objects subsequently
-- created by the principal executing this script (the migration login). They
-- guarantee that a future table gets no runtime DML until it is reviewed here.
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON TABLES FROM epick_runtime, epick_worker, epick_lookup, epick_w3_authority, epick_w4_context, epick_deleter;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM epick_runtime, epick_worker, epick_lookup, epick_w3_authority, epick_w4_context, epick_deleter;
