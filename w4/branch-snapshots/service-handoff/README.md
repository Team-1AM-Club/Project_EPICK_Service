# Project_EPICK_Engine
Agent Engine Repository for Project EPICK

**서비스 재현 CI 추가:** 잠금 의존성 설치, 전체 테스트와 두 가상 HTTP 시연, 성공·실패 산출물 보관을 구성했다.
[CI 실행·결과 확인](docs/w4-service-ci.md) · [D-05 공동 합의 요청](docs/w4-d05-contract-decisions.md).
GitHub 실행 여부는 Actions 기록으로 확인하며, 로컬 통과를 원격 CI 통과로 표시하지 않는다.

**2026-09-09 팀 피드백 반영:** 서버 조회·정책 재검사, W3 기업 근거 연결, 입출력 JSON Schema와 가상 HTTP 통합 테스트를 추가했다.
새 연결은 `create_service_router`를 사용한다. [실행·계약·파일 안내](docs/w4-service-handoff.md).
실제 팀 인증·DB 연결, W3 계약 승인, 정렬 정책 승인과 운영 모델 선정은 남아 있다.

아래 v4 성능 수치와 216개 테스트는 이번 연결부 보완 **이전의 평가 기록**이다.

**2026-09-09 v4: 채점기 분리·새 사례 336회 비교·두 모델 조합·문항 5개/상세 점검 10개 연결을 완료했다.**
Gemma 12B B의 추출은 83.33%, 경험 판단 최고점은 Qwen 14B A와 Gemma 26B B의 70.83% 동점이다.
문항·매칭 종합 최고점은 Gemma 12B B의 70.83점이지만, 모든 후보가 필수 검사에서 실패해 운영 선정은 보류했다.
실제 조합 시연 52회와 보완 판단 35회를 별도로 확인했고 테스트 216개가 통과했다.
[최신 결과·실행 안내](docs/w4-review-v4-2026-09-09.md) · [백엔드 연결 계약](docs/w4-backend-integration.md) · [이전 v3 결과](docs/w4-review-v3-2026-09-09.md).
`POST /w4/recommend-from-raw`는 서로 다른 추출·판단 모델로 문항별 상세 점검을 적용한다.
팀 서비스 `feat/BE`는 빈 구조 파일만 있어 실제 인증·DB·검색 연동은 남아 있다.
기존 규칙·모의 출력과의 구분은 [데모 상태](docs/w4-demo-status.md)에 정리했다.

## W4 첫 구현: 문항별 경험 매칭·추천

문항과 사용자 경험을 입력하면 네 모듈을 거쳐 근거가 연결된 추천 JSON을 반환한다.
Python 3.10 이상으로 실행한다. 공통 계약 검증에는 Pydantic을 사용하며, 기본 규칙 모드에는 API 키·DB 연결이 필요 없다.
LLM 비교 평가의 최고 점수 모델을 선택하는 기능과 FastAPI 연결부를 구현했다.
실제 전송 어댑터는 Upstage Solar와 로컬 llama.cpp용 LocalClient다. 로컬 4개 모델은 예비 비교했고,
상용 API 비교·최종 모델 선정과 Graph·Vector 검색 연결은 아직 완료하지 않았다.

| 모듈 | 구현 | 반환 |
|---|---|---|
| [Question Analyzer](epick_w4/question_analyzer.py) | 문항에서 지원하는 평가 기준·역할/행동/결과 요구·글자 수 추출 | 문항 원문 위치가 붙은 판단 기준 |
| [Candidate Retriever](epick_w4/candidate_retriever.py) | 사용자 소유권·제외·스냅샷 버전 확인 후 경험 목록 검색 | 관련 후보 전체 |
| [Candidate Validator](epick_w4/candidate_validator.py) | 에피소드·버전·발췌 위치·기술/수치 변조·본인 행동 확인 | 허용할 원문 근거와 확인할 사항 |
| [Recommendation Composer](epick_w4/recommendation_composer.py) | 문항 부합을 우선해 후보 정렬, 기업 맥락을 보조 연결 | 상태·이유·강점·한계·비교 정보 |

