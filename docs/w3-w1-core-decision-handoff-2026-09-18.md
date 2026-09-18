# W3 → W1 Core Decision 인계 — 2026-09-18

후속: W1이 이 계약을 41dd0c21692c7f59c87962cf1e7d2746bac73303에서 채택했다고 회신했다.
아래는 최초 전달 시점 기록이며, 최신 생산/relay 구현 상태는 `w3-core-runtime-handoff-2026-09-18.md`를 따른다.

상태: **W3 독립 생산 모듈·계약 후보 제공 / W1 미채택 / queue 미연결 / 공동 CT-12 미실행**.
정본은 이 문서다. 전달 ZIP의 `HANDOFF_RECEIPT.json`에 실제 full commit, 고정 URL,
이 문서의 SHA256과 원격 검증 결과를 기록한다. 문서 안에 자기 commit/hash를 넣지 않는다.

## 1. 이번 제공 범위

W1 R2-02/R2-04와 W2 2026-09-18 R3-05에 대한 W3 회신이다.
W1이 소비할 **COMPANY_KNOWLEDGE 판단 생산 경계**를 별도로 제공한다.
W2 Source Event→W3 C01, W3→W4 usability/Signal, W1→W2 commit gate와 혼용하지 않는다.
QUESTION_MATCHING→W4의 company 결속 충돌은 W1/W4 담당이며 여기서 바꾸지 않는다.

W1 검토 기준은 Service `afec08a9602132e5e433b523b0b6804850440524`다.
`backend/app/runtime/core_decision_binding.py`와
`backend/contracts/w1/v1/private-w2-command-dispatch.schema.json`의 원본 바이트를
`tests/fixtures/w1_core_decision_pin/`에 보관하고 manifest에 출처/SHA256을 기록했다.
해당 validator를 실제 호출한 테스트는 **이미 존재하는 pin과 W2 command의 값 호환성**만 증명한다.
W1 DB row/pin 생성, dispatch 전체 Schema 또는 실제 inbound 수신 성공을 증명하지 않는다.

W1 회신에 기록된 이전 W3 `b6a3631… / pushed=false`는 과거 전달본이다.
C01 후속 `05f26b4c0a52aecf155a66df0fcaa38e6fbd4cf5`는 push 완료됐지만,
그 C01 문서도 본 Core Decision 계약을 대신하지 않는다.

## 2. artifact와 실행

| 대상 | 경로 |
|---|---|
| strict event/context 모델·검증·durable producer | `src/w3_knowledge/core_decision.py` |
| JSON Schema·정상2/음성8·consumer 시나리오 | `contracts/core-decision/v0.1-candidate/` |
| 생성/드리프트 검사 | `scripts/export_core_decision_contract.py` |
| 재시작·동시성·위조·입력·W1 호환성 테스트 | `tests/integration/test_core_decision.py` |
| W1 원본·출처 해시 | `tests/fixtures/w1_core_decision_pin/manifest.json` |
| 구현 계획 | `docs/superpowers/plans/2026-09-18-w3-core-decision.md` |

```sh
uv sync --locked
uv run --locked python scripts/export_core_decision_contract.py --check
uv run --locked pytest tests/integration/test_core_decision.py -q
uv run --locked pytest -q
```

생산 API는 `CoreDecisionProducer(path).publish(...)`다. 호출자는
`requested: DecisionContext`, 별도로 확인한 `current: DecisionContext`,
`authenticated_principal="w3"`, `idempotency_key`, `decision_code`, `reason_code`를 전달한다.
context는 `job_id`, `company_id`, `source_id`, `analysis_input_version`이다.
정상/비정상 입력의 실행 가능한 사용 예는 위 테스트에 있다.

**current와 principal 문자열을 넘기는 것 자체는 인증이 아니다.**
이 API는 공개 HTTP endpoint가 아닌 신뢰된 내부 호출용이다. 현재 인증/Job 문맥 조회 어댑터는
미연결이며 외부 사용자 JSON을 current/principal로 바로 전달하면 안 된다.
W1은 Job의 소유자·현재 입력·Source 등록/연결·취소/삭제·권한을 자신의 DB에서 검증해야 한다.
입력 문자열은 정렬 가능한 시간/숫자가 아닌 opaque ID다. 정확히 같은 값인지만 검사한다.

## 3. event 계약

