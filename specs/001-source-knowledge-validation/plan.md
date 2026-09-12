# Implementation Plan: 수집 자료의 근거 검증과 지식 구조화

**Branch**: 미생성 — 기능 식별자 `001-source-knowledge-validation` | **Date**: 2026-09-07 | **Spec**: [spec.md](spec.md)

**Input**: `specs/001-source-knowledge-validation/spec.md`

**Note**: `$speckit-plan`의 활성 plan 템플릿 해석·setup 절차로 작성했다.
현재 저장소에는 Git 브랜치 생성 훅과 .git이 없으므로 경로 식별자를 실제 브랜치 생성 결과로 보고하지 않는다.
헌법 1.0.0을 적용하며 이 산출물은 Phase 0 조사와 Phase 1 설계다.

**개정**: 2026-09-07 사용자 요청에 따라 PM 입력을 반영하고 이어서 [tasks.md](tasks.md)를 생성한다.
기능 범위·헌법은 유지하며 계약/검증 변경은 research의 D-10에 기록한다.

## Summary

W2가 전달한 자료에서 수집 성공과 근거 충분성을 분리하고,
W4가 사용할 수 있는 Claim·명시 Requirement·원문 연결 및 자료별 제한을 반환한다.
Python 내부 모듈에서 입력·허용 검토, 추출 후보 생성, 기계적 근거 검사, 의미·필요한 추가 검증,
계보·범위 보존, 소비자별 결과 투영을 수행하도록 설계한다.

원문·SourceVersion·위치·기대 해시가 부족한 자료는 검증 완료로 표시하지 않는다.
검증 완료는 원문 부합 검사 결과이며 기업 사실의 독립 인증이나 사용자 자격 충족 판정이 아니다.
기존 요약 샘플은 원문 부재 회귀 사례로 유지한다.
새 PM 관측 패키지는 제공된 Evidence·SourceVersion·원래 위치·해시 대상과 재검증 한계를 검토한다.
정상 자동 검증은 별도 합성 원문과 사전 기대 결과로 수행한다.

입력/출력 계약은 W2·W3·W1·W4 검토를 위한 `0.2.0-draft` 제안이다. PM 입력의 `0.1.0-draft / PROPOSED`와 별도다.
애플리케이션 코드·스키마 코드·테스트 구현은 후속 단계이며 이번 요청은 문서 보완과 tasks 생성까지다.

## Technical Context

**Language/Version**: Python 3.12 계열을 초기 호환 기준으로 선택. 프로젝트 패치 버전은 팀 호환 검증 후 잠금.
로컬 Python 3.12.14 실행 확인은 제품 환경 검증 완료가 아니다.

**Primary Dependencies**: Pydantic v2(엄격한 구조·참조 검사), OpenAI Python SDK(Upstage 호환 어댑터, 자동 재시도 끔).
개발용 pytest·Ruff·uv. 패키지 정확 버전은 구현 시 호환 검증과 lock으로 기록한다.
Upstage Solar Pro 4의 공식 예제 ID `solar-pro4`는 실제 계정 PoC 대상이며 운영 검증 완료 값이 아니다.

**Storage**: 이번 모듈은 메모리 처리. PostgreSQL의 기준 ID·버전·소유권을 유지하며 저장·권한·보존 포트를 소비한다.
새 DB·마이그레이션·Graph·Vector·Cache를 만들지 않는다.

**Testing**: pytest의 AS ID 기반 합성 인수/계약 검사, 공급자 HTTP Mock, 명시적 별도 Solar live PoC.
원문·해시·위치·조건 트리·권한 철회·부분 실패·로그 유출을 검사한다.
Ruff는 정적 검사에 사용하며 의미 검증을 대신하지 않는다.

**Target Platform**: 로컬 Windows 개발 환경과 팀 Python Backend에서 호출 가능한 모듈.
Linux 서버·배포 호환은 실제 대상 결정 후 확인하며 플랫폼 지원을 검증 없이 보장하지 않는다.

**Project Type**: 내부 library + 개발 검증 CLI. 독립 API 서버·관리 화면·마이크로서비스가 아니다.

**Performance Goals**: 응답시간·처리량·운영 정확도 목표를 새로 만들지 않는다.
정의된 AS 36개·SC 9개의 결과로 판정하고 실제 처리 관측은 PoC 기록으로 남긴다.

