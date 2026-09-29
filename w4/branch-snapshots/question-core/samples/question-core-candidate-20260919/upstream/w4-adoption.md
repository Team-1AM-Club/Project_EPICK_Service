# W1 → W4 Question Core 계약 채택 요청

상태: **READY_TO_SEND / W4_RESPONSE_PENDING**  
작성일: 2026-09-18  
W1 기준 revision: `41dd0c21692c7f59c87962cf1e7d2746bac73303` (`develop`)

## 1. 요청 목적과 근거

이 문서는 다음 두 요청을 하나의 구현 가능한 W1↔W4 경계로 확정하기 위한 채택 요청이다.

- W2 회신 §4: `QUESTION_MATCHING` pin은 `company_id=null`인데 W2 command에는 company UUID가
  필요하므로, W1이 현재 Job 관계에서 회사를 신뢰 가능하게 해석하고 세 경계(command 생성,
  relay, protected lookup)에서 재검증해야 한다.
- W2 회신 R3-05: W1이 W4의 Core Decision inbound schema, 정상/음성 fixture, 접근 가능한 pin,
  input/owner/stale/dedup 규칙, producer 준비 상태와 공동 CT-12 조건을 취합해야 한다.

W4 전달본은 회사 관계 조회 후보와 로컬 부분 통합 근거를 제공하지만, W4 이벤트에 명시적
`job_id`가 없고 실제 W4 inbound 영속화·durable outbox·SQS 전송·PostgreSQL·공동 CT-12는
미완료라고 명시한다. 따라서 W1은 전달본의 Service patch를 통째로 병합하지 않고, 아래 계약이
채택된 뒤 필요한 부분만 선택적으로 이식하고 강화한다.

## 2. 검토 대상

- 후보 wire schema:
  `specs/004-w1-w4-question-core/contracts/w4-question-core-decision.event.schema.json`
- 의미와 인증 경계:
  `specs/004-w1-w4-question-core/contracts/README.md`
- W4 근거 전달본:
  `w4/EPICK_W1_W4_Question_Core_Local_2026-09-18/`
- W2 원문:
  `md/orig2/W2_Implementation_Handoff_2026-09-18.md` §4, §5 R3-05

후보 schema의 핵심은 다음과 같다.

1. W4는 W1이 발급한 불변 `job_id`를 trusted decision context로 받아 같은 값을 반환한다.
2. `message_id`와 W4의 `decision_id`를 분리한다.
3. `decision_scope=QUESTION_MATCHING`, `decision_owner=W4`, `producer=w4`를 고정한다.
4. question pin의 `company_id`는 `null`로 유지한다. owner, project, company hint는 W4가 보내지
   않으며 W1이 `job_id`에서 현재 관계를 검증한다.
5. `question_version_id`, `source_id`, `analysis_input_version`, `decision_version`을 명시한다.
6. body의 `producer=w4`만으로 인증하지 않는다. W4 전용 queue와 설정된 transport sender가
   일치해야 한다.
7. W1은 Job을 추정하거나 “현재 사용자의 최근 Job”으로 대체하지 않고, `job_id` 누락 시
   fail-closed 처리한다.

## 3. 채택 요청 항목

### A1. Wire 계약과 Job 결속

다음 항목을 `채택 / 수정 / 보류` 중 하나로 답한다.

- 후보 JSON Schema 전체와 flat event 형식
- W1-issued `job_id` 필수 및 Job 추정 금지
- `message_id`와 `decision_id` 분리 및 재시도 시 불변 유지
- owner/project/company hint를 payload에서 제외
- `QUESTION_MATCHING.company_id=null` 유지
- W4 전용 schema/principal/queue 사용과 W3 worker 재사용 금지
- 동일 제출 재시도 시 ID·`occurred_at`·canonical body 동일성 유지

수정이 필요하면 필드명, 형식, 의미, 호환성 및 fixture 예시를 함께 제시한다. `job_id`를 채택할
수 없다면 안전한 대체 결속이 합의될 때까지 W1 구현은 Phase 1에서 중단된다.

### A2. P1–P3 판단 정책

| ID | 채택 요청 |
| --- | --- |
| P1 | W4는 host가 승인한 명시적 문항/Source 근거만 판단한다. LLM `SUPPORTED`, 경험 우선순위 또는 W3 C01 `USABLE`을 자동으로 Core로 변환하지 않는다. |
| P2 | 평가에 반드시 필요한 자료는 `CORE_REQUIRED`, 맥락 보완 자료는 `NON_CORE_OPTIONAL`이다. 기준이 불명확하면 발행하지 않고 보류한다. |
| P3 | reason code 후보는 Core에 `QUESTION_EVIDENCE_REQUIRED`, Non-Core에 `SUPPLEMENTARY_CONTEXT`이다. 원문·개인정보를 reason code에 넣지 않는다. |

