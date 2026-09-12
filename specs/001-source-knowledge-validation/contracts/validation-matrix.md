# 요구·인수 시나리오 검증 매핑

**상태**: 구현 후 검증 매핑. 실행 결과와 미검증 인수조건은 [validation-results.md](../validation-results.md)를 기준으로 한다.
**기준**: [spec.md](../spec.md)의 기능 FR 21개·AS 36개·SC 9개.
**실행 방법**: [quickstart.md](../quickstart.md). 테스트 경로는 현재 구현에 존재하며, 표의 모든 AS가 자동화 완료되었다는 뜻은 아니다.
**작업 연결**: [tasks.md](../tasks.md)의 작업 ID를 각 AS에 연결한다. 완료 여부와 GATE는 tasks 및 검증 결과에서 함께 확인한다.

## 기능 요구와 설계·PRD

| 기능 요구 | 구현 설계 경계 | 원본 PRD 연결 |
|---|---|---|
| FR-001, FR-003, FR-006, FR-018, FR-021 | 입력 검토, TextArtifact/Evidence, 불변 버전·충돌 | PRD FR-09, FR-23, FR-32 |
| FR-002, FR-014, FR-015, FR-016 | SourceReview, Dependency, KnowledgeBundle·부분 실패 | PRD FR-13, FR-32 |
| FR-004, FR-007, FR-017, FR-019 | PolicyPort, 검증 단계, audience 투영·안전한 진단 | PRD FR-01, FR-11, FR-28, FR-30, FR-31 |
| FR-005, FR-010, FR-011, FR-012, FR-013 | 후보·의미 검증, 수치/범위/Origin·진술 관계 | PRD FR-10, FR-12, FR-16, FR-31 |
| FR-008, FR-009, FR-010 | Requirement 필수성·조건 트리·기술 표현 | PRD FR-16 |
| FR-007, FR-014, FR-020 | 검사/활용 상태 분리, 결과 계약, 평가 모드 | PRD FR-31, FR-32 |

기능 FR과 PRD FR은 별개 식별자다. 연결된 PRD 전체가 이번 기능에서 구현되는 것은 아니다.
AS·SC 상세 의미는 spec을 기준으로 하며 이 표는 별도 제품 요구를 추가하지 않는다.

## 인수 시나리오별 검증

각 테스트는 AS ID를 pytest case ID로 포함한다.
정상 기대 결과는 [합성 사례](synthetic-examples.md)와 추가 fixture의 사전 정답으로 검증한다.
테스트 대역의 추출 결과·검증 판정과 코어의 계산 결과를 구분한다.

