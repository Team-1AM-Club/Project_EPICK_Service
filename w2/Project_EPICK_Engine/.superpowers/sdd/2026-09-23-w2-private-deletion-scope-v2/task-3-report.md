# Task 3 — private writer/runtime/commit-gate deletion fence 보고서

작성일: 2026-09-24

기준 브랜치: `feat/crawler`

작업 시작 기준: `7806cb0689139f7941b9fc02377b8bb028eb2d50`

## 1. 결과 요약

W2의 private write 경로에 `PrivateWriteScope` proof를 필수 입력으로 연결하고, owner state를 다른 mutable lock보다 먼저 잠그도록 통일했다. proof는 owner/job/command/epoch/project에 정확히 결합되며, nullable `project_ref`나 wire payload에서 scope를 추론하지 않는다. provider가 없는 production composition은 fail-closed 한다.

적용된 경로는 다음과 같다.

- request deduplication 및 legacy prepared commit/replay
- collection runtime reserve/claim/renew/release
- collection candidate commit 및 staged replay
- private stage/apply gate
- source runtime collection/gate consume 및 claim heartbeat
- commit-gate consume/operator synthetic input
- private staged-result/ACK relay

public canonical commit은 변경하지 않았고, operation-local `PURGE` 의미도 보존했다. migration/schema, Task4 deleter, Task5 operator head 문서와 기존 plan formatting diff는 수정하지 않았다.

## 2. 신뢰 경계와 계약

### W2가 보장하는 사항

- `PrivateWriteAuthorityProvider`는 W2 wire가 아니라 외부 인증 경계에서 만들어진 단일 `PrivateWriteAuthorityDecision`을 반환한다.
- runtime/provider가 없으면 collection/gate consume 및 synthetic stage는 거부된다.
- W2는 decision을 `PrivateWriteScope`로 감싸고 모든 writer에서 owner, deletion epoch, command, job, project를 재검증한다.
- ACCOUNT proof에 project wire binding이 있거나 PROJECT proof의 UUID 문자열이 wire `project_ref`와 정확히 일치하지 않으면 거부한다.
- `project_ref is None`을 ACCOUNT로 간주하지 않으며, 기존 `UNKNOWN` attribution을 ACCOUNT로 승격하지 않는다.
- owner-state row lock이 command/attempt/authority 등 다른 mutable lock보다 선행한다.
- legacy authority grant도 동일 proof를 포함해야 하며, `lock_authority`는 transaction 안에서 한 번만 호출한다. pre-auth 조회와 DB authority lock을 중복 호출하지 않는다.

### W1 외부 구현 의무

W1 Service repository는 Task3 write ownership 밖이다. W1 production composition은 인증된 identity/authorization 결과로 `PrivateWriteAuthorityDecision`을 생성하는 adapter를 구현하고 W2 provider에 주입해야 한다. W2는 wire 필드나 nullable `project_ref`로 이를 대체하지 않는다. adapter 미주입 상태는 의도적으로 fail-closed 한다.

테스트의 직접 호출자는 `None => ACCOUNT` fallback 대신 synthetic **trusted** decision adapter를 명시적으로 주입한다.

## 3. 3a — writer/store/commit-gate fence

### RED

초기 focused regression은 다음 결함을 드러냈다.

- proof 없이 private stage/dedup/legacy commit이 계속 진행되어 `DID NOT RAISE`가 발생했다.
- writer API에 `private_scope`가 없어 exact binding과 owner-first lock을 표현할 수 없었다.
- persisted attempt/stage attribution과 재호출 proof를 비교하는 경계가 없었다.

### GREEN 구현

- 공통 `bind_private_write_scope`로 exact binding을 검증한다.
- dedup, runtime attempt, commit stage, collection attempt에 authenticated scope attribution을 기록한다.
- owner state lock 뒤에 command/attempt/authority lock을 취한다.
- stage/apply/replay/commit의 기존 row를 재사용할 때 persisted scope와 현재 proof를 비교한다.
- legacy commit/replay는 proof 선검증 → owner lock → 기존 `lock_authority` 1회 → grant/proof exact comparison 순서를 사용한다.
- public canonical commit과 operation-local `PURGE`는 기존 동작을 유지한다.

### 3a 검증

- atomic persistence 전체 GREEN(이후 전체 suite에도 포함)
- collection finalize pointer: `19 passed`
- private commit gate focused: `53 passed, 1 deselected` (`deselected`는 Task5 migration-head assertion)
- commit-gate delivery focused에서 Task3 경로 GREEN
- commit-gate operator storage: `3 passed`
- legacy 직접 호출 계열(job posting/import/recovery/retention/restriction/static/version) synthetic trusted adapter 전환 후 GREEN

## 4. 3b — runtime/claim/relay fence

### RED

production signature에 proof를 먼저 연결했을 때 기존 source runtime/router 직접 호출 테스트가 provider 없이 실행되어 15건이 실패했다. 이는 production fail-closed 계약은 맞지만, 테스트가 trusted decision adapter를 주입하지 않던 문제였다. direct callers를 synthetic trusted adapter로 전환한 뒤 GREEN으로 복구했다.

