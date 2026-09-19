# W3 restriction revision 결정 반영

W2/제품의 2026-09-20 결정으로 W3-REPEAT-02의 단위 선택을 종료한다.
Source별 단일 restriction_revision과 별도 transport revision을 사용한다.
다른 restriction ID/Version도 같은 Source restriction 순번을 공유한다.
Version/Observation은 restriction 순번을 소비하지 않는다.

기존 교차 시퀀스는 같은 ID active→cleared→active, 다른 ID 제한 유지,
Version 범위 제한과 null의 Source 전체 제한, 재색인 전 INDEX_PENDING을 검증한다.
per-ID fixture는 미채택 대안의 거부 검증으로 유지한다.

추가 발견·수정: 동일 restriction ID의 source_version_id를 바꾼 cleared event가
기존 event/replay/snapshot 경로에서 INDEX_PENDING으로 수락됐다. 세 경로의 재현 테스트를
추가하고 기존 관측 사실(transport gap으로 대기 중인 event 포함)과 범위를 대조해 CONFLICT로 차단했다.
같은 ID를 다른 Source에서 재사용하는 경우도 Source/Version tuple 비교로 차단하며 별도 회귀를 추가했다.
새 wire field/Schema version을 추가하지 않았다. 현재 store가 관측하지 못한 과거 사실은
추정하지 않으며 producer도 ID별 범위 고정을 보장해야 한다.

남은 P4/P5 정책, W2 registry, 실제 adapter·복구·재색인·W4 공동 검증 gate는 유지한다.
Core Decision CT12와 private commit-gate CT15는 이번 결정의 변경 대상이 아니다.
이전 날짜 검증 JSON은 당시 기록이며 이번 재실행 결과로 소급 변경하지 않는다.

## 실제 재검증

- 전체 pytest: 249 passed / 외부 Solar live 1 skipped.
- 새 범위 변경 회귀: event/replay/snapshot 및 다른 Source 재사용 4개. 수정 전 수락을 재현하고 수정 후 통과.
- C01 HTTP smoke: 18/18, error=null.
- C01 계약 export: 20 files, drift 없음. Schema 변경 없음.
- Ruff check 통과, format 78 files already formatted.
- 실제 W2 송신·W4 소비·운영 배포·공동 검증은 이번에 실행하지 않음.
