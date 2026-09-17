# W1–W2 Source Collection 실행 계약 v1

이 디렉터리와 `../fixtures/v1/w1/`은 W1이 제공하는 W2 연동 정본이다. W2는
이 파일들을 포함한 Service commit SHA를 pin하여 검증한다. 실제 비밀값·사용자
콘텐츠·DB 접속 정보는 계약에 포함하지 않는다.

## 1. W1 → W2 dispatch 및 lookup

W1이 W2에 전달하는 Core 분석 실행 단위는
`private-w2-command-dispatch.schema.json`이다. 분석 없는 단독 Source 등록은
Core Decision의 의미를 빌리지 않고, 아래 §1.1의 별도 계약을 사용한다.

- `message_type`은 `w1.private.w2.collection-command.v1`이고 producer는 `w1`이다.
- `payload_schema_version`은 `w2.collection.v1`이며 `payload`는 W2 소유
  `source-collection.command.schema.json`을 통과해야 한다.
- W2 dispatch deduplication key는 `command_id`이다. W1은 같은 command 재전달 시
  `message_id == payload.command_id`를 유지한다.
- W2는 실행 시작·재개 전마다 `POST /internal/v1/job-commands/lookup`으로
  `lookup_request`를 그대로 제출한다. `AVAILABLE`가 아닌 결과에서는 외부 fetch,
  저장, 재개를 해서는 안 된다.
- SQS send 성공과 relay의 `PENDING → ENQUEUED` DB 표기는 원자적으로 묶일 수 없다.
  따라서 W2가 방금 받은 동일 `command_id`를 lookup할 때에는, W1이 현재
  Job/lease/fence/epoch를 모두 검증한 범위에서 `PENDING` command도 `AVAILABLE`로
  응답할 수 있다. 이는 새 command를 임의로 조회할 수 있게 하는 예외가 아니다.

### 1.1 W1 단독 Source 등록 dispatch

분석이 없는 Source 등록은
`private-w2-direct-source-registration-dispatch.schema.json`을 사용한다.

- `message_type`은 `w1.private.w2.direct-source-registration.v1`이고 producer는 `w1`이다.
- `direct_source_registration_pin`은 `DIRECT_SOURCE_REGISTRATION` scope,
  `decision_owner: "W1"`, `is_core: false`, `decision_code: "NON_CORE_OPTIONAL"`,
  `purpose: "DIRECT_SOURCE_REGISTRATION"`을 모두 고정한다. `question_version_id`는
  반드시 `null`이다.
- pin의 `registration_input_version`은 W1이 생성해
  `analysis_source_decisions.analysis_input_version`에 저장하는 opaque 불변 값이다.
  pin의 `decision_version`은 W2 payload의 `input_version`,
  `core_source_decision.decision_revision`,
  `core_source_decision.analysis_input_version`에 같은 양의 integer로 결속된다.
- W1 내부 `DirectSourceRegistrationService`만 이 pin과 `SOURCE_REGISTRATION` Job,
  `JobSourceLink`, `AnalysisSourceDecision`, 초기 execution command를 한 transaction에서
  만든다. client와 W2는 이 private 필드를 전달하거나 변경할 수 없다.
- W1 worker는 DB의 W1 decision·Source link·input version이 pin과 모두 일치할 때만
  dispatch한다. `SOURCE_COLLECTION` 같은 분석 Job이 이 non-core 예외를 사용하거나
  pin의 core/owner/scope/version을 변경하면 W2 Outbox를 만들지 않고 Job을
  `WAITING_USER`로 전환한다.

두 dispatch 모두 `message_id == payload.command_id`, 동일한 lookup request, canonical
fence/epoch 규칙을 공유한다. W2는 둘 중 어느 경우에도 lookup이 `AVAILABLE`이 아닐 때
외부 fetch·저장·재개를 해서는 안 된다.

lookup request와 W2 payload의 `execution_fence`는 표현만 다르다.

- lookup의 `execution_fence`: 양의 JSON integer가 canonical 값이다.
- W2 payload의 `execution_fence`: 그 값을 선행 0·부호 없이 10진 문자열로 렌더한
  값이다. 예: integer `1` ↔ string `"1"`.
- `command_id`와 `owner_deletion_epoch`는 양쪽 값이 정확히 같아야 한다.

### 1.2 W2 private commit gate

`private-w2-commit-gate.schema.json`은 collection command 또는 lookup을 대체하지 않는다.
W2가 collection result를 private staging에 보관한 뒤, W1이 현재 command/fence/deletion
epoch를 transaction에서 재검증해 발행하는 별도 가시화 제어 command다.

