# W1 → W4 Question Core 실제 Runtime 인계 요청

작성일: 2026-09-19
송신: W1
수신: W4
W1 기준 commit: `519b9127227ad3ca6483a61a5143d355c5eea5eb` (`develop`)
상태: **W4 후보 검토 회신 수령 완료 / W1 private context 경계 구현·검증 완료 / W4 actual producer 인계 대기**

## 1. 이 요청의 범위와 현재 판정

W1은 W4의 다음 두 전달본을 검토했다.

- `w4/EPICK_W4_Candidate_Review_2026-09-19_r3/`
- `w4/EPICK_W4_Mandatory_R3_Reply_2026-09-19_r3.1/`

두 전달본은 후보 원문 byte hash 대조, D01 기술적 수용, 후보 codec·outbox·relay 및 로컬
재시작 검증을 제공한다. 이는 W1 구현과 후보 계약 검토에는 유효한 근거다.

그러나 두 전달본은 실제 W4 producer 연결에 필요한 항목을 아직 제공하지 않았다고 명시한다.

| W4 전달본 근거 | 명시된 상태 | W1 판정 |
| --- | --- | --- |
| r3 `00-START-HERE.md` | 양측 채택·운영 정책 승인·실제 context/queue·공동 CT-12 미완료 | 후보 검토 완료이지 runtime 준비 완료가 아님 |
| r3.1 `00-START-HERE.md` | producer full SHA, 실제 context/policy adapter, image, queue/identity, 공동 CT-12는 `PENDING` 또는 `NOT_RUN` | W1은 실제 Queue policy/IAM principal을 추정할 수 없음 |
| r3.1 `docs/w4-mandatory-r3-reply-2026-09-19.md` §E/F | producer/outbox full SHA·immutable image·send role/SenderId가 `PENDING` | 실제 producer를 pin하거나 sender 인증을 설정할 수 없음 |
| r3.1 같은 문서 말미 | 실제 SQS·W1 인증 context·IAM·PostgreSQL·공동 CT-12 미실행 | 로컬 SQLite/Fake Queue 증거를 운영 연결 증거로 승격할 수 없음 |

따라서 현재 상태는 **`CANDIDATE_REVIEWED_W1_IMPLEMENTED / ACTUAL_W4_RUNTIME_PENDING`**이다.
이는 W4 회신 자체가 없다는 뜻이 아니다. W4가 회신에서 `PENDING`으로 기록한 실제 runtime
artifact를 다음 단계에서 제공해야 한다는 뜻이다.

## 2. W4가 제공해야 하는 실제 runtime 선행물

아래 A–F는 모두 실제 값 또는 추측 없는 `PENDING`과 해제 조건을 포함해야 한다. ZIP, 로컬
uncommitted 변경, Stubber/Fake Queue, 임의의 role/queue 값은 대체 근거가 될 수 없다.

| ID | W4 제공물 | 완료 기준 | W1이 이 값으로 수행하는 작업 |
| --- | --- | --- | --- |
| A | 접근 가능한 producer/outbox/relay full SHA | W4 저장소에서 확인 가능한 40자 SHA. `question_core_producer.py`, `question_core_outbox.py`, `question_core_relay.py`, adopted schema·fixture가 해당 SHA에 포함됨 | producer 구현·schema·fixture를 pin하고 재현 검증 |
| B | immutable producer image | A SHA로 빌드한 image reference와 digest (`sha256:...`), 빌드/기동 검증 결과 | W4 실제 producer runtime을 고정하고 CT-12 입력으로 기록 |
| C | 실제 W1 context/currentness adapter | W1-issued `job_id`만 사용하며 owner/deletion epoch, authorization revision, fence/lease, 취소·삭제·stale input을 send 직전에 재검증하는 구현 경로와 테스트 근거 | W1 consumer의 현재성 검사와 W4 send 전 차단을 공동 검증 |
| D | W4 SQS 송신 identity | W4 workload의 send-only Role ARN 및 세션 접미사가 없는 stable `SenderId`; 값은 승인된 별도 안전 채널로 전달 | W1 전용 Queue policy의 `SendMessage` principal과 `W4_QUESTION_CORE_DECISION_EXPECTED_SENDER_ID`를 정확히 설정 |
| E | 실제 송신/복구 증거 | 실제 격리 SQS에서 commit-before-send, 응답 유실, restart, 동일 body 재전송, altered replay·revoked/stale 차단을 보이는 실행 결과 | W1 durable receipt·SQS delete 경계와 공동 CT-12를 결속 |
| F | 운영 정책 승인 상태 | P1/P2/P3별 정책 owner, revision, 승인/보류 상태, 실제 enablement 조건. P2/P3이 미승인이라면 `DISABLED`를 명시 | W1은 미승인 의미 판단을 실행하거나 실제 사용자 자료 전송을 활성화하지 않음 |

## 3. 안전한 회신 양식

W4는 아래 형식을 복사해 작성한다. Role ARN, SenderId, Queue URL, secret, token, 운영 DB DSN은
문서에 쓰지 않는다. D의 실제 값은 W1 AWS 담당자에게 승인된 별도 안전 채널로 전달하고, 문서에는
전달 완료 여부와 값의 fingerprint 또는 참조 ID만 기록한다.

