# W1 → W2: T050 최초 write의 canonical scope 결속 회신

- 작성일: 2026-09-26
- 대상: W2 Source Runtime 담당자
- 회신 대상: `w2/W2_W1_T050_Initial_Write_Scope_Binding_Request_2026-09-26.md`
- W1 구현 full SHA: `8d80a6f0edddd350a1e0308751fdb19bf7318d76` (`codex/008-phase4-us2`)
- 코드 확인: <https://github.com/Team-1AM-Club/Project_EPICK_Service/commit/8d80a6f0edddd350a1e0308751fdb19bf7318d76>

## 1. W1이 확정한 경로: 요청서의 ② protected current-write scope 조회

W1은 기존 collection/direct-registration dispatch의 `v1` wire를 변경하지 않았다. W2가 최초 예약 전에 원본 dispatch의 `command_id`, `job_id`, `authenticated_owner_ref`, `execution_fence`, `owner_deletion_epoch`로 아래 W2 전용 보호 조회를 호출한다.

`POST /internal/v1/w2-private/current-write-scope-lookup`

요청 schema version은 `w1.private.w2-current-write-scope-lookup.v1`이다. 요청 본문은 `schema_version`, `owner_user_id`, `owner_deletion_epoch`, `command_id`, `job_id`, `execution_fence`만 받는다. 후보 `scope`나 `project_ref`를 입력받지 않으므로 ACCOUNT/PROJECT 탐색 경로가 없다. 인증에는 기존 protected lookup의 W2 bearer와 `X-EPICK-Service-Principal: w2`를 사용한다. 비밀값은 이 문서에 포함하지 않는다.

W1은 저장된 `JobCommand`의 owner·Job·command type·상태·fence·deletion epoch, `Job`의 실행 상태·active lease, 저장된 `w2_command`의 동일 binding과 `project_ref`를 확인한다. 저장된 `project_ref`가 W1 `Job.project_id`와 정확히 같아야 한다. `Job.project_id IS NULL`을 W1이 확인한 경우에만 `{"type":"ACCOUNT"}`를, 그렇지 않으면 `{"type":"PROJECT","project_id":"<W1 Job의 canonical UUID>"}`를 반환한다. 현재 owner/Project/Job 권한도 같은 transaction에서 검사한다. 응답에는 `authority_ref`가 없으며 **scope 식별 자체는 private write 허가가 아니다.**

따라서 W2는 수신한 기존 `v1` dispatch의 `project_ref`를 조회 응답과 별도로 대조해야 한다. ACCOUNT 응답에는 `project_ref=null`, PROJECT 응답에는 동일한 canonical Project UUID가 요구된다. null 값만으로 ACCOUNT라고 추정하거나 URL·본문에서 scope를 만들지 않는다. 대조 실패 시 예약·쓰기 없이 거부한다. scope가 일치한 뒤에도 각 private write 직전에 별도의 fresh `POST /internal/v1/w2-private/authority` 결정을 받아야 한다.

기존 `v1` dispatch는 그대로 유지되므로 W1 측 wire 호환을 깨지 않는다. W1 실제 producer/relay를 거치는 테스트가 Core collection과 direct Source registration의 ACCOUNT/PROJECT 네 조합에서 dispatch `project_ref` = 저장된 W1 command `project_ref` = `Job.project_id`임을 확인한다. W2의 **수신 메시지** 대조 구현은 별도 W2 작업이며 W1 테스트로 대체하지 않는다.

## 2. 최초 예약·재시작·terminal cleanup 및 실패 규칙

W2는 첫 `reserve_collection_attempt` 전에 위 조회와 수신 dispatch 대조를 수행하고, 원본 `command_id`와 canonical scope의 결속을 W2의 내구 상태에 저장해야 한다. 재시작·SQS 재전달에는 동일 command binding과 저장된 scope를 재사용하고, 달라진 `project_ref`·owner·Job·fence·epoch 또는 scope를 새 후보로 받아들이지 않는다. 동일 원본 binding에 대한 W1 scope 재조회 결과는 안정적이며, W1 현재 상태가 바뀌면 조회는 fail-closed 한다.

W1 조회의 `401`(인증 실패), `403`(principal 또는 binding/currentness 거부), `422`(형식 오류)는 fail-closed 처리한다. `503`(DB 불확실성)과 transport timeout에는 private write를 하지 않고 **동일 원본 binding으로만** 재시도한다. 다른 scope 후보를 넣어 다시 호출하지 않는다. `200` scope 응답 후 W1 상태가 바뀌어 fresh write-authority가 거부해도 쓰지 않는다.

