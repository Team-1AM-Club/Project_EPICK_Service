# W1 → W2: T050 private-write authority 경계 확정 및 W2 구현 요청

- 작성일: 2026-09-26
- 회신 대상: `w2/W2_W1_T050_Authority_Clarification_Request_2026-09-25.md`
- W1 구현 pin: `codex/008-phase4-us2` / **`14daafe22dbdfcb6aa7f3e78c6247fef1c2d2ea6`** (이 문서의 후속 커밋과 구분)
- 대조 기준 W2 owner SHA: `feat/crawler` / `e2491a4084ed50090d5135ba177a3772e9a44f5a`
- 판정: **W2가 요청한 두 W1 소유 계약 경계의 후보 구현·로컬 검증 완료. W2 production 적용, 공동 T050/T058, 운영 배포 및 서비스 READY는 미완료.**

이 문서는 [W1의 기존 T050 요청](W1_W2_Phase4_T050_W2_Action_Request_2026-09-25.md) 중 **authority 상태 전이와 호출 단위**를 구체화한다. 기존 문서의 W1 SHA `b68c45a…`는 그때의 후보이며, 이 경계의 현재 **구현** pin은 위 `14daafe…`이다. W2 v2 삭제 command/ACK schema와 `0010_private_deletion_scope_v2`는 기존 채택 계약을 유지한다. W2 owner 코드, 원시 개인 데이터, credential, DSN, bearer 및 실제 queue URL은 이 회신에 포함하지 않는다.

## 1. W1이 확정·구현한 계약

| 용도 | 보호된 W1 endpoint / 요청 schema | 결정의 한계 |
| --- | --- | --- |
| 현재 유효한 W2 개인 쓰기·STAGED 송신 | `POST /internal/v1/w2-private/authority` / `w1.private.w2-write-authority.v1` | W1 command·Job·owner·epoch·fence·ACCOUNT/PROJECT scope가 현재 상태로 일치할 때만 허용. 저장한 과거 응답은 다음 operation의 권한이 아님. |
| W1이 발행한 gate action 적용 및 해당 ACK 재전달 | `POST /internal/v1/w2-private/gate-authority` / `w1.private.w2-gate-authority.v1` | `operation_id`, revision, action, phase, result digest, 원래 command·Job·owner·fence·epoch·scope를 정확히 대조. `APPLY`와 `ACK_RELAY`를 구분하며, 과거 ACK는 W1 발행 outbox 증거가 있을 때만 허용. |
| W1 종료 전이 후 이미 존재하는 W2 개인 행의 정리 | `POST /internal/v1/w2-private/terminal-cleanup-authority` / `w1.private.w2-terminal-cleanup.v1` | 원래 바인딩과 W1의 종료·stale 상태를 확인한 뒤 `OWNER_LOCKED_PRIVATE_CLEANUP_ONLY`만 반환. `RESERVATION_RELEASE`, `CLAIM_RELEASE`, `STAGED_OUTBOX`의 기존 행 해제·폐기용이며 **수집·stage·gate 적용·SQS 송신 권한이 아님**. |

세 endpoint 모두 W2 서비스 principal 인증을 요구한다. 인증 실패는 `401`, 유효한 요청의 semantic mismatch·상태 거부는 `403`, W1 DB 오류는 `503`/`INTERNAL_RETRYABLE`, 잘못된 요청 형식은 `422`이다. W2는 `403`을 우회하거나 이전 `authority_ref`를 bearer처럼 재사용하지 않는다. timeout은 응답을 받지 못한 것으로 간주한다. 코드: [authority 판단](../../backend/app/runtime/w2_private_write_authority.py), [보호된 HTTP 라우트](../../backend/app/runtime/lookup_adapter.py).

정리 전용 요청/응답의 W1 정본 schema 및 SHA256은 다음과 같다. W2는 임의로 필드나 효과를 확장하지 말고 이 bytes와 응답 모델을 대조해 달라.