relay deletion race regression은 network send와 deletion commit 사이에 owner fence가 없으면 삭제 commit 이후 private outbound send가 가능함을 대상으로 했다.

### GREEN 구현

- source runtime consume은 collection/gate 모두 provider를 한 번 호출해 proof를 전달한다.
- reserve/claim/renew/release와 heartbeat가 동일 proof를 유지한다.
- commit-gate consume/operator 역시 provider 부재를 거부한다.
- relay는 wire에서 scope를 추론하지 않고 persisted private stage attribution만으로 fence proof를 복원한다.
- claim/authorization 단계는 owner lock을 command lock보다 먼저 취한다.
- 실제 `before_send`와 `queue.send` 동안 owner lock을 유지하므로 deletion transaction이 먼저 commit한 뒤 send할 수 없다.
- network send 동안 command lock은 잡지 않고, send 이후 command lock과 persisted scope/payload/claim을 다시 검증해 delivered marker를 기록한다. 이 구조로 기존 bounded probe와의 command-lock deadlock을 제거했다.

### 3b 검증

- source runtime/router focused: `30 passed`
- source_collection unit 전체: `857 passed`
- operator focused unit: `89 passed`
- source_collection integration: `372 passed, 15 failed, 1 skipped`
  - Task3 실패: 0
  - 기존 baseline `RenderedCollector` 미구현: 8
  - Task1/Task5 진행 중 schema/migration-head assertion: 7

## 5. 최종 검증

승인된 격리 PostgreSQL test container에 process-local 환경변수로 연결했다. credential/DSN은 소스나 보고서에 기록하지 않았다.

- `uv run ruff check .`: PASS
- `uv run mypy`: PASS — `Success: no issues found in 34 source files`
- `uv run python -m pytest -q tests/unit/source_collection`: PASS — `857 passed`
- operator focused unit 3개 파일: PASS — `89 passed`
- source_collection integration: `372 passed, 15 failed, 1 skipped`
- full suite 1회: `1890 passed, 16 failed, 1 skipped`
- `git diff --check`: PASS (Windows checkout의 LF→CRLF warning만 존재)

### full-suite 기존/중간 실패 분리

1. 기존 baseline 8건: `tests/integration/source_collection/test_rendering_safety.py`
   - `collector.RenderedCollector`가 아직 존재하지 않아 모두 `AttributeError`.
   - Task3와 무관하며 작업 전 알려진 baseline이다.
2. Task5 head 갱신 전 interim 8건
   - contract migration head 1건
   - integration migration head 6건
   - collection runtime metadata constraint exact-set assertion 1건
   - `0010_private_deletion_scope_v2`와 새 metadata를 반영하는 Task5 범위이며 Task3에서 수정하지 않았다.

그 외 Task3 관련 신규 실패는 없다.

## 6. 변경 파일별 책임

### Production

- `persistence.py`: 공통 binding, dedup/legacy/candidate/replay/attempt attribution fence
- `commit_gate_store.py`: stage/apply owner-first scope fence
- `source_runtime_store.py`: reserve/claim/renew/release fence
- `source_runtime_gate.py`: gate proof 전달
- `source_runtime.py`: provider protocol, consume/heartbeat proof 전달 및 fail-closed
- `commit_gate_runtime.py`: provider protocol, consume fence, persisted-scope relay fence
- `commit_gate_operator.py`: runtime provider wiring 및 synthetic stage fail-closed

### Tests

- 핵심 보안 regression: `test_atomic_persistence.py`, `test_collection_runtime_storage.py`, `test_commit_gate_delivery.py`, `test_private_commit_gate.py`, `test_private_deletion.py`
- runtime/provider regression: `test_commit_gate_runtime.py`, `test_source_runtime.py`, `test_source_runtime_router.py`
- operator/storage 및 direct-call adapters: 나머지 수정된 source_collection integration tests

## 7. 자체 검토와 잔여 우려

- provider decision을 W2 wire에서 생성하는 fallback은 없다.
- nullable project field 기반 scope inference는 없다.
- owner lock 선행 순서를 store/legacy/relay 경로에서 확인했다.
- provider/authority callback 중복 호출을 제거하고 관련 test double 호출 횟수를 검증했다.
- relay send가 owner lock transaction 안에서 수행되므로 느린 queue는 같은 owner 삭제를 지연시킨다. 이는 “삭제 commit 이후 send 금지” 규칙을 만족하기 위한 의도적 직렬화다. queue의 at-least-once 특성상 send 성공 뒤 DB commit 실패 시 재전송 가능성은 기존과 동일하다.
- production W1 adapter는 외부 handoff이며 이 repository에서 검증할 수 없다. W1 adapter가 배포 composition에 연결되기 전에는 의도대로 private runtime이 fail-closed 한다.
- full suite의 16건은 위 두 기존/중간 묶음뿐이며, Task3가 소유하지 않는 파일을 고쳐 숨기지 않았다.

