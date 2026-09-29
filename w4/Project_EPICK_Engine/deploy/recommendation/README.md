# W4 추천 런타임 정본·빌드 기준

## 저장소가 정본

이 브랜치의 `Project_EPICK_Engine` 전체 Git 커밋이 추천 런타임의 정본이다. 수령자는 전달 회신의 **full commit SHA**를 checkout한다. 별도 r5 ZIP이나 Service checkout을 빌드 입력으로 섞지 않는다.

Engine `9121219e06c83a29251ee60c6c48849bd902c4c7`에는 `epick_w4/w1_runtime.py`와 이 Dockerfile이 없었다. 해당 커밋의 CI는 Question Core 컨테이너와 당시 W4 코드의 검증이며 추천 워커 이미지 검증이 아니었다.

이 변경은 그 커밋을 부모로 하며, Service `a7e70cbac366b0128d559773ed9ebf240ac96ba6`의 `w4/EPICK_W4_HTTP_Runtime_2026-09-20_r5/`에서 W1 통합 코드를 가져온다. 경로에 r5가 있지만, 그 디렉터리는 W1이 추가한 통합 코드를 포함하므로 최초 W4 r5 배포 ZIP과 동일하다고 간주하지 않는다. 원본 파일 해시 및 테스트/빌드 변경 내역은 [upstream-source.json](upstream-source.json)에 있다.

과거 T104 기록의 Engine `df41433218918e4167784243dc9b88e5a858278d` + Service integration `e36f2ac6ff9e83c3a9c9c0c2672455f9cfbe7ad6` + image digest는 해당 과거 실행의 출처다. 이번 커밋·빌드 결과로 그 기록을 소급 변경하지 않는다.

## 포함된 실행 경로

- `epick_w4/w1_runtime.py`: SQS 참조 메시지를 받아 `W1ExecutionAdapter` 실행.
- `epick_w4/w1_http_run_store.py`: W1 private HTTP의 acquire/context/authorize/publish/fail 연결.
- `epick_w4/w1_bridge.py`: W1 통합의 합성 모델 명시적 opt-in/한계 표시 패치를 함께 포함. 두 실행 파일만 옮기면 기존 실제 추론 전용 검사와 충돌하므로 이 패치도 필요하다.
- `epick_w4/acceptance_bootstrap.py`: 명시적으로 허용된 가상 검증용 고정 모델과 C-01 검사 연결.
- `uv.lock`: 전체 Python 의존성 잠금.
- `deploy/recommendation/requirements.lock`: 같은 uv.lock에서 transport 의존성을 export한 정확한 버전·해시 목록. Docker는 `pip --require-hashes`로 설치.
- `deploy/recommendation/Dockerfile`: digest로 고정한 Python 3.12.14 이미지, non-root 10001:10001, `python -m epick_w4.w1_runtime` 실행.

현재 bootstrap은 `fixed-synthetic-v1`, `simulated=True`다. 실제 Gemma, GPU 드라이버/llama-server/GGUF, REAL 데이터 승인, AWS 공동 실행을 포함하지 않는다. 실제 모델 전환은 이 정본을 기반으로 별도 구현·검증한다. 기존 SQS/RunStore 입출력 계약은 변경하지 않았다.

## Clean clone 및 의존성 설치

Linux 또는 Git Bash에서 실행한다. `<전달받은_full_SHA>`는 회신의 40자리 커밋으로 교체한다.

```bash
git clone https://github.com/Team-1AM-Club/Project_EPICK_Engine.git epick-w4-recommendation
cd epick-w4-recommendation
git checkout --detach <전달받은_full_SHA>
git rev-parse HEAD
git status --porcelain
uv sync --locked --extra api --extra test
```

CI의 uv는 0.12.0으로 고정한다. `git status`가 빈 상태에서 검사한다. 기존 `.venv`나 r5 압축 파일을 복사할 필요가 없다.

## 추천·C-01 검증

```bash
uv run --locked --extra api --extra test python -m unittest discover -s tests -p 'test_w1*.py' -v
uv run --locked --extra api --extra test python -m unittest discover -s tests -p 'test_c01*.py' -v
uv run --locked --extra api --extra test python scripts/verify_service_handoff.py --output-dir output/recommendation-verification
```

