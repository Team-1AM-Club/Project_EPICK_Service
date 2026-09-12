# 기존 요약 전용 수집 샘플의 제한 진단 매핑

**대상**: [기존 요약 샘플](../../../Data/skhynix_collection_contract_sample.json)
**관찰일**: 2026-09-07
**어댑터 상태**: 샘플 형식 전용 제안. 일반 계약은 [structure-contract.md](structure-contract.md)를 따른다.

이 파일은 기존 Data 샘플만 대상으로 한다. 새 PM 입력은 [관측 패키지 어댑터](observed-package-adapter.md)를 따른다.
두 어댑터가 생성할 내부 모델 버전은 0.2.0-draft이며 원본 샘플 버전·필드는 변경하지 않는다.

## 관찰값과 변환 규칙

| 샘플 입력 | 변환·진단 | 금지하는 해석 |
|---|---|---|
| 최상위 샘플 schema_version | 이 어댑터가 해석하는 초안 버전으로 식별 | 일반 SourceEnvelope 계약 버전으로 사용 |
| source_id 2개 | 원래 ID와 입력 위치 보존 | W3가 새로운 영구 Source ID를 발급 |
| SourceVersion 식별 없음 | source_version_id=null, SOURCE_VERSION_MISSING | 수집일·문서 ID·임의 해시로 불변 버전 생성 |
| evidence_segments 9개 | 각각 segment ID·입력 위치와 원문 누락 진단 보존 | segment가 있다는 이유로 Evidence 생성 |
| 모든 raw_text_in_sample=null | RAW_TEXT_MISSING, 관련 범위 검증 미완료 | normalized_summary를 text/exact_quote로 복사 |
| normalized_summary | W2 보완 검토용 비신뢰 메타데이터, 모델 사실 추출 입력에서 제외 | 검증된 Claim·세부 기술 요구로 승격 |
| sample_extraction_locator.web_line_range | 원래 문자열과 LOCATOR_UNVERIFIED 보존 | 보존 원문에 재현 가능한 코드포인트 위치로 간주 |
| collection.content_hash=null | HASH_EXPECTATION_MISSING | 샘플 JSON·제목·요약의 해시로 원문 무결성 통과 |
| 수집/파싱 SUCCESS 또는 PARTIAL | W2가 보고한 관찰 상태로 보존 | 원문 확보·검증 완료·허용 완료를 추정 |
| 첨부 3개, 다운로드 실패·UNPARSED | 부모 Source 연결, 역할, 실패 원인, ATTACHMENT_UNPARSED | 자료 부재·직무 요구 없음·사용자 부적격으로 표현 |
| required_for_full_analysis=true인 한국어 JD | 해당 상세 직무 분석 범위의 의존성 보고 | 이름이 JD인 모든 첨부를 필수로 변경 |
| false인 영어 JD·FAQ | 필수 아님이라는 샘플의 제공값과 미파싱 상태 보존 | 첨부가 불필요하므로 진단에서 누락 |
| 기사 관련 채용 사이트 링크 | 미수집 관련 링크로 보존 | 링크 방문·수집 또는 대상 버전/요구 자동 생성 |
| 허용 상태 없음 | ACCESS_UNCONFIRMED 및 공용 사용 제한 | 공식 도메인이므로 보존·재배포 허용으로 간주 |
| importance_for_downstream=HIGH | 임시 메타데이터 | 검증 정책·공용 승인·자동 중단 결정으로 사용 |
| 공고와 기사의 다른 시점 | 각 원문 기원·제공된 시점 보존 | 뒤의 기사가 앞 공고의 조건을 대체하거나 전략 전환이라고 확정 |

## 예상 진단

권한 있는 로컬 검토에서 본문 자료 2개와 첨부 3개를 모두 추적한다.
첨부는 각각 SourceInput에 대응하는 진단 단위로 만들되 attachment_id를
합의되지 않은 전역 source_id로 승격하지 않는다.
샘플의 attachment_id는 어댑터의 입력 위치 참조로 보존하고 source/version 미정 상태를 명시한다.
부모와 첨부 관계는 누락시키지 않는다.

9개 구간의 원문 누락을 각각 추적할 수 있어야 한다.
W2_REVIEW에서 제출 입력 ID·누락 종류·영향·보완 주체를 확인하되,
운영 로그에는 요약·제목·원문·전문을 복사하지 않는다.
W4용 결과에는 사용 가능한 Claim과 Requirement가 없으며,
그 의미는 `요구가 없음`이 아니라 `근거가 부족하여 검토하지 못함`이다.
실제 공용 활용 허용이 없는 이 파일의 기업 내용을 공용 결과에 복사하지 않는다.

기대 집계는 **검증된 Claim 0개, 검증된 Requirement 0개**다.
이 숫자는 현재 파일의 인수 기대값이며 일반 계약의 고정 자료 수·성능 기준이 아니다.
다운로드·파싱 실패 원인이 달라지거나 샘플이 개정되면 원본을 변경하지 않은 채 어댑터 기대값과 계약 버전을 재검토한다.

## 보완 요청의 책임

- W2: 실제 허용된 원문 발췌, 본문·첨부 버전·위치, 기대 해시와 보존 표현, 첨부 파싱 결과.
- W1/W2/W3: 기준 ID와 보존 참조·권한의 의미 및 버전 충돌 처리 합의.
- W3/W4: 구조화 결과·미완료 검증·상세 JD 의존성의 소비 계약 합의.
- W1/W4: 사용자가 선택할 제한 분석·중단·재시도의 안내와 실행 제어.

이 문서는 진단 기대값과 연결 책임을 정의한다.
샘플을 수정하거나 웹 재수집·첨부 다운로드·Neo4j 적재를 수행하지 않는다.