| 필드 | 규칙 |
|---|---|
| schema_version | `w3.private.core-decision/0.1-candidate` |
| message_type / visibility_scope | `w3.private.w1.core-decision` / `PRIVATE` |
| message_id / occurred_at | UUID / timezone 있는 날짜·시간. 재전달 시 모두 동일 |
| producer / decision_owner | `w3` / `W3`; transport 인증 주체도 W3여야 함 |
| decision_scope | `COMPANY_KNOWLEDGE` 고정 |
| job_id / company_id / source_id | UUID 필수. W1 현재 Job·회사·Source 관계와 일치 |
| question_version_id | 필드를 반드시 포함하고 null |
| analysis_input_version | 1~64자, 공백만인 값 거부. W1 현재 입력 문자열과 정확히 일치 |
| decision_version | bool이 아닌 양의 정수 |
| decision_code / is_core | `CORE_REQUIRED`/true 또는 `NON_CORE_OPTIONAL`/false |
| reason_code | `^[A-Z][A-Z0-9_]{0,63}$`; 자유 서술·원문·개인정보를 싣지 않음 |

event에서 decision_id를 발급하지 않는다. W1이 수락 transaction에서 DB decision_id를 발급하고
`origin_message_id=message_id`로 결속한다. W1 pin의 analysis_input_version은 문자열을 보존한다.
Core인 경우 W2 projection의 `input_version`, `core_source_decision.decision_revision`,
`core_source_decision.analysis_input_version`은 **모두 decision_version 정수**다.
W2의 `decided_by=W3`, `rationale=reason_code`도 같아야 한다.
NON_CORE_OPTIONAL은 Core command를 승인하지 않는다. 해당 판단의 W1 저장·Job 전이 채택은 확인이 필요하다.

이 모듈은 **명시적으로 내려진 W3 판단을 생산**한다. C01 READY, 모델 confidence,
자료 누락을 근거로 임의로 CORE_REQUIRED를 결정하지 않는다. 실제 기업 분석 서비스가 이 API에
도메인 판단을 공급하는 연결과 업무별 reason_code 목록은 아직 없다. fixture reason은 합성 예시다.

## 4. revision·중복·stale 제안

생산 revision 할당 단위는 `(company_id, source_id)`이며 W3 COMPANY_KNOWLEDGE 전용이다.
Job/분석 입력이 바뀌어도 초기화하지 않는다. W1 decision 저장 식별에 job_id가 없으므로
다른 Job의 동일 회사/Source/입력에서 같은 revision을 재사용하는 것을 방지한다.
다른 Source에는 독립 순번을 쓴다. transport cursor나 C01 restriction_revision과 관계없다.

- 동일 SQLite DB의 `BEGIN IMMEDIATE` 안에서 revision 할당과 outbox 저장을 함께 확정한다.
- idempotency key 범위는 `(job_id, source_id, key)`. 같은 요청이면 원본 event/ID/시각/revision 반환.
- 같은 key로 회사/입력/판단/reason을 바꾸면 `IDEMPOTENCY_CONFLICT`, 추가 쓰기 없음.
- 재시작·동일 요청 경쟁·서로 다른 요청 경쟁을 실제 SQLite로 검사했다.
- 모든 W3 producer는 **같은 durable DB**를 사용해야 한다. 서로 독립된 DB, DB 유실/초기화,
  다중 호스트의 독립 counter는 지원하지 않는다. DB 교체는 W1과 기준 revision을 조율한 뒤 수행한다.
- `pending(current)`은 해당 Job/Source/회사/입력의 보존 이벤트만 순번대로 반환한다.
  새 입력의 pending에 과거 입력 이벤트를 넣지 않는다. 읽기 시 current는 caller 책임이며 송신 직전과
  W1 수신 transaction에서 다시 확인해야 한다. 모든 항목은 receipt 계약 전까지 pending으로 보존한다.

W1 inbound 채택 시 아래 순서를 제안한다. 구현/통합 완료 선언이 아니다.

1. transport principal W3, 지원 schema, scope/owner, 필드 pair를 검증한다.
2. W1 currentness lock 안에서 Job·회사·Source link·분석 입력·취소/삭제/권한을 확인한다.
   다른 Job의 현재성이나 Source 전체 최신값으로 이 Job의 유효 입력을 대체하지 않는다.
3. `message_id`와 event 정규 JSON의 SHA256을 inbox에 결속한다. JSON은 sorted keys/compact/UTF-8,
   문자열 Unicode와 배열 순서를 보존하며 NaN을 금지한다. event 전체가 digest 대상이다.
   같은 ID/같은 digest는 추가 쓰기 없는 중복, 같은 ID/다른 digest는 충돌이다.
   이미 종료/변경된 Job의 중복도 과거 receipt를 신규 승인으로 해석하거나 재활성화하지 않는다.