취소·삭제 후에는 current-write scope 조회와 새 private write가 `403`으로 닫힌다. 이미 존재하는 W2 private 상태의 정리가 필요하면 W2에 저장한 **원래 scope**로 별도 `POST /internal/v1/w2-private/terminal-cleanup-authority`를 요청한다. 이는 Core/direct 및 ACCOUNT/PROJECT에 동일하게 적용한다. 기존 `gate-scope-lookup`은 발행된 gate 증거를 요구하므로 최초 collection 예약의 대체 경로로 사용하지 않는다.

## 3. 정본 경로·해시와 검증 근거

- 요청 schema: `backend/contracts/w1/v1/w2-current-write-scope-lookup.request.schema.json`
  SHA-256 `6e408368995eba4d17144b963cf10c4ce11657e08ad8cacb93c566f1f4dd092f`
- 응답 schema: `backend/contracts/w1/v1/w2-current-write-scope-lookup.response.schema.json`
  SHA-256 `7715bc88b2f293c571d95c60fead9d3bc9f5028e85e29e08003a1d089d693101`
- ACCOUNT/PROJECT 요청·응답 fixture: `backend/contracts/fixtures/v1/w1/w2-current-write-scope-{account,project}.{request,response}.json`
- W1 모델·결속 검사: `backend/app/runtime/w2_private_write_authority.py`의 `CurrentWriteScopeLookupRequest`, `CurrentWriteScopeLookupResponse`, `resolve_current_write_scope`
- 보호 route·HTTP 오류 처리: `backend/app/runtime/lookup_adapter.py`
- 재현 테스트: `backend/tests/contract/test_w2_current_write_scope_lookup_contract.py`, `backend/tests/runtime/test_w2_current_write_scope_lookup_unit.py`, `backend/tests/integration/db/test_w2_current_write_scope_lookup.py`, `backend/tests/integration/db/test_runtime_workers.py`

격리 PostgreSQL에서 기존 W1 authority/gate 관련 회귀까지 포함한 선택 실행 결과는 **83 passed, 2 dependency deprecation warnings**였다. 여기에는 Core/direct × ACCOUNT/PROJECT의 W1 실제 dispatch, 동일 command 재조회, 다른 owner, 변경된·누락된·잘못된 UUID `project_ref`, Job/command 불일치, stale fence/epoch, 취소·삭제의 fail-closed, 원래 scope의 terminal cleanup이 포함된다. 이번 문서 대조에서 계약·단위 테스트를 다시 실행해 **6 passed**를 확인했다. 현재 로컬 Docker 엔진에 연결되지 않아 격리 DB 테스트를 이번 대조에서 재실행하지는 못했다. W2 실제 runtime의 수신·재시작·timeout 테스트나 운영 배포를 통과했다고 주장하지 않는다.

## 4. W2에 요청하는 후속 작업과 완료 경계

W2는 위 W1 full SHA와 두 schema SHA-256을 pin한 뒤, **Core collection과 direct Source registration 각각의 ACCOUNT/PROJECT**에 대해 다음을 W2 소스에서 구현·검증해 달라.

1. 최초 예약 전에 원본 binding으로 scope를 조회하고 수신 dispatch `project_ref`와 정확히 대조한다. 첫 예약과 함께 원래 command–scope 결속을 영속화한다.
2. 예약·claim·heartbeat·stage 등 각 private write 직전에 별도의 fresh write-authority를 사용한다. 재시작·재전달에서 저장된 원래 결속을 유지하고 변경된 scope는 거부한다. `401/403/422`는 fail-closed, `503`/timeout은 쓰기 없이 동일 binding만 재시도한다.
3. 취소·삭제 이후 새 write는 막고, 남아 있는 private 상태만 저장된 원래 scope와 별도의 terminal-cleanup authority로 정리한다. 다른 owner·stale epoch/fence·변조된 수신 `project_ref`·잘못된 Project UUID·Job scope 불일치도 테스트한다.
4. W2 격리 PostgreSQL에서 첫 예약, DB/SQS 불확실성, 재시작·늦은 worker replay를 확인하고, W1/W2 격리 왕복 증거와 W2 clean full SHA를 회신한다.

**이번 회신은 요청서가 막고 있던 W1 소유 canonical scope 출처를 확정한 것이다. T050 공동 READY, W2 구현·테스트, AWS 배포·운영 검증을 완료로 표시하지 않는다.** 실제 credential, DSN, bearer, queue URL, 원시 개인 데이터는 포함하지 않았다.
