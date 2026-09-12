# Data Model: 근거 검증과 지식 구조화

**상태**: W3 내부 계약 제안 `0.2.0-draft`, 2026-09-07. 두 입력 어댑터와 근거 검증 출처를 반영한 초안 개정이며 DB 스키마·마이그레이션이 아니다.
**근거**: [spec.md](spec.md), [연구 결정](research.md), [입출력 계약](contracts/structure-contract.md).

## 공통 규칙과 소유권

W2는 Source·SourceVersion 및 보존 표현을 제공하고 W1의 기준 ID·권한·보존 계약과 맞춘다.
W3는 입력을 변경하지 않고 구조화 파생 결과를 만든다. W4는 결과의 근거·제한을 소비한다.
아래 필드·상태·ID 규칙은 공유 계약 합의 전 제안이며 현재 샘플의 필드명을 표준으로 확정하지 않는다.

- ID는 비어 있지 않은 불투명 문자열이다. SourceVersion 누락을 새 ID로 보정하지 않는다.
- 일반 nullable 값은 미확인을 뜻한다. `0`·`false`·빈 목록·현재 시점으로 바꾸지 않는다.
  필드 누락은 계약 오류, 명시적 `null`은 해당 값의 미확인이다. 목록 `[]`은 전달된 항목 0개일 뿐 대상 세계의 부재가 아니다.
- 날짜는 날짜 정밀도, 시각은 시간대가 있는 시각으로 구분한다. 부분 날짜·기간은 원문과 정밀도를 유지한다.
  수집 시각으로 발행일·유효기간을 메우지 않는다.
- 수치의 원문을 보존하고 정규화 값은 십진 문자열로 표현한다. 의미가 불명확하면 정규화 값은 null이다.
- 구조 검사는 strict 타입·알 수 없는 속성 거부·교차 참조 검사로 수행한다.
  관찰 가능한 원본의 임시 메타데이터는 명시적 비신뢰 부속 영역에만 두고 LLM·권한·공용 결과에 자동 전달하지 않는다.
- 객체는 불변 값으로 취급한다. 기존 SourceVersion·텍스트·검증 보고서를 제자리 수정하지 않는다.
  새로운 처리는 새 bundle과 파생 버전으로 표현한다.

## 입력 및 신뢰 문맥

| 개념 | 필드·형식 | 관계·검증·의미 |
|---|---|---|
| StructureRequest | `contract_version`, `request_id`, `requested_scope`, `items: SourceInput[]`, `origin_links: OriginLink[]`, `known_versions: VersionManifestEntry[]` | W2 자료를 W1/W3 호출 경계에서 전달. 요청 ID는 진단 상관관계이며 출처 버전이 아니다. 알 수 없는 계약 버전은 envelope 거부. |
| SourceInput | `input_item_id`, `source_id: str|null`, `source_version_id: str|null`, `id_namespace`, `parent_item_id: str|null`, `document_role`, `source_url: str|null`, `scope`, `dates`, `observations`, `artifacts: TextArtifact[]`, `dependencies: Dependency[]`, `untrusted_notes[]` | 본문과 첨부를 각각 item으로 둔다. 부모·의존 참조는 요청 내에서 유효해야 한다. 누락 버전도 진단 대상이므로 item 자체를 버리지 않는다. PM 샘플 ID는 sample_only 네임스페이스로 보존하고 운영 레지스트리 ID로 승격하지 않는다. |
| CollectionObservations | `fetch`, `parse`, `current_access`, `retention` 각각 `outcome`·원래 상태값·실패 사유 참조 | outcome 제안: SUCCESS / PARTIAL / FAILED / NOT_ATTEMPTED / UNKNOWN. 서로 독립적이며 W2 관찰이다. `SUCCESS`가 검증·법적 허용·전체 완전성을 뜻하지 않는다. |
| Dependency | `target_item_id`, `affected_scope`, `needed_for_scope: bool|null`, `basis_ref: str|null` | 상세 요구 분석에 필요한 첨부 등의 의존성. null이면 필요 여부도 미확인. 문서의 HIGH 값으로 필요 여부를 확정하지 않는다. |
| Scope | 기업·조직·공고·직무의 각 원본 ID 또는 원문 표현·근거 참조, `coverage_description` | 확인한 범위만 기록. 미상 조직·직무를 전체 기업으로 확대하지 않는다. ID가 없으면 원문 표현을 보존하고 기준 ID를 발급하지 않는다. |
| SourceDates | `published`, `collected`, `effective_period` 각각 값·정밀도·확인 근거 | 미상과 시점 차이를 유지한다. 배열 순서나 날짜 크기만으로 대체·최신 판정하지 않는다. |
| TrustedContext | `principal_ref`, `purpose`, `audience`, `evaluation_mode`, `policy_ref`, `verification_policy_ref` | 서버가 주입. audience는 W2_REVIEW 또는 W4_KNOWLEDGE. evaluation_mode는 SYNTHETIC_TEST / PROVIDER_MOCK / CONTRACT_SAMPLE_DIAGNOSTIC / LIVE. 자료 입력에서 설정·덮어쓰기 불가. |
| EffectivePolicyDecision | `decision_ref`, `policy_version`, `subject_ref`, `resource_ref`, `operation`, `decision`, `validity_ref` | PolicyPort의 현재 판정. operation은 READ_SOURCE / PROCESS / SEND_TO_PROVIDER / RETAIN_EXCERPT / SHARE_PUBLIC / RETURN_TO_CALLER. decision은 ALLOW / DENY / UNKNOWN. UNKNOWN은 허용이 아니다. 삭제·철회된 참조도 DENY. |

