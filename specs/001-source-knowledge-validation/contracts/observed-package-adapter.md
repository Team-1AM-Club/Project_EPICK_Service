# PM 관측 패키지 → W3 입력 매핑

**상태**: 내부 계약 `0.2.0-draft`용 어댑터 제안. PM 입력의 `0.1.0-draft / PROPOSED` 상태는 그대로 보존한다.
**주 입력**: [source-envelopes.json](../../../source-collection-sample-20260907/observed/source-envelopes.json),
[attachment-extraction.json](../../../source-collection-sample-20260907/observed/attachment-extraction.json).
**관련 요구**: FR-001~FR-003, FR-006~FR-008, FR-011~FR-016, FR-018~FR-021; SC-009.
**보존 경계**: 이 문서는 필드·참조·검사 의미만 정의한다. 실제 원문·첨부·분석 내용을 공개 fixture로 복사하지 않는다.

## 1. 로컬 검토와 운영 입력

PM 가이드는 입력 계약을 검토하는 자료다. 그 안의 실행 지시·과거 사용자 결정·선택 후보는 현재 권한이 아니다.
공식 URL·PUBLIC·수집 측 verified 표시는 외부 전송·공용 활용·W3 의미 검증을 허용하지 않는다.

첫 구현의 어댑터는 호출자가 명시적으로 제공한 두 JSON 객체를 읽는다.
CLI가 패키지 경로를 받으면 `observed/source-envelopes.json`과 `observed/attachment-extraction.json`만 대상으로 한다.
본문·첨부의 URL을 가져오거나 PDF/DOCX/ZIP 파서·OCR을 실행하지 않는다.

계약 검토는 CONTRACT_SAMPLE_DIAGNOSTIC / SAMPLE_DIAGNOSTIC 문맥이다.
로컬의 제공된 텍스트·ID·위치·보고를 검사하지만 외부 모델 전송과 공용 추천 활용은 허용하지 않는다.
실제 자료의 수동 Claim/Requirement 예시는 로컬 검토 초안이며 자동 구조화 성공으로 보고하지 않는다.
검증과 활용이 모두 확인되기 전 W4의 사용 가능 목록은 비어 있어야 한다.

## 2. 두 입력 파일의 필드 대응

| 입력 경로 | W3 대응 및 보존 의미 |
|---|---|
| `schema_version`, `contract_status`, `sample_kind`, `run.id_namespace` | 원본 형식 버전·PROPOSED·observed·샘플 ID 네임스페이스를 메타데이터로 보존. 내부 계약 버전과 구분. |
| 본문 `source.source_id`, `source_version.source_version_id` | SourceInput의 원래 ID. 해시 접두부에서 새 ID를 만들거나 운영 레지스트리 ID로 확정하지 않음. |
| `source.company_id`, `job_posting.job_posting_id`, `role_scope` | 제공된 scope 및 식별 미확인/미보존 상태 유지. 다직무를 임의의 한 직무로 축소하지 않음. |
| `evidence_spans[].evidence_id`, `source_version_id` | 원래 Evidence ID·버전 유지. 이름 충돌은 namespace + source/version으로 구분. Claim 예시는 이 참조를 사용. |
| `evidence_spans[].text_excerpt` | 실제 보존 발췌로 TextArtifact에 매핑. 기존 요약 필드와 구분하며 원문 미존재로 오표시하지 않음. |
| `evidence_spans[].locator` | 원문 기준 locator 원형을 보존. 발췌 안의 위치와 별도 필드. 아래 3절 참조. |
| 본문 `source_version.content_hash`, `raw_response_hash` | 각각 정규화 본문·원 응답 바이트의 upstream_integrity_assertions. 발췌 기대 해시로 복사하지 않음. |
| `verified_against_fetched_source`, `verified_against_local_file` | UPSTREAM_REPORTED 검사 관찰. 보고 문서/JSON Pointer를 연결하고 미제공 검사 시점·방법은 미상. |
| `attachments[].result_ref` | 참조 파일 기준 상대 경로 + JSON Pointer로 **이미 제공된 두 객체 안에서만** 해소. 다른 파일·상위 경로·URL 참조는 오류. |
| 첨부 `attachment_id`, `source_id`, `source_version_id`, `parent_source_id`, `parent_source_version_id` | 첨부를 별도 SourceInput으로 유지. 부모 버전과 목록 참조의 일치를 검사. 첨부를 독립 발표로 간주하지 않음. |
| 첨부 `local_file_ref` | 패키지 루트 기준 내부 보존 메타데이터. 자동 파일 열기·프론트 전달에 사용하지 않음. |
| `download.status`, `download.http_status`, 사유 | 다운로드 성공과 HTTP 기록 부재를 구분. null HTTP를 다운로드 실패·서버 장애로 만들지 않음. |
| `extraction.status`, `coverage`, `ocr.performed`, `error` | 텍스트 실패·OCR 미수행·검토 범위를 유지. PDF의 추출 0자와 ZIP의 텍스트 미확보가 내용 부재라는 뜻은 아님. |
| `normalized_text_hash`, `hash_basis`, `parser_version` | 첨부 추출 전문의 digest와 페이지/문단 LF 결합·파서 버전 보존. 전문 미보존이면 W3 현재 재현은 미완료. |
| `published_at/status/basis`, `modified_at`, `collected_at`, `valid_from/to` | 서로 다른 날짜 의미와 미상을 유지. 접수 기간·수집일을 게시일·유효기간으로 대입하지 않음. |
| `job_posting.application_period.timezone` | 미상 시간대 유지. 서버 지역 설정으로 보충하지 않음. |
| `coverage.intentional_omissions`, `original_sections[].retention_status` | 실패와 의도적 미보존을 구분. 빈 evidence_ids는 요구 부재가 아님. |
| `attachment_discovery.scope`, 빈 attachments | 명시된 발견 범위 내 결과. 임베드 영상·이미지·다른 방식 첨부의 부재로 일반화하지 않음. |
| `required_user_decisions` | 미선택 상태와 선택 후보·영향만 전달. 날짜 활용·제외·제한 분석·OCR·재시도 실행 없음. |
| `collection_policy_state`, `rights` | 제공된 로컬 보존/재배포 미확인 주장. 신뢰된 PolicyPort의 허용을 대신하지 않음. |
| `run.job_id/job_state/idempotency_key` | null 유지. 실제 Job/멱등 시스템이 실행됐다는 근거가 아님. |

