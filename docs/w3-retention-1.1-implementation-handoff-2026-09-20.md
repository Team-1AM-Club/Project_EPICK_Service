# W3 `w3.retention/1.1` 구현 인계

상태: **LOCAL_VERIFIED / LIVE_INTEGRATION_PENDING / DEPLOYMENT_NOT_RUN / JOINT_CT12_NOT_RUN**

구현 source SHA: `3b23e0843a134fb341e6a256576ccf52fedbf4a8`

## 구현 범위

- 승인 정책 revision과 대상별 초 단위 기간을 코드로 고정했다.
- SQLite에 `created_at`, `held_at`, 최초 `terminal_at`, 최초 `deleted_at`, retired Source를 추가하는
  additive migration을 구현했다.
- supply·relay·replay가 본문 사용 전에 deadline을 검사한다.
- day-29 handoff/replay도 최초 생성 30일 상한을 연장하지 않는다.
- terminal metadata, owner tombstone, retired Source counter 정리를 구현했다.
- backup은 본문 제거·quarantine 후 생성시각과 원본 deadline으로 제한된 만료시각을 기록한다.
- inspect/CLI는 `w3.retention/1.1`, 안전한 lifecycle 시각과 migration blocker count를 노출한다.
- 분석 caller 입력에 신뢰된 request UUID와 최초 발급 시각을 추가했다. W3→W1 event wire는 변경하지 않았다.

## 안전한 migration

기존 delivery의 `created_at`은 저장 event의 검증 가능한 `occurred_at`에서만 파생한다. 본문이 없으면
delivery metadata를 제거하고, event가 유효하지 않으면 본문을 제거해 `MIGRATION_BLOCKED`로 둔다.
날짜가 없는 legacy owner tombstone에는 새 365일을 부여하지 않고 inspect blocker로 표시한다.

## 로컬 검증

검증 결과:

- focused runtime/retention: **32 passed**
- full suite: **259 passed, 1 skipped** (`live` provider test는 명시적 실행 권한·자격증명 없음)
- Ruff: **PASS**
- Core Decision export: **12 files, drift 0**
- C01 export: **20 files, drift 0**
- restriction export: **19 files, mismatch 0**
- readiness JSON parse: **PASS**

이 SHA가 `w3.retention/1.1` runtime·migration·테스트 구현 pin이다. 이후 문서-only receipt commit은
이미지 source pin을 대체하지 않는다.

## 실환경 미완료

`docs/w3-integration-input-request-2026-09-20.md`의 실제 caller, Authority, 삭제 dispatcher, Source
registry, image/storage, IAM/SQS, scheduler/경보, 공동 CT12 입력이 제공되지 않았다. 따라서 배포,
실제 AWS 송신, W1 수락, deletion propagation 및 공동 CT12는 `PENDING`/`NOT_RUN`이다.
