# W1 → W4 CT-12 격리 환경·현재성 Context 계약

작성일: 2026-09-19
상태: **W1 구현·격리 DB 검증 완료 / W4 adapter 채택·actual image·SQS 송신 증거 대기**
W1 기준 변경: migration `029_w4_question_core_context` 및 W4 context adapter
적용 범위: **Question Core CT-12 합성 데이터 전용**

## 1. 이 문서가 확정하는 것

W4 r4 회신의 C1/E1 blocker를 다음처럼 분리한다.

- W1은 W4 전용 private context adapter와 격리 실행 경계를 제공한다.
- W4는 이 adapter를 실제 producer/outbox/relay에 연결하고, immutable image와 실제 SQS 송신
  증거를 제공한다.
- W1의 context 조회는 **사전 차단** 경계다. W1 inbound consumer의 User → Job → Project →
  Question → Source lock과 commit 시 재검증을 대체하지 않는다.
- 이 계약은 W1 receipt ACK API/event를 새로 만들지 않는다. W4는 기존 방식대로 전용 SQS main
  queue로 immutable decision을 보낸다.

`REAL` 사용자 자료의 W4 발행은 P2/P3 운영 정책이 승인될 때까지 계속 **DISABLED**다. 이번
interface가 존재한다는 사실만으로 REAL 처리가 허용되지는 않는다.

## 2. CT-12 실행 환경 결정

| 구분 | 확정한 방식 | 금지 사항 |
| --- | --- | --- |
| 실행 위치 | staging private Worker EC2의 Docker Compose network `worker_default` | public IP, public API router, host-port 공개 |
| W1 context service | Compose service `w1-w4-question-core-context`, Docker 내부 `8081`만 `expose` | W2 protected lookup의 `8080`/bearer 재사용 |
| W4 producer | W4 immutable image의 별도 container. `worker_default`에 external network로 연결해 service DNS `w1-w4-question-core-context:8081`만 호출 | W1 DB login, W1 worker env 파일, W1 queue receive/delete 권한 |
| W1 CT-12 DB | 이름에 `w1`·`ct12`가 들어간 disposable PostgreSQL. W1 context service와 inbound consumer가 이 DB만 사용 | RDS 운영 DB, T043/T059/T060 DB, W4 SQLite DB 공유 |
| W4 durable outbox | W4 전용 persistent volume/SQLite WAL. W4가 start/restart 증거를 담당 | W1 PostgreSQL table 직접 write 또는 volume 공유 |
| 이미지 registry | W1이 W4 tracked SHA 수령 후 staging account ECR의 W4 전용 repository/push 권한을 만든다. URI/digest는 생성 후 안전 채널로 전달한다. | tag만으로 배포, W1 backend ECR repository 덮어쓰기 |
| AWS identity | W1 Worker EC2 role은 W4 전용 send-only role만 `sts:AssumeRole`할 수 있게 제한한다. W4 container는 그 역할로 main queue `SendMessage`만 수행한다. | W4 container에 W1 EC2 role 전체 권한, receive/delete/purge, W1 DB 접근 |
| 비밀 전달 | W1이 별도 root-owned env/Secret Manager 참조로 `W1_W4_CONTEXT_BEARER`와 context DB login을 주입한다. 문서·commit·console 로그에는 값이 없다. | token/DSN/Role ARN/SenderId/Queue URL 문서 기재 |

이 표는 환경 **원칙과 연결 방식**을 확정한다. 실제 ECR URI, Role ARN, stable SenderId, queue URL은
W4 SHA·image·deployment entrypoint를 받은 뒤 W1이 생성하며, 이 문서가 그 값을 추측하는 근거가
될 수 없다.

## 3. W1 private context interface

구현 경로:

- model: `backend/app/models/w4_question_core.py`
- currentness resolver: `backend/app/runtime/w4_question_core_context.py`
- private FastAPI adapter: `backend/app/runtime/w4_question_core_context_adapter.py`
- startup: `backend/scripts/run_w4_question_core_context_adapter.py`
- Compose profile: `w4-question-core-context`
- migration: `029_w4_question_core_context`

### 3-1. 인증·전송 규칙

```text
POST /internal/v1/w4/question-core-contexts/resolve
Content-Type: application/json
Authorization: Bearer <W1_W4_CONTEXT_BEARER>
X-EPICK-Service-Principal: w4
```

- `W1_W4_CONTEXT_BEARER`는 W2 lookup bearer와 완전히 다른 값이다.
- service principal은 정확히 `w4`여야 한다.
- route는 public FastAPI router에 등록되지 않으며, OpenAPI/docs와 host port를 노출하지 않는다.
- context key는 W1이 transaction 안에서 생성한 무작위 UUID다. W4는 저장·재사용만 하며 파생하거나
  다른 job ID로 바꾸지 않는다.

요청 body:

```json
{
  "schema_version": "w1.private.w4-question-core-context.v1",
  "context_key": "<W1-issued opaque UUID>"
}
```

성공 응답의 schema는 동일하며, 아래 필드만 제공한다.

| 필드 | 의미 |
| --- | --- |
| `context_key`, `job_id`, `question_version_id`, `source_id`, `analysis_input_version` | W1이 발급 시 고정한 결속값. W4 event는 이 값과 정확히 일치해야 한다. |
| `authorization_revision` | owner deletion epoch, execution fence, Job 상태·lease, 질문/Source/action 현재성, W4 decision cursor를 포함해 W1이 계산한 opaque SHA-256 marker. 원값은 노출하지 않는다. |
| `current_decision_version` | 해당 W4/QUESTION_MATCHING 결속의 현재 최대 version, 없으면 `0`. |
| `data_kind` | 현재 발급은 `SYNTHETIC`만 가능하다. `REAL` 발급은 코드에서 거부한다. |
| `processing_allowed` | W4가 send를 진행할 수 있는지의 사전 판정. `false`이면 새 outbox 작성·기존 outbox 재전송을 하지 않는다. |
| `question_current`, `source_active`, `revoked`, `valid_until` | fail-closed 판단에 필요한 상태. `valid_until`은 UTC RFC3339 timestamp다. |