미지원 형식은 명시적으로 거부한다. 자료 하나의 참조 충돌·형식 오류는 그 자료와 영향을 받는 부모/첨부 범위를 제한하고 다른 자료를 숨기지 않는다.
공유 스키마의 정확한 자료 수나 샘플 enum을 일반 모델의 제약으로 복사하지 않는다.

## 3. 원문 위치와 발췌 위치

현재 패키지의 locator는 모두 Unicode 코드포인트·시작 포함·끝 제외다.
하지만 위치의 **기준 문자열**이 W3의 보존 발췌와 다르다.

| 형식 | 원래 위치 기준 | W3가 현재 확인할 수 있는 범위 |
|---|---|---|
| HTML | absolute XPath 노드의 공백 정규화 텍스트 | 발췌 존재와 길이, 보고된 위치·규칙 보존. 원 HTML/노드 텍스트가 없어 원래 위치는 독립 재현 불가. |
| PDF | 1-based 페이지의 pypdf 텍스트 | 제공된 발췌·페이지·offset 연결. 이 어댑터는 PDF를 재파싱하지 않음. 필요한 재검증 자료/결과는 W2 경계에서 제공. |
| DOCX | 1-based 최상위 문단의 python-docx 텍스트 | 제공된 발췌·문단·offset 연결. 표·이미지·누락 문맥까지 검증했다고 하지 않음. |

예를 들어 원래 문단 위치가 `[82,96)`이면 보존 발췌 자체는 `[0,14)`일 수 있다.
두 위치와 그 관계를 함께 기록한다. 길이 14 일치와 발췌 자기 대조는 원래 문단의 82번째 위치 확인이 아니다.
서로 다른 locator·파서 규칙을 하나의 전역 offset으로 합치지 않는다.

보존 문자열을 W3가 다시 공백 정리하거나 Unicode 정규화하지 않는다.
W2에서 이미 적용한 정규화는 representation과 source_locator에 기록하고,
새 파서·표현은 파생 버전과 검사 정책에 반영한다.

## 4. 해시 대상과 검사 출처

