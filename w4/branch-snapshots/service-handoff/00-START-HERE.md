# W4 팀 전달본 사용 안내

새 서비스 계약·연결 코드·가상 통합 테스트를 포함한 실행 가능한 전달본입니다.

**추가 검토 반영:** [CI 설치·실행·산출물 안내](docs/w4-service-ci.md)와 [D-05 공동 합의 요청서](docs/w4-d05-contract-decisions.md)를 추가했습니다.
CI는 전체 테스트와 두 HTTP 시연을 자동 실행하도록 구성했습니다. 실제 GitHub 실행은 Actions 기록에서 별도로 확인해야 합니다.

1. [피드백 반영 결과](docs/w4-handoff-review-response-2026-09-09.md)를 먼저 읽습니다.
2. [실행 방법과 백엔드 계약](docs/w4-service-handoff.md)으로 설치·테스트·가상 시연을 재현합니다.
3. [공개 요청](samples/service-handoff/request.json), [서버 입력](samples/service-handoff/server-context.synthetic.json), [추천 결과](samples/service-handoff/results/synthetic-company.json)를 비교합니다.
4. [기업 근거 부족 결과](samples/service-handoff/results/diagnostic-company.json)에서 원본 SK하이닉스 샘플의 제한을 확인합니다.

## 검증과 범위

이전 서비스 전달본은 전체 249개 테스트를 통과했습니다. 별도 폴더의 새 Python 3.13.12 환경에서도 설치·249개 테스트·두 가상 HTTP 시연을 재현한 기록은 VERIFICATION.json에 보존했습니다.
현재 테스트 수와 실행 환경은 CI/로컬 실행의 `summary.json` 및 `tests.json`을 확인합니다. CI 실패·누락 방지 검사 7개를 추가했습니다.
새 서비스 시연의 모델은 simulated-* 고정 응답입니다. 실제 모델 성능 보고서는 [이전 v4 보고서](docs/w4-review-v4-2026-09-09.md)입니다.
이번 기업 연결 프롬프트의 실제 LLM 의미 정확도는 새로 측정하지 않았습니다. 실제 팀 DB/인증·W3 계약 승인·정렬 정책 승인·운영 모델 확정은 남아 있습니다.

## Git 커밋으로 가져오기

압축을 푼 파일만으로 실행할 수 있습니다. 함께 들어 있는 repository.bundle로 검증한 로컬 커밋을 가져올 수도 있습니다.

```powershell
git clone --branch feat/w4-service-handoff repository.bundle epick-w4-review
```

커밋 ID와 파일 무결성은 REVISION.json과 FILES.sha256.json에 기록됩니다. GitHub 게시나 PR 생성은 하지 않았습니다.
기존 프로젝트 폴더 위에 일괄 덮어쓰기 전에 별도 폴더에서 검토하는 용도입니다.

## 구현 파일

| 파일 | 기능 |
|---|---|
| [__init__.py](epick_w4/__init__.py) | 패키지 공개 진입 함수 |
| [__main__.py](epick_w4/__main__.py) | 명령행 실행 진입점 |
| [api.py](epick_w4/api.py) | HTTP 요청 검사·안전한 오류 응답·서비스 router 등록 |
| [candidate_retriever.py](epick_w4/candidate_retriever.py) | 입력 경험 목록에서 후보 구성 |
| [candidate_validator.py](epick_w4/candidate_validator.py) | 소유권·버전·경험 경계·원문 근거의 정합성 검사 |
| [company_context.py](epick_w4/company_context.py) | 검증·사용 가능 기업 진술/조건과 출처 수용, 진단 샘플 거부 |
| [company_detail.py](epick_w4/company_detail.py) | 기업 근거를 포함한 판단 프롬프트와 실제 사용 근거 검사 |
| [contracts.py](epick_w4/contracts.py) | 기존 일반 추천 입력 계약 및 공통 입력 검증 |
| [detail_contract.py](epick_w4/detail_contract.py) | 기존 문항 5개·점검 10개 초안과 상세 판단 규칙 |
| [detailed_recommendation.py](epick_w4/detailed_recommendation.py) | 원문 추출부터 문항 상세 판단·순위까지 연결 |
| [evaluate.py](epick_w4/evaluate.py) | 기존 추천 결과 평가 도구 |
| [evaluation_diagnostics.py](epick_w4/evaluation_diagnostics.py) | 원문·문맥·의미 정확도와 위험 참조 진단 |
| [evidence_extraction.py](epick_w4/evidence_extraction.py) | 원문을 구간으로 나누고 모델 분류를 정확한 인용·위치와 연결 |
| [extraction_eval.py](epick_w4/extraction_eval.py) | 경험 원문 근거 추출 평가 |
| [handoff_contract.py](epick_w4/handoff_contract.py) | 공개 요청·서버 입력·기업 묶음·결과 검증 모델 및 JSON Schema 기준 |
| [knowledge_gate.py](epick_w4/knowledge_gate.py) | 기존 일반 추천의 기업 근거 검증 게이트. 새 투영의 USABLE과 별도 |
| [llm_contract.py](epick_w4/llm_contract.py) | 공급자 공통 호출 인터페이스, 안전한 오류, 엄격한 JSON 파서 |
| [llm_prompts.py](epick_w4/llm_prompts.py) | 일반 문항 분석·경험 판단·근거 추출 프롬프트 |
| [local_client.py](epick_w4/local_client.py) | 로컬 llama.cpp 호출. 서비스는 capture_traces=False로 사용 |
| [model_eval.py](epick_w4/model_eval.py) | 문항·매칭 모델 비교 평가 |
| [model_selection.py](epick_w4/model_selection.py) | 평가 결과와 필수 검사에 따른 모델 선택/보류 |
| [output_schemas.py](epick_w4/output_schemas.py) | 모델 응답의 구조와 허용 ID를 제한 |
| [pipeline.py](epick_w4/pipeline.py) | 기존 일반 문항 추천 흐름 |
| [question_analyzer.py](epick_w4/question_analyzer.py) | 기존 규칙 기반 문항 기준 추출 |
| [recommendation_composer.py](epick_w4/recommendation_composer.py) | 기존 일반 추천의 상태·이유·순위 구성 |
| [semantic_matching.py](epick_w4/semantic_matching.py) | 일반 문항·경험의 LLM 판단과 근거 검사 |
| [service_adapter.py](epick_w4/service_adapter.py) | 서버 데이터 조회, 리비전 변화 감지, 모델 호출 전·반환 전 권한/정책 재검사 |
| [solar_client.py](epick_w4/solar_client.py) | Upstage HTTP 호출 연결. 이번 전달 검증에서는 실제 호출하지 않음 |
| [stage_eval.py](epick_w4/stage_eval.py) | 단계별 평가 항목·선정 조건 |
| [synthetic_policy.py](epick_w4/synthetic_policy.py) | 등록된 가상 내용만 실제 모델 전송에 허용 |

