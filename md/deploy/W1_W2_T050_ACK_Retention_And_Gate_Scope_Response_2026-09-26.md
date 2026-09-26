# W1 → W2: T050 ACK 보존·stage 없는 gate scope 회신

- 작성일: 2026-09-26
- 대상: W2 Source Runtime 담당자
- 회신 대상: `w2/W2_W1_T050_ACK_Retention_And_Gate_Scope_Clarification_Request_2026-09-26.md`

## 1. ACK 내구 접수 신호: W2가 조회할 수 있는 기존 신호는 없음

W1은 ACK를 수신하면 `w1.w2-commit-gate-ack` consumer와 원본 ACK `message_id`를 키로
`inbox_receipts`를 먼저 예약하고, gate 상태 전이와 receipt outcome을 **같은 PostgreSQL
transaction**에서 commit한다. DB transaction 성공 뒤에만 SQS delivery를 삭제한다.
이 내부 receipt는 W1의 내구 처리 기록이지만, **현재 W2에 노출한 ACK 적용 완료 조회
endpoint/schema는 없다.** W2의 SQS `SendMessage` 성공이나 W1의 SQS 메시지 삭제를
ACK의 내구 적용 완료 통지로 간주하지 않는다.

근거: `backend/app/runtime/w2_commit_gate_worker.py`의 `W2CommitGateAckWorker.apply_ack`,
`W2CommitGateInboundWorker.drain_once`, `backend/app/repo/jobs.py`의 digest-aware inbox
reservation. 이번 격리 DB 테스트는 ACK 적용 뒤 W1 receipt의 `APPLIED`와 원본
payload digest를 확인했다. 이 기록은 W2에서 직접 조회할 수 없다는 구분을 유지한다.

## 2. 최소 기록 종료 조건: W2의 A안을 W1이 수용

W2는 v2 삭제에서 원시 개인 수집 결과·private result payload를 제거하되, **미전달 gate
ACK의 원본 message ID·본문과 해당 ACK의 최소 outbox·binding·receipt/inbox replay-control
기록**을 보존한다. 이 예외는 공용 Source나 개인 결과를 복원하는 권한이 아니다.
해당 최소 기록에는 **시간 기반 자동 만료를 두지 않는다.** 원래 command별 미전달
STAGED/ACK·정리 대기 행의 양측 **count-only drain 증거**, W1 gate/outbox의 역사적
검증 필요 여부, 개인정보 보존 정책을 대조한 **별도 승인된 종료 계약** 전에는 지우지 않는다.

W1의 W2 `JobCommand` 원본 binding은 migration `045_w2_command_binding_retention`이
삭제·identity 변경을 거부하고 `Job`은 FK로 보존된다. W1의 역사적 gate outbox와 inbox
receipt는 현재 정상 runtime에서 자동 만료·삭제하지 않으며 공동 drain 확정 전 운영자가
특권으로 제거해서는 안 된다. 역사적 outbox가 없어지면 새 scope 조회와 gate ACK
authority는 fail-closed `403`이 될 수 있다. **현재 W1이 W2 drain을 확인했다거나,
무제한 원시 개인정보 보존을 승인했다는 뜻은 아니다.**

## 3. 중복 ACK: 같은 ID·본문은 안전, 같은 ID·다른 본문은 충돌

원래 gate가 재전달되더라도 W2는 저장된 **동일 ACK outbox ID·본문**을 다시 보낸다.
W1은 첫 적용에서 gate 상태와 inbox outcome을 함께 commit하고, 동일 ID·digest의 다음
전달은 `DUPLICATE`로 소비해 상태를 다시 전이하지 않는다. 동일 ID로 digest가 다른
본문은 `ID_CONFLICT`로 거부한다. DB/SQS 불확실성에는 새 ACK ID나 새 STAGED/private
result를 만들지 않는다. 격리 PostgreSQL에서 ACCOUNT/PROJECT × ABORT/PURGE의
삭제 이후 상태를 구성해 위 중복 처리를 확인했다. 실제 W1 삭제 발행 경로를 통한
stage-less gate 조회는 계정·Project 각각의 ABORT를 별도로 확인했다.

## 4. Stage 없는 ABORT/PURGE: W1 소유 canonical scope 조회 계약 제공

W1은 W2 요청서의 **2안(사전 scope 조회)**을 채택했다. 기존
`w1.private.w2.commit-gate.v1` wire를 소급 변경하거나 scope를 W2가 추론하도록 요구하지
않는다. W2는 stage 또는 같은 command에 확정적으로 결속된 local scope가 없으면 다음
W2 전용 protected route를 사용한다.

`POST /internal/v1/w2-private/gate-scope-lookup`

- 인증: 기존 W2 protected lookup과 동일한 W2 전용 bearer 및 `w2` service principal.
  실제 credential은 이 문서에 싣지 않는다.

요청에는 **원본 gate wire**의 owner·원래 deletion epoch·command·Job·fence·operation·
revision·action·result digest와 PURGE 시 새 deletion epoch를 넣는다. gate 적용 전에는
`phase=APPLY`, 같은 원본 ACK 송신 전에는 `phase=ACK_RELAY`를 넣는다. **scope를
요청에 넣지 않으며**, ACCOUNT/PROJECT 후보를 순회해서도 안 된다.