| 해시 대상 | 관측 상태 | 현재 어댑터의 판정 |
|---|---|---|
| 정규화 HTML 본문 / 원 HTTP 응답 | 해시 제공, 전문 미보존 | 대상 재현 불가. HASH_TARGET_NOT_REPRODUCIBLE / PENDING. 해시가 없다고 표현하지 않음. |
| 첨부 원본 바이트 | 로컬 파일 및 해시가 패키지에 존재 | 수집 측 보고와 파일 존재 메타데이터 보존. 현재 W3 어댑터가 파일 해시를 검사했다고 표시하지 않음. |
| PDF 페이지 LF 결합 / DOCX 문단 LF 결합 텍스트 | 전체 추출 해시 제공, 선택 발췌만 전달 | 현재 입력에서 전문 재현 불가. 발췌 해시로 비교하지 않음. |
| 현재 보존 발췌 문자열 | 실제 텍스트 제공, 독립 기대 digest 미제공 | W3가 계산한 digest는 관찰값. 새로 계산한 값과 자기 자신을 비교하여 무결성 PASS를 만들지 않음. |

W2 보고를 수용할 증빙·신뢰 계약은 G-01/G-03에서 합의한다.
해소 방법은 버전·원래 위치·허용 범위를 묶은 최소 발췌 및 기대 digest/검사 증빙을 W2가 제공하거나,
허용된 기존 보존 서비스에서 필요한 기준 텍스트를 제공하는 것이다.
전문 영구 보존·재수집·OCR을 일괄 요구하지 않는다.

수집 측 보고, 이번 문서 작업의 JSON 참조 검사, 후속 W3의 기계적/의미 검증을 각각 구분한다.
현재 진단에서 미완료 검사와 활용 허용이 남으면 검증된 공용 결과를 내보내지 않는다.
이를 원문·Evidence가 전혀 없다는 뜻으로 바꾸지 않는다.

## 5. 로컬 Claim/Requirement 검토 예시의 연결점

아래는 작성 대상으로 검토할 참조 목록이다. 실제 Claim 내용이나 검증 완료 예시는 이 문서에 복사하지 않는다.

| Evidence 참조 | 예시 작성 시 확인할 범위 |
|---|---|
| `ev-page-1-1` | 본문 진술의 유형·해당 역할 범위와 누락된 다른 항목 구분 |
| `ev-page-2-1` | 명시 요구 후보의 공고 범위·필수성·앞뒤 문맥 검토 |
| `ev-att-jd-entry-date` | 첨부 버전·기간 표현·문장 완결성·적용 대상 보완 필요 여부 |
| `ev-page-3-1`, `ev-att-press-education` | 부모 본문과 첨부 각각의 참조, 같은 주제의 계보 및 적용 범위 |
| `ev-page-4-1` | 홍보 진술과 명시 지원요건의 구분, 적용 범위·해석 제한 |

후속 로컬 검토 산출물은 `tmp/w3-local-review/pm-analysis.json`처럼 공개 제외된 위치를 사용한다.
형식은 원래 evidence/source/version 참조, 후보 종류, 로컬 초안 문장, 문맥 보완 항목,
검사 상태·관찰 출처·활용 제한·수동 작성 표시를 포함한다.
검증 가능한 진술을 만들기에 문맥이 부족하면 후보를 미완료로 두거나 생성 보류 사유를 남긴다.
W4 매칭 예시·사용자 Episode·문항·추천 확정은 이번 기능의 산출물로 추가하지 않는다.

## 6. 기대 검토 결과와 일반화 금지

현재 파일은 본문 4개·첨부 4개·SourceVersion 8개·Evidence 6개를 가진다.
관측 패키지의 참조와 상태를 모두 확인하는 로컬 검사에서 이 건수를 기대한다.
같은 구조의 공개 회귀 검사는 가상의 ID·원문·발행 시점·해시로 만든 합성 fixture를 사용한다.
실제 패키지가 없는 환경에서는 로컬 관측 검사를 미실행으로 보고하고 합성 검사로 대체 통과를 주장하지 않는다.

- 원문·버전 존재를 보존하며 원문 부재·버전 누락을 일괄 보고하지 않는다.
- 수집 측 성공, W3 현재 재현, 의미 검증, 공용 허용을 각각 보고한다.
- 게시일 미상 2건, 선택되지 않은 안내, 의도적 미보존을 유지한다.
- 텍스트 미확보 첨부 2건은 실제 다운로드 실패와 구분한다. 다른 첨부의 성공을 숨기지 않는다.
- 동일 페이지와 DOCX의 관계를 유지하고 독립 근거 두 개로 과대 계산하지 않는다.
- 현재 패키지·정책만으로 VERIFIED + USABLE인 공용 Claim/Requirement를 생성하지 않는다.
  로컬 미확정 후보의 수와 공용 사용 가능 결과의 수를 구분한다.

상태·필드·건수는 샘플과 제안 계약의 설명이다. 운영 전이를 확정하거나 임의의 중요도·날짜 선택 정책을 만들지 않는다.