| W1 계약 | 경로 | SHA256 |
| --- | --- | --- |
| request | [`backend/contracts/w1/v1/w2-terminal-cleanup-authority.request.schema.json`](../../backend/contracts/w1/v1/w2-terminal-cleanup-authority.request.schema.json) | `e2d1127c07fb88df249b2bc1294cc930b1191560d04408499587fc54a361e913` |
| response | [`backend/contracts/w1/v1/w2-terminal-cleanup-authority.response.schema.json`](../../backend/contracts/w1/v1/w2-terminal-cleanup-authority.response.schema.json) | `cfba5f5c3886291430ff09640ddc02e83de4cab26bf87338bbba88` |

### 1.1 상태별 W1 판단 — W2 요청 1번에 대한 답

아래의 `허용`은 **그 요청에 대한 신선한 W1 응답**을 뜻한다. W2 owner DB의 행·scope·epoch를 다시 잠그고 검증할 책임까지 대신하지 않는다. `JobCommand`의 `CONSUMED` 자체는 금지 상태가 아니며, `Job`의 `FAILED_RETRYABLE`도 원래 fence·epoch가 여전히 현재이면 자동으로 종료 상태가 되지 않는다. 반대로 `JobCommand=INVALIDATED/FAILED`, `Job=CANCEL_REQUESTED/CANCELLED/FAILED_FINAL`, owner 비활성·epoch 증가, Project archive 또는 fence 불일치는 새 개인 쓰기를 막는다.

| W2 operation | 필요한 W1 command/Job·gate 상태 | 새 W1 결정 | 보존·종료 처리 |
| --- | --- | --- | --- |
| reservation·claim·renew/heartbeat·정상 release·replay·stage, STAGED outbox claim/send/retry | 원래 command·Job·owner·scope·fence·epoch가 현재이고 W1 쓰기 금지 상태가 아님 | 매 논리 operation마다 일반 write-authority `200`. 다른 owner, Project, epoch, fence 또는 행 부재는 `403`. | 별도 자동 만료 없음. **SQS send는 claim 때의 응답을 재사용하지 않고 새 조회**한다. |
| 이미 stage/commit된 STAGED를 W1 취소·최종 실패·계정/Project 삭제 **후** 보내거나 다시 보내려는 경우 | 원래 바인딩은 남아 있지만 새 쓰기는 종료·stale | 일반 write-authority `403` → **송신 금지**. 정확한 종료 바인딩의 정리 전용 조회가 `200`이면 W2 owner lock 아래 기존 STAGED/outbox를 해제·tombstone한다. | W1이 새 STAGED를 기다리도록 과거 결정을 되살리지 않는다. 정리 전용 조회도 `403`이면 mutation 없이 격리/lease 만료로 둔다. |
| `PREPARE`·`FINALIZE` **적용** | 동일 W1 gate operation이 해당 action의 `*_PENDING`/정확한 revision이고 owner·Job·Command가 현재 | gate-authority `phase=APPLY` `200`; 이미 다른 action/revision, 종료된 forward write, missing gate는 `403`. | W2의 stage/apply transaction은 별도 새 조회. |
| `ABORT` **적용** | W1이 동일 operation/revision/digest로 `ABORT_PENDING`을 실제 발행 | gate-authority `phase=APPLY` `200`. W1 취소·삭제로 원래 쓰기가 금지되어도 정확한 ABORT 정리는 허용. 발행 증거·바인딩이 없으면 `403`. | 기존 private stage의 정리이며 PREPARE/FINALIZE 권한으로 전용되지 않음. |
| `PURGE` **적용** | 동일 `PURGE_PENDING`과 원래 바인딩이 있고 W1의 현재 owner deletion epoch가 요청의 새 purge epoch와 일치 | gate-authority `phase=APPLY` `200`; 다른 epoch·operation·revision은 `403`. | W2 v2 삭제 transaction 자체의 authority와 혼동하지 않는다. |
| `PREPARE`·`FINALIZE`·`ABORT`·`PURGE` ACK의 최초 송신·중복·재시작 후 동일 outbox 재전달 | gate가 pending 또는 해당 action 적용 상태이거나, 그 뒤로 진행했더라도 W1이 **해당 revision/action을 발행한 outbox 증거**가 있음 | 송신 시마다 gate-authority `phase=ACK_RELAY` 새 조회. 정확한 역사적 ACK는 Job 취소·삭제 뒤에도 `200`; 다른 ACK, 발행 증거 소실, 행 부재는 `403`. | ACK 발송 성공 전 W2 outbox를 종료로 표시하지 않는다. 같은 outbox ID/내용을 유지하고 재시도한다. |
| W1 `JobCommand`·`Job` 행 자체가 없음 | W1에서 원래 owner/binding을 증명할 수 없음 | 모든 authority `403`; 호출자가 과거 응답으로 대신할 수 없음. | 아래 보존 정책상 정상 운영 경로에서 W2 command/Job 행을 삭제하지 않는다. 발생 시 계약 위반으로 격리·조사한다. |