인증 자체는 W1에서 구현한다. 개인·비공개 자료는 원문 구조화에 들어가지 않으며,
접근이 허용된 제출자에게만 자신의 입력 위치와 최소 보완 사유를 반환한다.
다른 사용자 자료의 제목·ID·건수·사유로 존재를 누설하지 않는다.

purpose는 SYNTHETIC_ACCEPTANCE / SAMPLE_DIAGNOSTIC / PROVIDER_POC / PRODUCTION_STRUCTURE로 구분한다.
실제 공급자를 호출한 합성 PoC는 evaluation_mode=LIVE, purpose=PROVIDER_POC다.
PROVIDER_POC는 신뢰된 합성 fixture 등록 범위만 처리하고 운영 추천 결과로 소비할 수 없다.
G-02 미완료 상태에서 운영 검증 완료 설정을 부여하지 않는다.

## 보존 텍스트와 Evidence

### TextArtifact

| 필드 | 형식·규칙 |
|---|---|
| `text_artifact_id` | SourceVersion 안에서 고유한 불투명 ID. 전체 키는 source ID + version ID + artifact ID. |
| `source_ref` | 부모의 source ID·version ID와 일치해야 한다. 미확인 입력에서는 null 가능하나 검증 완료는 불가. |
| `text` | 실제 보존 문자열 또는 null. 요약은 이 필드에 매핑하지 않는다. |
| `retained_ref` | 상위 보존 서비스의 불투명 참조 또는 null. null text를 허용된 포트에서 해소할 때만 사용. URL·파일 경로 직접 실행 금지. |
| `retention_scope` | FULL_TEXT / EXCERPT / UNKNOWN. 전문 여부와 분석한 범위를 구분한다. |
| `source_locator` | 보존 표현이 원본 어느 부분인지 나타내는 `scheme`·`value`·버전 연결 확인 참조. 예: 합의된 페이지/구간 식별. 단순 샘플 줄 번호는 확인된 위치가 아니다. |
| `representation` | W2의 파싱·표현 방식과 버전. 원본 PDF 바이트와 파싱 텍스트를 혼동하지 않는다. |
| `integrity` | `algorithm=sha256`, `encoding=utf-8`, `target_artifact_id`, `target_scope=exact_text`, `expected_digest: str|null`. 64자리 소문자 hex. |
| `observed_digest` | W3가 별도 검토 결과에 기록하는 실제 digest. 입력에 채워 넣지 않는다. 기대값 없이 관찰값만 있으면 PENDING. |
| `upstream_evidence_id` | W2가 제공한 원래 Evidence ID 또는 null. 참조 키는 id_namespace + source/version + evidence ID. 원래 ID를 파생 artifact ID로 대체하지 않는다. |
| `upstream_integrity_assertions[]` | 원문 응답 바이트·정규화 본문·첨부 바이트·추출 전문 등에 대한 제공된 digest. 각 항목에 알고리즘, 대상 종류·참조, 변환/결합 규칙, parser 버전, 대상 현재 가용 여부를 보존한다. 발췌용 integrity로 복사하지 않는다. |
| `upstream_checks[]` | 수집 시 원문/로컬 파일 대조 보고와 그 출처 참조. W3의 새 PASS가 아니다. 검사 주체·시점·방법이 없으면 미상으로 남긴다. |