| AS | 입력·검증 핵심 | 예상 테스트 경로 | 성공 기준 | 작업 연결 |
|---|---|---|---|---|
| AS-101 | 원문·버전·위치·해시·허용이 충분한 범위 식별 | tests/acceptance/test_input_review.py | SC-001 | T009, T012, T017 |
| AS-102 | 현재 샘플의 2 Source·9 구간·3 첨부 진단, 검증 Claim/Requirement 0 | tests/contract/test_collection_sample.py | SC-001, SC-003 | T010, T014, T017 |
| AS-103 | 버전 누락·다른 버전·위치 미확인 각각 검증 차단 | tests/acceptance/test_input_review.py | SC-001, SC-003 | T009, T012, T013, T017 |
| AS-104 | 기대 해시 미계산과 불일치를 각각 PENDING/FAIL로 구분 | tests/unit/test_evidence.py | SC-001, SC-003 | T011, T013, T017 |
| AS-105 | 성공·실패·미파싱 자료가 빠짐없이 별도 진단 | tests/acceptance/test_bundle.py | SC-001 | T009, T012, T016, T017 |
| AS-106 | 개인·비공개·허용 미확인 자료가 공용 결과·관계에 없음 | tests/acceptance/test_boundaries.py | SC-001, SC-007 | T011, T016, T034, T035, T039 |
| AS-107 | PM 제공 ID·Evidence·부모 참조 유지, 원문/버전 부재 일괄 진단 및 수집 측 PASS 승격 없음 | tests/contract/test_observed_package.py | SC-009 | T010, T015, T017 |
| AS-108 | 추출 성공의 범위·의도적 미보존·다운로드와 HTTP 기록 부재·OCR 상태 구분 | tests/acceptance/test_input_review.py | SC-009 | T009, T012, T015, T017 |
| AS-201 | 합성 PLAN의 주체·유효시점·원문 추적 및 기대 Claim 생성 | tests/acceptance/test_claims.py | SC-002 | T018, T021, T022, T023, T025 |
| AS-202 | 없는 숫자·기술·채용 조건·성과를 주입하면 검증 차단 | tests/acceptance/test_claims.py | SC-002 | T018, T021, T022, T025 |
| AS-203 | URL 불가와 허용된 보존 발췌를 구분, 전문 생성 없음 | tests/acceptance/test_claims.py | SC-006 | T018, T020, T023, T025 |
| AS-204 | 인용 위치는 맞지만 의미가 다른 후보·다른 버전 근거 모두 실패 | tests/acceptance/test_claims.py | SC-002 | T018, T020, T022, T025 |
| AS-205 | 필요한 추가 검증 미완료·정책 누락은 VERIFIED/USABLE 불가 | tests/acceptance/test_claims.py | SC-002 | T018, T022, T025 |
| AS-206 | 처리 전후 원문·버전 동일, 파생 버전 식별 가능 | tests/unit/test_versions.py | SC-008 | T018, T020, T025 |
| AS-207 | 원문 locator와 발췌 위치 분리, 발췌 자기 대조로 원래 위치 PASS 금지 | tests/unit/test_native_locators.py | SC-009 | T018, T020, T025 |
| AS-208 | 해시 대상별 보고·현재 재현 분리, 다른 대상 해시 대입·자기 비교 PASS 금지 | tests/acceptance/test_claims.py | SC-009 | T018, T020, T022, T025 |
| AS-301 | OR·운영 결합·동일 경험과 필수성 보존 | tests/acceptance/test_requirements.py | SC-002, SC-004 | T026, T028, T030, T032 |
| AS-302 | 우대 유지, 필수성 불명확은 GENERAL/UNSPECIFIED | tests/acceptance/test_requirements.py | SC-002, SC-004 | T026, T028, T032 |
| AS-303 | 명확한 약어 연결, Java와 JavaScript 분리 | tests/acceptance/test_requirements.py | SC-002, SC-004 | T026, T029, T032 |
| AS-304 | 공고·조직·직무 범위 확대 없음 | tests/acceptance/test_requirements.py | SC-002, SC-004 | T026, T028, T030, T032 |
| AS-305 | 본문 정상 결과와 필수 JD 미파싱 영향 동시 보존 | tests/acceptance/test_bundle.py | SC-002, SC-003, SC-004 | T033, T036, T039 |
| AS-306 | GTE 3개월·제외 조건·필수성 보존, 수치 공통정책화 없음 | tests/acceptance/test_requirements.py | SC-002, SC-004 | T026, T028, T030, T032 |
| AS-307 | 짧은 발췌의 문맥/범위 부족을 미확정 후보로 구분, 미보존 항목의 요구 없음 판정 금지 | tests/acceptance/test_requirements.py | SC-009 | T026, T028, T031, T032 |
| AS-401 | 사용 가능·검증 미완료·실패 및 자료 완전성을 별도 전달 | tests/acceptance/test_bundle.py | SC-001, SC-006 | T033, T035, T036, T039 |
| AS-402 | 핵심 의존 자료·보완 주체·영향·상위 진행 선택 전달, 자동 수집/재시도 없음 | tests/acceptance/test_bundle.py | SC-001, SC-006 | T033, T035, T036, T039 |
| AS-403 | 빈 입력·원문 부족·실패와 NONE_IN_EXAMINED_SCOPE 구분 | tests/acceptance/test_bundle.py | SC-003, SC-006 | T033, T031, T035, T039 |
| AS-404 | 악성 원문이 권한·도구·전송 목적지·검증 상태를 변경하지 못함 | tests/acceptance/test_boundaries.py | SC-007 | T034, T035, T036, T039 |
| AS-405 | 개인 승인·전략 해석을 공용 사실/승인으로 승격하지 않음 | tests/acceptance/test_boundaries.py | SC-007 | T034, T035, T039 |
| AS-406 | 금지 원문·테스트 비밀값·프롬프트/응답 전문의 결과·로그 유출 없음 | tests/acceptance/test_boundaries.py | SC-007 | T034, T035, T039 |
| AS-407 | 게시일/시간대 미상과 미선택 안내 유지, 자동 날짜 보충·OCR·진행 선택 없음 | tests/acceptance/test_bundle.py | SC-009 | T033, T035, T036, T039 |
| AS-501 | 확인된 재게시 5건의 참조를 보존하되 한 Origin으로 구분 | tests/acceptance/test_relations.py | SC-005 | T040, T042, T044, T045 |
| AS-502 | 기간·단위·범위·비교 기준 차이를 유지 | tests/acceptance/test_relations.py | SC-005 | T040, T042, T045 |
| AS-503 | 동일 지표 값 충돌에서 양쪽 근거 보존, 정답 임의 선택 없음 | tests/acceptance/test_relations.py | SC-005 | T040, T042, T045 |
| AS-504 | 다른 시기 자료를 대체/Drift로 확정하지 않고 미상 발행일 유지 | tests/acceptance/test_relations.py | SC-005 | T040, T042, T045 |
| AS-505 | 계보 미상 분리, 같은 ID·버전의 다른 내용 모두 충돌, 덮어쓰기 없음 | tests/acceptance/test_relations.py | SC-005, SC-008 | T040, T041, T043, T045 |
| AS-506 | 본문/첨부의 각 Evidence 유지, 연결 관계만으로 독립성·완전한 동등성·과거 조건 대체 확정 금지 | tests/acceptance/test_relations.py | SC-009 | T040, T042, T044, T045 |

