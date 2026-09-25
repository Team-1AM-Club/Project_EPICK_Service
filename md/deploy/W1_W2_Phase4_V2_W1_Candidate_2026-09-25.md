# W1 → W2 private deletion v2: W1 후보 검증 및 공동 완료 요청

판정: **W1 로컬 후보 구현·집중 검증 완료, 공동 T050/T058 및 서비스 READY 미완료.**
이 문서는 `specs/008-end-to-end-service/tasks.md`의 T050-W1-01~06과
`w2/W2_W1_Private_Deletion_Scope_V2_Handoff_2026-09-23.md`를 대조한 W1 증거다.
W2/W3/W4 소유 코드는 변경하지 않았다. 인계할 W1 full commit SHA는 이 문서를
포함하는 후보 커밋을 push한 뒤 별도로 전달한다. W1/W2 배포 이미지는 아직 만들지 않았다.

로컬 검증: W1 v2 계약·DB·런타임 집중 회귀 **58 passed**; 기존 계정·Project 삭제,
migration, commit-gate까지 확대한 W1 회귀 **85 passed** (5 library/ORM warnings).
변경 W1 경로 Ruff check, format check, `git diff --check`와 Compose 구문 검사
(`docker compose config --no-interpolate --quiet`) 통과. mypy는 이 가상환경에
설치되지 않아 미실행이다. 전체 backend suite는 이번 후보 검증에서 새로 완료하지
않았으며, 앞선 cross-check의 W3 관련 실패를 green으로 간주하지 않는다.

## W1 후보 경계

| 항목 | W1 구현·검증 경로 | 공동 완료 전 확인할 것 |
| --- | --- | --- |
| T050-W1-01 pin·0010 gate | `backend/contracts/w2/v2/private-deletion-manifest.json`은 W2 full SHA `e2491a4084ed50090d5135ba177a3772e9a44f5a`, command SHA256 `73eb3a51d15923969d483b13fdbf498e0a6cd8cfa18c019a66f5f532cfd64067`, ACK SHA256 `22cd9cd44061b5c14c9852634417482993a8ffb8f69dc8e98e3b6cd71b891bc9`를 고정한다. `integration_preflight.py`와 `preflight_w2_deletion_activation.py`는 실제 PostgreSQL `alembic_version`이 정확히 한 행 `0010_private_deletion_scope_v2`인지 읽고 짧은 수명의 activation proof를 만든다. 전용 릴레이는 증명이 없거나 10분보다 오래되거나 digest·schema가 다르면 시작을 거부한다. | W2 실제 DB migration, 선택 이미지의 OCI source-label 및 immutable manifest digest를 독립 확인하고 같은 대상의 proof를 만들어야 한다. 로컬 테스트의 synthetic 0010 테이블은 실제 W2 migration 증거가 아니다. |
| T050-W1-02 epoch·target | W1 migration `042_w2_private_deletion_target.py`, `043_w2_private_attempt_detachment.py`, `deletion.py`/`repo/deletion.py`는 W2 target, 계정·Project 삭제 범위, 현재 epoch 잠금, public Source 및 다른 owner 보존을 구현한다. | W2 owner-state transaction과 연동된 다음 epoch 직렬화는 공동 실행에서 확인한다. |
| T050-W1-03 write authority | `w2_private_write_authority.py`와 보호된 `lookup_adapter.py`는 인증된 W2 요청에서 command/Job, owner, epoch, fence, ACCOUNT 또는 canonical PROJECT ID의 현재 W1 행을 대조하고 결손·stale·다른 owner를 거부한다. | W2 collection/reservation/replay와 commit-gate stage/apply/relay **각 private-write seam**이 실제로 매번 이 결정을 호출해야 한다. W1 endpoint 존재만으로 이를 증명하지 못한다. |
| T050-W1-04 v2 dispatch | `w2_private_deletion_v2_boundary.py`, `deletion.py`, `outbox_relay.py`는 W1 outer와 W2 inner의 owner/target/epoch/scope를 일치시켜 영속 outbox에 담고 전용 `w2-deletion` 릴레이만 v2 큐로 보낸다. 재시도는 같은 deletion ID·본문을 쓴다. `w1-runtime.compose.yml`에 별도 profile·env·proof mount가 있다. | 실제 W2 v2 consumer/queue policy와 immutable W1/W2 이미지를 연결한 재전달·재시작 시험이 필요하다. |
| T050-W1-05 purge·ACK | `w2_deletion_callback.py`는 별도 bearer로 v2 ACK를 받아 current target을 재검증한다. `deletion.py`는 ACCOUNT/PROJECT W1 개인 참조를 지우고 APPLIED/DUPLICATE만 완료하며 STALE·purge 실패는 pending으로 둔다. | W2의 **receipt DB commit 이후** callback 및 purge 순서를 실제 양측 저장소에서 검증해야 한다. 콜백은 Compose 내부 노출만 있으므로 승인된 private ingress도 아직 필요하다. |
| T050-W1-06 W1 회귀 | 계정/Project, duplicate/stale, cross-owner, public Source, writer-first/deletion-first, purge/ACK failure와 exact-body replay를 W1 PostgreSQL/계약 테스트로 검증했다. | W2 owner lock 및 late replay가 함께 보장되는지는 T058 공동 테스트 영역이다. |