**Constraints**: 원문/버전/권한 불변식, 미확인 상태 보존, 명시 요구의 의미 보존, 외부 지시문 비신뢰,
자동 재수집·재시도·모델 전환 금지, 운영 로그의 원문·프롬프트/응답 전문·키 금지.
timeout·입력 한도 등 실행 값은 명시적 설정과 G-07 검증 대상으로 두고 임의 기본 수치를 넣지 않는다.

**Scale/Scope**: 첫 W3 기능 하나. spec의 5개 사용자 시나리오·FR-001~FR-021 전체를 포함한다.
기존 샘플 2 Source·9 요약 구간·3 첨부는 고정 제품 규모가 아니다.
새 PM 패키지의 본문 4개·첨부 4개·SourceVersion 8개·Evidence 6개도 현재 입력의 관찰값이다.
실제 Graph 적재·임베딩·검색·개인 CRUD/인증 구현·동기화/삭제 시스템·최종 추천·선택 저장·POST-MVP는 제외한다.

## Constitution Check

*GATE: Phase 0 조사 전 검토하고 Phase 1 설계 후 재검토한다.*

각 PASS는 이 기능의 **설계 준수**다. 구현 테스트·실제 연동·공개 배포 GATE 통과가 아니다.

| 원칙 | 조사 전 | 설계 후 | 준수 근거와 이번 범위의 한계 |
|---|---|---|---|
| I. 제품 목적·MVP | PASS | PASS | 문항·경험 기반 추천에 필요한 기업 근거 제공. 최종 소재 선택·초안 등 기능을 추가하지 않음. |
| II. 기준 기록·파생 상태 | PASS | PASS | 원본 ID·버전 유지, 메모리 파생 결과. SQL·Graph 저장소 신설과 구버전 최신화 없음. |
| III. 근거·사실·해석 | PASS | PASS | TextArtifact/Evidence 추적, 후보·기계적/의미/추가 검증·활용 분리. 요약·개인 승인·전략 해석을 검증 사실로 승격하지 않음. |
| IV. 의미·경험·시간 | PASS | PASS | 평면 조건 트리, 필수성, 부정·예외, 동일 경험, 원 기술 표현, 수치·범위·Origin 보존. 최종 충족 판정 없음. |
| V. 공용·개인 격리 | PASS | PASS | 서버 주입 PolicyPort, 읽기/모델 전송/반환 전 검사, audience 투영. 실제 인증 구현과 전 경로 검색 격리는 후속 연동. |
| VI. 검색·추천 책임 | PASS | PASS | W3는 구조화 근거·버전·제한만 제공. 검색 점수·후보 추천·사용자 능력 판정 없음. |
| VII. 버전·멱등성·삭제 | PASS | PASS | 입력 불변, ID 충돌·요청 내 중복 검사, 결정적 파생 식별, 철회/삭제 참조 재노출 차단 포트 검사. 전체 삭제/재색인/복원은 제외. |
| VIII. 계약·기술 결정 | PASS | PASS | draft 계약의 소유자·소비자·호환/되돌림 명시. 모델·공유 계약 합의 GATE 유지. 기술 근거는 research의 D-01~D-10. |
| IX. 검증·정보 보호 | PASS | PASS | FR/AS/SC 매핑, 합성/Mock/live/샘플 진단 구분, 안전한 오류·로그. 문서 작성과 실제 검증 완료를 구분. |

원칙 위반을 면제하는 설계 예외는 없다.
직접 해당하지 않는 전체 Graph·개인 검색·삭제·선택 저장의 통합 검증을 이번 기능의 완료 조건으로 확장하지 않는다.
이 기능에서 제공하는 권한·버전·근거 경계는 [검증 매핑](contracts/validation-matrix.md)에 포함한다.

### 열린 GATE와 진행 조건