text와 retained_ref가 함께 있으면 같은 표현을 가리키는지 검사한다.
외부 참조 해소 실패는 원문 부재·접근 거부 등 실제 원인으로 전달한다.
각 artifact를 전문으로 확장하거나 요청에 없는 자료를 가져오지 않는다.
예상 digest가 맞더라도 source/version/locator 연결을 확인할 수 없으면 VERIFIED가 아니다.

PM 패키지의 발췌에는 별도 TextArtifact를 파생시킬 수 있지만 source/version/evidence ID는 그대로 유지한다.
원문 노드·페이지·문단 기준 locator의 위치·정규화 규칙은 source_locator에 원형으로 보존한다.
발췌 자체의 위치를 `[0, len(excerpt))`로 매핑해도 원래 위치 검증을 완료한 것이 아니다.
자세한 필드 대응과 검증 한계는 [PM 관측 어댑터](contracts/observed-package-adapter.md)를 따른다.

### Evidence

`evidence_id`, `input_item_id`, `source_ref`, `text_artifact_id`, `start`, `end`, `exact_quote`, `source_locator_ref`를 갖는다.
start/end는 해당 보존 문자열의 Unicode 코드포인트 기준 `[start, end)`다.
원래 Evidence ID가 있으면 그 네임스페이스·source/version과 함께 보존한다.
`source_locator_ref`는 원문 기준 위치, start/end는 보존 발췌 기준 위치이며 서로 바꾸지 않는다.

검사는 `0 <= start < end <= len(text)` 및 `text[start:end] == exact_quote`다.
잘못된 surrogate·UTF-8 변환 오류는 진단하고 대체문자로 고치지 않는다.
분해형 한글·이모지·CRLF·공백·U+FEFF도 그대로 보존한다.
한 Claim에 여러 구간이 필요하면 모든 Evidence를 참조하고 전부 검사한다.
모델에 보낸 주변 문맥과 최종 인용 범위를 구분하며 문맥 누락이 있으면 의미 검증을 보류한다.

## 진술과 명시 Requirement

| 개념 | 주요 필드 | 검증·소비 의미 |
|---|---|---|
| ClaimCandidate | `candidate_ref`, `statement`, `kind`, `subject`, `predicate`, `object`, `scope`, `temporal_context`, `measurements[]`, `qualifiers[]`, `evidence_refs[]` | 후보에는 검증·권한 필드가 없다. kind는 DECLARATION / PLAN / OBSERVED_ACTION / REPORTED_RESULT / HIRING_REQUIREMENT. 해석 불가 유형은 후보 미완료로 남긴다. 출처의 계획·보고 결과를 실제 성취로 인증하지 않는다. |
| Measurement | `raw_expression`, `value_decimal: str|null`, `unit_raw`, `comparator`, `period`, `population_scope`, `comparison_basis`, `evidence_refs[]` | comparator는 원문 의미에 따라 EQ / GT / GTE / LT / LTE / RANGE / UNKNOWN. 무단 단위 환산·기간 합산 금지. 모든 정규화 값에 원문 대조 필요. |
| RequirementCandidate | ClaimCandidate 공통 정보 + `raw_requirement`, `necessity`, `necessity_basis`, `condition`, `skill_mentions[]` | necessity는 REQUIRED / PREFERRED / GENERAL. 불명확하면 GENERAL 및 `necessity_basis=UNSPECIFIED`를 남긴다. 사용자 자격 충족 결과 없음. |
| SkillMention | `raw_text`, `evidence_refs[]`, `canonical_ref: str|null`, `alias_basis: str|null`, `dictionary_version: str|null` | 명확한 동의어만 연결. 원문 약어 정의 또는 검토된 사전 근거가 필요. 기술 이름 유사도만으로 동의어를 추가하지 않는다. 신규 전역 사전 구축은 범위 밖. |
| StructuredClaim / StructuredRequirement | 후보 내용 + `derived_id`, `derivation_version`, `validation_refs[]`, `verification_status`, `usage_status`, `limitation_refs[]` | 코어만 최종 상태를 계산한다. W4의 사용 가능 목록에는 VERIFIED이고 현재 사용 허용인 결과만 포함한다. |

### ConditionExpression: 평면 노드로 표현한 트리

`root_node_id`와 `nodes[]`를 갖는다. 각 노드는 `node_id`, `operator`, `child_ids[]`,
`raw_expression`, `evidence_refs[]`, `atom`을 가진다. atom은 leaf에만 값이 있고 그 외는 null이다.