## 8. Review fix round 1 — persisted scope exact binding

기준 commit: `566681e19162372f9f5e2dee5d6d7d19607b269f`

### 검토 결과와 원인

리뷰 지적을 현재 코드에서 확인했다. trusted proof 자체와 owner epoch은 검증되고 있었지만, 일부 replay/transition helper가 이미 저장된 `private_scope_kind/project_id`를 현재 proof 또는 관련 parent row와 exact compare하지 않았다.

- legacy `CollectionAttempt` replay는 nullable command project만 비교하고 scope kind를 비교하지 않았다.
- candidate commit/staged replay의 `_assert_bound_attempt`는 runtime attempt의 scope attribution을 비교하지 않았다.
- staged replay의 `_bound_row` 호출은 현재 proof를 전달하지 않았다.
- collection gate transition은 candidate와 stage scope를 비교하지 않았다.
- commit-gate inbox exact replay는 stage scope 검사보다 먼저 stored ACK를 반환했다.

따라서 historical `UNKNOWN` 또는 다른 PROJECT attribution row가 현재 ACCOUNT proof로 re-arm/finalize될 수 있었다. 이 row들을 재분류하거나 backfill하지 않고, 현재 trusted proof와 exact match하지 않으면 거부하도록 수정했다.

### RED

승인된 격리 PostgreSQL 환경변수는 process-local로만 설정하고 다음 focused command를 실행했다.

```text
uv run python -m pytest -q tests/integration/source_collection/test_atomic_persistence.py::test_legacy_replay_rejects_historical_unknown_scope_without_reclassifying_it tests/integration/source_collection/test_collection_runtime_storage.py::test_candidate_commit_rejects_historical_unknown_runtime_scope_without_writes tests/integration/source_collection/test_collection_runtime_storage.py::test_collection_replay_rejects_other_project_persisted_scope tests/integration/source_collection/test_private_commit_gate.py::test_exact_inbox_replay_rejects_a_different_trusted_scope tests/integration/source_collection/test_private_commit_gate.py::test_collection_gate_rejects_candidate_to_stage_scope_mismatch_without_ack
```

결과: `6 failed`. 여섯 케이스 모두 예상한 `Failed: DID NOT RAISE PrivateScopeRejected`로 실패했다.

- finalized legacy parent를 `UNKNOWN`으로 seed한 replay
- reserved runtime attempt를 `UNKNOWN`으로 seed한 candidate commit
- runtime attempt를 다른 PROJECT로 seed한 staged replay
- private stage를 다른 PROJECT로 seed한 staged replay
- 동일 inbox message를 다른 authenticated PROJECT proof로 exact replay
- candidate와 stage가 서로 다른 scope인 FINALIZE transition

### GREEN 구현

- `_replay_attempt`에 trusted proof를 필수 전달하고 persisted kind/project를 exact compare한다.
- `_assert_bound_attempt`가 writer call에서 proof를 받아 runtime attribution을 exact compare한다.
- staged replay `_bound_row`에 proof를 전달한다.
- collection transition은 stage↔proof 및 candidate↔stage scope를 mutation 전에 비교한다.
- inbox/receipt replay보다 먼저 persisted stage binding/scope를 검사한다. exact inbox가 있는데 stage가 없으면 fail-closed 한다.
- historical `UNKNOWN` row는 `UNKNOWN` 상태로 남으며 ACCOUNT/PROJECT로 자동 분류하지 않는다.

동일 focused command 결과: `6 passed in 2.34s`.

### 회귀 및 정적 검증

- 기존 unfinalized replay fixture는 현재 ACCOUNT row를 의도하므로 `private_scope_kind="ACCOUNT"`를 명시했다. 별도 focused 결과: `1 passed`.
- covering integration 3개 파일: `134 passed, 3 failed`
  - 실패 3건은 기존 Task5 metadata/migration-head assertion이다.
- source_collection integration 전체: `378 passed, 15 failed, 1 skipped`
  - 기존 `RenderedCollector` 8건 + Task5 schema/head 7건만 실패했다.
- project full suite 1회: `1896 passed, 16 failed, 1 skipped`
  - 기존 `RenderedCollector` 8건 + Task5 schema/head 8건만 실패했다.
- `uv run ruff check .`: PASS
- `uv run mypy`: PASS — `Success: no issues found in 34 source files`
- `git diff --check`: PASS (Windows checkout LF→CRLF warning만 존재)

### 잔여 우려

새 backfill이나 자동 classification은 추가하지 않았다. 따라서 v1 historical `UNKNOWN` row는 private replay/write 경로에서 명시적으로 거부되며 원래 attribution을 유지한다. 해당 row를 다시 처리하려면 W1의 인증된 별도 재승인/재생성 절차가 필요하고, W2가 nullable wire 값으로 승격해서는 안 된다.
