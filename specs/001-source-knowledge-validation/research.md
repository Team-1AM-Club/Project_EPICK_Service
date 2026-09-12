# Research: 수집 자료의 근거 검증과 지식 구조화

**기능**: `001-source-knowledge-validation`
**작성일**: 2026-09-07
**기준**: [spec.md](spec.md), [헌법 1.0.0](../../.specify/memory/constitution.md)
**상태**: 설계 결정 완료. 공유 계약 합의·실제 공급자 PoC·운영 GATE는 미완료.

## 조사 범위와 확인한 환경

저장소는 문서와 Spec Kit 중심이며 애플리케이션 코드, Python 의존성 선언, 테스트, DB 연결 구성이 없다.
공통 문서의 FastAPI/Python 및 Upstage Solar Pro 4 방향을 따른다.
로컬에서 Python 3.12.14와 uv 0.11.27 실행 가능성을 확인했지만 이는 프로젝트 환경 설치·호환 검증의 완료가 아니다.
공식 문서 조사는 2026-09-07에 수행했다. 실제 모델 호출, 키 조회, 원문 재수집, DB 연결은 수행하지 않았다.

초기 기술 미정 항목인 실행 단위, 근거 위치·해시, 조건 표현, 검증 단계, 공급자 계약, 테스트 구분은 D-01~D-09로 정리했다.
PM 관측 입력에 따른 개정은 D-10이며 기존 결정을 보완한다.
제품 원칙에 대한 추가 질문은 없다. 실제 계약 당사자의 승인이나 PoC가 필요한 사항은 아래 GATE 표에 남긴다.

## D-01. Python 내부 모듈과 검증용 CLI

**Decision**: Python 3.12 계열을 첫 호환 검증 기준으로 삼고 `src/w3_knowledge/`에 내부 라이브러리를 설계한다.
타입 검사는 Pydantic v2, 인수 검증은 pytest, 개발 정적 검사는 Ruff, 프로젝트 환경·잠금은 uv를 사용한다.
정확한 패치와 SDK 버전은 구현 착수 때 호환 검증 후 `pyproject.toml`·`uv.lock`에 잠근다.
개발용 CLI는 합성 fixture·샘플 진단과 명시적인 live PoC 실행에만 사용한다.

