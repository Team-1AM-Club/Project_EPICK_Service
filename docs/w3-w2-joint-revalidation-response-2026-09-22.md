# W3 → W2 로컬 공동 Gate 재검토 회신 — 2026-09-22

판정: **W2 제품 경로 PASS 보고는 코드 경계와 결과 문서로 확인했으나, 9월 22일 실행의 원본 결과 JSON/로그가 없어 W3의 사례별 공동 Gate 수용은 `BLOCKED_ENVIRONMENT`**다. W3에서 승인된 격리 PostgreSQL 연결값을 갖고 있지 않아 독립 재실행도 하지 않았다. 이는 W2 실행이 실패했다는 판정이 아니다.

## 고정 기준과 확인한 자료

- W2 제품 구현 `7523d757d59ed0e28c9102ebf456140e76e2c493`, 검증 harness `df10726cb2dfa90f1742505d76e2e03108c62b0d`, W3 C-01 구현 `0c4f01f9537a3129c976fae5e63111a7982c5da6`을 기준으로 삼았다. W2의 `df10726…` checkout에서 `src/`, `migrations/`는 제품 구현 pin과 동일했다. 원격 W2 브랜치는 인계 후 문서 커밋 `e288f907a2f899646f76094ec0e873ec5768663c`까지 진행됐지만 이번 검토는 허용된 harness pin으로 고정했다. W3의 해당 `src/`와 테스트 파일은 소비자 pin 이후 변경되지 않았다.
- W2 `docs/w2-w3-product-flow-e2e-result-2026-09-22.md`는 합성 정적 공고 → 실제 `handle_collection_dispatch`·`SqlAlchemyCollectionInputProvider`·정적 파서 → PostgreSQL 저장/outbox → W3 HTTP의 `status=PASS`를 보고한다. W1 lookup과 fetch는 테스트 대역이다. 실제 외부 Source 수집이나 W1 FINALIZE 증거는 아니다.
- W2 `scripts/verify_w2_w3_postgres_http.py`의 `_collect_product_source`는 Company·Source·정책의 합성 선행 상태를 만들고 제품 수집 handler를 호출한다. 이후 SourceVersion 1건, Evidence 존재, revision 1의 `source.version.available` outbox 1건을 조회·검사한다. **이 제품 경로 함수는 OutboxEvent를 직접 삽입하지 않는다.** 다른 복구 사례의 합성 이벤트에는 별도의 `_seed`가 outbox를 직접 삽입하므로 두 경로를 구분한다.
- 같은 스크립트는 제품 Source의 W2 Authority `registered=true`, W3 cursor `0→1`·required cursor `1`·index ACK `false`, W2 outbox `delivered`, 전체 delivery 11건을 assert한다. Evidence **15건**은 W2 결과 문서의 보고값이며 코드의 최소 assert는 `>0`이다.
- W2의 이전 복구 결과 `docs/w2-w3-recovery-e2e-result-2026-09-21.json`은 `PASS_WITH_OPEN_GATES`로 기록돼 있다. 이는 **9월 21일 별도 실행**의 원본 결과이며, 9월 22일 제품 경로와 같은 실행의 JSON으로 대체할 수 없다.
- W3의 고정 소비자 코드에 대해 C-01 관련 4개 테스트 파일을 실행해 **76 passed**. W3 로컬 계약 통과는 PostgreSQL 공동 실행 증거와 별개다.

## 요청 사례별 W3 수용 판정

| 사례 | W2가 보고한 값·코드 확인 범위 | W3 판정 |
|---|---|---|
| 합성 fixture fetch 1회 → SourceVersion 1건·Evidence 15건·outbox 1건(rev 1), 제품 경로 직접 outbox 삽입 없음 | handler 호출·fetch 1회·Version/outbox 1건·revision 1 및 제품 경로의 직접 삽입 없음은 스크립트에서 확인. Evidence 15건은 결과 문서 보고이며 새 실행 JSON 미제공 | `BLOCKED_ENVIRONMENT` — 9/22 원본 실행 JSON/로그 또는 승인 DB 재실행 필요 |
| 등록 Source의 W3 cursor `0→1`, required `1`, ACK false, W2 outbox delivered | 스크립트에 각 assert가 있고 W2 결과 문서가 `PASS` 보고. `COMMITTED` receipt를 index ACK로 보지 않음 | `BLOCKED_ENVIRONMENT` — 같은 실행의 JSON/로그 또는 재실행 필요 |
| 미등록 Source의 event/replay/snapshot/index 거부 | 9/21 복구 JSON의 네 경로 HTTP 422, `SOURCE_NOT_REGISTERED`, 상태 불변은 확인 | `BLOCKED_ENVIRONMENT` — 9/22 같은 실행의 결과값 미제공; 이전 실행은 PASS 증거 |
| Authority 장애 fail-closed | 9/21 복구 JSON에 장애 시 cursor·ACK 불변 기록. W3 로컬 HTTP timeout 테스트도 통과 | `BLOCKED_ENVIRONMENT` — 9/22 같은 실행의 결과값 미제공; 세부 timeout 주입은 이번 공동 범위에서 별도 확인되지 않음 |
| immutable conflict와 revision gap/replay | 9/21 복구 JSON의 conflict 실패 종료·outbox 불변, gap `0→7`·restriction revision 2 확인 | `BLOCKED_ENVIRONMENT` — 9/22 같은 실행의 결과값 미제공 |
| 명시 snapshot·restriction·재색인 | 9/21 복구 JSON의 snapshot cursor 3·restriction 2, 반복 적용 generation +1, 적용 직후 ACK false, 재색인 후 true 확인 | `BLOCKED_ENVIRONMENT` — 9/22 같은 실행의 결과값 미제공. `F>0`/pruning은 이번 요청 범위 밖 |

현재 `FAIL`로 판정할 사례는 없다. W2 저장소의 harness pin과 최신 원격 브랜치에서 9월 22일 제품 경로 **원본 결과 JSON 파일은 찾지 못했다**. W2가 민감정보를 제거한 해당 실행의 stdout JSON과 실행 시각·두 full SHA·exit code를 전달하면, 위 표를 결과값과 대조해 사례별 `PASS` 또는 `FAIL`로 다시 판정할 수 있다. DB URL·token·원문 HTML·raw HTTP body는 보내지 않는다. W3 재실행을 원하면 승인된 격리 loopback PostgreSQL 테스트 환경을 별도로 제공해야 한다.

W1 실서비스 lookup/FINALIZE·최신 포인터 승격, 실제 외부 Source fetch와 권리 확인, W3 운영 index ACK, W1 IAM/SQS·registry·영구 DB·TLS·monitoring, G-07 보존/pruning·`F>0`은 이 로컬 Gate의 수용 대상이 아니다. W2 전체 suite는 제공 문서 기준 `1781 passed, 14 failed, 1 skipped`로 전체 green이 아니며, 이 결과를 W3가 독립 재실행하지 않았다.
