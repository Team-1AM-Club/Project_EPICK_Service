# W3 → W2 로컬 공동 Gate 재검토 회신 — 2026-09-22

판정(9월 22일 W2 결과 JSON 수신 후): **고정 W2/W3 조합의 합성 로컬 제품 경로·복구 공동 Gate를 증거 검토 방식으로 사례별 `PASS` 수용**한다. W3는 승인된 격리 PostgreSQL 연결값이 없어 독립 재실행하지 않았다. `PASS`는 W2의 원본 결과 JSON·실행 스크립트·고정 커밋 대조에 근거하며 운영 배포 완료가 아니다.

## 고정 기준과 확인한 자료

- W2 제품 구현 기준 `7523d757d59ed0e28c9102ebf456140e76e2c493`, 검증 harness `df10726cb2dfa90f1742505d76e2e03108c62b0d`, **실제 실행 checkout `d99eb516c64dc16e92a5a76e528a8098024c1778`**, W3 C-01 구현 `0c4f01f9537a3129c976fae5e63111a7982c5da6`을 대조했다. W2 JSON의 `w2_code_sha`는 실행 checkout인 `d99eb…`다. `7523d…`와 `d99eb…` 사이의 `src/` 차이는 `source_runtime_operator.py`의 SQS preflight `maxReceiveCount` 형식 검사 한 곳뿐이다. 수집 handler·input provider·parser·outbox/HTTP 전송·migration 및 E2E 스크립트는 harness 이후 동일하다. 따라서 수집 경로 수용을 막지는 않지만, W2 결과 설명의 “`src`, `migrations`가 `7523d…`와 동일”이라는 문장은 정확히는 수정해야 한다. W3의 해당 `src/`와 테스트 파일은 소비자 pin 이후 변경되지 않았다.
- 첨부된 `w2-w3-product-flow-e2e-result-2026-09-22.json`은 W2 원격 `feat/crawler`의 `2bf8156371b87894b92cd92959bc31b44c534795`에 보관된 동명 파일과 **줄 단위 내용이 일치**한다. JSON은 `started_at_utc=2026-09-22T07:05:48.767Z`, `executed_at_utc=2026-09-22T07:06:11.624Z`, `exit_code=0`, `result.status=PASS`, W2/W3 full SHA를 담고 있다. 두 로컬 파일의 원시 SHA-256은 줄바꿈 차이로 달랐으므로 바이트 동일성은 주장하지 않는다.
- W2 `docs/w2-w3-product-flow-e2e-result-2026-09-22.md`는 합성 정적 공고 → 실제 `handle_collection_dispatch`·`SqlAlchemyCollectionInputProvider`·정적 파서 → PostgreSQL 저장/outbox → W3 HTTP의 `status=PASS`를 보고한다. W1 lookup과 fetch는 테스트 대역이다. 실제 외부 Source 수집이나 W1 FINALIZE 증거는 아니다.
- W2 `scripts/verify_w2_w3_postgres_http.py`의 `_collect_product_source`는 Company·Source·정책의 합성 선행 상태를 만들고 제품 수집 handler를 호출한다. 이후 SourceVersion 1건, Evidence 존재, revision 1의 `source.version.available` outbox 1건을 조회·검사한다. **이 제품 경로 함수는 OutboxEvent를 직접 삽입하지 않는다.** 다른 복구 사례의 합성 이벤트에는 별도의 `_seed`가 outbox를 직접 삽입하므로 두 경로를 구분한다.
- 같은 스크립트는 제품 Source의 W2 Authority `registered=true`, W3 cursor `0→1`·required cursor `1`·index ACK `false`, W2 outbox `delivered`, 전체 delivery 11건을 assert한다. Evidence **15건**은 이번 JSON의 `product_collection_evidence_count=15`에서 확인했다. 코드의 최소 assert는 `>0`이므로 정확한 개수는 JSON 결과에 의존한다.
- 이번 결과 JSON은 `outbox_delivered=11`, `replay_recovery_gap_to_cursor_7=true`, `snapshot_recovery_cursor_3_restriction_2=true` 등 제품 경로와 복구 사례가 **같은 실행**에 포함됐음을 기록한다. 이전 `docs/w2-w3-recovery-e2e-result-2026-09-21.json`은 선행 비교 증거일 뿐 이번 판정의 대체물이 아니다.
- W3의 고정 소비자 코드에 대해 C-01 관련 4개 테스트 파일을 실행해 **76 passed**. W3 로컬 계약 통과는 PostgreSQL 공동 실행 증거와 별개다.

