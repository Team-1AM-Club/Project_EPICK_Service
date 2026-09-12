# W3 구조화 기능 검증 결과

**실행일**: 2026-09-07
**구현 범위**: 입력 진단, 원문 발췌·digest·locator 검증, Claim/Requirement 후보 검증 경계, 소비자 투영, PM/기존 샘플 로컬 진단.
**운영 상태**: 합성·Mock·로컬 진단 검증만 통과했다. W1 실제 권한 연동과 Solar 실제 PoC는 미실행이며 관련 GATE는 OPEN이다.

**최근 로컬 회귀**: 2026-09-11에 T044 관계·투영 통합을 보완했다. `pytest`는 60 passed (Solar live excluded)이고 `ruff check src/w3_knowledge tests`는 통과했다. skip은 명시적 `--run-live`와 외부 실행 허용이 필요한 Solar PoC다.

## 실행 결과

| 구분 | 명령 또는 입력 | 결과 |
|---|---|---|
| 잠금 환경 | `uv sync --locked --group dev` | 통과 |
| 정적 검사 | `ruff check src tests` | 통과 |
| 합성·Mock·계약 테스트 | `pytest` | 60 passed (Solar live excluded) (`test_solar_live`) |
| 합성 CLI | `w3-knowledge validate --fixture tests/fixtures/synthetic/normal --mode synthetic` | `COMPLETED`, 제한 0, 종료 0 |
| 기존 수집 샘플 로컬 진단 | `diagnose-sample --input Data/skhynix_collection_contract_sample.json` | Source 2, 제한 5, `LIMITED`, 종료 0 |
| PM observed 로컬 진단 | `diagnose-sample --package source-collection-sample-20260907` | SourceVersion 8, 제한 12, `LIMITED`, 종료 0 |
| Solar 실제 PoC | `pytest -m live --run-live` | 미실행: 명시적 허용·설정·비밀 주입이 없음. G-02 OPEN |

진단 명령은 원문·프롬프트·응답 전문을 출력하지 않았다. PM 수동 검토의 SourceVersion/Evidence ID와 제한은 배포 제외 경로 `tmp/w3-local-review/pm-analysis.json`에만 기록했다.

## 인수 시나리오·성공 기준의 현재 판정

| 범주 | 현재 판정 | 근거와 한계 |
|---|---|---|
| AS-101, AS-103~AS-108 | 부분 통과 | 정상/원문 누락/필수 첨부 미파싱/부분 자료/권한 차단의 합성·계약 테스트를 실행했다. 실제 PM 입력은 제한 진단으로만 확인했다. |
| AS-102, AS-107 | 부분 통과 | 기존·PM 어댑터가 요약을 Evidence로 승격하지 않고 ID·부모 참조를 보존하는 경계를 확인했다. 계획에 적힌 모든 샘플 건수·상태 변형은 아직 1:1 자동화하지 않았다. |
| AS-201, AS-208 | 부분 통과 | 합성 Claim의 SourceVersion·Evidence 경로와 없는 Evidence 차단을 확인했다. 계획/성과·수치·다른 버전의 모든 변형 조합은 미검증이다. |
| AS-202~AS-207 | 미검증 | 테스트 이름은 준비됐지만 각 인수조건 전체를 독립 자동화하지 않았다. 운영 Claim 검증 완료로 해석하지 않는다. |
| AS-301, AS-305 | 부분 통과 | 합성 OR 조건, 필수성, NOT_ASSESSED, 잘못된 조건 트리 차단을 확인했다. |
| AS-302~AS-304, AS-306~AS-307 | 미검증 | 우대/일반, 약어 사전 근거, 범위 확대, 수치·예외, 짧은 발췌 문맥의 전체 인수 조합이 남아 있다. |
| AS-401~AS-403 | 부분 통과 | 사용 상태, 제한, `NONE_IN_EXAMINED_SCOPE`와 `NOT_ASSESSED` 경계를 합성으로 확인했다. |
| AS-404~AS-407 | 미검증 | 악성 문서 지시문, 개인 승인, 로그 비노출, 날짜 미상 등은 구현 원칙으로 차단하지만 독립 인수 테스트가 남아 있다. |
| AS-501~AS-506 | 부분 통과 | 동일 문구가 아닌 동일 Evidence를 요구하고 기간·범위·검증 결과 차이를 유지하는 관계 검사를 실행했다. 동일 Origin·동일 검사 결과 Claim은 결정적으로 하나만 남기며, W4 투영은 차단된 Claim만 참조한 Evidence를 제거한다. 계보·재게시·충돌의 전체 사례는 미검증이다. |
| SC-001, SC-003, SC-006, SC-007, SC-009 | 부분 통과 | 입력/제한/권한/안전 경계의 일부 합성·계약 검사를 통과했다. |
| SC-002, SC-004, SC-005, SC-008 | 미검증 | 전 인수 사례별 독립 판정과 버전·관계·조건의 확장 사례가 남았다. |

`부분 통과`는 해당 범주의 일부 구현 테스트가 통과했다는 뜻이며, 시나리오 전체 또는 운영 정확도를 보장하지 않는다.

## GATE와 후속 검증

| 항목 | 상태 | 완료에 필요한 근거 |
|---|---|---|
| G-01 W2/W3/W4 공유 계약 | OPEN | 소유자·소비자·호환·적용/되돌림 공동 검토 |
| G-02 Solar PoC | OPEN | 명시적 live 허용, 합성 입력, 모델·스키마 결과, 오류/중단 증거 |
| G-03~G-05 W1 권한·보존·삭제/외부 처리 | OPEN | 실제 W1 정책 참조·철회·반환 전 재검사 처리 증거 |
| AS/SC 전체 자동화 | OPEN | 이 문서에서 미검증으로 남긴 시나리오별 fixture와 독립 테스트 |


## Local completion update (2026-09-11)

This section supersedes the earlier partial local snapshot.

| Scope | Result |
|---|---|
| Unit, contract, acceptance, Solar mock | 60 passed |
| Ruff check | passed |
| Ruff format check | 41 files already formatted |
| Synthetic CLI validation | COMPLETED; 0 limitations; 0 errors |
| Legacy sample diagnostic | LIMITED; 2 sources; 5 limitations; 0 errors |
| Observed-package contract checks | 2 passed |
| Legacy-sample contract checks | 2 passed |
| Observed-package diagnostic | LIMITED; 8 source versions; 12 limitations; 0 errors |

AS-201 through AS-208 and AS-301 through AS-307 now have local automated coverage. The W3 implementation distinguishes an upstream integrity report from W3 current validation, validates artifact/excerpt digest scope, propagates failed Evidence checks to Claims and Requirements, and rejects malformed condition trees.

Solar live execution remains unrun because explicit --run-live authorization and provider configuration were not supplied. W1 authorization, retention, revocation, and deletion behavior remain OPEN pending actual integration evidence; no synthetic test is treated as that evidence.
