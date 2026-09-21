# W2 → W3 로컬 공동 E2E 계약 회신 — 2026-09-21

판정(9월 21일 W2 후속 증거 반영): **W2 격리 PostgreSQL outbox ↔ W3 HTTP의 합성 공동 사례 일부 PASS / W3 C-01 로컬 계약 테스트 76 PASS / W2–W3 로컬 Gate 전체 보류**. W3는 이번 PostgreSQL 실행을 재현하지 않았으며, W2가 제공한 실행 결과와 고정 증거를 검토했다.

기준은 W2 Engine `feat/crawler` 코드 SHA
`40ca63447287432d5a127f6001fc984dc8980dd5`(인계 문서 HEAD
`7876b824da368b78e28ce9ddf049694c1112f5ad`)와 W3 Service
`feat/w3-knowledge-validation` 구현 SHA
`0c4f01f9537a3129c976fae5e63111a7982c5da6`다.

## 1. W3 SourceAuthority client

- 코드: `src/w3_knowledge/c01/source_authority_http.py`
- 테스트: `tests/integration/test_c01_source_authority_http.py` — 9 passed
- 주입 factory:
  `w3_knowledge.c01.source_authority_http:create_source_authority`
- W2 호출: Bearer 인증 `GET /internal/v1/sources/{canonical_source_id}/authority`
- `200`의 정확한 `{"source_id":"<요청 UUID>","registered":true}`만 등록으로 인정한다.
  literal `false`는 W3 `SOURCE_NOT_REGISTERED` 422다.
- W2 `503`·`401`, 통신 장애, 연결/읽기/전체 timeout, 잘못된 JSON·ID·Content-Type은
  `SOURCE_AUTHORITY_UNAVAILABLE` 503으로 닫는다. 응답 본문·token은 오류에 포함하지 않는다.
- W3 기존 `Store`가 event, replay, snapshot, index 전에 원 Source와
  `replacement_ref`의 대체 Source를 재검사한다. 로컬 관측 테이블을 fallback으로 쓰지 않는다.
- 평문 HTTP는 loopback 주소만 허용한다. 원격은 HTTPS와 인증서 검증을 사용한다.

## 2. 새 W2 SHA pin 검증

- 결과: `docs/w2-w3-local-http-pin-verification-2026-09-21.json`
- 재현 스크립트: `scripts/verify_w2_w3_pin.py`
- W2 공개 wire SHA-256:
  `c964bdd81bda7c9c5b3710c8cc6310a3521178839a24b744ba277f4b4e489b3e`
- W2 `_source_event_from_outbox_row`와 실제 `source_event_to_w3_wire` 출력을 사용했다.
  종전의 W3 검증용 수동 envelope 조립 함수는 제거했다. W2 내부 모델을 만드는 합성 입력은
  기존 W3 fixture를 사용한다.
- 결과: version 2, observation 1, restriction 1 모두 outbox 전달 가능하고 W3 schema 수용.
  outer revision `[1,2,3,4]`, 별도 restriction revision `[1]`, W3 cursor `4`, restriction
  watermark `1`.
- W2 실제 publisher → W3 실제 HTTP server의 `COMMITTED` receipt 4건과 W3 → W2 실제
  Authority ASGI endpoint HTTP 조회를 합성 세션으로 함께 실행했다. 최종 `index_ack=false`이며
  이유는 `OBSERVATION_BLOCKED`다. receipt를 index ACK로 취급하지 않았다.
- 이 실행은 W2 PostgreSQL 세션을 합성 세션으로 대체했으며 실제 outbox DB delivery 증거가 아니다.

```powershell
.runtime\w2-engine-2cc9153\.venv\Scripts\python.exe scripts\verify_w2_w3_pin.py `
  --w2-checkout .runtime\w2-engine-40ca634 `
  --w2-sha 40ca63447287432d5a127f6001fc984dc8980dd5 `
  --w3-sha 0c4f01f9537a3129c976fae5e63111a7982c5da6 `
  --output docs\w2-w3-local-http-pin-verification-2026-09-21.json
```

## 3. 동일 호스트 실제 실행 경로

W1 image registry·IAM/SQS 없이 **같은 호스트의 loopback HTTP로 연결 가능**하다. 최초 회신 시
W3 PC의 PostgreSQL 환경이 없어 DB 공동 실행을 하지 못했다. 이후 W2가 별도의 승인된 격리
loopback PostgreSQL에서 아래 §4의 합성 공동 검증을 실행했다. 현재 W3 환경에는
`EPICK_TEST_DATABASE_APPROVED`와 `EPICK_TEST_DATABASE_URL`이 없어 그 실행을 독립 재현하지
않았다. W2 실행은 W1 운영 배포 선행 조건을 검증한 것이 아니다.

