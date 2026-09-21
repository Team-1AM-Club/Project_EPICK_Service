# W1 → W2 연동 완료 요청 계약 회신 — 2026-09-21

수신: W2 연동 담당 / W1–W2 통합 조정자
대상: `w2/W1_W2_Integration_Completion_Contract_2026-09-21.md`
판정: **진행 회신 — W1–W2 서비스 연동 미완료**. 이 회신은 W1-01 구현 SHA와 미완료 입력을 전달하며, §4 완료 승인이나 운영 READY 선언이 아니다.

W1-01 구현은 W1 원격 브랜치 `codex/w1-w2-policy-resume`의 full SHA `187cbcbcb1ca8cd5c1fc5abe8c8e1820b627db4b`에 고정했다. `develop`에 아직 병합하지 않았고 배포 이미지도 만들지 않았다. W2의 이번 계약에서 명시한 정본 소스 pin은 `2cc9153b8401c04c93a2f5a069904ee32ac79cb2`다. 로컬 W2 clone HEAD `69f8984dba2f3c56d66c97f8a9edd5de5c4513ff`는 후속 커밋이지만 이 계약의 W2 이미지 pin을 임의로 바꾸지 않는다.

| 항목 ID | 상태 | 코드·설정 경로 | full SHA 또는 image digest | 실행 테스트와 결과 | 미완료 시 담당자·다음 산출물 |
|---|---|---|---|---|---|
| W1-01 | **W1 구현·로컬 검증 완료, W2 독립 확인 대기**. W2 양의 `policy_revision`을 checkpoint에 저장하고 Core/direct 재개 command에 동일하게 전달한다. 최초 `policy/null`은 유지한다. 기존 decision/registration pin을 계승하고 이전 SourceLink는 불변으로 보존한다. 복수 이력 링크에서 W4 context 조회도 유지한다. | `backend/app/runtime/workers.py`의 `_prepare_w2_resume`, `_resume_context`, 두 W2 dispatch 경로, `_append_checkpoint`; `backend/app/runtime/w4_question_core_context.py`의 `_source_is_active`; `backend/app/models/lifecycle_operations.py`, `backend/app/models/sources.py`, migrations `039_job_checkpoint_policy_revision.py`·`040_w2_resume_source_link.py` | W1 full SHA `187cbcbcb1ca8cd5c1fc5abe8c8e1820b627db4b` (`codex/w1-w2-policy-resume`) | 격리 PostgreSQL에서 `test_runtime_workers.py` **28 passed**. Core/direct 재개 2건에서 dispatch payload = protected lookup command, W2 실제 `CollectionCommand` parser 2건 통과, 이전 fence·다른 deletion epoch 거부, 재개 후 취소 시 `STALE_FENCE` 거부. W2 관련 W1 회귀 **107 passed**, W4 복수 SourceLink 회귀 **3 passed**, migration 역사적 upgrade 회귀 **12 passed**. API·계약 **286 passed**, runtime **257 passed/1 skipped**. 전체 DB 통합은 파일군별로 검증했으며 단일 재실행·원격 CI 결과는 아직 없다. | **W2 담당**: 위 W1 SHA의 Core/direct non-policy 재개 command를 W2 측 parser/lookup 회귀로 독립 확인해 결과를 회신한다. **W1 담당**: 원격 CI 및 develop 병합 전 확인을 마친다. |
| W1-02 | **미완료 — 실환경 입력·immutable image 미확정**. 승인 Python base image digest, W2 ECR repository/build·push 담당, 실제 IAM/SQS/HTTPS/PostgreSQL 설정 및 Linux 운영 담당을 아직 한 묶음으로 확정하지 않았다. | W1 배포 정의 `backend/infra/w1-runtime.compose.yml`, lookup `backend/app/runtime/lookup_adapter.py`, 권한 `backend/infra/postgres/runtime_privileges.sql`. W2 제공 build 정의 `Dockerfile.source-runtime`, `compose.source-runtime.yaml`은 W2 저장소 소유. | W2 소스 pin `2cc9153b8401c04c93a2f5a069904ee32ac79cb2`; 승인 `PYTHON_IMAGE@sha256` **없음**; W2 image manifest digest **없음**. 과거 W1/W4 이미지 digest를 W2 release pin으로 재사용하지 않는다. | 실제 workload identity, SQS/DLQ, private HTTPS, PostgreSQL migration/runtime role, Linux preflight **미실행**. | **W1 배포·AWS 담당**: region, ECR repository, 승인 base image, build·push 담당, 최소권한 role/queue 및 secret 주입 **경로·방식**을 확정한다. **W2 담당**: 위 계약 SHA 기준 image build·runtime preflight를 제공한다. Build·push 담당자는 그 SHA로 게시한 immutable image digest를 회신한다. Secret·DSN·bearer·raw queue URL은 승인된 비공개 채널로만 전달한다. |
| W1-03 | **미완료 — 공동 CT15 미실행**. W1의 CT15 격리 harness와 gate-only guard는 있으나 전용 실제 queue/DLQ, W2 sender identity와 양측 같은 `run_id` 증거가 없다. | `backend/app/runtime/w1_w2_ct15_harness.py`, `backend/infra/w1-runtime.compose.yml`, `md/w2/W1_W2_CT15_Verification_Response_2026-09-20.md` | W1 코드 pin `187cbcbcb1ca8cd5c1fc5abe8c8e1820b627db4b`; W1 CT15 배포 image digest **없음**. 코드 migration head는 `040_w2_resume_source_link`; 실제 배포 DB head는 미확인. | W2 관련 W1 회귀 **107 passed**는 실제 CT15의 대체가 아니다. CT15-01~09 실제 queue/ACK-loss/restart, invalid binding의 rollback·no ACK·retained receipt·retry/DLQ, cross-owner count 대조, queue depth 및 정리 **미실행**. | **W1/W2 공동 담당**: W2 immutable image·stable SenderId/role ID와 inspection/transport control을 받은 뒤 W1이 queue policy를 exact principal로 제한한다. 양측은 같은 `run_id`로 CT15-01~09 및 Linux 재시작·복구를 실행하고 count-only 증거와 teardown을 각각 기록한다. |
| W1-04 | **부분 구현 — W2 개인 삭제 T067 연결 미완료**. W1 일반 개인 삭제 epoch/fence와 W2 commit-gate ABORT/PURGE는 있으나 W2 private deletion 대상용 dispatcher/전용 command와 W2 T067 소비자 완료 증거는 없다. 공용 Source/Version/Evidence는 개인 삭제 cascade 대상이 아니다. | 일반 삭제 `backend/app/services/deletion.py`, target 목록 `backend/app/models/deletion.py`, 일반 schema `backend/contracts/w1/v1/deletion-command.schema.json`, commit-gate `backend/app/services/w2_commit_gate.py`. 현재 target 목록에는 W2 전용 store가 없다. | W1 기준선 `94b5832a6e3bae0486825e229ea7641b3770ff1b`; W2 전용 dispatcher 구현 SHA **없음**. | 이번 실행에서 W2 개인 삭제 실제 전송·ACK·중복·재시작·공유 Source 보존 검증 **미실행**. W1 commit-gate PURGE를 전체 W2 T067 완료로 간주하지 않는다. | **W1 삭제·배포 담당**: W2 전용 command/schema, scope, deletion epoch, retry/currentness 및 dispatcher 테스트가 다음 산출물이다. **W2 담당**: 해당 command 소비·개인 상태 purge와 공용 자료 보존 증거가 다음 산출물이다. 산출물 날짜는 양측 담당자가 확정 후 별도 회신해야 하며, 현재 미확정이다. |