`ABORT`/`PURGE`의 `200`은 **W1이 해당 gate action을 발행했다는 뜻**이지, 임의의 W2 cleanup을 허용한다는 뜻이 아니다. 반대로 종료된 STAGED의 정리 전용 `200`은 gate ACK나 SQS send를 허용하지 않는다. [gate 판단 및 역사적 발행 검증](../../backend/app/runtime/w2_private_write_authority.py), [좁은 DB 함수·조회 권한](../../backend/migrations/versions/044_w2_gate_lookup_grant.py)에 이 구분이 반영되어 있다.

### 1.2 W1 행 보존 — 종료 조건과 최소 기간

현재 W1 릴리스에서는 W2 `W2_SOURCE_COLLECTION`/`W2_DIRECT_SOURCE_REGISTRATION` `JobCommand`에 **시간 기반 만료나 자동 삭제 종료 시점을 두지 않는다.** [migration `045_w2_command_binding_retention`](../../backend/migrations/versions/045_w2_command_binding_retention.py)이 outbox를 먼저 없앤 경우에도 이 command의 `DELETE`와 ID·Job·owner·type·fence·원래 epoch 변경을 거부한다. `Job`은 `JobCommand`의 `RESTRICT` FK가 보존한다. 취소·최종 실패·계정/Project 삭제를 예외로 두지 않는다. 상태·현재 fence/epoch의 적법한 전이는 계속 가능하지만 원래 command 바인딩을 덮어쓰지 않는다.

즉 **최소 보존 기간은 W2의 미전달 STAGED/ACK가 없어졌다는 양측 증거와 별도 승인된 종료 계약이 마련될 때까지**이며, 현재는 그 종료 계약이 없으므로 자동 해제하지 않는다. 이 정책은 W2 outbox drain을 이미 확인했다는 뜻이 아니고, 민감 payload를 무기한 보유해도 된다는 개인정보 승인도 아니다. 향후 종료 시에는 W2 drain 증거, W1 gate/outbox의 역사적 ACK 검증 필요 여부, 개인정보 보존 정책을 함께 승인하고 별도 migration으로 바꿔야 한다. 운영자가 특권으로 행을 삭제하거나 역사적 W1 gate outbox를 제거하면 authority가 `403`으로 fail closed할 수 있으므로 공동 검증 완료 전 임의 정리는 금지한다.

## 2. 호출 단위·lock 순서·오류 정책 — W2 요청 2번에 대한 답

**결정 1회 = 동일 owner·command·Job·fence·epoch·scope의 W2 owner-scoped 논리 operation/transaction 1회**이다. 그 transaction의 여러 row mutation은 한 신선한 W1 결정으로 처리할 수 있다. 다음 transaction, lease 연장, relay claim, 실제 SQS send, 재시작·재시도에는 이전 응답을 캐시하지 않고 각각 새로 조회한다. 요청 필드는 W2가 보관한 canonical 식별자에서 매핑하고 null project ref·URL·payload text로 scope를 추측하지 않는다.