아래는 최초 회신에서 제시한 수동 프로세스 실행 경로다. W2의 이번 재현 스크립트는 별도
격리 schema에 migration을 적용하고 합성 `OutboxEvent`를 직접 저장한다. 실제 수집 producer
검증에는 그 앞단의 Source/Version/Observation/Restriction 저장·outbox 생성 경로를 추가로
연결해야 한다. 실제 연결값은 비밀 설정으로만 주입한다.

1. W3 DB 디렉터리: `New-Item -ItemType Directory -Force .runtime/local-w2-w3`
2. W2 Authority: `python -m uvicorn epick_engine.source_collection.source_authority_operator:create_app --factory --host 127.0.0.1 --port 8765`
3. W3 C-01: `python -m w3_knowledge.c01.http --db .runtime/local-w2-w3/w3-c01.sqlite --port 8764 --restriction-scope version --max-ttl-seconds <승인된 값> --source-authority w3_knowledge.c01.source_authority_http:create_source_authority`
4. W2 outbox: `python -m epick_engine.source_collection.w3_outbox_operator --limit 25`
5. W3 status: 인증된 `GET http://127.0.0.1:8764/c01/v1/status/{source_id}`에서 cursor,
   restriction watermark, `index_ack`를 별도로 확인한다. 같은 SQLite 파일로 W3를 재시작해
   복구 상태를 비교한다.

필수 설정 키 이름: W2 `EPICK_DATABASE_URL`, `EPICK_W2_SOURCE_AUTHORITY_TOKEN`,
`EPICK_W3_EVENT_ENDPOINT`, `EPICK_W3_W2_TOKEN`; W3
`W3_SOURCE_AUTHORITY_ENDPOINT`, `W3_SOURCE_AUTHORITY_TOKEN`, `W3_W2_TOKEN`,
`W3_OPERATOR_TOKEN`, `W3_W4_TOKEN`. 같은 쌍의 W2/W3 token 값은 안전한 채널로 일치시킨다.
선택 키: `W3_SOURCE_AUTHORITY_CONNECT_TIMEOUT_SECONDS`,
`W3_SOURCE_AUTHORITY_READ_TIMEOUT_SECONDS`,
`W3_SOURCE_AUTHORITY_TOTAL_TIMEOUT_SECONDS`, `W3_SOURCE_AUTHORITY_CA_FILE`.
loopback 예시 endpoint는 W2 `EPICK_W3_EVENT_ENDPOINT=http://127.0.0.1:8764/c01/v1/events`,
W3 `W3_SOURCE_AUTHORITY_ENDPOINT=http://127.0.0.1:8765`다. W3 bind는 `127.0.0.1:8764`,
W2 Authority bind는 `127.0.0.1:8765`; W3 SQLite 경로는 재시작에도 보존할 로컬 파일이다.
실제 token·DB URL·Source 원문은 문서와 로그에 기록하지 않는다.

## 4. W2 후속 PostgreSQL/HTTP 증거와 사례별 판정

W2 Engine `feat/crawler` HEAD `69f8984dba2f3c56d66c97f8a9edd5de5c4513ff`의
`src/`, `migrations/`는 위 제품 코드 pin `40ca63447287432d5a127f6001fc984dc8980dd5`와
동일하다. W2 증거 문서 `docs/w2-w3-postgres-http-e2e-result-2026-09-21.md`의 Git blob
SHA-256은 `62070AB7CE079F0F3EDA0FABF3C45D45EF8FB128BDD1EBA28EC6DF47C1109E4C`,
동명 JSON은 `F5F910FD5AE57144FA129873D0536B089E395B8F7D3DFC15C5CF17BE1E5052F2`,
`scripts/verify_w2_w3_postgres_http.py`는
`ABD14E3BAB7FFF8D12F6938F84051D520B022AA6B8BC3A61171B016FF537FC9F`로
인계값과 일치한다. W2 결과 JSON은 `status=PASS`, `outbox_delivered=10`으로 기록한다.
W2가 W3 fixture 및 추가 합성 이벤트를 **실제 W2 OutboxEvent에 직접 저장**하고 별도 W2/W3
프로세스와 HTTP로 전달한 검증이다. 실제 수집 producer 전체 경로 또는 운영 환경 검증은 아니다.