| operator | 자식 수 | 의미 |
|---|---|---|
| ATOM | 0 | 기술·활동·대상 등 원문 조건 하나. 새 요구를 추론하지 않음. |
| COMPARE | 0 | 비교 대상 원문, 연산자·값·단위·기간을 가진 조건. |
| AND / OR | 2 이상 | 원문의 동시 충족/대안 관계. |
| NOT | 1 | 해당 조건의 명시적 부정. |
| EXCEPT | 2 | 순서대로 기본 조건과 명시적 제외 조건. |
| SAME_EPISODE | 1 | 하위 조건들이 원문이 요구한 동일 경험에서 결합되어야 함. 실제 경험의 충족 판정은 하지 않음. |

전체 표현은 단일 루트의 연결된 비순환 트리여야 한다.
루트 외 노드는 부모가 정확히 하나여야 하며 참조 없는 노드·중복 node ID·역참조를 거부한다.
모든 노드의 근거가 요구 원문과 같은 적용 범위에 있는지 검사한다.
동일 경험 조건이 없는 원문에 SAME_EPISODE를 추가하지 않는다.

합성 AS-301의 의미 예시는
`SAME_EPISODE(AND(OR(AWS 사용, GCP 사용), 해당 서비스 운영 경험))`이다.
AS-306은 `EXCEPT(COMPARE(운영 경험, GTE, 3, 개월), 교육용 실습)`이다.
기술·수치는 해당 합성 원문의 예시이며 제품의 고정 요구가 아니다.

## 계보·동등성·충돌

| 개념 | 필드·상태 | 규칙 |
|---|---|---|
| OriginLink | `from_source_ref`, `to_source_ref`, `relation`, `basis_refs[]`, `verification_ref` | relation은 ORIGINAL / REPOST / QUOTES. 동일 원출처 확인 근거가 필요. W2 주장만으로 검증 완료 아님. 미상은 미상으로 보존. |
| ClaimRelation | `claim_refs[]`, `relation`, `compared_dimensions`, `basis_refs[]` | relation은 EQUIVALENT / DIFFERENT_SCOPE / POTENTIAL_CONFLICT / CONFIRMED_VALUE_CONFLICT / UNRESOLVED_ORIGIN. 충돌은 진술 간 관계이며 어느 쪽이 사실인지는 결정하지 않음. |
| OriginGroup | `origin_ref: str|null`, `source_refs[]`, `independence_status` | 알려진 재게시 5건은 한 계보. 미확인 계보를 독립 근거 수로 계산하지 않음. 문서 수·경로 수를 사실 신뢰 점수로 출력하지 않음. |
| VersionManifestEntry | `source_ref`, `text_artifact_id`, `representation_version`, `expected_digest` | 호출자가 제공한 기존 버전의 최소 검사 정보. DB 전체 상태를 읽었다는 의미가 아님. |

동등성은 유형·주체·술어·대상·범위·시점·수치·단위·비교 기준·부정·조건이 확인된 경우에만 성립한다.
미상끼리 같은 값이라고 병합하지 않는다. 병합 후에도 모든 Evidence와 원래 candidate 참조를 남긴다.
같은 버전·artifact에 다른 내용이 있으면 해당 키의 모든 입력을 충돌로 표시하고 첫 값/마지막 값을 선택하지 않는다.
다른 버전은 병존하며 최신 여부를 자동 추론하지 않는다.

## 검증·제한·출력