| W2 seam | 매번 새 W1 조회와 시점 | W1 `403` | W1 `503`/timeout |
| --- | --- | --- | --- |
| reservation | 예약 operation 직전 일반 write-authority | 예약 금지; 기존 종료 행 정리가 필요하면 정리 전용 별도 조회 | 예약 commit 없음; 새 조회로 재시도 |
| claim | claim transaction 직전 일반 write-authority | claim 금지 | claim 없음; 재시도 |
| renew/heartbeat | **각** lease 연장 transaction 직전 일반 write-authority | 연장 중지; 종료된 기존 claim의 release만 정리 전용 조회 | 연장 commit 없음; 기존 lease 만료·복구 경로 사용 |
| release | **각** 정상 release transaction 직전 일반 write-authority; terminal 403이면 `cleanup_kind=CLAIM_RELEASE` 또는 `RESERVATION_RELEASE`로 별도 조회 | 두 결정 모두 거부되면 mutation 금지; lease 만료를 기다리고 격리 | release commit 없음; 새 결정으로 재시도 또는 lease 만료 |
| replay | **각** 재실행/새 transaction 직전 일반 write-authority | 새 쓰기·송신 금지; 종료 행 정리만 별도 조회 | 재실행 중지·새 조회로 재시도 |
| stage | **각** stage transaction 직전 일반 write-authority | stage 금지; 이미 존재하는 STAGED/outbox만 정리 전용 조회 | stage commit 없음; 재시도 |
| gate apply | **각** PREPARE/FINALIZE/ABORT/PURGE 적용 transaction 직전 gate-authority `phase=APPLY` | action 적용 금지; 별도 W1 발행 증거 없는 임의 cleanup 금지 | 적용 commit 없음; 같은 operation/revision으로 새 조회 |
| relay outbox claim/release | STAGED는 일반 write-authority, ACK는 gate-authority `phase=ACK_RELAY`를 **각** claim/release transaction 직전 조회. 종료된 STAGED release만 정리 전용 조회 | outbox mutation 금지; terminal STAGED는 정리 전용 `200`에서만 tombstone/release | commit 없음; lease 만료·재획득 후 새 조회 |
| relay 실제 SQS send | claim 때와 **별개**의 새 조회. W2 owner row lock을 보유한 채 STAGED는 일반 authority, ACK는 gate `ACK_RELAY` 조회 → W2 epoch/scope/outbox 재검증 → send 완료까지 lock 유지 | **송신 금지**, 성공 처리 금지; terminal STAGED라면 새 정리 전용 조회 후 owner lock 아래 폐기 | **송신 금지**, 성공 처리 금지; outbox/lease를 복구 가능하게 두고 새 조회로 재시도 |

W1이 승인하는 기본 순서는 **W1 fresh lookup → W2 transaction에서 owner-state row를 먼저 lock → W2의 현재 epoch/scope·해당 행 바인딩 재검증 → mutation commit**이다. 다중 row mutation도 그 동일 transaction과 동일 바인딩 안에서만 묶는다. 실제 외부 SQS send는 위 표처럼 **owner lock을 보유한 상태에서 별도 W1 조회**가 선행되어야 한다. SQS send가 성공했으나 W2의 전송 완료 기록 commit이 실패하면 새 ID나 새 내용으로 바꾸지 말고 원래 outbox의 멱등 재전달·재시작 경로를 사용한다. W1 결정은 W2 DB lock이나 SQS 측 효과를 대신 보장하지 않으므로 W2가 이 순서를 코드와 장애 테스트로 증명해야 한다.

W2 v2 **삭제 transaction 자체**는 이 write-authority의 대상이 아니다. 그것은 인증된 v2 삭제 command와 W2 owner fence·receipt transaction을 따르고, W2 receipt **commit 후** W1 scope purge/callback/ACK로 이어지는 기존 계약을 유지한다. 삭제를 핑계로 아직 진행 가능한 일반 collection write의 proof 요구를 생략할 수는 없다.

## 3. W1이 완료한 범위와 재현 근거