마지막 명령은 전체 unittest와 가상 HTTP 데모 2개를 실행한다. 출력 디렉터리가 이미 있으면 다른 이름을 사용한다. `tests.json`에는 실행한 test ID와 실패/누락/skip 여부가 있고, `source-sha256.json`에는 검증한 파일 해시가 있다. CI `summary.json`의 `github_sha`는 해당 checkout의 full SHA다. 모의 SQS/HTTP 및 격리 SQLite 검사이며, 실서비스 DB·AWS 수락 검증·실제 모델 품질 검사를 뜻하지 않는다.

추천 bootstrap의 Service 원본 테스트 2개는 pytest 함수였다. 기존 W4 unittest 실행기에서 누락되지 않도록 unittest로 변환하고 REAL 차단 및 설정 기반 entrypoint 검사도 포함했다. 새로운 pytest 의존성은 도입하지 않았다.

## 정확한 이미지 빌드 방법

Docker build context는 **Engine 저장소 루트 `.`** 다. Dockerfile 디렉터리나 과거 r5 ZIP을 context로 지정하지 않는다.

```bash
W4_SOURCE_SHA="$(git rev-parse HEAD)"
uv export --locked --no-dev --extra transport --no-emit-project --output-file deploy/recommendation/requirements.lock > /dev/null
git diff --exit-code -- deploy/recommendation/requirements.lock
docker build --build-arg W4_SOURCE_SHA="$W4_SOURCE_SHA" \
  -f deploy/recommendation/Dockerfile \
  -t "epick-w4-recommendation:$W4_SOURCE_SHA" .
docker image inspect "epick-w4-recommendation:$W4_SOURCE_SHA"
```

빌드 인자는 40자리 SHA여야 한다. image의 `org.opencontainers.image.revision`과 checkout SHA를 대조한다. base image는 Dockerfile의 digest로 고정되며, 의존성은 wheel/sdist 해시로 검증한다. 새로운 빌드가 과거 AWS image digest와 같다고 보장하지 않는다.

CI의 `Recommendation container / fixed synthetic runtime`은 이 Dockerfile로 실제 이미지를 빌드하고, 네트워크가 없는 컨테이너에서 `test_w1*.py`를 실행한다. 증거 artifact에는 build log·image inspect·runtime test log가 포함된다. CI는 registry push나 AWS 배포를 하지 않는다. 로컬 image ID는 registry manifest digest와 구분한다.

## 배포 시 W1에서 제공할 항목

W1의 기존 private 계약과 추천 전용 dispatch schema를 사용한다. schema 파일은 W1이 채택한 해시를 대조한 후 read-only로 마운트한다. 임의로 만든 schema를 운영 입력에 쓰지 않는다.

- `AWS_DEFAULT_REGION`, `W4_RECOMMENDATION_EXECUTION_QUEUE_URL` 및 추천 큐 수신·삭제·visibility 갱신 권한.
- `W1_RECOMMENDATION_PRIVATE_BASE_URL`, `W1_RECOMMENDATION_PRIVATE_BEARER`, `W1_RECOMMENDATION_PRIVATE_AUDIENCE=epick-w4-recommendation-worker`.
- `W4_RECOMMENDATION_DISPATCH_SCHEMA_PATH`: 마운트한 W1 schema의 컨테이너 내부 경로.
- `W4_RECOMMENDATION_BOOTSTRAP=epick_w4.acceptance_bootstrap:build_acceptance_adapter`.
- `W4_RECOMMENDATION_SYNTHETIC_ACCEPTANCE=YES`, `W4_RECOMMENDATION_REAL_DATA_ENABLED=false`.
- 필요 시 `W1_RECOMMENDATION_HTTP_TIMEOUT_SECONDS`(기본 30), `W4_RECOMMENDATION_VISIBILITY_SECONDS`(기본 600).

현재 코드는 실행 직전 visibility를 한 번 연장하며, 주기적인 W1 lease 갱신은 없다. 장시간 Gemma 전환에 필요한 lease/visibility 상한·갱신, 실패/재시작 처리는 별도 남은 작업이다. 배포자가 REAL 설정을 바꾸는 것으로 사용자 자료를 승인할 수는 없다.
