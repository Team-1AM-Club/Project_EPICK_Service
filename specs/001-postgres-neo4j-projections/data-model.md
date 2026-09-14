# Data Model: PostgreSQL Authoritative Foundation

This is a phased relationship map, not replacement DDL. Exact column type, nullability, check,
index, foreign-key name, and delete behavior remain governed by EPICK_DB_AGENT_v1.3.md v1.5.

## Universal constraints

- PostgreSQL is authoritative. Graph/Vector records cannot be source-of-truth rows.
- Personal rows carry owner_user_id where practical and use owner-bearing composite FKs plus RLS.
- Stable records point to immutable Version records; a current pointer may reference only a Version
  of that same stable record.
- Snapshots preserve typed Version links and never automatically advance.
- JSON is restricted to allowlisted, versioned protocol metadata. It is not a replacement for a
  relation and never stores personal text, Prompt/Response, secret, token, or vector.

## Existing baseline: identity and Experience

| Area | Tables | Integrity requirement |
|---|---|---|
| Identity | users, auth_identities, auth_sessions, idempotency_records | One external issuer/subject maps to one internal user; token hashes only; idempotency is owner/method/path/key scoped. users carry a monotonic deletion epoch. |
| Experience | activities, activity_versions, episodes, episode_versions, episode_version_skills, experience_field_provenance | Version rows are append-only; Episode Version is constrained to its own Activity and Activity Version; field availability and provenance are preserved. |

## PG-1: workspace and Job acceptance

| Area | Tables | Integrity requirement |
|---|---|---|
| Workspace | companies, application_projects, application_project_versions, project_questions, question_versions | Project and Question versions remain owner/project scoped; current pointers use deferred composite FKs. |
| Job | jobs, job_input_refs, job_required_actions, job_commands | Job inputs are typed and exactly constrained; status is separate from completeness, action, and dispatch state; Command has a current fence and owner deletion epoch. |
| Delivery | outbox_messages, inbox_receipts | Domain mutation and Outbox insert are one transaction; public/private visibility has different payload constraints; consumer receipt is unique by consumer and event. |
| Execution | owner_execution_slots, job_execution_leases | Three owner slots, one active lease per Job and slot, row-lock claim, cancellation slot retention, fence/epoch validation. |

## PG-2: Snapshot, recommendation, and selection

| Tables | Integrity requirement |
|---|---|
| project_snapshots, snapshot_episode_versions | Snapshot fixes the Project Version and owned Episode Versions used at creation. |
| recommendation_runs, recommendation_candidates | Run Question, Snapshot, Project, owner, and Candidate Episode Version must form one valid scope. |
| material_selection_sets, material_selection_items | One current ordered selection set per Question; it refers only to Candidates from the valid Run/Snapshot. |

## PG-3: public knowledge and evidence

| Revision area | Tables/relations | Integrity requirement |
|---|---|---|
| Company and Source | aliases, identifiers, relations, interests; sources, source_versions, evidence_spans, source_collection_attempts; job_source_links | Source and evidence retain company/version lineage; collection attempt is append-only; private Job correlation stays private. |
| Posting and requirement | canonical skills, aliases, postings/versions, requirement groups/requirements/skills | Company and Version scope is relationally checked; original requirement and evidence are retained. |
| Claim and interpretation | claims/versions/evidence; public interpretations; private project interpretations and decisions | Public and private facts are physically distinct; no private experience context is copied into public knowledge. |
| Analysis and snapshot evidence | question analyses/intents, model executions, snapshot source/activity/question-analysis links | One snapshot stores the actual evidence/analysis versions it used. |

## PG-4: projection readiness and exclusions

| Tables | Integrity requirement |
|---|---|
| projection_sync_states | One state per resource/version/projection type, monotonic revision behavior, safe PENDING/STALE/ERROR/SYNCED lifecycle. |
| experience_exclusions, snapshot_exclusions | An exclusion targets exactly one owner resource and is fixed into a Snapshot only while active. |

This stage intentionally has no W3 typed ACK table, W3 usability enum, or W4 permission row.

## PG-5: lifecycle and deletion

| Area | Tables | Integrity requirement |
|---|---|---|
| Decisions | inference suggestions/decisions, duplicate suggestion/decision/merge record | A suggestion cannot overwrite an Experience fact or perform a merge without a user decision. |
| Recovery | job_checkpoints, notifications | Checkpoint is tied to typed input versions, fence, epoch, and a safe reference. |
| Preferences/privacy | settings, preferences, consents, retention, feedback, analytics, sensitivity records | Project override and snapshot effective value are distinct; sensitivity controls external processing. |
| Deletion | deletion_requests, deletion_targets | Completion requires every private storage target's acknowledgement at the deletion epoch; shared company data is outside the private target set. |