1. W1 `14daafe…`는 일반 write-authority, W1-issued gate `APPLY`/`ACK_RELAY`, 종료 후 정리 전용 authority를 [코드](../../backend/app/runtime/w2_private_write_authority.py)와 [W2 전용 라우트](../../backend/app/runtime/lookup_adapter.py)에 분리했다. 정확한 owner/command/Job/fence/epoch/ACCOUNT·PROJECT binding, 인증, 403/503, 삭제 순서, historical ACK 발행 증거를 [PostgreSQL 통합 테스트](../../backend/tests/integration/db/test_w2_private_write_authority.py)로 검사한다.
2. W1 migration `044`는 gate 조회에 필요한 좁은 열 접근 및 W1 발행 outbox의 boolean 증거 함수만 제공한다. migration `045`는 W2 command 보존을 DB trigger로 강제한다. [runtime 권한 테스트](../../backend/tests/integration/db/test_w1_runtime_access.py)는 lookup 계정이 owner context 없이 gate 행을 볼 수 없고, owner context에서는 허용 열만 읽으며 불필요한 열은 읽지 못함을 검사한다.
3. 새 [request](../../backend/contracts/w1/v1/w2-terminal-cleanup-authority.request.schema.json)·[response](../../backend/contracts/w1/v1/w2-terminal-cleanup-authority.response.schema.json) schema는 [계약 테스트](../../backend/tests/contract/test_w2_terminal_cleanup_authority_contract.py)에서 W1 Pydantic wire model과 대조했다. `SQS_SEND` 같은 효과 확장은 유효하지 않다.
4. 격리 PostgreSQL에서 최종 관련 집중 선택 **47 passed**; 별도 W2 gate·v2 삭제·런타임 확장 선택 **55 passed**. W1 변경 경로 `ruff check`와 `git diff --check` 통과. 전체 `tests/contract tests/integration/db`의 최초 확장 실행은 **577 passed, 10 failed, 1 skipped**였고, 그중 migration head·lookup 권한에 대한 W1 테스트 2건은 정정 후 **2 passed**로 재검증했다. 남은 8건은 이 격리 브랜치에 없는 W3 문서·증거 경로 및 W3 clone SHA 불일치로, 이번 authority 구현의 통과 증거 또는 전체 suite green으로 바꾸어 적지 않는다. 전체 묶음은 수정 뒤 재실행하지 않았다.
5. W1 후보는 feature 브랜치에 push했지만 **운영 이미지 빌드·digest pin, AWS 배포, 실제 W2 DB migration, 양측 실제 queue·PostgreSQL 공동 실행은 하지 않았다.** 이 로컬 결과는 W2 production client가 세 authority를 호출한다는 증거도 아니다.

재현 시에는 비밀값을 문서에 넣지 말고 승인된 **격리** PostgreSQL의 `TEST_DATABASE_URL`과 `DATABASE_URL`을 별도로 주입한다. W1 진입점은 `backend`에서 다음 파일들을 `python -m pytest ... -q`로 실행한다: `tests/integration/db/test_w2_private_write_authority.py`, `test_w2_commit_gate.py`, `test_w2_private_deletion_v2_apply.py`, `test_w2_private_deletion_callback.py`, `test_w2_private_deletion_v2_dispatch.py`, `test_w2_deletion_activation.py`, `test_migrations.py::test_current_migration_head_merges_w2_and_w3_runtime_branches`, `test_w1_runtime_access.py::test_worker_and_lookup_roles_receive_only_their_operational_rls_access`, `tests/contract/test_w2_terminal_cleanup_authority_contract.py`. 최종 집중 선택은 **47 passed**였다.

## 4. 이 회신 뒤 W2가 제공·구현·검증할 것

W2 소유 코드는 W1이 수정하지 않았다. 아래는 W2가 **새 clean full SHA**로 제시할 구현 및 독립 증거이며, W1이 이번 회신만으로 완료했다고 주장하지 않는 범위다.

