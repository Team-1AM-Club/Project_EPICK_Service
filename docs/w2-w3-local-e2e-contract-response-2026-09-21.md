# W2 → W3 로컬 공동 E2E 계약 회신 — 2026-09-21

판정: **W3 HTTP client 구현·새 W2 wire 검증 완료 / 합성 양방향 HTTP 통과 / PostgreSQL outbox 공동 E2E 미실행**

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

W1 image registry·IAM/SQS 없이 **같은 호스트의 loopback HTTP로 연결 가능**하다. 현재 PC에서는
Docker daemon이 실행 중이지 않고 `127.0.0.1:55439`의 PostgreSQL도 열려 있지 않아 W2 DB를
포함한 실제 공동 실행은 하지 못했다. 이는 W1 배포 선행 조건이 아니라 **로컬 PostgreSQL
환경 부재**다.

실행 순서는 다음과 같다. W2가 승인된 PostgreSQL에 migration을 적용하고 Source/Version/
Observation/Restriction 합성 producer 데이터를 준비해야 한다. 실제 값은 비밀 설정으로만
주입한다.

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

## 4. 공동 완료 사례 현황

| 사례 | 현재 증거 | 실제 PostgreSQL outbox 공동 결과 |
|---|---|---|
| version 2·observation 1·restriction ACTIVE 1 | W2 wire 4건 W3 schema·합성 양방향 HTTP PASS; cursor 4, restriction 1 | NOT_RUN |
| restriction CLEARED, 재색인 전후 index ACK | W3 로컬 계약 테스트에서 재색인 전 READY 금지 검증 | NOT_RUN |
| 미등록 원 Source·대체 Source | W3 HTTP client `registered=false` 및 Store 대체 Source 거부 테스트 PASS | NOT_RUN |
| W2 Authority 장애·timeout | W3 HTTP client 503·read/total timeout fail-closed 테스트 PASS | NOT_RUN |
| 동일 이벤트 duplicate, revision gap/conflict | W3 로컬 Store 회귀 테스트 PASS | NOT_RUN |
| replay·snapshot recovery, F/H | W3 로컬 Store 회귀 테스트 PASS | NOT_RUN |
| receipt와 index ACK 분리 | 합성 HTTP receipt 4건 `COMMITTED`, 최종 index ACK `false` | NOT_RUN |
| W3 재시작 후 cursor·restriction 복구 | 영구 SQLite 사용 명령 제공 | NOT_RUN |

모든 `NOT_RUN` 항목의 공통 차단 원인은 **이 PC의 실행 가능한 PostgreSQL 부재와 W2 DB에
확정된 합성 producer/outbox 데이터가 아직 준비되지 않은 것**이다. W2 담당자는 승인된 로컬
PostgreSQL·migration·producer fixture 및 기대 revision을 제공하고, W3 담당자는 위 구현 SHA로
W3 process를 실행해 사례별 receipt/status·재시작 증거를 기록한다. W1 운영 배포 입력은 이
로컬 Gate의 선행 조건이 아니다. 공동 실행 시간 창은 아직 합의되지 않았다.

W3 전체 검증은 `288 passed, 1 skipped`(live provider 제외), Ruff check/format 통과,
C-01 20·core decision 12·restriction 19 생성물 drift 0이다. 실제 PostgreSQL 공동 E2E가
통과하기 전에는 연동 완료로 표시하지 않는다.