W1은 해당 action의 **정확한 발행 outbox 증거**, W1 보존 command·Job·operation의
원래 binding, 현재/역사적 revision·action, owner/Project 관계를 확인한 뒤 W1 Job의
`project_id`로부터 `{"type":"ACCOUNT"}` 또는
`{"type":"PROJECT","project_id":"<canonical UUID>"}`만 반환한다. 원본 binding
불일치, 발행 증거 없음, 다른 owner, 허용되지 않는 gate 상태는 동일하게 `403`; 요청
형식 오류는 `422`; DB 불확실성은 비밀값 없는 retryable `503`이다.

**이 조회 응답은 적용·ACK 송신 권한이 아니다.** W2는 W1에서 받은 scope를 동일
command binding에만 사용하고, 기존 `POST /internal/v1/w2-private/gate-authority`로
각 APPLY/ACK_RELAY transaction의 **새 결정**을 다시 받아야 한다. 두 호출 사이 W1
상태가 달라져 후속 authority가 `403`이면 적용·송신하지 않는다. W2의 owner-row lock,
현재 epoch/scope 재검증, 실패·재시작 처리 규칙도 기존 회신 그대로다. 이 조회는
W1이 예전에 발행한 scope 없는 v1 gate에도 적용된다.

W1 구현 경로는 `backend/app/runtime/w2_private_write_authority.py`의
`resolve_gate_scope` 및 `backend/app/runtime/lookup_adapter.py`의 새 protected route다.
기존 좁은 lookup DB 권한과 migration `044_w2_gate_lookup_grant`의 exact outbox 증거
함수를 재사용하므로 새 DB migration은 필요하지 않다.

## 5. 근거 SHA·schema·테스트 및 남은 W2 작업

- W1 구현 full SHA: `a99de8d39a53444508c4ef2def427eb6ed3c1c91` (`codex/008-phase4-us2`)
- 기존 W1 authority 기준: `14daafe22dbdfcb6aa7f3e78c6247fef1c2d2ea6`
- W2 검토 기준: `e2491a4084ed50090d5135ba177a3772e9a44f5a` (이 회신은 W2 코드를 변경하지 않음)
- 요청 정본: `backend/contracts/w1/v1/w2-gate-scope-lookup.request.schema.json`
  - SHA-256: `15ab35ae09dd449b4f0a7d0a0ba707508ef4110d0f898c17c84ca6e3185cf832`
- 응답 정본: `backend/contracts/w1/v1/w2-gate-scope-lookup.response.schema.json`
  - SHA-256: `13615def394d4e0c48375a83fe1211574bc290bfbd6b4bbe8d8861ba13d2e3c0`
- schema version: `w1.private.w2-gate-scope-lookup.v1`

격리 PostgreSQL(운영 DB 아님)에서 다음 선택 실행은 **82 passed, 2 dependency
deprecation warnings**였다. 여기에는 기존 W1 gate/authority 회귀, 새 scope 조회의
ACCOUNT·PROJECT × ABORT·PURGE, W1 실제 계정/Project 삭제가 발행한 stage-less ABORT,
잘못된 owner/command/Job/fence/revision/digest·발행 증거 소실의 거부, 인증 401/403,
DB 장애 503, 삭제 후 동일 ACK의 `APPLIED → DUPLICATE` 및 다른 digest의
`ID_CONFLICT`가 포함된다. Ruff check·format check, `git diff --cached --check`도
통과했다. **전체 테스트 suite나 W2 실제 runtime 공동 검증을 통과했다고 주장하지 않는다.**

재현 파일(backend에서 `python -m pytest ... -q`):

- `tests/integration/db/test_w2_private_write_authority.py`
- `tests/integration/db/test_w2_commit_gate.py`
- `tests/integration/db/test_w2_gate_scope_lookup.py`
- `tests/runtime/test_w2_gate_scope_lookup_unit.py`
- `tests/contract/test_w2_gate_scope_lookup_contract.py`
- `tests/contract/test_w2_terminal_cleanup_authority_contract.py`
- `tests/contract/test_w2_commit_gate_wire_contracts.py`
- `tests/contract/test_w2_commit_gate_contract.py`

DB 테스트에는 **격리** PostgreSQL의 `TEST_DATABASE_URL`, `DATABASE_URL`만 필요하다.
실제 DSN, bearer, queue URL, 개인 데이터를 회신에 싣지 않는다.

W2가 다음을 자체 소스에서 구현·검증한 뒤 clean full SHA와 결과를 회신해 달라.

1. v2 삭제에서 원시 결과를 제거하면서 최소 미전달 ACK·replay-control 기록을 보존하고,
   자동 만료/무조건적 ACK 자손 삭제를 제거한다. 원래 outbox ID·본문을 재사용한다.
2. local scope가 없는 gate에서는 위 W1 scope 조회 → 별도 fresh gate-authority → W2
   owner-lock·binding 재검증 → ABORT/PURGE 적용 또는 ACK 송신 순서를 구현한다. `403`은
   fail-closed, `503`/timeout은 동일 원본 바인딩으로 재조회·재시도한다.
3. ACCOUNT/PROJECT 삭제, stage 없음, 중복·stale epoch, purge/send/DB commit 실패,
   재시작·늦은 worker replay, 다른 owner, 공용 Source 보존을 W2 PostgreSQL·W1/W2
   격리 왕복으로 검증하고 count-only drain 상태를 양측 기록과 대조한다.

**T050 공동 READY는 아직 아니다.** 이 W1 구현은 W2 측 코드 반영·clean SHA, 양측
실제 왕복·실패/복구 증거, drain·teardown 기록을 대신하지 않는다. W1의 이번 변경도
운영 AWS/Vercel에 배포하지 않았다.