다음은 응답에 포함하지 않는다: owner ID, company/project ID, prompt, source URL/content, raw deletion
epoch, raw execution fence, W1 DB credential, SQS credential.

### 3-2. 현재성·경쟁 상태 책임

W1 resolver는 요청 때마다 다음을 다시 읽는다.

1. context의 Job/owner 결속, Job `execution_fence`, owner deletion epoch, input version, Job 상태 및
   active lease
2. 현재 Project version과 ACTIVE Question/current QuestionVersion
3. JobSourceLink와 Source 존재, open `CORE_DECISION_REQUIRED` action
4. 현재 W4 `QUESTION_MATCHING` decision version

다음 중 하나라도 달라지면 `processing_allowed=false`로 응답하고 `authorization_revision`도 바뀐다:
취소·삭제, deletion epoch/fence/input 변경, Job이 `WAITING_USER`가 아님, lease 획득, 현재
Question/Source/action 변경, context revoke/만료.

그러나 resolver 응답 뒤에 상태가 바뀔 수 있다. 따라서 W4는 **prepare 직전·send 직전·재시작 복구
직후**에 다시 resolve해야 하고, W1은 수신 후 `W4QuestionCoreInboundService`의 locked transaction에서
동일한 owner/job/question/source/action 결속을 다시 검증한다. 사전 조회 성공은 commit 권한이 아니다.

### 3-3. 상태 코드

| HTTP | code | W4 처리 |
| --- | --- | --- |
| 200 | context 응답 | `processing_allowed=true`일 때만 다음 단계 진행 |
| 401 | `UNAUTHENTICATED_SERVICE_PRINCIPAL` | terminal; 설정/secret 교체 전 재시도 금지 |
| 403 | `FORBIDDEN_SERVICE_PRINCIPAL` | terminal; W4 principal 설정 수정 필요 |
| 404 | `W4_CONTEXT_NOT_FOUND` | terminal; 새 W1-issued context 없이 재시도 금지 |
| 422 | `INVALID_W4_CONTEXT_REQUEST` | terminal; body/schema 수정 필요 |
| 503 | `INTERNAL_RETRYABLE` | W4 durable outbox lease 규칙에 따라 동일 body/ID만 재시도 |

## 4. DB role·설정 준비

`029_w4_question_core_context`은 별도 NOLOGIN group role `epick_w4_context`를 요구한다. 이 role은
private adapter가 계산에 필요한 **column-level SELECT**만 받고 INSERT/UPDATE/DELETE, prompt·URL·content
column, W1 worker role membership을 받지 않는다. W1이 RDS 관리자 세션에서 이 group role과 전용 login을
만들고 `runtime_privileges.sql`을 재적용한 뒤, 다음 이름만 별도 root-owned env에 넣는다.

```dotenv
W4_CONTEXT_DATABASE_URL=<epick_w4_context_login DSN; secret manager reference>
W1_W4_CONTEXT_BEARER=<random secret manager value>
```

W4 container가 받는 값은 다음뿐이다. 값 자체는 W1이 안전 채널로 전달한다.

```dotenv
W1_W4_CONTEXT_BASE_URL=http://w1-w4-question-core-context:8081
W1_W4_CONTEXT_BEARER=<same secret, injected at deploy time>
W1_W4_CONTEXT_PRINCIPAL=w4
```

## 5. W4가 지금 수행할 작업

1. 이 문서의 route/schema/status·`processing_allowed=false` 규칙을 W4 source에 채택한다.
2. W4 `W1ContextPort`의 실제 HTTP adapter를 구현한다. local fake adapter를 runtime 근거로 바꾸지 않는다.
3. `prepare`, send 직전, restart recovery에서 resolve를 호출하고, `authorization_revision` 변경·false·만료 때
   새 send를 차단하는 테스트를 commit에 포함한다.
4. producer/outbox/relay와 adopted schema/fixture를 포함한 접근 가능한 full SHA를 제공한다.
5. image entrypoint, SQLite volume 경계, 필요한 env 변수명, W4 send-only role assume-role 요구를 제공한다.

W4가 1–5를 제공하면 W1이 다음 단계에서 ECR repository, disposable CT-12 DB/login/bearer, W4 send-only
role, dedicated SQS main/DLQ와 queue policy를 순서대로 만든다. Queue identity는 W4 role이 실제로 생성된
뒤에만 expected SenderId로 고정한다.

## 6. 현재 검증 증거와 남은 검증

W1은 격리 PostgreSQL `epick_w1w4_phase1_test`에서 다음을 검증했다.

- migration `029` 적용
- `epick_w4_context` role의 column-limited SELECT로 adapter가 context를 resolve함
- 정상 synthetic context에서 `processing_allowed=true`
- Job cancel 주입 후 `processing_allowed=false`, `revoked=true`, opaque authorization revision 변경
- unknown context `404`, bearer 누락 `401`
- `REAL` context 발급 거부

이는 W1 private context 경계의 증거다. W4 actual image, W4 durable outbox restart, actual AWS SQS send,
stable SenderId, W1/W4/W2 공동 CT-12은 아직 검증하지 않았으며 완료로 선언하지 않는다.