| 항목 | 현재 상태·필요 시점 | 담당·완료 증거 |
|---|---|---|
| 수집-지식 계약 TODO / G-01·G-03·G-05 | OPEN. 설계안 준비, 공유 구현/첫 실제 구조화 전 합의 필요 | W2/W3 + W1/W4. 버전·해시·보존 발췌·위치·첨부·허용/검증 의미와 호환 전략 검토 기록. |
| G-01 런타임·지원 형식 | OPEN. 의존성 잠금·공유 구현 착수 전 | 개발팀. 실제 프로젝트 호환 및 담당자·지원 범위 결정. |
| G-02 실제 분석 경로 | OPEN. 운영 결과 제공 전 | AI/Backend. 실제 계정 모델 ID·스키마·한국어 의미 검증·오류 PoC. 별도 합성 PROVIDER_POC 경로로 검증 가능. |
| G-03 권한·무효 참조 | OPEN. 실제 W1/W2 연동 전 | W1/Backend·W3. 현재 권한·버전·삭제/철회 참조 거부의 실제 증거. 전체 검색·삭제 전파는 후속 기능. |
| G-04·G-05 처리·보존·공유 | OPEN. 해당 실제 외부 처리·보존 및 공개 전 | PO/운영·관련 검토 담당. 공급자와 자료별 허용 기록. |
| G-07 실행·운영 한도 | OPEN. 운영 전; PoC에는 실험용 명시 설정 필요 | Backend/운영. 설정·관측·오류 동작 검증. 새 timeout/TTL/보유기간을 확정하지 않음. |
| G-06·G-08 | 기존 OPEN 유지 | 파일럿 평가·POST-MVP 단계의 기존 결정권자. 이번 작업으로 닫지 않음. |

합성 인수 설계·tasks 작성은 진행 가능하다.
공유 계약과 검증 정책의 구현 착수 의존성을 tasks에 반영해야 하며, 실제 연동을 이미 허용된 기본 경로로 취급하지 않는다.
결정이 필요한 제품정책은 PO에게, 기술 계약은 소유자·소비자에게 근거와 함께 남긴다.

새 입력 반영 후에도 설계 준수 PASS를 유지한다. 원문 기준 locator와 발췌 위치,
해시 대상별 보고와 현재 재검증, 미선택 안내의 비신뢰 경계를 보완했으며 GATE를 닫지 않았다.
후속 로컬 합성 구현·계약 검토는 draft 기준으로 진행할 수 있다.
공유 계약 확정·운영 연동·실제 외부 전송은 해당 합의와 검증 증거가 필요한 조건부 작업이다.

## Project Structure

### Documentation (this feature)

```text
specs/001-source-knowledge-validation/
├── spec.md                         # PM 입력 사례를 반영한 명세
├── checklists/requirements.md       # 개정 명세의 품질 검토
├── plan.md                         # 이번 계획
├── research.md                     # Phase 0 조사·결정
├── data-model.md                   # Phase 1 논리/전송 모델 제안
├── quickstart.md                   # 구현 후 실행할 검증 가이드
├── tasks.md                        # 이 개정 후 생성한 구현 작업 목록
└── contracts/
    ├── structure-contract.md       # W2→W3→W4 입출력·포트·오류·권한
    ├── collection-sample-adapter.md # 기존 요약 샘플 제한 진단
    ├── observed-package-adapter.md # 새 PM 관측 패키지 매핑·재검증 한계
    ├── synthetic-examples.md       # 합성 원문·위치·해시·기대 결과
    └── validation-matrix.md        # FR/AS/SC와 검증 경계 매핑
```

`tasks.md`는 개정 spec·plan·계약을 입력으로 `$speckit-tasks`의 활성 템플릿을 통해 생성한다.

### Source Code (repository root)

아래는 **후속 구현의 예상 구조**다. 지금 생성된 소스 디렉터리가 아니다.

```text
pyproject.toml
uv.lock
.gitignore
src/w3_knowledge/
├── models.py                # 불변 입력·후보·검사·결과
├── ports.py                 # 권한·보존·추출·검증 경계
├── config.py                # 명시적 실행 모드·목적·모델/정책 설정
├── errors.py                # 원문을 배제한 오류·로그
├── review.py                # 입력·범위·의존성·누락 검사
├── evidence.py              # 버전·위치·해시·인용 대조
├── validation.py            # 의미/추가 검증 결합·상태 계산
├── requirements.py          # 필수성·조건 트리·문맥 범위
├── skills.py                # 근거 있는 기술 동의어 연결
├── relations.py             # 명확한 동등성·Origin·충돌
├── projection.py            # 반환 전 권한·참조 폐쇄성
├── service.py               # 순차 오케스트레이션·부분 결과
├── cli.py                   # 개발 검증·샘플 진단
└── adapters/
    ├── collection_sample.py # 임시 수집 샘플의 제한 매핑
    ├── observed_package.py  # PM 두 JSON의 ID·위치·검사 보고 매핑
    └── solar.py             # Upstage 추출·의미 검증 어댑터
tests/
├── conftest.py
├── support/
├── unit/
├── contract/
├── acceptance/
├── provider/
│   ├── test_solar_mock.py
│   └── test_solar_live.py
└── fixtures/
    └── synthetic/
```

