# Data Model: W1–W3 Actual Runtime Integration

## 1. W3 Runtime Deployment

Deployment metadata, not authoritative user state.

| Field | Type | Rules |
| --- | --- | --- |
| `w3_implementation_sha` | 40-char git SHA | Must equal `3b23e0843a134fb341e6a256576ccf52fedbf4a8`; image source pin |
| `w3_receipt_head_sha` | 40-char git SHA | Documentation/readiness successor recorded separately; not substituted for image source |
| `image_repository` | string | Logical ECR repository; no credentials |
| `image_digest` | `sha256:<64 hex>` | Required before M1 closure; deploy by digest |
| `runtime_host_class` | enum | `PRIVATE_WORKER_EC2` only for this plan |
| `state_volume` | string | One local named volume, mounted at `/state` |
| `database_path` | string | `/state/core.db` only |
| `runtime_uid` | positive integer | Non-root |
| `rootfs_read_only` | boolean | Must be true |
| `w3_review_revision` | string/null | W3-D disposition; required for final M1 closure |

**Lifecycle**: `RECIPE_DRAFTED → LOCALLY_VERIFIED → REVIEWED → DIGEST_FIXED → DEPLOYED`.
It must not enter `DIGEST_FIXED` as production-ready without W3-D.

## 2. Authority Request

Ephemeral request from the W3 adapter to W1; never persisted as authoritative state.

| Field | Type | Rules |
| --- | --- | --- |
| `schema_version` | const | `w1.private.w3-authority-request.v1` |
| `job_id` | UUID | Required |
| `source_id` | UUID | Required |

Authentication comes from the private service boundary, not request fields. Owner ID, company ID
and deletion epoch are intentionally absent so callers cannot select an authorization context.

## 3. Authority Snapshot

Minimal W1-derived response matching W3's `Authorization` model.

| Field | Type | Rules |
| --- | --- | --- |
| `schema_version` | const | `w1.private.w3-authority-response.v1` |
| `context.job_id` | UUID | Equals request Job |
| `context.company_id` | UUID | Derived through current W1 binding |
| `context.source_id` | UUID | Equals request Source |
| `context.analysis_input_version` | 1–64 chars | Exact opaque current value |
| `owner_id` | UUID | Derived from Job; never caller supplied |
| `owner_epoch` | integer >= 0 | Current User deletion epoch |
| `active` | boolean | True only for current, non-cancelled, non-deleting context |

Error responses use the established private error shape and separate non-retryable
authentication/not-found/conflict from retryable infrastructure failure. The response contains no
prompt, Source body, canonical URL, token or database credential.

## 4. W3 Deletion Target / Dispatch

W1 PostgreSQL remains authoritative. Existing `deletion_requests`, `deletion_targets` and
`outbox_messages` are extended rather than adding an independent deletion workflow.

| Field | Type | Rules |
| --- | --- | --- |
| `store_type` | enum | Add `W3_CORE_RUNTIME` to the approved target set |
| `deletion_request_id` | UUID FK | Existing W1 request |
| `target_id` | UUID | Existing deletion target identity |
| `command_id` | UUID | Existing outbox message ID; idempotency identity |
| `owner_id` | UUID | Private payload, never log |
| `owner_deletion_epoch` | integer >= 1 | Must equal current confirmed W1 epoch |
| `attempt` | integer >= 1 | Existing aggregate revision/target attempts |

**State transitions**:

```text
QUEUED → DISPATCHED → ACKNOWLEDGED
   └──────────────→ FAILED_RETRYABLE → QUEUED
```

W3 `delete_owner` returning an idempotent already-deleted/current result maps to ACKNOWLEDGED. A
lower/stale epoch, adapter failure or unavailable runtime never advances the W1 target to complete.
The precise response mapping remains gated on W3-C review.

## 5. W3 Delivery State

W3-owned SQLite state described by the handed-off runtime; W1 does not migrate or write it directly.

| State | Meaning | Allowed next state |
| --- | --- | --- |
| `PENDING` | Event durably staged, not handed to transport | `TRANSPORT_HANDOFF`, `RETRY`, `HELD`, `DELETED` |
| `RETRY` | Prior bounded send failed; scheduled for same-body retry | `TRANSPORT_HANDOFF`, `RETRY`, `HELD`, `DELETED` |
| `TRANSPORT_HANDOFF` | SQS accepted body and MessageId | expired/redacted metadata state or `DELETED` |
| `HELD` | Automatic retry budget exhausted | explicit audited replay or `DELETED` |
| `DELETED` | Owner purge/tombstone applied | terminal for that owner/epoch |

`TRANSPORT_HANDOFF` is not evidence of W1 application. `w3_core_counters` survives body expiry and
owner deletion as documented by W3. Other owners and shared company/Source counters remain intact.

## 6. W3 Source Retirement Dispatch

W1's authoritative Source registry distinguishes permanent retirement from transient unavailable
or unknown. Only a durable permanent transition dispatches `(company_id, source_id, retired_at)` to
W3 `retire_source`; retry is idempotent and the identifier pair is never reused. The dispatch carries
no Source content or URL. Transient failures remain retryable W1 state and never create a W3 retired
Source tombstone.

## 7. Runtime Configuration

Root-owned mode `600`, absent from Git/image/logs.

| Name class | Examples | Rule |
| --- | --- | --- |
| W3 state | DB path, policy revision, handoff seconds, max attempts | DB path fixed to `/state/core.db`; revision `w3.retention/1.1`; handoff seconds exactly `1209600` |
| AWS | region, Main Queue URL, expected stable Role ID | workload credential chain only |
| Authority | private URL, bearer/identity material, timeout | final names/auth gated on W3-B |
| Operations | relay/expire cadence, HELD alert target, quarantine backup | five-minute expire runner and fifteen-minute logical-deletion SLO; alert destination remains deployment configuration |

## 8. Joint CT-12 Evidence Manifest

Non-secret durable integration record.

| Field | Type | Rules |
| --- | --- | --- |
| `run_id` | string | Unique synthetic run |
| `w1_source_sha`, `w3_source_sha` | git SHA | Full values |
| `w1_image_digest`, `w3_image_digest` | digest | Immutable image references |
| `role_identity_fingerprint` | redacted/fingerprint | Do not publish raw sensitive identity values |
| `scenario_results` | object | CT12-01~12 pass/fail and reason |
| `w3_counts` | object | State/count-only inspect output |
| `sqs_counts` | object | Main/DLQ visible/inflight/delayed |
| `w1_counts` | object | receipt/decision/binding/action/command/outbox counts |
| `cleanup` | object | container/volume/DB/queue/env teardown status |

The manifest contains no owner UUID, AnalysisPlan body, Source content, queue URL, role ARN, token or
database URL.

## Relationships

```text
Actual Analysis Caller
  └─ AnalysisPlan + stable key ──> W3 Runtime Deployment
                                    ├─ Authority Request ──> W1 Authority Snapshot
                                    ├─ W3 Delivery State ──> SQS ──> W1 inbound/PostgreSQL
W1 Deletion Request
  └─ W3 Deletion Target/Dispatch ──> W3 delete_owner/tombstone
W1 Permanent Source Retirement
  └─ W3 Source Retirement Dispatch ──> W3 retire_source/counter tombstone

Joint CT-12 Evidence Manifest references every immutable revision and count boundary above.
```