전체 연결 함수는 `epick_w4.recommend(payload, user_id=authenticated_user_id)`다.
`user_id`는 백엔드 인증 결과에서 주입해야 한다. 요청 본문에서 받은 값을 인증 정보로 간주하면 안 된다.

## LLM 및 API 연결

`recommend(payload, user_id=..., llm=client)`로 JSON 응답 어댑터를 주입할 수 있다.
문항 기준 해석과 경험의 의미 매칭을 분리하고, 문항 분석 1회 뒤 경험마다 따로 호출한다.
모델이 반환한 근거 ID·버전·상태를 코드로 검사하고 최종 순위를 정한다.
`SolarClient`는 Upstage API, `LocalClient`는 로컬 llama.cpp 통신을 담당한다.
모의 어댑터는 테스트와 명시적인 모의 데모에 사용한다.
`samples/w4_semantic.json`은 단어 규칙으로 찾기 어려운 가상 문항과 경험을 담고 있다.
최종 호출 모델은 `select_evaluated_client(...)`가 비교 평가의 단독 1위로 선택한다. Solar를 고정하지 않으며,
호스트는 다른 공급자의 어댑터도 등록할 수 있다. 평가 미완료·동점·1위 어댑터 부재 시 임의로 대체하지 않는다.

- LLM/API 연결 당시 **82개 테스트 통과**: 기존 30개, 의미 판단 24개, Solar 통신 17개, API 11개.
- 테스트는 모의 모델 응답과 프로세스 내부 HTTP 요청을 사용한다. 실제 Solar 응답 품질을 입증하지 않는다.
- `--engine evaluated`는 평가 결과로 모델을 선택한다. `--engine solar`는 Solar 단독 통신 점검용이다. 외부 호출에는 해당 모델의 키와 비용이 필요하다.
- `SYNTHETIC` 표시와 함께 저장소의 가상 샘플 내용 해시를 대조한다. 실제·미등록 데이터는 전송하지 않는다.
- `create_router(authenticate=..., client_factory=make_selected_client)`를 팀 FastAPI 앱에 등록하면
  `POST /w4/recommend`로 같은 JSON을 받아 추천한다. 인증 함수는 팀 백엔드에서 제공해야 한다.