**Structure Decision**: 하나의 W3 패키지로 도메인·포트를 분리한다.
공통 문서의 Python Backend 방향을 유지하면서 API·DB·Job 구현을 중복 생성하지 않는다.
합성 대역은 테스트 전용으로 두고 운영 설정의 기본값이 되지 않게 한다.
실제 패키지는 로컬 opt-in으로 읽는다. 원문·첨부·로컬 분석 초안을 public fixture로 복사하지 않는다.
필요한 수동 검토 예시는 공개 제외 경로 `tmp/w3-local-review/pm-analysis.json`에만 두며
그 경로의 보호 설정을 구현 작업에서 확인한다. 이번 문서 작업에서 실제 분석 파일은 생성하지 않는다.

### PM 입력의 기술 매핑

[PM 어댑터](contracts/observed-package-adapter.md)가 본문·첨부의 원래 ID와 계보를 보존한다.
source_locator에는 원문 노드/페이지/문단의 위치·정규화 규칙을, Evidence에는 보존 발췌 안의 위치를 둔다.
본문/첨부/추출 전문의 해시는 upstream_integrity_assertions로 보존하고 발췌 기대 digest와 혼용하지 않는다.
수집 측 verified는 UPSTREAM_REPORTED 보고이며 원래 텍스트·신뢰 계약이 없으면 필요한 W3 검사는 PENDING이다.
AVAILABLE·HTTP 기록 null·의도적 미보존·selected=null을 각각 유지한다.

### 처리 흐름과 구현 순서의 기준

1. 계약·ID·허용/검증 의미와 팀 런타임 검토를 선행한다.
2. 불변 모델, 자료별 검토, 코드포인트 Evidence·무결성, 충돌 검사를 만든다.
3. 추출 후보와 별도 의미 검증을 연결하고 필수성·조건·수치·기술 원문을 보존한다.
4. 알려진 Origin과 동일 진술만 묶고 차이·충돌·미확인을 유지한다.
5. 필요한 추가 검증과 현재 권한을 결합하여 KnowledgeBundle·보완 안내를 투영한다.
6. 정상·변형·부분 실패·권한·반복 검증을 AS/SC에 연결하고 공급자 Mock/live 결과를 구분한다.

이 순서는 tasks의 의존성 기준이며 세부 작업 목록을 대신하지 않는다.
핵심 근거가 부족해도 사용자의 진행 선택·재시도는 W1/W4 책임이다.

### 문서 검증과 실행 증거의 구분

최초 계획에서 문서 참조·합성 예제를 검사했고, 이번 개정 후에는 FR 21개·AS 36개·SC 9개의
명세/검증/작업 연결, tasks 형식·의존성, 보호 대상 파일 불변성을 다시 확인한다.
검토 결과는 [품질 체크리스트](checklists/requirements.md)와 tasks의 Notes에 기록한다.
수집 측 validation-report의 PASS를 W3 실행 결과로 재사용하지 않는다.
애플리케이션 테스트·실제 모델 API·DB 검증은 이번 문서 작업에서 수행하지 않는다.

## Complexity Tracking

헌법 위반이나 별도 복잡성 예외가 없다.
새 서버·저장소·작업 엔진을 도입하지 않으며, 포트는 기존 역할 소유 경계와 검증 대역 주입에 필요한 최소 경계다.
재귀 조건 의미는 보존하되 공급자 전송에서는 평면 트리를 사용한다.
공유 계약의 승인과 실제 의미 검증 능력은 문서만으로 입증할 수 없으므로 GATE 상태를 유지한다.