- `message_type`과 `schema_version`은 모두 `w1.private.w2.commit-gate.v1`이고 producer는
  `w1`이다. `message_id`는 W2의 delivery deduplication key다.
- immutable binding은 `operation_id`, `command_id`, `job_id`, `authenticated_owner_ref`,
  `execution_fence`, `owner_deletion_epoch`, `result_digest`다. action이 바뀌어도 이 값은
  바꾸지 않는다.
- `PURGE`에는 불변 `owner_deletion_epoch`와 별도로 더 큰
  `purge_owner_deletion_epoch`가 필수다. 전자는 W2 staging result가 생성된 원래 epoch이고,
  후자는 그 owner의 삭제가 확정된 epoch다. 이 둘을 하나의 가변 필드로 합치면 늦은
  FINALIZE replay를 판별할 수 없으므로 절대로 대체하지 않는다.
- `operation_revision`은 W1이 state를 바꿀 때 단조 증가한다. W2는 현재보다 낮은
  revision의 `FINALIZE`/`ABORT`/`PURGE`를 적용하면 안 된다.
- `PREPARE`는 비가시 staging을 준비 상태로만 전이한다. `FINALIZE`만 private visibility를
  허용한다. 취소가 먼저 확정되면 `ABORT`, owner deletion이 W1 committed result 뒤에
  확정되면 `PURGE`를 사용한다.
- 이 command는 owner-scoped private staging/cache/derived result만 대상으로 한다. W2는
  `sources`, `source_versions`, evidence 등 공용 canonical Source를 삭제·변경할 권한이
  없다.
- lookup `AVAILABLE`은 작업을 시작·재개할 수 있는 사전 currentness 확인일 뿐, W2 private
  결과의 commit 또는 재노출 권한이 아니다. commit-gate의 `FINALIZE` 전에는 어떤 사용자
  가시 projection도 만들면 안 된다.

W2 → W1 ACK Schema와 W2 local staging transaction은 W2 소유 artifact다. W1은 W2가
canonical Schema·fixture·commit SHA를 제공하고 채택할 때까지 이를 backend 소유 정본으로
표기하거나 W1-R2-03 전체 완료를 선언하지 않는다.

### 1.3 W1 내부 Job execution dispatch

`private-job-dispatch.schema.json`은 W1 outbox relay가 W1 execution queue에만 보내는
참조 메시지다. W2/W3/W4와 공유하는 payload가 아니다.

- `message_id`와 `command_id`는 항상 같은 UUID다. SQS Standard 재전달도 같은 값을 유지한다.
- body에는 owner ID, command payload, 원문, token을 넣지 않는다. worker는 수신 뒤 canonical
  `JobCommand`와 `Job`을 PostgreSQL에서 다시 읽고 fence·deletion epoch를 확인한다.
- `payload_ref`는 private Outbox UUID이며 디버그 상관관계용이다. payload 자체를 전달하거나
  읽을 권한을 부여하지 않는다.

`private-command-lookup-response.schema.json`의 semantic response는 모두 HTTP 200으로
돌려준다. `AVAILABLE`만 `command`를 포함한다. `NOT_FOUND`, `STALE_FENCE`,
`STALE_DELETION_EPOCH`, `DELETED`, `INVALIDATED`, `EXPIRED`는 non-retryable terminal
결과이며 `command`는 `null`이다. 일시적 W1 인프라 오류만 protected error의
`INTERNAL_RETRYABLE` 및 HTTP 503으로 표현한다.

이 protected route의 caller는 service principal `w2` 하나다. 전송은 TLS 위에서
`Authorization: Bearer <W2 service credential>`와
`X-EPICK-Service-Principal: w2`를 함께 사용한다. 누락·검증 실패는 HTTP 401 /
`UNAUTHENTICATED_SERVICE_PRINCIPAL`, 인증됐지만 다른 principal은 HTTP 403 /
`FORBIDDEN_SERVICE_PRINCIPAL`이다. 오류 body는 `private-error.schema.json`만 사용한다.

## 2. W2 결과 수신과 W1 receipt

W2 결과는 `private-message-envelope.schema.json`으로 감싼다.

- channel: `w1.private.w2.collection-result.v1`
- message type: `w2.collection.result.v1`
- producer: `w2`
- payload: W2 소유 `source-collection.result.schema.json`

W2는 같은 결과의 재전달에서 `message_id`를 바꾸지 않는다. W1의 inbox
deduplication key는 `(consumer = "w1.collection-result", message_id)`이다. 새 message
ID라도 현재 command의 `command_id`, execution fence, owner deletion epoch가 맞지
않으면 W1은 `STALE_DISCARDED` receipt를 준다.

