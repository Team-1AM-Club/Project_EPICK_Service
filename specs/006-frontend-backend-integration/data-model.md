# Data Model: Frontend–W1 Backend Integration

## Scope and authority

PostgreSQL remains authoritative. This feature reuses existing user, workspace, Job, recommendation, selection, and deletion tables. The only new state is authentication protocol/configuration and frontend cache state; no Neo4j or W2~W4 private entity becomes browser-visible.

## 1. User *(existing)*

**Purpose**: Internal owner of all personal EPICK data.

**Relevant fields**:

- `id`: internal UUID; never accepted from browser payloads as authority.
- `display_name`, `email`, `locale`, `timezone`.
- `account_status`: `ACTIVE | SUSPENDED | DELETION_PENDING | DELETED`.
- `deletion_epoch`: current owner lifecycle fence.
- `created_at`, `updated_at`, `deleted_at`.

**Rules**:

- All owner-scoped repository work runs with PostgreSQL RLS owner context.
- `DELETION_PENDING` and `DELETED` users cannot establish or refresh sessions.

## 2. AuthIdentity *(existing)*

**Purpose**: Stable mapping from a verified Google identity to one internal User.

**Relevant fields**:

- `user_id`.
- `provider`: fixed to `google` for this feature.
- `provider_subject`: verified OIDC `sub`.
- `provider_email`, `provider_email_verified`.
- `created_at`, `last_login_at`.

**Constraints**:

- Unique `(provider, provider_subject)`.
- Email is profile metadata, never an account-merge key.
- New identity and User creation occur in one PostgreSQL transaction.

## 3. AuthSession *(existing)*

**Purpose**: Server-revocable EPICK refresh session after successful Google login.

**Relevant fields**:

- `id`, `user_id`, `auth_identity_id`.
- `refresh_token_hash`: peppered digest of an opaque refresh token; raw token exists only in the HttpOnly cookie.
- `token_family_id`: groups rotations and enables replay-family revocation.
- `issued_at`, `expires_at`, `last_used_at`, `rotated_at`.
- `revoked_at`, `revoke_reason`, `replaced_by_session_id`.
- `created_ip_hash`, `user_agent_summary`: bounded, non-identifying security metadata.

**Validation**:

- `expires_at > issued_at`.
- Refresh is accepted only when hash, session, user status, expiry, rotation lineage, and revocation state are current.
- A used refresh token is rotated once; detected reuse revokes the token family.
- Logout and account deletion revoke the session transactionally.

**State transitions**:

```text
ACTIVE
  ├─ refresh ──> ROTATED ──> replacement ACTIVE
  ├─ logout ───> REVOKED(LOGOUT)
  ├─ expiry ───> EXPIRED
  ├─ replay ───> FAMILY_REVOKED(REUSE_DETECTED)
  └─ deletion ─> REVOKED(DELETION_PENDING)
```

`ACTIVE`, `ROTATED`, and `EXPIRED` are derived from timestamps/links; existing persisted revocation fields remain authoritative.

## 4. OAuth Login Transaction *(transient)*

**Purpose**: Bind one browser login attempt to Google callback without adding a durable user row before authentication.

**Fields in a short-lived signed/encrypted HttpOnly cookie**:

- random `state` digest.
- OIDC `nonce`.
- PKCE `code_verifier`.
- validated relative `return_to` path.
- `issued_at`, `expires_at` (maximum 10 minutes).

**Rules**:

- Single use; callback clears it on success or failure.
- No Google client secret, authorization code, access token, or user profile is stored in it.
- Callback requires exact state and redirect URI match.

## 5. EPICK Access Token *(transient)*

**Purpose**: Short-lived bearer used by the current W1 principal dependency.

**Claims**:

- `iss`: EPICK deployment issuer.
- `sub`: internal User UUID.
- `sid`: AuthSession UUID.
- `iat`, `exp` (default 15 minutes, deployment-configurable).
- `aud`: EPICK public API audience.
- `jti`: unique token ID.

**Rules**:

- Signed by deployment-provided key; never persisted in localStorage/sessionStorage.
- Backend verifies signature, issuer, audience, expiry, active session, and active account before establishing `CurrentPrincipal`.
- Frontend keeps it only in memory and performs at most one refresh/retry after a 401.

## 6. Workspace entities *(existing)*

### Activity and Episode

- Activity is the top-level experience record; Episodes are structured incidents within it.
- Current immutable versions remain the source for recommendation inputs.
- Draft/complete and field availability semantics remain unchanged.
- Delete uses the generic deletion workflow with an explicit scope; deletion must not silently rewrite historical results that are still legally retained, but deleted content cannot remain selectable.

### ApplicationProject and Question

- Project owns role/company/job-posting linkage and immutable project versions.
- Question owns immutable question versions and current selection relation.
- Project delete retains source Activities/Episodes and deletes or invalidates project-private snapshots, selections, checkpoints, and active Jobs within the approved scope.

## 7. Job Read Model *(existing public projection)*

**Public axes**:

- `status`: lifecycle (`QUEUED`, `RUNNING`, `WAITING_USER`, `PAUSED_RATE_LIMIT`, `SUCCEEDED`, `FAILED_RETRYABLE`, `FAILED_FINAL`, `CANCEL_REQUESTED`, `CANCELLED`).
- `dispatch_status`: transport progress (`OUTBOX_PENDING`, `ENQUEUED`, `CLAIMED`, `BLOCKED`, `INVALIDATED`).
- `completeness`: `none | partial | complete`.
- `stage`, `progress`, `required_actions`, `checkpoint`, `failure`, `limitations`.

**Rules**:

- These axes are not collapsed into one frontend enum.
- A W2 receipt, W3 decision/ACK, or W4 delivery alone does not imply Job success.
- Private owner, command, queue, lease, fence, deletion epoch, and worker fields are excluded.

## 8. Recommendation entities *(existing)*

### RecommendationRun

- Frozen to `project_id`, `question_id`, `question_version`, `snapshot_id`, and `snapshot_version`.
- Status and result status remain separate.
- `result_origin` distinguishes `SYNTHETIC` and `ENGINE`.

### RecommendationCandidate

- Carries `match_status`, reason, strength, limitation, validation status, and `result_version`.
- Can be selected only when its run/result is available and current under the existing service rules.

### MaterialSelection

- One current selection per question under current ownership/version rules.
- Save/replace/clear is idempotent and survives frontend reload.

## 8A. ENGINE Recommendation Execution *(private, Stage 4.5)*

These records are W1-owned PostgreSQL state. They are never returned as private transport DTOs to the browser; the browser continues to consume the existing `RecommendationRun` and `RecommendationCandidate` projections.

### RecommendationExecutionBinding

**Purpose**: Freeze one W4 execution attempt without holding a transaction open during model work.

**Relevant fields**:

- `run_id`, `owner_user_id`, `project_id`, `question_id`, `question_version_id`, `snapshot_id`.
- `lease_token`, `lease_expires_at`, `attempt_no`, `execution_status`.
- `owner_deletion_epoch`, `context_sha256`, `contract_version`, `engine_source_revision`.
- immutable Episode mapping from `(episode_id, episode_version)` to W1 `episode_version_id`.
- `created_at`, `acquired_at`, `published_at`, `failed_at`, safe `failure_code`.

**Rules**:

- Only an owner-scoped `ENGINE/PENDING` Run can be acquired.
- Acquire is a short atomic transition to `RUNNING`; identical completed work is idempotent, while another live lease cannot be stolen.
- The context hash covers the exact question version, snapshot/Episode versions, exclusions, policy revision, and company-knowledge/Source revisions.
- Cancellation, owner deletion epoch, access/consent withdrawal, lease expiry, or any bound revision change prevents publication.

### RecommendationPublication

**Purpose**: Preserve the validated W4 result that produced the public candidate projection.

**Relevant fields**:

- `run_id`, `owner_user_id`, `lease_token`, `result_version`.
- W4 schema/engine revision and bounded full result body.
- `input_data_kind`: initially only `SYNTHETIC`.
- `processing_status`, `limited_analysis`, limitation codes.
- created/published timestamps and content digest.

