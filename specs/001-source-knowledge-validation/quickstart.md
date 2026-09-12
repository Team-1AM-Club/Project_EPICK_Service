# Quickstart: 구조화 기능 검증 가이드

**작성일**: 2026-09-07
**현재 상태**: 로컬 Python 구현과 합성·Mock·계약 검증이 준비되었다. 실행 결과와 미검증 범위는 [validation-results.md](validation-results.md)를 기준으로 한다.
**기준**: [plan.md](plan.md), [입출력 계약](contracts/structure-contract.md), [검증 매핑](contracts/validation-matrix.md).

**개정**: PM 관측 패키지 추가. 내부 계약은 0.2.0-draft이며 실제 패키지의 PROPOSED/0.1.0-draft는 유지한다.

## 1. 단계와 전제

`src/w3_knowledge/`, `tests/`, `pyproject.toml`, `uv.lock`이 생성되어 있다.
Python 3.12 호환 환경·uv·프로젝트 잠금 파일이 필요하다.
이 저장소 작업 지침에 따라 명령은 RTK를 통해 실행한다. 작업 디렉터리는 저장소 루트다.

합성 fixture와 Mock 검증은 API 키·PostgreSQL·Neo4j 없이 실행 가능해야 한다.
정상 fixture는 [합성 예제](contracts/synthetic-examples.md)의 원문·버전·위치·기대 digest와 사전 정답을 사용한다.
fixture의 `request.json`, `extraction.json`, `validation.json`, `expected.json`은 각각 입력·추출 대역·검증 대역·최종 기대 결과로 분리한다.
변형 사례는 후보만 바꿔 거부 동작을 검사한다. 기대 결과를 실제 출력에서 자동 생성하지 않는다.

팀 공유 계약·런타임 합의는 G-01, 실제 공급자 경로는 G-02,
실제 권한·보존 연동은 G-03, 실제 자료의 외부 처리·보존·공개는 G-04·G-05 확인이 필요하다.
게이트 없이 합성 대역을 실운영 기본값으로 사용하지 않는다.

## 2. 구현 후 환경 준비와 정적 검사

개발 의존성은 `dev` 그룹에 pytest·Ruff를 선언하고 구현 시 호환 검증 후 잠근다.
버전 잠금 변경을 검토한 상태에서 다음을 실행한다.

```powershell
rtk proxy uv sync --locked --group dev
rtk proxy uv run --locked ruff check src/w3_knowledge tests
rtk proxy uv run --locked ruff format --check src/w3_knowledge tests
```

기대 결과는 잠금 파일과 일치하는 환경 및 정적 검사 통과다.
라이브러리 설치나 포맷 검사가 의미적 정확성·공급자 호환성을 증명하지 않는다.
RTK를 사용할 수 없는 실행 환경은 팀 실행 지침에 맞는 등가 명령을 정하되 검사 내용을 생략하지 않는다.

## 3. 오프라인 계약·인수 검증

```powershell
rtk proxy uv run --locked pytest tests/unit tests/contract tests/acceptance tests/provider/test_solar_mock.py -q
rtk proxy uv run --locked python -m w3_knowledge.cli validate --fixture tests/fixtures/synthetic/normal --mode synthetic
rtk proxy uv run --locked python -m w3_knowledge.cli diagnose-sample --input Data/skhynix_collection_contract_sample.json
```

CLI 이름과 옵션은 이번 계획의 구현 계약이다.
`validate`는 명시한 로컬 fixture 디렉터리만 읽는다.
자료 본문·참조 URL·retained_ref가 임의의 파일 접근 경로를 선택하게 하지 않는다.
`diagnose-sample`은 샘플 어댑터와 제한 진단만 실행하고 공급자를 호출하지 않는다.
기본 자동 회귀에는 합성 대응 fixture를 사용한다. 실제 패키지·기존 샘플 파일을 CI/public fixture에 복사하지 않는다.

실제 PM 파일을 명시적으로 제공한 로컬 검사는 다음과 같다. 원문 재수집·첨부 재파싱·OCR·모델 호출은 수행하지 않는다.

```powershell
rtk proxy uv run --locked pytest tests/contract/test_observed_package.py --pm-sample source-collection-sample-20260907 -q
rtk proxy uv run --locked pytest tests/contract/test_collection_sample.py --legacy-sample Data/skhynix_collection_contract_sample.json -q
rtk proxy uv run --locked python -m w3_knowledge.cli diagnose-sample --package source-collection-sample-20260907
```

`--pm-sample`과 `--legacy-sample`은 pytest 옵션으로 등록되어 있으며, 실제 패키지 검사는 CLI의 명시적 `--package`/`--input` 경로로 수행한다.
옵션이 없으면 로컬 관측 검사만 SKIPPED이며 동일 구조의 합성 회귀는 실행한다.
옵션으로 지정한 파일이 없거나 잘못되면 실패로 보고하고 다른 데이터로 조용히 대체하지 않는다.
`--input`은 기존 요약 파일, `--package`는 PM의 두 observed JSON이며 동시에 지정하지 않는다.