W1 receipt는 `private-delivery-receipt.schema.json`을 사용한다. `APPLIED`,
`DUPLICATE`, `STALE_DISCARDED`, `REJECTED_SCHEMA`, `REJECTED_PRINCIPAL`,
`RETRYABLE_INFRA_FAILURE`의 fixture가 모두 있다. `RETRYABLE_INFRA_FAILURE`만
`retryable: true`이며, 그 밖의 outcome은 재시도 없이 폐기한다.

## 3. Core Decision pin

W1 inbound Core Decision은 `private-message-envelope.schema.json`과
`core-source-decision.schema.json`을 함께 검증한다.

- `COMPANY_KNOWLEDGE`는 channel `w1.private.w3.core-source-decision.v1`, producer `w3`만
  허용한다.
- `QUESTION_MATCHING`은 channel `w1.private.w4.core-source-decision.v1`, producer `w4`만
  허용한다.
- `decision_id`, inbound `message_id`, scope별 대상 ID, `source_id`, opaque
  `analysis_input_version`, 양의 `decision_version`, `is_core`, `decision_code`,
  `reason_code`가 모두 필수다.

W1은 검증된 Core Decision만 pin하고, `private-w2-command-dispatch`의
`core_decision_pin`에 원본 message/decision 식별자와 검증 값을 보존한다. W2 payload의
편의 필드는 이 pin을 대체하거나 재판정할 수 없다. 같은 W1 consumer/message ID는
`DUPLICATE`, 같은 scope·대상·source·analysis input의 낮거나 이미 적용된 decision
version은 `STALE_DISCARDED`로 처리한다.

### 3.1 Core pin과 W2 command 결속

Core dispatch에서 `JobCommand.payload.w2_command`가 유일한 canonical W2 command다.
Outbox dispatch의 `payload`와 lookup의 `AVAILABLE.command`는 이 저장값을 새로 만들거나
보정하지 않고 그대로 사용한다. transport metadata를 제외한 세 command object는 동일해야
한다.

| Core pin / DB 정본 | W2 command 필드 | 규칙 |
| --- | --- | --- |
| `source_id` | `source_id` | 정확히 같아야 한다. |
| `decision_scope` + DB `decision_owner` | `core_source_decision.decided_by` | `COMPANY_KNOWLEDGE → W3`, `QUESTION_MATCHING → W4`의 대문자 owner를 정확히 사용한다. |
| `is_core=true`, `decision_code=CORE_REQUIRED` | `core_source_decision.is_core=true` | 하나라도 다르면 command를 보정하지 않고 거부한다. |
| `decision_version` | `input_version`, `core_source_decision.decision_revision`, `core_source_decision.analysis_input_version` | 세 정수는 모두 같은 양의 `decision_version`이다. W1 `command_sequence`는 이 값의 대체값이 아니다. |
| `reason_code` | `core_source_decision.rationale` | 정확히 같아야 한다. |

Job Worker와 relay는 DB decision까지 포함해 이 결속을 검사한 뒤에만 W2 command를
생성·publish한다. lookup은 저장된 pin과 command의 pure 결속을 다시 확인한다. 어느
경계에서든 불일치하면 W2 command를 수정하지 않고 send/`AVAILABLE`을 차단한다.

## 4. Public retry 호환성

사용자 retry의 canonical 진입점은
`POST /api/v1/jobs/{job_id}/actions`의 `action: "RETRY"`다. 성공한 수락은 HTTP 202,
public Job body, 그리고 `Location: /api/v1/jobs/{job_id}` header를 반환한다. 202는 DB
수락일 뿐 W2 전달·worker claim·수집 완료를 뜻하지 않는다.

`POST /api/v1/jobs/{job_id}/retry`는 v1 호환 경로로 계속 지원한다. 현재 sunset 또는
지원 종료일은 없으며, 제거는 새 major-version 계약과 migration notice 없이 하지 않는다.
두 경로의 request/response 정본은 `public-job-*.schema.json`과 fixture다.

공개 오류는 `public-api-error.schema.json`을 사용한다.

| HTTP | code |
| --- | --- |
| 404 | `RESOURCE_NOT_FOUND` |
| 409 | `STALE_INPUT` |
| 409 | `ACTION_NOT_ALLOWED` |
| 409 | `IDEMPOTENCY_CONFLICT` |

## 검증

Service repository root에서 다음 명령은 모든 W1/W2 fixture, cross-schema binding, W1
private producer/channel 경계, 그리고 공개 OpenAPI 경계를 검증한다.

```bash
cd backend
python -m pytest tests/contract -q
```