**Rationale**: W1의 인증·Job과 W2 수집, W4 추천 경계에서 호출할 수 있는 작은 처리 단위가 필요하다.
현재 기능은 API 서버나 배포 구성을 요구하지 않는다. 불변 입력 모델과 명시적인 교차 참조 검사가 근거 보존에 맞는다.
Pydantic strict mode는 암묵적 타입 변환을 줄이지만 별도 의미 검증을 대체하지 않는다.
uv는 프로젝트 의존성 선언과 잠금 파일을 관리한다.
[Pydantic strict mode](https://pydantic.dev/docs/validation/latest/concepts/strict_mode/),
[uv 프로젝트](https://docs.astral.sh/uv/guides/projects/)

**Alternatives considered**: 별도 FastAPI 서버는 인증·배포·Job 경로를 중복시키므로 보류한다.
LangGraph/Celery/Neo4j는 이 기능의 순차 구조화 계약에 필요하지 않다.
규칙만으로 자연어 전체를 분석하거나 mock 응답만 제공하는 구현은 정상 추출 요구를 충족하지 못한다.

## D-02. 보존 텍스트와 Evidence의 기준

**Decision**: W2의 `source_id`·`source_version_id`를 보존한다.
별도의 `TextArtifact`는 특정 버전에서 허용된 전문 또는 최소 발췌를 담고, 추출 표현·원문 위치·보존 범위를 명시한다.
Evidence는 그 텍스트의 Unicode 코드포인트 기준 0-based `[start, end)`와 `exact_quote`로 지정한다.
W3는 텍스트를 정규화하거나 새 SourceVersion으로 보정하지 않는다.

**Rationale**: URL이 사라져도 허용된 발췌의 근거를 확인할 수 있어야 한다.
전문 보존을 강제하거나 웹 화면 줄 번호를 불변 위치로 취급하면 이 요구를 만족하지 못한다.
Python 문자열의 코드포인트와 W3C 위치 선택자의 시작 포함·끝 제외 방식을 참고한다.
W3C의 HTML 정규화 전부를 구현한다는 의미는 아니다.
[Python 문자열](https://docs.python.org/3/library/stdtypes.html#text-sequence-type-str),
[W3C Text Position Selector](https://www.w3.org/TR/annotation-model/#text-position-selector)

**Alternatives considered**: URL만 저장하면 버전·발췌를 재현할 수 없다.
UTF-16 단위나 화면 줄 번호는 Python과 소비자의 위치 해석 차이를 만든다.
조용한 NFC/NFKC·개행 변환·공백 제거는 원문 위치를 바꾸므로 하지 않는다.
소비자가 JavaScript라면 코드포인트를 명시적으로 세어야 한다.

## D-03. 무결성·충돌·중복은 서로 다른 검사

**Decision**: `TextArtifact`의 기대 해시는 전달 문자열을 변경하지 않고 UTF-8 strict로 인코딩한 바이트의 SHA-256이다.
BOM을 추가하지 않으며 문자열 안의 실제 U+FEFF·CRLF도 그대로 포함한다.
알고리즘·인코딩·대상 텍스트·범위를 함께 기록한다.
기대 해시가 없으면 계산한 관찰 digest는 진단에만 쓰고 무결성 통과로 승격하지 않는다.

같은 복합 ID·버전·표현에 다른 내용이 있으면 충돌이다.
같은 텍스트라도 다른 Source라면 동일 Origin의 증명이 아니다.
현재 호출과 명시적으로 전달된 기존 버전 manifest 안에서만 충돌을 검사한다.

**Rationale**: 바이트 비교에는 표준 hashlib으로 충분하다.
발췌 해시는 전체 HTML/PDF 무결성이나 기업 사실의 진위를 인증하지 않는다.
확인된 동일 계보와 동등한 진술만 묶고 모든 출처를 보존해야 한다.
[Python hashlib](https://docs.python.org/3/library/hashlib.html)

**Alternatives considered**: 해시를 새 SourceVersion ID로 대체하거나 처음/마지막 입력을 선택하면 원본 충돌이 숨겨진다.
본문 해시만으로 재게시·독립성을 판단하거나 미상 필드끼리 같다고 병합하지 않는다.
전역 DB 충돌 검사·영구 중복 방지 시스템은 후속 연동 범위다.

## D-04. 추출 후보와 검증·활용 결정을 분리

**Decision**: 입력 검토 → 후보 추출 → 기계적 참조 검사 → 의미 검증 → 필요한 추가검증 결합 → 현재 권한 재검사 → 결과 투영 순서를 사용한다.
추출기는 후보·근거·의미 성분만 생성한다. 검증 완료나 공용 활용 허용을 설정할 수 없다.
의미 검증은 유형·주체·대상·시점·수치·조건별 대조 기록을 별도 단계에서 생성한다.
중요 정보의 추가 검증은 신뢰된 `VerificationPolicy`와 내부 검증 포트의 판정으로 결합한다.
필요 여부를 정할 정책 또는 필요한 판정이 없으면 미완료다.

**Rationale**: 정확한 인용과 해시만으로는 계획을 성과로 바꾼 진술을 발견할 수 없다.
별도 검증 호출도 독립적인 사실 인증은 아니다.
모델 자기 확신, 동일 모델 응답의 반복 일치, JSON 형식 통과를 의미 검증의 충분조건으로 삼지 않는다.
기계적 불일치는 의미 검증 결과로 덮을 수 없다.
결과는 원문 부합 검사와 남은 한계를 설명한다.

**Alternatives considered**: 추출 응답의 `verified=true`를 신뢰하거나 단일 confidence로 합치는 방식은 배제한다.
사용자 승인 UI나 별도 사실 검증 서비스를 새로 만들지 않는다.
실제 의미 검증의 유효성은 합성 정답집 기반 Solar PoC와 사람의 사례별 검토로 확인한다.

## D-05. Solar 구조화 출력과 평면 조건 트리

**Decision**: Upstage 호환 어댑터를 분리한다.
공식 문서의 `solar-pro4`, `https://api.upstage.ai/v1`, Chat Completions의 strict JSON Schema를 PoC 후보로 사용한다.
런타임은 검증 기록에 연결된 명시적 모델 ID·스키마 버전·프롬프트 버전을 받는다.
문서 예제의 성공을 실제 계정 검증으로 간주하지 않는다.

공급자 JSON에는 root object, required 속성과 nullable 미상 값, `additionalProperties=false`를 적용한다.
재귀 조건 객체 대신 `root_node_id`와 평면 `nodes`·자식 ID 참조로 요구 조건을 표현한다.
서버가 단일 루트, 참조 존재, 연결성, 순환 금지, 연산자별 인자 수와 Evidence를 검사한다.

**Rationale**: 공식 문서는 recursive ref를 지원하지 않고 객체 깊이에 제한을 둔다.
평면 표현으로 중첩 AND/OR·예외·동일 경험을 보존하며 공급자 스키마와 내부 계약을 검증할 수 있다.
문서도 스키마 적합성이 값의 신뢰를 보장하지 않는다고 설명한다.
[Upstage 구조화 출력](https://console.upstage.ai/docs/capabilities/generate/structured-outputs)

**Alternatives considered**: 무제한 재귀 스키마, 임의 JSON 보수, 조용한 다른 공급자 전환은 하지 않는다.
토큰 한도로 원문이나 조건이 잘리면 영향 범위를 실패·미완료로 반환한다.
이번 기능에서 임의 청킹·자동 재수집으로 누락된 문맥을 메우지 않는다.

## D-06. 실패와 재시도 제어

**Decision**: SDK 자동 재시도는 `max_retries=0`으로 끈다.
429 발생 시 다음 공급자 호출을 중단하고 완료 결과, 미처리 범위, 오류 코드, 제공된 Retry-After를 W1에 전달한다.
W3가 자동 재시도·sleep·Job·checkpoint·모델 전환을 실행하지 않는다.
시간 제한과 입력 한도는 명시적 배포 설정으로 받고 합의 전 운영 기본 숫자를 만들지 않는다.

**Rationale**: Upstage의 rate limit과 사용 한도 오류는 다른 조치를 요구한다.
SDK 기본 재시도는 사용자가 결정하는 재시도 정책과 충돌할 수 있다.
오류 message 전문 대신 상태·code를 분기하며 누락된 code도 안전하게 처리한다.
[Upstage 오류](https://console.upstage.ai/docs/resources/error-codes),
[OpenAI Python SDK retries](https://github.com/openai/openai-python#retries)

**Alternatives considered**: 429 무한 재시도, 전체 성공 덮어쓰기, 임의 TTL·backoff 확정은 배제한다.
개별 자료의 형식·의미 실패는 다른 자료의 정상 결과를 숨기지 않는다.

## D-07. 상위 권한 경계와 저장소 경계

**Decision**: `TrustedContext`와 `PolicyPort`를 원문 입력과 별도로 주입한다.
원문 읽기 전, 모델 전송 직전, 결과 반환 직전에 현재 유효한 허용 결정을 검사한다.
자료의 권한 주장이나 공개 URL을 허용 근거로 신뢰하지 않는다.
개인 자료는 이번 공용 구조화 경로에서 배제한다.

코어는 메모리 안에서 처리하며 DB·Graph·Vector·Cache에 쓰지 않는다.
텍스트 참조를 해소할 때도 허용된 상위 보존 서비스 포트만 사용하고 문서의 URL·로컬 경로를 직접 열지 않는다.
운영 결과·원문·프롬프트 전문은 파일·로그에 자동 저장하지 않는다.

**Rationale**: 인증·삭제·저장 정책은 W1, 수집과 보존 표현은 W2 소유다.
W3는 권한 적용과 안전한 반환을 담당한다.
재호출 때 철회·삭제된 참조를 다시 허용하지 않는 포트 계약을 검증하지만 전 경로 삭제 완료를 주장하지 않는다.

**Alternatives considered**: 입력의 tenant ID나 승인 필드를 신뢰하거나 사후 필터 하나에 의존하지 않는다.
이 기능을 위해 SQL/Neo4j 저장소·새 인증 서버를 만들지 않는다.

## D-08. 샘플 어댑터는 제한 진단용

**Decision**: 샘플 전용 변환은 별도 어댑터로 격리한다.
2개 Source·9개 요약 구간·3개 첨부를 보존하되 원문·버전·기대 해시·안정 위치·허용 미확인을 구분한다.
검증된 Claim/Requirement는 0개다.
샘플 데이터의 임시 필드명·enum을 일반 계약에 복사하지 않는다.

**Rationale**: 샘플은 수집 계약의 빈 곳을 보여준다.
누락 메타데이터를 W3가 만들어 채우면 첫 실제 연동 전 합의가 필요한 문제를 숨기게 된다.
정상 구조화 검증은 가상 기업의 별도 합성 원문과 기대 결과로 수행한다.

**Alternatives considered**: 요약을 원문으로 전환하거나 직무 이름으로 첨부 요구를 추정하지 않는다.
샘플 기사 시점만으로 이전 공고 조건을 대체하지 않는다.

## D-09. 세 종류의 검증을 구분

**Decision**: 합성 fixture+대역의 코어 인수 검증, 공급자 HTTP Mock 검증, 실제 Solar 호출 PoC를 별도로 실행·보고한다.
정상 fixture에는 기대 진술·요구를 미리 정해 빈 출력이 성공이 되지 못하게 한다.
원문 의미 변형, 권한 철회, 참조 충돌, 부분 실패를 독립적인 부정 사례로 둔다.
pytest parameterization을 이용해 AS ID와 결과를 연결한다.
[pytest 매개변수화](https://docs.pytest.org/en/stable/how-to/parametrize.html)

**Rationale**: 대역은 오케스트레이션과 불변식의 증거이고 모델 품질의 증거는 아니다.
실제 PoC 역시 정의한 사례의 결과이며 운영 정확도·응답시간 보장이 아니다.

**Alternatives considered**: Mock 통과로 G-02를 닫거나 평균 점수로 권한 유출·근거 왜곡을 상쇄하지 않는다.
Neo4j·임베딩·개인 추천의 통합 검증은 후속 기능으로 남긴다.

## D-10. PM 관측 패키지의 근거 매핑과 재검증 범위

**Decision**: 기존 요약 전용 어댑터를 유지하고 PM의 두 observed JSON을 읽는 별도 어댑터를 추가한다.
내부 계약은 `0.2.0-draft`로 개정한다. PM의 `0.1.0-draft / PROPOSED` 입력 버전은 바꾸지 않는다.
원래 source/version/evidence ID와 샘플 네임스페이스, 부모 참조, 원문 기준 locator, 해시 대상·변환 규칙을 보존한다.
발췌 내부 위치와 원래 노드·페이지·문단 위치, 수집 측 보고와 W3 현재 검사를 각각 기록한다.

**Rationale**: 새 패키지는 실제 발췌 6개와 SourceVersion 8개를 제공한다.
반면 HTML·추출 전문은 보존하지 않아 원래 위치/전체 해시를 현재 두 JSON만으로 재현할 수 없는 부분이 있다.
발췌가 있는 입력을 원문 없음으로 처리하거나, 발췌 자기 대조를 원래 위치 검증으로 삼으면 모두 잘못이다.
출처·검사 증빙 계약이 합의되지 않은 현재 상태는 필요한 검사 PENDING과 공용 사용 제한을 유지한다.
근거: [PM 패키지 README](../../source-collection-sample-20260907/README.md),
[수집 측 검증 보고](../../source-collection-sample-20260907/validation-report.md).
이 문서의 실행 기록은 W3가 새로 수행한 검증이 아니다.

**Alternatives considered**: PM 전체 본문 hash를 발췌 기대 digest로 복사하거나,
offset을 0으로 바꾼 뒤 원래 위치를 삭제하는 매핑은 배제한다.
W3 어댑터에 PDF/DOCX 파서·OCR·재수집을 추가하지 않는다.
수집 측 verified boolean만으로 의미 검증·공용 활용을 허용하지 않는다.

실제 내용이 들어가는 수동 Claim/Requirement 검토 예시는 공개 제외된 로컬 산출물로만 만든다.
가상의 구조 대응 fixture로 자동 회귀를 실행하고 실제 패키지 검사는 로컬 opt-in으로 분리한다.
실제 패키지를 합성으로 재라벨하거나 실제 원문을 공개 테스트 데이터에 복사하지 않는다.

현재 Python 코어·공급자·저장소 선택을 바꾸는 새로운 기술 조사는 필요하지 않다.
D-01~D-09의 실행 방식과 불변식을 유지하며 공유 계약 TODO에
원래/발췌 위치 매핑, 해시 대상, 수집 측 증빙의 수용 기준을 추가한다.


## 남은 결정·GATE와 적용 시점

| 항목 | 결정권자·필요 시점 | 해소 근거 및 진행 가능 범위 |
|---|---|---|
| 수집-지식 계약 TODO / G-01·G-03·G-05 | W2/W3, W1 ID·보존 담당, W4 소비 담당 · 공유 모델 구현 착수 및 첫 실제 입력 구조화 전 | 아래 설계는 제안이다. 버전 발급·텍스트 표현·해시 대상·위치·첨부 연결·허용·검증 의미와 호환 전략의 검토 기록 필요. tasks 작성과 독립 합성 실험은 가능하나 합의 전 공유 계약 확정·실제 연동은 불가. |
| 의미·추가 검증 정책 / G-01·G-02 | W3/AI·Backend, 제품정책 영향 시 PO · 실제 검증 완료 결과 제공 전 | 중요 정보 구분과 검사 항목·판정 주체의 정책 버전, 사람 검토 정답집, 미완료 처리 검증 필요. 자료의 HIGH 표시는 정책 아님. |
| 실제 Solar PoC / G-02 | AI/Backend · 실제 분석 경로 검증 | 계정 모델 접근·반환 ID, 프로젝트 스키마, 한국어 의미 보존, 오류·중단·입력 제한을 확인. 임베딩·Neo4j는 후속 기능. |
| 현재 권한·무효 참조 / G-03 | W1/Backend·W3 · 실제 연동 전 | 원문 읽기·외부 처리·반환 시 유효성 확인 및 철회·삭제 참조 거부의 실제 연동 증거 필요. 전체 삭제 전파는 후속 기능. |
| 외부 처리·보존·공용 사용 / G-04·G-05 | PO/운영·관련 검토 담당 · 실제 외부 전송·보존 및 공개 전 | 공급자 처리·학습·보관 조건, 자료별 발췌·보존·공유 허용 기록 필요. 합성 fixture가 실제 자료 허용을 대체하지 않음. |
| 런타임 호환·지원 범위 / G-01 | 개발팀 · 의존성 잠금·공유 구현 착수 | 팀 런타임과 패키지 호환 확인, 지원 수집 형식·계약 책임자 확인. 새 일정·예산은 정하지 않음. |
| 실행 한도 / G-07 | Backend/운영 · 운영 전 | timeout·입력 한도·전역 호출 한도 설정 및 검증 필요. 상위 Job 제어와 연결. |
| G-06·G-08 | PRD의 기존 결정권자·단계 | 파일럿 평가와 POST-MVP 결정 유지. 이번 계획으로 닫거나 선행 구현하지 않음. |

이 단계의 기술 설계 미정은 해소했다. 미결 GATE는 실제 승인·검증이 필요한 의존성이며,
안전한 실패 동작을 설계했다는 이유로 통과 상태로 변경하지 않는다.