| 사례 | W2 격리 PostgreSQL/HTTP 증거 | W3 독립 계약 검증 / 남은 범위 |
|---|---|---|
| version 2·observation 1·restriction ACTIVE 1 | **PASS(합성)**: 4건 전달, cursor 4·restriction revision 1 | W3 고정 fixture·wire 검증 PASS. 실제 수집 producer 경로는 미검증 |
| restriction CLEARED, 재색인 전후 index ACK | **PASS(합성)**: 미등록 대체 Source는 pending, 등록 후 해제; 별도 허용 Source에서 ACTIVE 중 검색·ACK 차단, CLEARED 직후에도 ACK false, 재색인 후 true·검색 복구 | W3 로컬 index fence 계약 PASS |
| 미등록 원 Source·대체 Source | **부분 PASS**: Authority false 및 미등록 대체 Source pending | W3 로컬 event/replay/snapshot/index 등록 재검사 및 대체 Source 거부 PASS. **원 Source 미등록의 네 경로를 실제 W2 Authority와 공동 실행한 증거는 없음** |
| W2 Authority 장애·timeout | **부분 PASS**: Authority 중단 중 최초 4건 pending, 복구 후 전달 | W3 HTTP client의 read/total timeout fail-closed PASS. 공동 timeout 주입은 미실행 |
| 동일 이벤트 duplicate, revision gap/conflict | **부분 PASS**: duplicate cursor 불변; rev 7 선전달 때 cursor 5·required 7, rev 6 후 cursor 7 | W3 동일 event ID의 다른 body conflict 및 revision 충돌 로컬 PASS. **공동 conflict 주입 미실행** |
| replay·snapshot recovery, F/H | **부분 PASS**: 동일 event ID 재전달과 재시작 상태 보존 | W3 F와 cursor 경계, H 유지, `SNAPSHOT_REQUIRED`·원자 snapshot/reindex 로컬 PASS. **W2 batch replay·snapshot producer 및 공동 복구 미구현/미실행** |
| receipt와 index ACK 분리 | **PASS(합성)**: outbox delivered 10건과 ACK 별도 확인; 원 fixture Source 최종 ACK false(`OBSERVATION_BLOCKED`) | W3 receipt·index ACK 구분 유지 |
| W3 재시작 후 cursor·restriction 복구 | **PASS(합성)**: 동일 SQLite 재시작 후 cursor 4·restriction revision 1 유지 | W3 로컬 영속화 테스트 PASS |

W3 독립 확인: 고정 구현 pin `0c4f01f9537a3129c976fae5e63111a7982c5da6`의
`test_c01.py`, `test_c01_confirmed_contract.py`, `test_c01_http.py`,
`test_c01_source_authority_http.py`를 새 임시 경로에서 실행해 **76 passed**. 이는 W3
소비자 구현·계약의 증거이며, 위 표의 공동 미실행 사례를 PASS로 대체하지 않는다. W2 전체
회귀는 인계상 `1693 passed, 14 failed, 1 skipped`로, 전체 green이 아니다.

## 5. W3 로컬 Gate 판정과 남은 최소 작업

**수용:** 위 합성 OutboxEvent → HTTP event 전달, authoritative Source 조회, restriction·index
차단/재색인, 중복·gap, receipt/ACK 분리, W3 SQLite 재시작의 **검증된 하위 Gate**.
**보류:** W2–W3 로컬 연동 전체 완료, 실제 수집 producer 경로, replay·snapshot 복구 Gate,
운영 배포. W3 구현에 지금 추가할 필수 코드 변경은 확인되지 않았다.

- **W2:** 정본 `docs/w3-additional-reply-2026-09-16.md` §5·§7의 확정된 F/H 경계와 원자
  snapshot 소비 조건에 맞춰 batch replay·snapshot producer를 구현하고, 실제 producer
  persistence/outbox 연결을 증명한다. 이 소유권과 규칙을 다시 결정할 필요는 없다.
- **W2·W3 공동:** 같은 pin에서 원 Source 미등록의 event/replay/snapshot/index, Authority
  timeout, 동일 revision·다른 body conflict, F/H retention-floor 및 snapshot-required·복구를
  PostgreSQL/HTTP로 실행해 사례별 receipt·cursor·restriction·index ACK와 결과 JSON을 남긴다.
  W3 로컬 계약 PASS와 공동 미실행을 구분한다.
- **W1/운영:** registry·IAM/SQS, 영구 DB·volume, 모니터링 및 실제 배포 환경은 별도 Gate다.

W3의 이번 검토 환경에는 승인된 격리 DB 연결 변수가 없어 공동 스크립트를 재실행하지 않았다.
W2가 기록한 검증의 실행 환경은 격리 loopback PostgreSQL 임시 schema와 별도 W2/W3 HTTP
프로세스다. W2가 사용한 W3 TTL 300초는 시험값이다. W3 이외의 전체 회귀·생성물 검증은
이번 후속 검토에서 새로 실행하지 않았다.