## Activation operator 절차와 제한

1. W2 소유자가 승인한 full SHA의 immutable image digest와 이미지의
   `org.opencontainers.image.revision` label이 일치하는지 독립 확인한다. W2
   handoff는 image digest를 제공하지 않았으므로 SHA를 digest로 대신하지 않는다.
2. 해당 W2 DB에 read-only로 접속하는 `W2_DELETION_PREFLIGHT_DATABASE_URL`,
   검증한 `W2_DELETION_SOURCE_SHA`, `W2_DELETION_IMAGE_DIGEST`를 **비밀 저장소/운영 환경**에서
   주입하고 `python scripts/preflight_w2_deletion_activation.py --output <host-proof-path>`를
   실행한다. 성공 시 비밀을 출력하지 않고 `PREFLIGHT_PASSED`만 출력한다.
3. 전용 relay env 파일은 `DELETION_WORKER_DATABASE_URL`,
   `W2_DELETION_COMMAND_QUEUE_URL`, `W2_DELETION_IMAGE_DIGEST`,
   `W2_DELETION_ACTIVATION_PROOF_PATH=/run/epick/w2-deletion-activation.json` 등
   좁은 값만 가진다. Compose host 변수 `W2_DELETION_ACTIVATION_PROOF_PATH`는 방금 생성한
   proof의 host 경로이며, callback은 다른 env 파일·DB login·bearer를 사용한다.
4. Proof는 시작 시 10분 이내여야 한다. 재시작 전 새 proof를 발급하고 W2 DB head,
   이미지 label/digest, schema pin을 다시 대조한다. Proof의 source SHA와 digest는
   **운영자가 독립 확인한 값을 입력**하는 구조이지, W1 스크립트가 OCI 이미지를
   자체적으로 조사했다는 뜻은 아니다. 현재 Compose callback은 host port를 열지 않는다.

## 공동 T050/T058에서 W2·운영 담당자에게 필요한 정확한 결과

1. W2 clean full SHA 및 해당 SHA로 빌드한 immutable OCI manifest digest,
   revision label, 실제 W2 DB head `0010_private_deletion_scope_v2`, 두 v2 schema
   hash를 같은 run ID로 기록한다. W2 runtime은 모든 private-write seam에서 W1
   authority를 사용하며 proof 누락·mismatch를 SQS side effect 전에 거부해야 한다.
2. 양측 실제 PostgreSQL과 큐에서 ACCOUNT/PROJECT 삭제, 미분류 legacy row,
   다른 owner/공용 Source 보존, writer-first/deletion-first, 중복·stale epoch,
   purge/ACK 실패와 동일 `deletion_id` 재전달, late worker replay 및 재시작을
   count-only로 독립 대조한다. W2 receipt commit → W1 scope purge → ACK → 다음
   epoch 순서를 확인한다. W2→W3 및 CT15-01~09까지 완료되어야 T058을 닫는다.
3. W1 callback에 W2 runtime이 도달할 수 있는 인증된 private ingress, 별도
   workload identity/IAM/queue policy를 승인한다. W1/W2 full SHA와 이미지 digest,
   실행 ID, 큐/DLQ/DB count와 restart 기록이 합치기 전에는 배포·테스트 자원
   teardown·서비스 READY를 완료로 표시하지 않는다.

원시 개인 payload, credential, DSN, bearer, queue URL은 증거 문서에 포함하지 않는다.
