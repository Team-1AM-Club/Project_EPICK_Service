# W1 → W3: Phase 4 C-01 정본 확정 및 T058 공동 검증 요청

- 작성일: 2026-09-25
- 대상: W3 `Project_EPICK_Service` 담당자
- 범위: Service `specs/008-end-to-end-service/tasks.md`의 **Phase 4 / US2만**
- 관련 작업: T042, T054~T058. Phase 5 W1→W3 AnalysisPlan·W3→W4 REAL 추천은 이 요청의 완료 조건이 아니다.

## 결론과 현재 source pin

W1의 Phase 4 단독 후보 구현은 GitHub에 게시돼 있다. 그러나 **W1 Job에서
W2가 확정한 public Source 이력이 W3 C-01에 반영되고, W3가 재시작 후에도
현재 READY 상태를 유지·복구한다는 T058 공동 증거는 아직 없다.** W3의 로컬
C-01 후보와 W3 owner 브랜치도 동일한 소스가 아니다. 이 문서는 W3 소유
정본·재현 방법·공동 검증 증거를 요청하며 Phase 4 완료를 선언하지 않는다.

- W1 Phase 4 후보 브랜치:
  [`codex/008-phase4-us2`](https://github.com/Team-1AM-Club/Project_EPICK_Service/tree/codex/008-phase4-us2)
- W1 구현 full SHA: `b68c45afafa8b5dc99757388eb47940b4852cae6`
  (이후 이 브랜치의 문서 커밋은 W1 구현 SHA를 변경하지 않는다)
- W2 현재 owner `feat/crawler`: `e2491a4084ed50090d5135ba177a3772e9a44f5a`;
  T050 production runtime은 별도 W2 담당자에게 요청 중
- W3 현재 owner `feat/w3-knowledge-validation`:
  `17afd922089da880b9efd56504c310f7a8da015f`
  (2026-09-25 원격 ref 재확인)
- W3 로컬 후보 `9f49a0b71dc35c1b420422a24ea0ee5ceec01d11`은
  owner `17afd92…`에서 파생됐지만 **W3 승인·게시 정본이 아니다**.
- 현재 `.runtime/evidence/w1-w2-w3-local.json`의 상태는 `PARTIAL`이다.
  이 파일은 동일 W1 Job의 실제 W2 FINALIZED → W3 READY·재시작을 입증하지 않는다.

## W1이 Phase 4에서 준비한 것과 한계

1. W1은 Job/Source의 W2 direct-registration·collection과 commit-gate를
   잇는 기존 경로, policy revision/fence/epoch의 dispatch·protected lookup
   동등성 회귀를 후보 브랜치에 유지했다. W2 v2 삭제 경계도 별도 후보로
   구현했으며 관련 W1 로컬 삭제·migration 회귀 85개가 격리 PostgreSQL에서
   통과했다. 이는 **W3 C-01과의 실제 관통 성공을 뜻하지 않는다**.
2. Phase 4의 W3 입력은 **W2의 public Source event/outbox와 W2 Source Authority**다.
   W1이 W3 C-01에 Source event를 직접 주입하거나 W3 DB를 수동으로 수정하는
   방식으로 T058을 통과시키지 않는다.
3. W1은 W2 T050와 W3 정본이 확정된 뒤 실제 로컬 저장소·큐를 준비하고,
   frontend-created W1 Job에서 시작하는 T058 실행 ID, W1 Job/commit-gate
   상태 및 count-only 증거를 모아 양측 기록을 대조한다. AWS 배포는 그 뒤다.

## 현재 문제 1 — W3 owner 브랜치에 Phase 4 후보가 채택되지 않음

`specs/008` 교차검증에서 W3 owner `17afd92…`에는 다음 지정 산출물이 없었다.

- T042: `tests/integration/test_c01_joint_runtime.py` — Source Authority
  false/503/timeout, restriction clear 후 재색인, Source replacement, restart.
- T054: `deploy/Dockerfile` — non-root·immutable C-01 실행 이미지.
- T055: `deploy/c01.compose.yml` — durable SQLite volume, 분리된 인증 주체,
  private network, TTL/restriction 설정 및 운영 entrypoint.

이 파일들과 관련 operator/store/HTTP 변경은 로컬 후보 `9f49a0b…`에 있지만,
로컬 후보의 집중 검증 **9 passed**는 owner 브랜치의 테스트 결과가 아니다.
현재 owner 코드의 C-01 기본 동작을 부정하는 뜻도 아니다. **문제는 W3가 승인한
하나의 clean SHA에서 위 운영 패키징·공동 회귀를 재현할 수 없다는 점**이다.

**W3에 요청:** W3 소유 브랜치에서 위 동작의 동등한 구현을 검토·제공하고
새 clean full SHA를 push해 달라. `9f49a0b…`는 W1 내부 대조용 로컬 후보이며
W3 담당자가 fetch할 수 있는 원격 정본이라고 가정하지 않는다. 필요한 경우
후보 diff는 별도로 제공할 수 있다. W1은 W3 소유 코드를 임의로 수정·병합하지
않는다. 위 T042/T054/T055의 각 동작이 어느 파일·테스트로 충족되는지 적어 달라.

## 현재 문제 2 — W2→W3 실제 관통·복구 증거 없음

현재 `PARTIAL` 증거는 W2 PostgreSQL outbox → W3 C-01 SQLite의 동일 실행을
완료로 보이지 않는다. transport receipt를 READY/index ACK로 간주할 수 없고,
W2 Source Authority의 거부·장애, gap/conflict, 재시작 뒤 최신 상태 회복도
동일 run ID로 대조되지 않았다. W2 T050도 아직 미완료이므로 W3 단위 테스트만으로
T058을 닫을 수 없다.

**W3에 요청하는 공동 실행 준비·증거:**

1. 현재 W2 public event 계약에 대해 W3가 수신하는 version 두 세대,
   observation, restriction ACTIVE/CLEARED, duplicate, gap, conflict, replay,
   snapshot, Source replacement의 입력·receipt·index/READY 의미를 확정한다.
   private Job/owner payload가 C-01 public 지식으로 흘러가지 않는지도 검증한다.
2. W2 Source Authority가 `false`, 503, timeout이거나 restriction이 active이면
   READY를 막고, clear 이후에도 정확한 key의 재색인이 끝나야 READY가 되는
   것을 검증한다. receipt와 READY를 분리하고 cursor·restriction revision·
   generation·Source/version/index key를 기준으로 stale/중복을 처리한다.
3. W3 C-01을 **실제 durable SQLite volume**과 승인된 private HTTP/queue
   topology에서 재시작하고, 수신 이력·gap/replay 상태·제한·현재 READY가
   단조롭게 복구되는지 확인한다. health/inspect/replay/snapshot/re-index,
   expire/purge/backup entrypoint와 bounded retry의 실행 방법을 제공한다.
4. W1/W2 담당자와 같은 synthetic run ID로 W1 Job → W2 direct-registration/
   collection → commit-gate FINALIZE → W2 public outbox → W3 READY를 실행한다.
   W3는 event/cursor/receipt/READY/restart 전후의 **count-only** 결과를 남기고
   W1/W2의 Job·outbox 기록과 대조한다. CT15-01~09와 W2 v2 삭제의 전체
   T058 판정은 W1/W2/W3 공동 범위이며 W3 단독 완료로 표시하지 않는다.

## W3 회신에 포함해 달라는 산출물

1. W3 owner 브랜치에 push한 **새 clean full SHA**, T042/T054/T055와
   관련 operator/store/HTTP 변경 파일 목록, 사용한 C-01 event/receipt/status/
   Source Authority 계약 경로와 파일 SHA256. 기존 계약을 유지한다면 그대로
   유지한다고 명시한다.
2. 해당 SHA의 잠긴 의존성, Dockerfile, Compose, non-root 실행 사용자,
   SQLite volume·backup/restore 경로, private 인증/CA/timeout·TTL/restriction
   환경 변수 이름과 health/inspect/replay/snapshot/re-index/expire 실행 방법.
   **비밀값과 AWS 계정 접근은 요청하지 않는다.**
3. 재현 가능한 W3 집중·전체 테스트 명령과 pass/fail/skip 개수, lint/type
   결과. 특히 Authority false/503/timeout, restriction clear+재색인,
   Source replacement, 재시작, 중복·gap·conflict·replay·snapshot을
   어떤 테스트가 검증하는지 대응표를 제공한다.
4. W2 새 SHA가 나온 뒤 공동 T058에서 W3 측이 남길 count-only 증거 형식:
   run ID, W3 source SHA, W2 event schema hash, Source/version의 익명 참조,
   수신/receipt/index/READY 수, cursor·restriction revision·generation,
   재시작 전후 상태와 오류 분류. 원시 event 본문·개인 데이터·DSN·bearer는 제외한다.
5. W2 event/Authority 계약 또는 W1 T058 하네스와 맞지 않는 필드·상태가
   있다면 **정확한 경로·fixture·기대값·실제값**을 회신한다. W1/W2 계약을
   W3가 임의 변경해 우회하지 않는다.

## 책임 경계와 완료 순서

- **W3:** 위 C-01 정본 코드·테스트·운영 패키징과 W3 측 T058 증거.
- **W2:** T050 production 수집/runtime, public event outbox·Source Authority의
  새 clean SHA와 W2 측 증거. W3가 W2 코드를 고칠 필요는 없다.
- **W1:** W1 Job/commit-gate 기점, 양측 source·contract pin 확인, 격리
  이미지 digest와 private topology 준비, 동일 run ID의 공동 T058 실행·대조.
  AWS/ECR/IAM 배포 설정은 W1 운영 측 책임이다.

W3 clean SHA와 W2 T050 SHA가 확인된 뒤 실제 로컬 PostgreSQL/SQLite·큐에서
T058을 수행한다. **W2 FINALIZED와 W3 READY가 같은 W1 Job에서 관찰되고,
fault/restart 및 양측 count-only 증거가 일치할 때만** Phase 4를 완료로
표시한다. 이 문서는 W3 소유 산출물과 공동 검증을 요청할 뿐, Phase 4·T058
완료나 서비스 READY를 선언하지 않는다.