4. fresh 비교는 `(W3, job_id, source_id)`의 수락 revision에서 수행한다. 새 메시지가 이전 수락값보다
   작으면 stale, 같으면 충돌, 크면 현재 입력 조건 하에 수락 가능하다. 다른 Job에 할당된 순번 때문에
   revision gap은 정상이며 연속 replay를 요구하지 않는다. 다른 Job 수락값으로 이 Job을 차단하지 않는다.
5. 수락 시 inbox·decision row·Job pin을 한 transaction으로 생성/갱신한다. 예외 시 모두 rollback.
   NON_CORE를 Core로 승격하지 않는다. out-of-order 수신도 오래된 pin으로 되돌리지 않는다.

consumer-scenarios.json에는 이 수용 조건을 재현할 합성 입력이 있다.
이는 W1 inbox·DB transaction을 실행한 테스트가 아니다. W1이 저장·경합·rollback 구현과 함께 CT-12를 수행한다.

## 5. 전달·인증·보관 경계 제안

**권고:** W1 소유 전용 private inbound queue, W3 IAM service role은 SendMessage만,
W1 consumer는 수신·삭제 권한을 갖는다. 후보 topic/message type만 제공하며 기존 W2 result queue에 연결하지 않는다.
W1이 queue 방식 대신 private TLS endpoint를 채택하면 wire 의미를 보존하는 별도 adapter를 검증한다.
실제 queue/endpoint·principal 식별자·재전달/DLQ·ACK 규격과 환경은 W1과 합의 전이다.

제안 설정 이름: `W3_CORE_DECISION_OUTBOX_PATH`, `W1_CORE_DECISION_QUEUE_URL`, `AWS_REGION`.
현재 모듈은 생성자 DB 경로만 사용하며 환경 변수 자동 로드/queue SDK/relay는 구현하지 않았다.
실제 값은 배포 secret/config 경로로 전달하고 Git/fixture/문서에 넣지 않는다.
전달 성공을 W1 수락으로 간주하지 않는다. receipt·ACK 채택 전 event 삭제/완료 표시 API도 제공하지 않는다.

로컬 outbox에 Job/회사/Source ID와 판단 metadata가 남는다. 원문·발췌·사용자 텍스트는 넣지 않는다.
운영 보관 TTL, 사용자 삭제 신호, backup/DB 초기화·counter 보존 정책은 W1과 확정해야 한다.
현재 자동 삭제/TTL/운영 배포는 없다. 이 모듈로 W1/W2 private 삭제·CT15 완료를 주장하지 않는다.

## 6. W1 회신 요청 및 CT-12 완료 조건

| 담당 | 회신/작업 |
|---|---|
| W1 | event shape·revision 할당/비교·NON_CORE 처리·reason registry의 채택/수정 필요 여부 |
| W1 | 실제 current context 조회 경계, queue/endpoint·principal·receipt·보관/삭제 계약 |
| W1 | inbox/decision/pin 원자 수신 코드와 CT-12 harness 제공 |
| W3 | 채택된 경계에 맞춘 실제 분석 caller·transport relay 연결과 재시도 검증 |
| W4 | QUESTION_MATCHING 계약 별도 제공, W1과 company 모순 수정 |
| W1→W2 | W3/W4 각각의 수신/채택/생산 준비 상태 및 고정 revision을 취합 공유 |

CT-12 공동 완료는 실제 W3 event를 W1 authenticated consumer에 전달하여 정상 적용,
동일 event 중복, 같은 ID 다른 본문, stale 입력/revision, 잘못된 Source/회사/owner,
순서 역전, DB 실패 rollback, cancel/delete 이후 재전달의 무효화를 확인한 뒤다.
실제 DB row/pin·inbox 변화와 side effect 부재를 검사하고 양측 full SHA/실행 기록을 남긴다.

## 7. 검증 기록의 해석

최종 실행 수와 명령은 ZIP `HANDOFF_RECEIPT.json` 및 `verification/pytest.xml`을 기준으로 한다.
W3 로컬 테스트는 producer·JSON Schema·원본 W1 binding 함수 호환성을 검증한다.
W1 수신 runtime, 실제 인증/queue 왕복, CT-12, 배포, 자동 Core 판단 품질은 검증하지 않았다.
W2 인계서의 1006 통과/기존 삭제6 실패 등은 W2 보고이며 이번 W3 실행 결과에 합산하지 않는다.