## 구현 경계의 추가 검증

다음은 기존 FR을 구현하는 기술 경계 검사다. 새 제품 기능·운영 수치 목표가 아니다.

| 검사 | 기대 결과·관련 요구 |
|---|---|
| Unicode·개행·해시 | 분해형 한글·이모지·CRLF·U+FEFF에서 코드포인트 위치와 UTF-8 digest 재현. 잘못된 surrogate 진단. FR-003, FR-006. |
| 조건 트리 무결성 | 알 수 없는 자식 ID, 사이클, 고립 노드, 연산자별 인자 수 오류 거부. FR-009. |
| 호출 간 반복·manifest 충돌 | 동일 파생 내용은 같은 derived_id, ID 충돌은 덮어쓰기 없음. 검사한 manifest 범위를 명시. FR-012, FR-018. |
| 권한 경계·철회·삭제 참조 | 읽기 전/외부 전송 전/반환 전 거부, 재호출로 삭제 참조가 재노출되지 않음. mock PolicyPort 범위의 검증. FR-004, FR-019. |
| 투영의 참조 폐쇄성 | W4 결과에 숨긴 자료의 관계·ID·집계가 남지 않음. FR-019. |
| 평가 모드·목적 누출 | 운영 소비자는 SYNTHETIC_TEST·PROVIDER_MOCK·CONTRACT_SAMPLE_DIAGNOSTIC 및 LIVE인 PROVIDER_POC bundle도 거부. FR-020. |
| HTTP Mock 오류 | 잘린 응답·잘못된 JSON/enum·도구 호출·429·코드 없는 오류를 성공으로 보수하지 않음. FR-007, FR-014, FR-015. |
| 429 호출 횟수 | SDK 자동 재시도 0, 429 이후 새 공급자 호출 0, 완료 결과·미처리 범위 유지. 헌법 VII, FR-015. |
| 계약 호환 | 미지원 버전은 명시 거부, 샘플 어댑터와 일반 계약 버전 분리. FR-001, FR-014. |
| PM 참조 해소 | 허용된 두 JSON 내 상대 참조·Pointer만 해소. 경로 탈출·다른 파일·URL·부모 버전 불일치·중복 ID 진단. FR-001, FR-017, FR-018. |
| 검사 출처와 범위 | UPSTREAM_REPORTED/W3_CURRENT/TEST_DOUBLE 및 원래 위치/발췌 위치 구분. 전달 범위 밖 hash·파서 보고를 현재 PASS로 계산하지 않음. FR-003, FR-006, FR-007. |
| 안전한 오류 기록 | Pydantic/SDK 오류의 입력값·본문·키가 로그·traceback에 출력되지 않음. FR-019. |

## 실제 PoC 및 후속 검증의 경계

새 PM 입력의 구조 회귀는 가상의 ID·원문·해시를 가진 합성 대응 fixture로 기본 실행한다.
실제 두 JSON은 별도 로컬 opt-in 검사이며 [PM 어댑터](observed-package-adapter.md)의 건수·참조·진단 기대값을 확인한다.
실제 패키지를 공개 fixture로 복사하지 않고, 파일 부재로 로컬 검사를 생략하면 미검증으로 보고한다.
PM 검증기의 기존 PASS·현재 문서 참조 검사·W3 애플리케이션 검증을 서로 대체하지 않는다.

`tests/provider/test_solar_live.py`는 키와 명시적 live 실행, 필요한 GATE 조건이 있을 때만 실제 API를 호출한다.
같은 합성 정답집을 사용하여 추출과 의미 검증의 정상·변형 사례를 확인한다.
HTTP Mock 통과와 실제 모델 결과를 별도 보고하고 미실행은 SKIPPED/미검증으로 기록한다.
계정 모델 접근·반환 ID, 프로젝트 스키마 수용, 한국어 조건·수치·부정 보존, 오류/중단 동작이 G-02의 증거다.
사례별 실패를 남기고 임의 정확도 평균으로 통과시키지 않는다.

W1의 실제 권한·보존 서비스 연동은 G-03 증거가 필요하다.
Graph/Vector·Cache·Job·Checkpoint·백업 복원 전 경로 삭제, 최종 경험 매칭·선택 저장은 후속 기능의 검증이다.
이번 mock 경계 테스트만으로 해당 통합 검증이나 공개 배포 GATE가 완료되지 않는다.
