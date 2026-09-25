# W1 → W2: Phase 4 T050 완료를 위한 W2 작업 요청

- 작성일: 2026-09-25
- 대상: W2 `Project_EPICK_Engine` 담당자
- 기준: Service `specs/008-end-to-end-service/tasks.md` T050·T058 및 W2의
  `W2_W1_Private_Deletion_Scope_V2_Handoff_2026-09-23.md`

## 결론과 현재 pin

W1은 W2 private deletion v2의 **W1 측 후보 경계**를 구현·push했다. 그러나
W1 endpoint가 존재한다는 것과 **W2 production runtime이 그 endpoint를 모든
private write에서 사용하고, v2 삭제 명령과 ACK를 끝까지 처리한다는 것**은 다르다.
T050과 T058, 배포 및 서비스 READY는 아직 완료가 아니다.

- W1 후보 브랜치:
  [`codex/008-phase4-us2`](https://github.com/Team-1AM-Club/Project_EPICK_Service/tree/codex/008-phase4-us2)
- W1 full SHA: `b68c45afafa8b5dc99757388eb47940b4852cae6`
- W1 코드·증거: [W1 v2 후보 인계서](W1_W2_Phase4_V2_W1_Candidate_2026-09-25.md)
- 대조한 W2 owner 브랜치 `feat/crawler`: `e2491a4084ed50090d5135ba177a3772e9a44f5a`
  (2026-09-25 원격 ref 재확인)
- 현재 W2 v2 계약: command SHA256
  `73eb3a51d15923969d483b13fdbf498e0a6cd8cfa18c019a66f5f532cfd64067`,
  ACK SHA256 `22cd9cd44061b5c14c9852634417482993a8ffb8f69dc8e98e3b6cd71b891bc9`,
  필수 W2 DB head `0010_private_deletion_scope_v2`

## W1이 이미 제공한 것 — W1 후보 범위에 한정

1. W2 v2 command·ACK 원문 bytes와 해시, W2 source SHA 및 필수 DB head를
   `backend/contracts/w2/v2/`에 pin했다. W1 삭제 릴레이는 실제 W2 DB의
   `alembic_version`을 확인해 발급한 짧은 수명의 proof가 없으면 시작하지 않는다.
   이는 **운영 환경의 W2 DB migration이나 이미지 digest가 이미 검증됐다는 뜻이 아니다**.
2. W1 migration `042`·`043`, ACCOUNT/PROJECT별 W2 target, 동일 owner의 다음
   deletion epoch 차단, W1-private 참조 purge와 public Source·타 owner 보존을
   구현했다.
3. 인증된 `POST /internal/v1/w2-private/authority`를 제공한다. 현재 요청은
   `owner_user_id`, `owner_deletion_epoch`, `command_id`, `job_id`,
   `execution_fence`, 명시적 `ACCOUNT` 또는 canonical `PROJECT/project_id`
   scope를 요구한다. W1의 현재 command/Job/owner/Project 행과 맞지 않으면
   거부한다. 이 결정은 재사용 가능한 토큰이 아니며 **각 private write 직전**
   다시 확인해야 한다.
4. W1 outbox는 `w1.private.w2.deletion-command.v2`만 전용 큐로 보낸다.
   outer `w1.private.w2-deletion-dispatch.v2`와 inner
   `w2.private-deletion.v2`의 owner·target·epoch·scope가 다르면 발행하지
   않는다. 재시도 시 동일 `deletion_id`와 본문을 사용하며 v1 fallback은 없다.
5. 인증된 `POST /internal/v1/w2-private/deletion/ack`는 W2 v2 ACK가 W1의
   현재 target과 일치할 때만 W1-private scope purge를 적용한다.
   `APPLIED`/`DUPLICATE`만 완료 후보이며 `STALE`·purge 실패는 pending이다.
6. W1 집중 회귀 58개와 삭제·migration 확장 회귀 85개가 격리 PostgreSQL에서
   통과했다. **W1/W2 공동 실행 증거는 아니다.** W1 전체 backend suite는
   green으로 선언하지 않는다.

## 현재 문제와 W2에 요청하는 해결·증거

### 1. T050 production runtime 경로

- **근거:** `specs/008` 교차검증에서 W2 owner SHA `e2491a4`의 bounded
  collection/gate loop와 일부 health는 확인했지만 rendered collection·v2
  deletion·inspect의 production wiring 완료 근거는 없었다. 그 SHA의 집중
  실행은 115 passed, 8 failed, 1 skipped였고 8건은 `RenderedCollector`
  부재였다.
- **W2 작업:** 현재 v2 계약을 유지하면서 `source_runtime.py`의 실제 operator에
  rendered collector, v2 삭제 consumer, health/inspect, bounded scheduling을
  연결한다. 이미 구현된 부분은 교체하지 말고 호출 경로와 테스트로 증명한다.
- **요청 증거:** 새 clean full SHA에서 rendered 성공·timeout·크기 제한·안전성,
  health/inspect, bounded scheduling, direct-registration/collection/commit-gate
  및 v2 삭제 runtime 경로의 테스트 명령·결과. 실제 browser/egress 검증이 여전히
  skip이면 필요한 승인 환경과 남은 gate를 별도 표기한다.

### 2. 모든 W2-private write의 W1 authority 사용

- **근거:** W2 handoff는 collection/runtime reservation·claim·replay,
  commit-gate stage/apply/relay 등 *모든* W2-private write에 trusted W1 결정을
  요구한다. W1은 endpoint만 제공한 상태다.
- **W2 작업:** 각 private-write seam에서 W1의 현재 owner·epoch·scope·
  command/Job 결정을 write 직전에 확인하고, proof 누락·불일치·W1 403/503·
  timeout에서 fail closed/안전한 재시도를 구현한다. URL, null project ref,
  payload 텍스트로 scope를 추론하지 않는다. **W1의 현재 endpoint는
  `command_id`와 `job_id`를 모두 요구한다.** 어느 seam에 이 식별자가 없다면
  우회하지 말고 정확한 seam·보유 식별자·필요한 W1 계약 변경을 회신한다.
- **요청 증거:** seam별 write 위치, 전달 가능한 canonical owner/epoch/scope/
  command/Job/fence, W1 조회 시점, W2 owner-row lock 및 commit 순서,
  missing/stale/mismatch/503 테스트 결과. W1 계약 변경이 필요하면 변경 전
  공동 확정한다.

### 3. v2 삭제 명령·ACK의 실제 왕복

- **근거:** W2의 `process_private_deletion_v2` seam과 0010 migration은
  인계됐지만 W1 outer envelope → W2 production consumer → W2 receipt commit →
  W1 purge/ACK를 하나의 실행에서 관찰하지 못했다.
- **W2 작업:** W1 v2 전용 메시지를 인증된 producer/queue 경계에서 수신하고
  outer/inner binding 및 v1 거부를 확인한다. W2 owner-state transaction에서
  삭제·tombstone·receipt를 먼저 commit한 뒤에만 W1 callback을 호출한다.
  callback 실패·재시작 시 **동일** deletion ID/body/receipt로 purge·ACK를
  재시도하고 STALE에는 callback을 호출하지 않는다.
- **요청 증거:** ACCOUNT/PROJECT, UNKNOWN legacy row rollback, duplicate,
  stale, writer-first/deletion-first, callback·purge 실패 후 재전달/재시작,
  late replay, 다른 owner 및 public Source 보존에 대한 W2 PostgreSQL 기반
  count-only 결과와 W1 callback/receipt 교차 기록. W1 envelope 파서에
  불일치가 있다면 필드·fixture를 특정해 회신한다.

## W2 회신에 포함해 달라는 산출물

1. `feat/crawler`에 push한 **새 clean full SHA**와 T050 변경 파일 목록. W2
   v2 command/ACK schema 또는 migration head가 바뀌었다면 새 원문 경로·SHA256·
   migration 순서를 명시한다. 변하지 않았다면 위 pin 유지라고 명시한다.
2. `source_runtime.py`에서 실제로 선택되는 collector, scheduler, v2 deletion
   consumer, ACK callback, health/inspect 경로와 각 entrypoint 실행 방법.
   W1 outer envelope fixture/parser 상호 검증 결과도 포함한다.
3. 위 private-write **전 seam inventory**와 W1 authority 요청 필드 매핑.
   필드가 부족하거나 W1 계약이 맞지 않는 지점은 `seam → 부족한 값 → 제안 계약`
   형식으로 적는다. W1 소유 코드를 W2가 임의로 고칠 필요는 없다.
4. 재현 가능한 집중·전체 W2 테스트 명령과 pass/fail/skip 개수, Ruff/mypy,
   rendered browser/egress 검증 상태. 실패나 skip을 숨기지 말고 T050의
   승인 조건에 영향이 있는지 표시한다.
5. W1이 **자체 격리 환경에서** W2 `0010` migration, immutable image 빌드,
   실행 및 공동 T058을 재현할 수 있는 Dockerfile/잠긴 의존성/entrypoint/환경
   변수 이름/DB migration 명령. 비밀값·실제 AWS 권한은 보내지 않는다.

**AWS/ECR/IAM/ALB 설정과 immutable image manifest digest 확정은 W1 운영 측
책임**이다. W2에 AWS 접근이나 이미지 digest를 만들어 달라는 요청이 아니다.
W2는 reproducible source SHA·build/runtime 방법과 W2 소유 테스트 증거를 제공한다.
W1 callback의 private ingress·queue policy도 W1이 별도로 준비한다.

## 이후 순서와 완료 판정

1. W2가 위 산출물을 제공하면 W1은 새 W2 SHA/계약을 독립 확인하고 필요 시 W1
   pin·인터페이스를 수정한다. 양측 parser/authority 회귀를 재실행한다.
2. W1은 승인된 W2 SHA로 격리 이미지를 빌드해 digest를 고정하고 실제 W2 DB를
   `0010`까지 migration·preflight한다. W3 owner의 clean SHA 확정은 별도
   병행 과제이며 W2에 W3 코드를 수정하라는 요청이 아니다.
3. 그 뒤에만 동일 run ID의 실제 로컬 저장소·큐로 T058을 수행한다:
   frontend-created W1 Job → W2 direct-registration/collection → commit-gate
   FINALIZE → W3 READY, CT15-01~09, W2→W3 fault/restart 및 위 v2 삭제 경합·
   실패·재전달. W1/W2/W3 SHA, v2 schema hash, W2 DB head, 이미지 digest,
   count-only before/after와 재시작 증거가 양측 기록에서 일치해야 한다.

이 문서는 **W2 T050 작업과 공동 검증을 요청하는 것**이며, T050/T058 완료,
AWS 배포 승인 또는 서비스 READY 선언이 아니다. 원시 개인 데이터, credential,
DSN, bearer, 실제 queue URL은 회신·증거에 넣지 않는다.