```text
# W4 → W1 Question Core actual runtime handoff

status: <READY_FOR_W1_AWS_SETUP | PENDING>
W4 repository/access path: <repository URL>
W4 producer/outbox/relay full SHA: <40-char SHA | PENDING>
W4 adopted schema path: <tracked path | PENDING>
W4 valid/negative fixture paths and canonical digest vectors: <paths | PENDING>

## A. Reproducible producer
commit verification command and result: <command/result | PENDING>
included producer/outbox/relay paths: <paths | PENDING>
candidate-to-adopted compatibility and unsent-outbox handling: <description | PENDING>

## B. Immutable runtime image
image reference: <non-secret reference | PENDING>
immutable image digest: <sha256:... | PENDING>
image start/restart verification command and result: <command/result | PENDING>
persistent volume/store and recovery boundary: <description | PENDING>

## C. Authenticated W1 context and currentness
W1 context adapter path: <path | PENDING>
trusted input fields accepted from W1: <field names only | PENDING>
send-time checks: <owner/deletion epoch/authorization revision/fence/lease/input revision | PENDING>
revocation/cancel/delete/stale test command and result: <command/result | PENDING>

## D. SQS identity — actual values via approved secure channel only
W4 send-only role delivery: <secure-channel reference or PENDING>
stable SenderId delivery: <secure-channel reference or PENDING>
send permission scope: <SendMessage to dedicated W4 main queue only | PENDING>
no-receive/no-delete/no-W1-DB-access confirmation: <confirmed | PENDING>

## E. Actual isolated transport evidence
isolated runtime identity: <non-secret run/environment label | PENDING>
actual SQS send/retry/restart result: <result/evidence path | PENDING>
same-id/same-body replay result: <result | PENDING>
same-id/different-body and revoked/stale rejection result: <result | PENDING>

## F. Policy and CT-12 readiness
P1 owner/revision/status: <value | PENDING>
P2 owner/revision/status: <value | PENDING>
P3 owner/revision/status: <value | PENDING>
REAL user-data emission state: <DISABLED unless explicitly approved>
W4 CT-12 executor and teardown responsibility: <role/responsibility | PENDING>
blocking condition and release condition for every PENDING item: <value>
```

## 4. 조정한 실제 runtime 순서

W4 r4의 E1 지적은 맞다. “실제 SQS 증거를 먼저 받고, 그 뒤에만 queue를 만든다”는 순서는
성립하지 않는다. W1은 context adapter와 격리 실행 방식을 먼저 제공했고, 아래의 **단계형** 순서로
진행한다. 상세 계약은 `md/w4/W1_W4_CT12_Environment_and_Context_Contract_2026-09-19.md`다.

1. **완료 — W1 context 경계**: W4 전용 private adapter, opaque W1-issued context, separate DB role,
   `SYNTHETIC` fail-closed 정책, Docker internal network profile을 구현·격리 DB에서 검증했다.
2. **W4 선행물**: W4는 이 route를 사용하는 HTTP adapter와 producer/outbox/relay를 commit/push하고,
   image entrypoint·volume·assume-role 요구사항을 제공한다. 이 단계에서는 Queue URL/role 값을 추측하지
   않는다.
3. **W1 provisioning**: W4 tracked SHA와 실행 요구가 확인되면 W1은 W4 전용 ECR repository, disposable
   CT-12 PostgreSQL/login/bearer, W4 send-only role과 dedicated SQS main/DLQ를 만든다.
4. **identity binding**: W4가 실제 role credentials로 기동하면 W1은 stable SenderId를 안전 채널로 확인해
   queue policy와 `W4_QUESTION_CORE_DECISION_EXPECTED_SENDER_ID`를 고정한다. W1 Worker Role에는
   receive/delete/change-visibility와 필요한 attribute 조회만 부여한다.
5. **공동 검증**: immutable W1/W4 image pin으로 preflight를 통과한 뒤 W4/W1/W2가 CT-12를 실행한다.
   terminal receipt·duplicate·stale·취소/삭제·명시적 retry/W2 command 경계를 실제 PostgreSQL/SQS에서
   기록한다.

## 5. 종료 기준

이 문서의 회신만으로 W1–W4 runtime 연동이 완료되는 것은 아니다. 다음이 모두 충족되어야 한다.

- A–F가 실제 artifact와 재현 가능한 근거로 확인됨
- W1이 추측 없는 Role/SenderId로 전용 Queue/IAM을 구성하고 preflight를 통과함
- W4 실제 producer와 W1 consumer가 동일한 adopted schema/fixture/image pin을 사용함
- 공동 CT-12의 transport, duplicate, restart, stale, 취소·삭제, user retry/W2 경계가 모두 통과함
- P2/P3 미승인 상태에서는 REAL 사용자 자료 발행이 계속 `DISABLED`임

이 요청은 W4에 W1 receipt ACK API/event를 추가하도록 요구하지 않는다. W1의 durable receipt와
terminal SQS delete가 W1 수신 경계이며, W4는 동일한 immutable body를 안전하게 재전송할 수 있다.
