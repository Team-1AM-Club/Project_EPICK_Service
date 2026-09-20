# Frontend ↔ W1 Public API Mapping

## Boundary

- Browser base URL: environment-provided W1 public URL.
- Authentication endpoints and `/api/v1/*` are the only allowed browser network destinations for EPICK data.
- Every owner-scoped call uses the EPICK access token; browser payloads never contain a trusted `user_id`.
- W2/W3/W4 contracts, queue URLs, Neo4j identifiers, worker endpoints, and deployment credentials are forbidden in frontend source and bundles.

## Screen mapping

| Frontend capability | W1 public surface | Integration rule |
|---|---|---|
| Login | `GET /api/v1/auth/google/start`, callback, `POST /auth/refresh` | Navigate to start; keep returned access token in memory only. |
| Current profile | `GET /api/v1/users/me` | First authenticated bootstrap query after refresh. |
| Home and continue | `GET /api/v1/home`, `GET /api/v1/resume-items` | Use server URLs/types; do not reconstruct Job state from localStorage. |
| Experience list/detail | Activity list/detail/version endpoints | One UI experience card maps to one Activity. |
| Experience incident | Episode list/detail/version endpoints | Initial form creates one Episode; later UI may expose multiple Episodes. |
| Draft/complete | Activity/Episode complete endpoints | Preserve server validation and explicit field availability. |
| Project list/detail | `/api/v1/application-projects` endpoints | Use current version and If-Match behavior from OpenAPI. |
| Project questions | project question endpoints | Question order and version are server authority. |
| Analysis progress | `GET /api/v1/jobs/{job_id}` | Poll W1 only; keep Job/dispatch/completeness axes separate. |
| Job decision | Job actions/retry/cancel endpoints | Send required action ID, expected versions, and one idempotency key per intent. |
| Recommendation | recommendation-run and candidate endpoints | Display `result_origin`; only current READY/allowed LIMITED result is selectable. |
| Material selection | candidate select and question selection endpoints | Invalidate selection/project queries after mutation. |
| Settings/consent | settings, recommendation preferences, consent endpoints | Analytics OFF never blocks core workflow. |
| Notifications | notification endpoints | Safe metadata only. |
| Account deletion | existing account deletion preview/request/status/retry | Clear auth and private cache as soon as deletion starts. |
| Activity/project deletion | additive resource deletion surface | Required before enabling the delivered delete buttons against live data. |
| Company catalog/jobs | existing company and job-posting endpoints | Read-only catalog. |
| Company summary/evidence/conflicts | additive company evidence read model | Keep current mock sections behind a visible synthetic/demo flag until available. |

## Job presentation mapping

The frontend derives presentation from multiple fields; it MUST NOT persist a replacement Job enum.

| W1 condition | User presentation | Polling |
|---|---|---|
| `status=QUEUED`, `dispatch_status=OUTBOX_PENDING` | 요청 접수 | Continue |
| `status=QUEUED`, `dispatch_status=ENQUEUED` | 실행 대기 | Continue |
| `status=RUNNING`, collection stage | 기업 자료 수집 중 | Continue |
| `status=RUNNING`, projection/index stage | 검색 반영 중 | Continue |
| `status=RUNNING`, matching stage | 소재 추천 중 | Continue |
| `status=WAITING_USER` | 사용자 선택 필요 | Stop; show only returned required actions |
| `status=PAUSED_RATE_LIMIT` | 요청 한도로 일시 중지 | Stop until allowed explicit retry/action |
| `status=SUCCEEDED`, complete usable result | 결과 확인 가능 | Stop |
| `status=FAILED_RETRYABLE` | 재시도 가능한 실패 | Stop; show returned failure/action |
| `status=FAILED_FINAL` | 처리하지 못함 | Stop |
| `status=CANCEL_REQUESTED` | 중단 요청 처리 중 | Continue |
| `status=CANCELLED` | 작업 중단됨 | Stop |
| any status with `dispatch_status=BLOCKED` | 전달 차단/확인 필요 | Continue only if lifecycle remains active; never show running/success |
| any status with `dispatch_status=INVALIDATED` | 요청 무효화 | Stop |

`stage` values are presentation-mapped through an allowlist with a safe fallback. Unknown stages never imply success.

## Error contract

All API failures use the current W1 error envelope:

```json
{
  "error": {
    "code": "STABLE_CODE",
    "message_ko": "사용자에게 표시 가능한 설명",
    "retryable": false,
    "actions": [],
    "correlation_id": "opaque-id",
    "fields": []
  }
}
```

Frontend logic branches on HTTP status and `error.code`, never on `message_ko` text.

## Cache and late-response rules

1. Access tokens and personal API payloads are not persisted to localStorage.
2. Logout/account switch increments the client generation, cancels requests, clears query caches, and then removes the in-memory token.
3. Delete confirmation quarantines affected query keys before sending the request.
4. A response from an older generation, older resource version, or request aborted by navigation cannot repopulate current cache.
5. Network polling failure displays an observation error; it does not mutate or retry the Job.
6. Synthetic recommendation/company data always displays a non-production marker and cannot be merged silently with ENGINE results.