## §4 완료 기준 대조

| 번호 | 판정 | 현재 남은 증거 |
|---|---|---|
| 1 | 일부 충족 | 접근 가능한 W1-01 full SHA와 로컬 dispatch/lookup·W2 semantic parser 2건 확인. W2 독립 회귀와 원격 CI 확인은 대기 |
| 2 | 미충족 | 확정 W2 source SHA와 해당 소스에서 build한 image **manifest digest** 결속 |
| 3 | 미충족 | 실제 IAM/SQS/HTTPS/PostgreSQL 양측 preflight |
| 4 | 미충족 | 실제 runtime의 collection, direct-registration, commit-gate routing |
| 5 | 미충족 | 같은 `run_id`의 CT15-01~09 및 Linux restart/recovery 양측 일치 기록 |
| 6 | 미충족 | 테스트 전용 role/queue/profile 정리와 잔여 운영 자원 목록 |

따라서 현 시점에 **W1–W2 서비스 연동 완료·READY를 선언하지 않는다**. W2는 위 W1-01 SHA로 독립 회귀를 진행하고, 계약 소스 SHA 기준 image/runtime·deletion consumer·inspection/transport control을 병행 준비할 수 있다. 실제 배포 자원 생성과 공동 CT15 실행은 양측 artifact·identity·계약이 고정된 뒤 진행한다.