| 개념 | 주요 필드 | 의미 |
|---|---|---|
| ValidationCheck | `check_id`, `target_ref`, `kind`, `status`, `method`, `policy_version`, `input_versions[]`, `reason_codes[]`, `observation_origin`, `upstream_report_ref`, `reproduction_scope` | status: PASS / FAIL / PENDING / NOT_REQUIRED. observation_origin은 W3_CURRENT / UPSTREAM_REPORTED / TEST_DOUBLE. 수집 측 보고는 원래 판정과 함께 별도 보존하며 필요한 W3 검사 PASS로 자동 계산하지 않음. NOT_REQUIRED는 신뢰된 정책의 명시적 판정에만 사용. |
| VerificationPolicy | `policy_id`, `version`, `required_checks`, `additional_check_rules`, `decision_provenance` | 검사 항목·중요 정보 판단·추가 검증 주체를 정함. 자료 본문의 중요도·개인 승인 표시와 분리. 미정은 안전한 보류. |
| SourceReview | `input_item_id`, `source_ref`, `observations`, `examined_artifact_refs[]`, `coverage`, `intentional_omissions[]`, `pending_user_decisions[]`, `check_refs[]`, `limitation_refs[]`, `requirement_presence` | coverage는 COMPLETE_FOR_REQUESTED_SCOPE / PARTIAL / NONE / UNKNOWN. 필요 첨부가 미확인·실패면 전체 COMPLETE 불가. 추출 성공과 미보존 범위를 함께 보존. 미선택 제안은 selected=null로 유지하며 권한·사용자 승인으로 사용하지 않음. |
| RequirementPresence | `state`, `examined_scope`, `basis_refs[]` | FOUND / NONE_IN_EXAMINED_SCOPE / NOT_ASSESSED. 충분한 문맥의 정상 추출·검증으로 부재를 확인한 범위에만 NONE. 모델 실패나 미수집을 NONE으로 쓰지 않음. |
| Limitation | `code`, `affected_refs[]`, `affected_scope`, `impact`, `required_information[]`, `resolution_owner`, `next_action`, `retry_hint` | impact는 영향 범위별 BLOCKS_VERIFICATION / BLOCKS_PUBLIC_USE / INCOMPLETE_COVERAGE / PROCESSING_FAILURE. next_action은 데이터 보완·정책 확인·상위 진행 선택 등 안내이며 실행 명령이 아님. |
| KnowledgeBundle | `bundle_id`, `contract_version`, `evaluation_mode`, `purpose`, `input_version_refs[]`, `derivation_version`, `claims[]`, `requirements[]`, `evidence[]`, `validation_checks[]`, `source_reviews[]`, `claim_relations[]`, `origin_groups[]`, `limitations[]`, `processing_status` | 권한에 맞게 투영된 결과. validation_refs와 check_refs는 허용된 validation_checks 항목으로 해소되어야 함. processing_status는 COMPLETED / LIMITED / PAUSED / FAILED. COMPLETED도 세계의 사실 완전성·지원자격을 의미하지 않음. |
| StructureResponse | `request_id`, `bundle`, `review_details[]`, `errors[]` | W2 상세 진단과 W4 사용 근거의 투영 경계를 유지. bundle=null은 envelope·인증 오류처럼 처리를 시작할 수 없는 경우. 정상적인 제한 결과는 빈 사용 목록을 가진 bundle로 반환. |

### 상태 계산과 변경

1. INPUT_REVIEW: 식별·해시·위치·허용을 검사한다. 누락은 PENDING, 불일치는 FAIL이며 자료별 진단을 남긴다.
2. CANDIDATE: 충분하고 처리가 허용된 범위만 추출한다. 이 단계는 검증 완료가 아니다.
3. VALIDATION: 기계적 검사, 의미 성분 검사, 정책이 요구한 추가 검증을 기록한다.
   원래 위치의 기준 텍스트 또는 해시 대상이 없으면 그 검사는 PENDING이다.
   수집 측 보고와 현재 검사를 구분하며, 발췌 문자열 대조 성공으로 원문 위치/전체 무결성까지 통과시키지 않는다.
4. `verification_status`: 필수 검사 중 FAIL이면 FAILED, 그 외 미완료가 있으면 PENDING, 모두 충족하면 VERIFIED.
   상태 승격은 코어에서만 수행한다.
5. `usage_status`: 현재 허용 조건과 결과 상태를 모두 충족하면 USABLE, 허용 거부면 BLOCKED,
   확인 미완료면 RESTRICTED. 원문 부합 검증 기록과 독립적으로 남긴다.
6. RETURN: 현재 권한을 다시 확인한 뒤 audience에 맞게 투영한다. 이때 철회·삭제되면 해당 원문·결과를 제거한다.
   과거 처리 성공을 현재 허용으로 사용하지 않는다.
7. 429 등 전역 호출 제약은 PAUSED와 미처리 범위를 반환한다. 완성된 자료도 반환 시점 허용 검사를 거친다.

각 재처리는 새 불변 bundle이다. `derived_id`는 결과 종류, 근거 참조, 의미 성분, 파생 버전으로 만드는 결정적 키로 설계한다.
직렬화 규칙은 UTF-8, 키 정렬, 간결한 JSON, 십진 수치 문자열이며 원문은 변환하지 않는다.
동일한 검증 결과의 반복은 같은 키로 식별하고, 달라진 추출 결과·정책·모델·프롬프트 버전은 별도 파생 결과로 구분한다.
W1의 영구 저장·멱등 upsert·전체 삭제 전파는 이 모델로 구현 완료되지 않는다.

로컬 계약 검토의 수동 Claim/Requirement 예시는 review_details에만 연결하는 미확정 초안이다.
작성 방법을 MANUAL_REVIEW_DRAFT로 명시하고 근거·문맥·검사 부족을 기록한다.
자동 추출이나 운영 품질의 증거로 사용하지 않으며 공용 claims/requirements 목록으로 승격하지 않는다.