## 요청 사례별 W3 수용 판정

| 사례 | W2가 보고한 값·코드 확인 범위 | W3 판정 |
|---|---|---|
| 합성 fixture fetch 1회 → SourceVersion 1건·Evidence 15건·outbox 1건(rev 1), 제품 경로 직접 outbox 삽입 없음 | 스크립트가 fetch·Version/outbox·revision을 assert하고 제품 경로에서 outbox를 직접 삽입하지 않음. JSON `product_collection_evidence_count=15`, `product_collection_fixture_type=synthetic_static_posting`, `status=PASS` | `PASS` — 합성 제품 경로에 한정 |
| 등록 Source의 W3 cursor `0→1`, required `1`, ACK false, W2 outbox delivered | 스크립트가 등록·cursor·required·ACK·outbox state를 assert. JSON `product_collection_w3_cursor=1`, `product_collection_outbox_delivered=true`, `outbox_delivered=11` | `PASS` — `COMMITTED` receipt를 index ACK로 해석하지 않음 |
| 미등록 Source의 event/replay/snapshot/index 거부 | JSON의 `unregistered_source_w2_authority_false`, `unregistered_source_w2_preflight_fail_closed`, 네 W3 `..._422`, `..._state_unchanged`가 모두 true. 스크립트는 `SOURCE_NOT_REGISTERED`와 상태 불변을 assert | `PASS` — 합성 미등록 Source 네 경로 |
| Authority 장애 fail-closed | JSON `authority_outage_pending=4`, `source_authority_outage_fail_closed=true`. 스크립트가 pending·cursor·ACK 불변을 assert | `PASS` — 공동 장애/복구. 세부 read/total timeout 주입은 W3 로컬 테스트 증거이며 이번 공동 실행 범위 밖 |
| immutable conflict와 revision gap/replay | JSON `immutable_conflict_fail_closed=true`, `gap_detected_at_cursor=5`, `gap_required_cursor=7`, `gap_recovered=true`, `replay_recovery_gap_to_cursor_7=true`, `replay_recovery_outbox_states_unchanged=true` | `PASS` — 합성 충돌·gap·replay 사례 |
| 명시 snapshot·restriction·재색인 | JSON `snapshot_recovery_cursor_3_restriction_2=true`, 반복 snapshot generation delta 1·상태/outbox 불변, 적용 직후 ACK false·재색인 후 true, `restriction_blocks_search=true`, `release_requires_reindex=true` | `PASS` — 합성 snapshot·restriction·재색인. `F>0`/pruning은 범위 밖 |

이번 요청 사례에서 `FAIL` 또는 `BLOCKED_ENVIRONMENT`는 남지 않는다. W3가 PostgreSQL 실행을 재현한 것은 아니므로 판정 출처는 **W2 실행 결과의 W3 독립 자료 검토**로 표시한다. W2 결과 문서는 코드 pin 설명을 위 실제 실행 checkout과 일치하게 정정하면 된다. 새 DB 연결값이나 재실행은 이 증거 검토 판정의 필수 조건이 아니다. DB URL·token·원문 HTML·raw HTTP body는 회신에 포함하지 않는다.

W1 실서비스 lookup/FINALIZE·최신 포인터 승격, 실제 외부 Source fetch와 권리 확인, W3 운영 index ACK, W1 IAM/SQS·registry·영구 DB·TLS·monitoring, G-07 보존/pruning·`F>0`은 이 로컬 Gate의 수용 대상이 아니다. W2 전체 suite는 제공 문서 기준 `1781 passed, 14 failed, 1 skipped`로 전체 green이 아니며, 이 결과를 W3가 독립 재실행하지 않았다.