| 실행 사례 | 기대 관찰 |
|---|---|
| 정상 합성 묶음 | 일반 Claim 1개·Requirement 2개가 사전 의미와 일치. 모든 원문·버전·위치·해시 검사 일치. 평가 모드는 SYNTHETIC_TEST. |
| 기존 요약 샘플 | CONTRACT_SAMPLE_DIAGNOSTIC. Source 2개·첨부 3개·원문 없는 구간 9개가 진단에서 추적됨. 검증 Claim/Requirement 0개. 원문·버전·해시·허용 미확인과 첨부 실패 구분. |
| 새 PM 관측 패키지 | CONTRACT_SAMPLE_DIAGNOSTIC. 본문 4개·첨부 4개·SourceVersion 8개·Evidence 6개 보존. 제공된 원문/버전을 누락으로 오표시하지 않음. 원문 기준/발췌 위치와 upstream 보고/현재 검사 구분, 재검증·허용 미완료 상태 유지. |
| 본문 정상·필수 JD 미파싱 | 본문에서 확인된 결과 유지, 전체 요청 범위 PARTIAL. JD 세부 요구는 NOT_ASSESSED. |
| 원문/버전/위치/해시 불일치 | 해당 범위 VERIFIED 불가. 누락과 실패 사유 구분. |
| 의미 변형 후보 | 인용 문자열이 맞아도 계획→성과, OR→AND, 숫자/단위·부정/예외 변형은 거부. |
| 알려진 재게시·시점 차이 | 계보와 원문 참조 유지. 독립 근거 수 과장·자동 최신 대체 없음. |
| 악성 지시·권한 철회 | 도구·정책·전송 목적지 변화 없음. 읽기/전송/반환 단계별 허용 검사 및 철회 차단. |
| 429·잘린 JSON Mock | 자동 재호출 0, 완료 자료와 미처리 범위 보존, 잘린 JSON 성공 보수 없음. |
| PM 부분 상태 | 추출 성공과 의도적 미보존, HTTP 기록 부재와 다운로드 성공, 날짜 미상·미선택 안내를 구분. 미확인 공용 Claim/Requirement 0개. |

CLI는 기본적으로 검사 ID·상태·안전한 건수·제한 코드만 출력한다.
원문·제목·요약·프롬프트·응답 전문·비밀값을 콘솔이나 자동 결과 파일에 출력하지 않는다.
소비자 결과의 원문·근거 대조는 권한 있는 테스트 메모리 안에서 수행한다.
실패 보고도 pytest의 원문 repr·locals·SDK body가 새지 않도록 구성한다.

CLI 종료 코드는 `0`이면 해당 검증 실행의 기대 결과 일치,
`1`이면 기대 결과 불일치, `2`이면 실행 설정·입력 사용 오류다.
샘플 진단의 제한 상태가 기대와 일치하면 0일 수 있다. 자료가 검증 완료되었다는 의미는 아니다.
일반 구조화의 업무 결과 상태는 KnowledgeBundle 필드로 전달하며 CLI 종료 코드와 혼동하지 않는다.

## 4. 실제 Solar PoC

다음은 자동 기본 테스트에서 제외한다.
합성 자료만 사용하는 실제 공급자 PoC도 명시적 live 실행과 해당 계정·처리 조건 확인이 필요하다.

전제는 다음과 같다.

- 상위 실행 환경이 `UPSTAGE_API_KEY`를 비밀로 주입한다. 키 값을 셸 명령·fixture·문서·로그에 적지 않는다.
- 팀이 검토한 PoC 설정에 실제 모델 후보 ID, 스키마·프롬프트·검증 정책 버전,
  명시적 timeout·입력 한도, `max_retries=0` 및 외부 전송 허용을 제공한다.
- 사전 합성 정답집과 사람 검토 담당을 지정한다.
- PoC 실행 설정과 운영 검증 완료 설정을 분리한다. PoC는 G-02를 검증하기 위한 경로이며
  G-02가 미완료여도 합성 입력의 실험은 가능하다. 해당 출력은 운영 추천에 전달하지 않는다.

```powershell
rtk proxy uv run --locked pytest tests/provider/test_solar_live.py -m live --run-live -q
```

`--run-live`는 후속 pytest 설정에서 등록할 명시적 실행 옵션이다.
옵션·키·필수 설정이 없으면 live 테스트는 SKIPPED 또는 명확한 설정 오류여야 한다.
환경만 보고 자동 호출하지 않으며 테스트 중에도 키를 출력하지 않는다.

확인할 것은 계정에서 사용한 실제 모델·응답 모델 ID, 전체 스키마 수용,
한국어 요구·부정·단위·동일 경험·계획 유형 보존, 실제 오류·중단 동작이다.
추출 정확성과 JSON 형식 적합성, 의미 검증의 놓친 변형을 각각 기록한다.
예상 후보를 하나도 내지 않는 모델은 정상 인수 성공이 아니다.
테스트 결과에 원문·프롬프트/응답 전문 대신 AS ID·판정·안전한 오류 코드·모델/정책/스키마 버전을 남긴다.

## 5. 완료 판정과 남은 연동

FR·AS·SC 추적은 [validation-matrix.md](contracts/validation-matrix.md)를 사용한다.
인수 시나리오 36개의 합성 검증과 로컬 실제 패키지 검토, 경계 검사, 실제 PoC의 실행/미실행·실패를 별도 표로 보고한다.
문서의 체크리스트 완료를 구현 테스트 통과로 사용하지 않는다.

W1의 실제 인증·보존 서비스, SQL/Graph 동기화·삭제, 임베딩/검색, 최종 추천/선택 저장은 이 quickstart에 포함하지 않는다.
이 기능의 포트에 대한 합성 검증이 해당 실제 연동의 완료를 의미하지 않는다.
공동 계약 합의와 live PoC가 남아 있으면 `합성 인수 통과, 실제 연동 미검증`처럼 범위를 구분해 보고한다.