평가 기반 `make_selected_client`와 API 등록은 [모델 평가·선택 안내](docs/w4-model-evaluation.md#최고-점수-모델을-실제-호출에-연결),
Solar 개별 통신과 키 설정은 [LLM 연결 안내](docs/w4-llm.md),
수정한 문제와 남은 검증은 [코드 리뷰](docs/w4-llm-review.md)를 참조한다.

## 경험 원문에서 근거 추출

`extract_evidence(payload, user_id=..., llm=client)`와 `POST /w4/extract-evidence`를 추가했다.
LLM이 원문 구간의 종류·주체·서술 상태를 분류하고, 코드는 원문 발췌와 위치를 연결한다.
역할·행동·결과 외의 목표·기간·난관·소회·상황은 별도 근거로 보존한다. 누락 항목은 확인 질문으로 반환한다.

```powershell
python scripts/demo-evidence-extraction.py --output output/evidence-extraction-demo.json
```

키 없이 실행하는 가상 원문 4개·모의 응답 데모다. 실제 Solar 호출을 확인한 결과가 아니다.
추출 및 HTTP 테스트 34개를 추가한 당시 **147개 테스트 통과**를 확인했다.
[추출 계약·실행법·검토 결과](docs/w4-evidence-extraction.md)를 참조한다.
실제 추출 결과를 문항 분석·경험별 판단·최종 추천으로 연결한 시연은 [최신 결과](docs/w4-review-v3-2026-09-09.md#팀원이-볼-실제-시연)에 있다.
SK하이닉스 문항별 상세 기준 초안의 자동 적용은 별도 후속 작업이다.

## 모델 비교 평가

문항 해석과 경험 매칭을 사람이 승인한 정답으로 채점하는 평가기를 추가했다.
두 축 점수와 승인된 가중치의 종합 점수, 동점 후보와 사례별 오류를 출력한다.
기본 경로는 정답·정책 승인이 필요하다. 명시적인 예비 평가 경로는 실제 응답을 초안과 비교하되 모델 선정을 보류한다.
모의 시연은 실제 성능 결과로 취급하지 않는다. 현재 로컬 실행은 [v3 실제 비교](docs/w4-review-v3-2026-09-09.md)를 참조한다.

- [v3 평가·실행 안내](docs/w4-review-v3-2026-09-09.md): 문항 24개, 경험별 판단 44개, 원문 12개·53구간을 설정·모델별 각 1회 측정. 정답·정책은 초안.
- [v2 평가·실행 안내](docs/w4-evaluation-v2.md)는 이전 문항 16개·판단 28개·원문 33구간의 기록이다.
- [이전 정답·가중치 검토안](docs/w4-model-evaluation-review.md)과 [기존 CLI 안내](docs/w4-model-evaluation.md)는 v1 기록이다.
- v2 구현은 `epick_w4/stage_eval.py`, 추출 별도 평가는 `epick_w4/extraction_eval.py`다. 기존 평가기는 호환 경로로 유지한다.
- 평가기 테스트 31개를 추가했고, 2026-09-08 노트북의 별도 환경에서 API 포함 **전체 113개 테스트 통과**를 확인했다.
- 오류 검토 후 현재 버전은 **전체 197개 테스트 통과**다. 이 수치는 실제 모델의 정답률과 별개다.

```powershell
python scripts/demo-model-evaluation.py --output-dir output/evaluation-demo
```

위 명령은 외부 모델을 호출하지 않는 계산 시연이다. 시연 점수는 실제 LLM 성능이 아니다.

## Windows에서 실행

이 저장소 폴더에서 아래 명령을 실행한다. 프로젝트 `.venv`, Codex에 포함된 Python,
설치된 Python 순서로 찾는다. `-PythonPath`로 실행 파일을 직접 지정할 수도 있다.

```powershell
powershell.exe -NoProfile -File .\scripts\run-demo.ps1
```

결과는 `output/collaboration-rules-시간.json`에 저장되고 파일 경로가 출력된다.
협업·문제 해결 문항에서 다음 결과를 기대한다.

| 후보 | 상태 | 이유 |
|---|---|---|
| `episode-team-api` | `DIRECT_MATCH` | 본인의 조율·오류 해결 행동 및 결과 원문 존재 |
| `episode-solo-debug` | `PARTIAL_MATCH` | 문제 해결 근거는 있으나 협업 행동 근거 부족 |
| `episode-team-unclear` | `NEEDS_CONFIRMATION` | 팀 성과와 구분되는 본인 역할·행동 확인 필요 |

학습 문항, 원본 진단 샘플, 전체 테스트는 각각 다음과 같이 실행한다.

```powershell
powershell.exe -NoProfile -File .\scripts\run-demo.ps1 -Scenario learning
powershell.exe -NoProfile -File .\scripts\run-demo.ps1 -Scenario diagnostic
powershell.exe -NoProfile -File .\scripts\run-demo.ps1 -Scenario tests
```

API 테스트까지 실행하려면 먼저 `uv sync --locked --extra api --extra test`로 의존성을 설치한다.
API 의존성이 없는 Python에서는 API 테스트가 생략되므로 전체 검증에는 위 의존성이 필요하다.

학습 문항은 `episode-learning`을 첫 후보로 반환한다. `diagnostic`은 기업 입력만 원본 SK하이닉스
샘플로 교체한다. 기업 진술은 사용하지 않고, 요청에 명시된 `OPTIONAL` 정책에 따라 가상 문항·경험으로만 추천한다.
기업 근거를 `REQUIRED`로 지정한 요청에서는 근거가 없을 때 `NEEDS_INPUT`과 빈 후보 목록을 반환한다.

## Python으로 직접 실행

```shell
python -m epick_w4 --input samples/w4_collaboration.json --user-id user-demo
python -m epick_w4 --input samples/w4_learning.json --user-id user-demo
python -m unittest discover -s tests -v
```

`--output`을 생략하면 JSON을 stdout으로 출력한다. 파일 저장 시에는 기존 파일을 덮어쓰지 않는다.
원문이 포함된 추천 결과는 사용자 데이터이므로 운영 로그·Analytics에 적재하면 안 된다.

## 데이터와 구현 전제

- `samples/upstream/`에는 제공받은 원본 수집 JSON·전달 JSON·설명 문서를 수정 없이 보존했다.
  해당 문서의 다른 프로젝트 문서 링크는 원래 작성 환경을 기준으로 한다.
- `samples/w4_collaboration.json`, `samples/w4_learning.json`은 **모두 가상 데이터**다.
  기업의 `VERIFIED` 값은 **W3가 검증을 마쳤다는 개발 전제**를 표현한다. 실제 검증을 실행한 기록이 아니며,
  SK하이닉스의 실제 평가 정책 또는 지원자의 실제 경험으로 사용할 수 없다.
- 원본의 `PENDING`, `RESTRICTED`, `production_usable=false`는 유지한다. 원본 진단 파일을 정상 기업 입력으로 승격하지 않는다.
- W4의 기업 입력 검사는 상태·근거 연결·범위·시점 검사다. 웹 원문을 다시 수집하거나 W3의 의미 검증을 수행하지 않는다.
- 지원 자격은 `NOT_ASSESSED`로 반환한다. GCP 경험이 관련 후보여도 AWS 필수요건을 충족했다는 판단은 하지 않는다.
- 기본 규칙 모드의 문항 분석은 협업, 문제 해결, 도전, 리더십, 학습, 책임감에 관한 제한된 규칙이다.
  지원 동기 등 지원하지 않는 문항과 부정·제외 조건이 있는 문항은 확인을 요청한다.
- 경험 근거는 Python 문자열 기준 `[start:end]`와 진술이 정확히 같아야 한다.
  규칙 모드의 본인 역할·행동은 명시적 일인칭으로 시작하는 발췌만 인정한다.
  어댑터 경로에서는 문맥 판단 결과를 받으며, 코드가 원문 위치와 근거 참조를 다시 검사한다.
  어느 쪽도 사용자가 실제 수행했다는 사실을 독립적으로 입증하지 않는다.

## 검증과 다음 연결 지점

테스트는 문항 변경, 세 후보 상태, 타 사용자 데이터 차단, 제외·버전 불일치, 원문 참조,
기술·수치 변조, 경험 경계, 부정 표현, 기업 근거 누락·만료·범위 오류, 날짜 미상 선택,
진단 샘플 차단, 실제 CLI 실행을 확인한다.

입출력 형식과 모듈 연결 규칙은 [W4 계약 초안](docs/w4-contract.md)에 정리했다.
이 형식과 오류 코드는 로컬 프로토타입용이며 팀의 확정 API 계약이 아니다.

현재 단계는 [작업 순서](docs/w4-workflow.md), 미확인 서식 조건을 강제하지 않는 문항별 기준은
[경험 선택 기준 초안](docs/w4-question-criteria.md)을 참조한다. 이 기준은 설계 자료이며 현재 추천기에 자동 로드되지 않는다.

다음 통합에서는 백엔드가 인증·프로젝트·Snapshot을 제공하고, 경험 목록 공급을 Graph·Vector 검색으로 교체한다.
LLM 문항 해석·의미 매칭·추출의 실제 예비 비교를 완료했다. 정답의 사람 검토, 관측된 오류 개선과 새 사례 재평가가 남아 있다.