W4 또는 product 승인이 필요한 정책은 담당자와 승인 조건을 명시한다. 승인 전 실제 사용자
자료에 대한 자동 발행은 비활성 상태로 유지한다.

### A3. T1–T5 책임 경계

| ID | 책임 |
| --- | --- |
| T1 W4 | 문항/Source 의미 판단, W1-issued `job_id` 보존, stable IDs/body, durable prepared/sent outbox, 발행 직전 currentness 재검사, W4 main queue에 대한 send-only relay |
| T2 W1 | transport principal 검증, Job/owner/project/question/Source/input/epoch/fence 현재성 검사, receipt·decision·binding 원자 저장, 사용자 retry, W2 command/outbox, relay·protected lookup 재검증 |
| T3 W2 | W1이 승인한 command만 처리하고 protected lookup과 commit gate를 수행. W4 receipt 자체를 수집 완료로 해석하지 않음 |
| T4 W3 | 분석 지식·사용 가능성 제공. W4 판단 또는 W2 ACK를 W3 usability 계약으로 대체하지 않음 |
| T5 재실행 | W4 receipt는 Job을 `WAITING_USER`에 유지한다. W2 command는 사용자의 명시적 retry가 새 fence를 만들 때만 생성 |

새 W1→W4 ACK API/event는 이번 범위에 추가하지 않는다. W1은 durable terminal outcome 뒤 SQS
delivery를 삭제하고 내부 receipt를 보존하며, W4는 자체 outbox 상태와 재시도 책임을 가진다.

### A4. W4 구현·증거 요청

W4는 채택 회신과 함께 다음을 제공한다.

1. 채택 schema를 실제로 사용하는 codec과 정상/음성 fixture 경로
2. `job_id`를 trusted context에 넣는 경로와 W1 context가 아닌 임의 입력을 거부하는 테스트
3. durable outbox의 prepared/sent 상태, 불변 message/decision/body, retry metadata
4. send 전 currentness·철회·stale 검사와 send-only SQS relay
5. commit-before-send, send-response loss, restart, duplicate, altered replay, revoked/stale 차단 테스트
6. 위 구현을 포함하고 W1이 접근 가능한 commit의 **full SHA**와 재현 명령
7. secret·token·운영 DB 값이 없는 안전한 환경 변수명 및 공동 CT-12 참여 조건

현재 전달된 `service-change.patch` 또는 SQLite/합성 adapter 결과는 위 producer/outbox 완료 증거로
간주하지 않는다.

## 4. W4 회신 양식

아래 표를 항목별로 작성한다. “전체 동의” 한 줄 대신 각 항목의 상태와 근거를 남긴다.

| 항목 | 상태 (`채택/수정/보류`) | 근거 파일·테스트 | 요청 변경 또는 blocker |
| --- | --- | --- | --- |
| A1 schema/flat event |  |  |  |
| A1 explicit `job_id` / no inference |  |  |  |
| A1 stable IDs/body retry |  |  |  |
| A1 null company / no hints |  |  |  |
| A1 dedicated auth/queue |  |  |  |
| P1 |  |  |  |
| P2 |  |  |  |
| P3 |  |  |  |
| T1 |  |  |  |
| T2 |  |  |  |
| T3 |  |  |  |
| T4 |  |  |  |
| T5 |  |  |  |

추가 회신 필드:

```text
W4 repository URL/access path:
W4 producer/outbox full SHA:
Schema path:
Valid fixture path:
Negative fixture paths:
Codec/currentness test command and result:
Outbox/restart test command and result:
Safe env var names:
Joint CT-12 owner/contact:
Earliest joint CT-12 milestone or blocking condition:
```

## 5. 채택 및 진행 기준

Phase 2 진입 조건은 다음과 같다.

- W4가 명시적 `job_id`와 후보 schema를 채택했거나, 합의된 모든 변경이 이 문서와 schema에
  반영되어 있음
- P1–P3의 미승인 항목이 실제 데이터 enablement blocker로 명시되어 있음
- T1–T5 책임에 이견이 없거나 이견별 owner와 해결 조건이 있음
- producer/outbox 구현의 접근 가능한 full SHA가 제공되었거나, 아직 미완료라는 사실과 목표
  milestone이 명시되어 있음

현재 결과는 **계약 요청 준비 완료**이지 **W4 채택 완료**, **producer 준비 완료** 또는
**공동 CT-12 완료**가 아니다.
