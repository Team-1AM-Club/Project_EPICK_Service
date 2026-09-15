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
    FROM epick_runtime, epick_worker, epick_deleter;
REVOKE ALL PRIVILEGES ON ALL SEQUENCES IN SCHEMA public
    FROM epick_runtime, epick_worker, epick_deleter;

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
    experience_exclusions,
    snapshot_exclusions,
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
    deletion_requests
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
    project_snapshots,
    snapshot_episode_versions,
    snapshot_activity_versions,
    snapshot_source_versions,
    snapshot_question_analyses,
    recommendation_runs,
    recommendation_candidates,
    material_selection_sets,
    material_selection_items,
    inference_suggestions,
    inference_suggestion_sources,
    experience_duplicate_suggestions
TO epick_runtime;

-- Worker-owned writes: execution ledger, Source ingestion, immutable
-- derivations, and projections. The worker can read the inputs it needs, but
-- does not receive interactive identity/settings mutation permissions.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO epick_worker;
GRANT SELECT, INSERT, UPDATE ON TABLE
    jobs,
    job_input_refs,
    job_required_actions,
    job_commands,
    outbox_messages,
    inbox_receipts,
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
    recommendation_candidates,
    material_selection_sets,
    material_selection_items,
    inference_suggestions,
    inference_suggestion_sources,
    experience_duplicate_suggestions,
    experience_merge_records,
    projection_sync_states
TO epick_worker;

-- The deleter advances deletion state and may remove owner-scoped data. It is
-- deliberately denied mutation of canonical Source/knowledge tables.
GRANT SELECT ON ALL TABLES IN SCHEMA public TO epick_deleter;
GRANT SELECT, INSERT, UPDATE ON TABLE
    deletion_requests,
    deletion_targets,
    jobs,
    job_required_actions,
    job_commands,
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
    REVOKE ALL ON TABLES FROM epick_runtime, epick_worker, epick_deleter;
ALTER DEFAULT PRIVILEGES IN SCHEMA public
    REVOKE ALL ON SEQUENCES FROM epick_runtime, epick_worker, epick_deleter;