1. **Authority client와 전 seam inventory.** 위 세 endpoint를 W2 production runtime에 연결한다. `reservation / claim / renew·heartbeat / release / replay / stage / gate apply / relay claim·release / relay send` 각각에 대해 W2 함수·transaction, `owner_user_id`, 원래 epoch, ACCOUNT/PROJECT와 canonical `project_id`, `command_id`, `job_id`, fence, gate의 operation/revision/action/digest의 출처와 새 W1 조회 시점을 표로 제출해 달라. 한 seam이라도 원래 command/Job ID가 없다면 우회하지 말고 `seam → 실제 보유 값 → 빠진 값 → 필요한 W1 계약 변경`을 특정해 회신해 달라.
2. **상태·실패 처리의 W2 실제 코드/테스트.** 현재 STAGED 송신, 취소·최종 실패·삭제 뒤 `403`과 정리 전용 tombstone, W1-issued ABORT/PURGE 적용, 종료 후 역사적 ACK 재송신을 owner lock과 실제 outbox/DB에서 검증해 달라. 매 transaction의 fresh lookup, claim renew/release, relay claim과 실제 send의 **별도** 조회, W1 `401/403/422/503`·timeout, W2 lock 재검증 실패, ACK loss, send 성공 후 completion 기록 실패, 중복·재시작·late replay를 각각 fail-closed/멱등 재시도로 시험하고 pass/fail/skip을 제시해 달라. 같은 run에서 원래 command별 미전달 STAGED/ACK와 정리 대기 행의 **count-only drain 상태**도 제출해 달라. 단, W2 count만으로 현재 W1의 무기한 보존을 자동 해제하지 않는다. `403` 우회·캐시 권한 재사용·null scope 추론은 불합격이다.
3. **T050 production wiring.** 기존 [W1 T050 요청](W1_W2_Phase4_T050_W2_Action_Request_2026-09-25.md)의 rendered collector, bounded scheduler, v2 deletion 전용 consumer와 callback, health/inspect가 실제 `source_runtime.py` entrypoint에서 선택·실행되는 경로를 증명해 달라. 이미 구현된 부분은 다시 만들 필요 없이 clean SHA의 코드 경로와 테스트로 입증하면 된다. 당시 W2 `e2491a4…` 검증의 `RenderedCollector` 관련 실패 8건 및 browser/egress skip 1건이 새 SHA에서 어떻게 해소되었는지 또는 어떤 승인 환경이 남았는지 명시해 달라.
4. **v2 삭제 왕복.** outer/inner owner·target·epoch·scope 일치와 v1 거부, W2 owner-state 삭제·tombstone·receipt **commit 후** W1 private scope purge/callback, `APPLIED`/`DUPLICATE`의 같은 `deletion_id`·본문·receipt 재전달, `STALE`의 zero purge/ACK, UNKNOWN Project row 전체 rollback을 W2 PostgreSQL 테스트로 제출해 달라. 다른 owner·공용 Source 보존, writer-first/deletion-first, purge·callback 실패와 재시작도 count-only로 기록한다.
5. **pin·재현 자료.** W2 `feat/crawler`에 push한 새 **clean full SHA**, 변경 파일 목록, 실제 Alembic head, W2 v2 command/ACK schema의 유지 또는 새 원문 경로·SHA256, 잠긴 의존성/Dockerfile/entrypoint, 비밀값을 제외한 필요한 환경 변수 **이름**, migration·preflight·실행 명령, focused/full W2 테스트 pass/fail/skip과 Ruff/mypy 결과를 보내 달라. 기존 `e2491a4…`/`0010`/schema pin이 변하지 않았다면 유지라고 명시한다. W2에 AWS 권한이나 ECR digest 생성을 요청하지 않는다.

W2가 이 자료를 제공하면 W1은 새 SHA/bytes를 독립 대조하고 필요한 W1 pin·상호 운용 테스트를 갱신한다. **W1의 다음 책임**은 승인된 W2 SHA의 immutable image manifest digest와 OCI revision label 확인, 실제 W2 DB `0010` migration/preflight, W1 callback private ingress·queue policy·IAM 및 런타임 배선이다. 이어 같은 `run_id`의 실제 저장소·큐에서 T050/T058 시나리오, CT15-01~09, W2→W3 READY·fault·restart, ACCOUNT/PROJECT 삭제·재전달을 양측 count-only 증거로 독립 대조한다. 이 단계 전에는 T050/T058·AWS 배포·서비스 READY·테스트 자원 teardown을 완료로 표시하지 않는다.

## 5. W2 회신에서 필요한 최소 판정 형식

W2는 `새 source full SHA → seam별 실제 함수/바인딩/authority endpoint/조회·lock·commit 순서 → 상태·오류별 테스트 결과 → runtime entrypoint/0010/schema pin → 남은 승인·skip` 순서로 제출해 달라. W1/W2가 같은 SHA·schema hash·DB head·이미지 digest·run ID로 공동 재현할 수 없으면, 미완료 항목을 `PENDING`으로 남긴다. 원시 개인 데이터나 실제 비밀·queue URL을 공유하지 않는다.