**Rules**:

- Full result, dependency rows, all candidates, and Run/result terminal state commit in one transaction.
- `result_origin=ENGINE` is assigned only inside a successful publication transaction.
- W4 candidate mapping is fixed: `DIRECT_MATCH → DIRECT_MATCH`, `PARTIAL_MATCH → PARTIAL_RELEVANCE`, `NEEDS_CONFIRMATION → NEEDS_VERIFICATION`.
- Initial accepted W4 publications remain `LIMITED`; schema validity is not promoted to `PASSED` quality.
- Failed ENGINE execution never invokes the synthetic adapter for the same Run.

### RecommendationSourceDependency

**Purpose**: Fence stored recommendation use against company-knowledge changes.

**Relevant fields**:

- `run_id`, `result_version`, W1-owned Source/SourceVersion reference.
- extraction/index representation and normalization revision where applicable.
- knowledge generation/restriction revision and dependency digest.

**Rules**:

- Dependencies are derived from the validated server context/result and cannot be supplied by the browser.
- Publication checks the current local Source epochs/revisions; reads/selections reject invalidated results.
- W4 cache invalidation alone does not make an already-persisted W1 result safe.

### ENGINE delivery state

- W1's existing private outbox is extended with a versioned recommendation execution message containing opaque identifiers and contract metadata only.
- SQS is at-least-once; duplicate messages resolve against the binding/lease and cannot create a second publication.
- W4 accesses context and publication only through authenticated private W1 HTTP. W4 receives no direct PostgreSQL credentials.
- Queue URL, receipt handle, lease token, context hash, epochs, raw W4 result, and Source dependencies remain private.

## 9. DeletionRequest and DeletionTarget *(existing, extended use)*

**Purpose**: Durable preview/confirmation/execution ledger for account, Activity, and Project deletion.

**Target types introduced to API/service mapping**:

- `ACCOUNT` / `ALL_PRIVATE_DATA` *(existing)*.
- `ACTIVITY` / `SOURCE_ONLY` or `INCLUDING_PERSONAL_SNAPSHOTS`.
- `APPLICATION_PROJECT` / `PROJECT_PRIVATE_SCOPE`.

**State transitions**:

```text
REQUESTED ──confirm──> CONFIRMED ──start──> RUNNING
    └─ttl──> EXPIRED                    ├─> COMPLETED
                                        ├─> PARTIALLY_COMPLETED
                                        └─> FAILED_RETRYABLE ──retry──> RUNNING
```

**Rules**:

- A one-time preview token is stored only as a hash.
- The frontend hides/quarantines the target after confirmation but does not announce completion until server state is terminal.
- Late Job/result writes remain rejected by existing fence/epoch/currentness checks.
- Public company knowledge is never deleted by a personal deletion request.

## 10. Company Evidence Read Model *(additive public projection)*

**Purpose**: Provide the data already represented by the company exploration UI without exposing internal Source or graph records.

**Fields**:

- company identity and identification status.
- sections containing safe summary or interpretation text.
- evidence references containing public source label, collected/published time, verification/availability state, and allowed excerpt.
- job postings and normalized requirement summaries.
- conflict groups with bounded, safe descriptions.
- result/update status and limitations.

**Rules**:

- Only W1-authorized public projections are returned.
- Restricted/deleted source content is excluded or redacted according to current restriction state.
- Internal graph IDs, embeddings, prompts, model reasoning, raw worker payloads, and private user context are excluded.
- AI interpretation approval means use/exclude, not factual verification.

## 11. Frontend server-state model *(client only)*

**Query keys**:

- current user/home/resume items.
- activities and activity detail/versions.
- projects, project detail, questions.
- Jobs by Job ID.
- recommendation run/candidates/selection.
- company catalog/detail/evidence/job postings.

**Client generation**:

- Increment on logout, account switch, and confirmed deletion.
- Responses started under an older generation cannot populate the new cache.

**No-persistence rule**:

- Access/refresh tokens, personal API payloads, candidate details, and deletion preview tokens are not placed in localStorage.
- Optional non-sensitive UI preferences may be persisted under a separate versioned key.
