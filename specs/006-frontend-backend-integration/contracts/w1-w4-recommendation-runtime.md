# W1–W4 Recommendation Runtime Contract Boundary

## Status and evidence

This is the Stage 4.5 private-contract plan. It does not change W1 public OpenAPI and is not a claim that REAL user data is approved.

Evidence used:

- `backend/app/services/recommendation_execution.py` defines W1's `RecommendationExecutionPort.execute(owner_user_id, run_id)`.
- delivered W4 `epick_w4/w1_bridge.py` implements that port shape and defines `RunStore.acquire/load_context/authorize/publish/fail`.
- `md/w4/W1_W4_CT12_Actual_Runtime_Result_2026-09-20.md` proves the separate Question Core W4→SQS→W1 transport/currentness pattern, including duplicates, response loss, restart, cancel, deletion epoch, and relation changes.
- the W4 bridge restricts context to `SYNTHETIC`, labels its publication `w4-w1-publication/0.1-draft`, and reports pending policy/model approval. A recommendation-specific acceptance run is still required.

Before implementation, copy the adopted schemas into a W1-owned private contract directory and record the exact W4 source SHA plus SHA-256 for each schema. A hash change fails compatibility tests; it is never accepted implicitly.

## Public boundary — unchanged

The browser continues to use:

- `POST /api/v1/questions/{question_id}/recommendation-runs`
- `GET /api/v1/recommendation-runs/{run_id}`
- `GET /api/v1/recommendation-runs/{run_id}/candidates`
- existing selection endpoints

The request does not gain provider, model, W4 URL, execution origin, owner, raw Episode, company knowledge, queue, lease, or fence fields. W1 deployment policy chooses `SYNTHETIC` or `ENGINE`. Existing response enums and fields keep their meanings.

## Private execution flow

```text
browser -> W1 public API -> PostgreSQL Run + binding + outbox
                                  |
                                  v
                              SQS trigger
                                  |
                                  v
                            W4 engine worker
                                  |
                    authenticated W1 private HTTP
                    acquire/context/authorize/publish/fail
                                  |
                                  v
                       W1 atomic PostgreSQL publication
                                  |
                                  v
                     existing W1 public read endpoints
```

### Queue trigger

The private message is versioned and contains only opaque execution references:

- message/event ID and occurred-at timestamp.
- `owner_user_id`, `run_id`, execution binding/attempt reference.
- contract version and pinned engine source/schema digest.
- correlation/trace identifier that contains no personal content.

It must not contain Episode text, question text, company evidence, model prompts/responses, provider credentials, private HTTP credentials, or queue receipt data.

Delivery is at least once. A duplicate or a send-response loss is resolved by W1 binding state, not by mutating the immutable message body. Retryable transport/5xx failures follow bounded backoff and DLQ policy; schema, principal, stale, cancelled, or policy-denied failures are terminal or blocked and are not blind-retried.

### Protected W1 operations

Exact paths are private implementation details and are excluded from public OpenAPI. Their semantic operations are:

| Operation | Required effect |
|---|---|
| `acquire` | Authenticate the W4 workload, lock owner then Run, change the exact `ENGINE/PENDING` attempt to `RUNNING`, and return an immutable binding/lease. An already published identical attempt is idempotent. |
| `load_context` | Return only the binding's owner-scoped question version, snapshot-pinned Episode versions, exclusions, and current C01/company context. Never substitute another current project Run. |
| `authorize` | Recheck the exact binding for `PROCESS`, `SEND_TO_PROVIDER`, or `RETURN_TO_CALLER`, including owner status, deletion epoch, access/consent, provider/model policy, revisions, lease, and Source currentness. |
| `publish` | Validate the W4 publication and atomically persist full result, Source dependencies, all candidates, result version, and terminal Run/result status only if every fence is current. |
| `fail` | Apply a safe failure code only while this lease owns `RUNNING`; never overwrite cancellation, a newer lease, or a completed publication. |

Authentication uses a workload identity/secret supplied by AWS deployment configuration, with TLS and least-privilege authorization. A user bearer or browser cookie is never accepted at this private boundary. Unauthorized and unknown owner/run combinations must not reveal existence.

## Adopted W4 semantic mapping

| W4 value | W1 persisted value |
|---|---|
| `DIRECT_MATCH` | `DIRECT_MATCH` |
| `PARTIAL_MATCH` | `PARTIAL_RELEVANCE` |
| `NEEDS_CONFIRMATION` | `NEEDS_VERIFICATION` |
| accepted initial publication | Run/result `LIMITED`, candidate validation `LIMITED` |
| actual W4 execution + successful publication | `result_origin=ENGINE` |

The W4 `result_version` is content-derived from Run, question version, context hash, and full result. A schema version is not reused as a result version. Full W4 output and Source dependencies are stored privately; only the existing safe candidate projection reaches the browser.

## Failure and currentness rules

- No ENGINE failure path calls `SyntheticRecommendationAdapter` for the same Run.
- Model calls occur outside long-lived W1 DB transactions.
- W1 rechecks lease, owner deletion epoch, cancellation, question/snapshot/Episode revisions, access/consent, provider/model policy, and Source revisions at publication.
- Candidate rows must not be partially committed.
- Duplicate, response-loss, and restart replay produce at most one logical publication.
- Stored result reads and selection apply the same currentness/dependency gate; W4-local cache invalidation is insufficient.
- Logs contain IDs/digests, safe codes, stage, latency, and correlation only—never raw user/company/model content.

## Enablement gates

### Stage 4.5 acceptance

- fixed synthetic server context and fake/approved test model clients.
- actual delivered W4 recommendation code and bridge, not the W1 synthetic adapter.
- normal, duplicate, response-loss, restart, cancel, deletion-epoch, question/snapshot, and Source-change cases.
- `ENGINE` plus explicit `LIMITED`/limitation display through the existing browser flow.

### REAL data — out of Stage 4.5

REAL input remains disabled until P2/P3 policy owners approve data transfer, retention/erasure, W3 projection/currentness, provider/model selection, logging, and rollout. Passing Question Core CT-12 or synthetic W4 recommendation acceptance does not satisfy this gate.