## 입력·출력·스키마

| 파일 또는 폴더 | 기능 |
|---|---|
| samples/service-handoff/request.json | 외부 클라이언트 요청. owner·경험 원문·회사 묶음·모델 설정을 받지 않음 |
| samples/service-handoff/server-context.synthetic.json | 가상 백엔드가 주입하는 Snapshot과 가상 기업 근거 |
| samples/service-handoff/server-context.diagnostic.json | 원본 기업 샘플을 근거 사용 거부 상태로 투영한 서버 입력 |
| samples/service-handoff/extraction-responses.simulated.json | 가상 추출 모델의 고정 응답 |
| samples/service-handoff/results/synthetic-company.json | 가상 기업 근거와 경험의 연결 결과 |
| samples/service-handoff/results/diagnostic-company.json | 기업 근거 사용 없이 자료 제한을 보존한 결과 |
| schemas/w4-service-input.schema.json | 공개 HTTP 요청 규격 |
| schemas/w4-server-context.schema.json | 인증 사용자 범위의 서버 내부 입력 규격 |
| schemas/w4-knowledge-bundle.schema.json | W3 결과의 로컬 소비 투영 초안 규격 |
| schemas/w4-detailed-output.schema.json | 근거 ID·제한·processing_status를 포함한 결과 규격 |
| epick_w4/*allowlist.json | 실제 호출에 허용하는 고정 가상 내용의 해시 |
| epick_w4/criteria_catalog.json | 공식 확정 전 문항·내용 점검 초안 |

## 실행·테스트·평가 자료

| 파일 또는 폴더 | 기능 |
|---|---|
| examples/w4_service_demo.py | 가상 조회·정책 서버와 고정 응답 모델. 실제 운영 인증/모델이 아님 |
| .github/workflows/w4-service-ci.yml | 잠금 설치 후 Windows/Linux 테스트·시연 실행, 성공·실패 산출물 14일 보관 |
| scripts/verify_service_handoff.py | 전체 테스트·두 시연의 결과/환경/해시 기록. 실패·skip·발견 누락 시 실패 종료 |
| tests/test_ci_verification.py | 테스트 누락·import 실패·skip·이전 성공 기록 재사용을 탐지하는 검사 |
| scripts/demo-service-handoff.py | 프로세스 내부 HTTP 시연·스키마·기존 결과 비교. --output-dir로 저장 위치 지정 |
| scripts/prepare-service-handoff.py | 이미 포함된 고정 가상 샘플·스키마 재생성 도구 |
| tests/test_handoff_service.py | 신규 계약·기업 근거·권한·정책·삭제·원문 유출 검사 31개 |
| tests/test_local_evaluation.py | HTTP 모의 통신과 서비스의 전문 미보존 검사 포함 |
| tests/의 다른 파일 | 기존 추출·판단·정렬·평가·API 회귀 검사 |
| samples/evaluation/, samples/extraction/ | 기존 가상 모델 평가 입력과 정답 초안 |
| output/local-ab-v4-20260909/ | 이전 실제 모델 비교의 집계·축별 성적·참조 검토 목록·감사 요약 |
| output/staged-demo-v4-20260909-r2/ | 이전 실제 모델 전문성 시연의 입력과 결과 |
| output/review-v4-20260909/verification-summary.json | 이전 v4 검증 기록. 이번 249개 테스트와 구분 |
| samples/upstream/, samples/verification/ | 원본 기업 전달 샘플과 별도 로컬 출처 검토 기록 |
| docs/ | 최신 연결 계약과 기존 구현·평가 기록 |
| scripts/의 다른 파일 | 기존 평가·시연·감사 도구. 실제 모델 실행에는 별도 모델/환경 준비 필요 |
| pyproject.toml, uv.lock | 패키지 의존성과 설치 버전 잠금 |
| VERIFICATION.json | CI 추가 전 249개 테스트의 원본/새 환경 검증 기록과 미완료 항목 |

과거 평가 보고서의 상세 원본 추론 로그·모델 가중치·소스 캡처·PDF 전체는 이 묶음에 포함하지 않았습니다.
해당 과거 문서에서 이 파일들을 가리키는 링크는 전체 작업 폴더용입니다. 이번 API 통합 테스트와 가상 시연은 포함 파일만으로 재현됩니다.
