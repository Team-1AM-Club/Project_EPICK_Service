# EPICK Backend

FastAPI와 PostgreSQL로 구성한 EPICK Service의 공개 v1 API입니다. API 컨테이너는 migration을 실행하지
않습니다. schema 변경은 별도 migration principal로 선검증·적용한 뒤 API 이미지를 배포합니다.

## 로컬 실행

`backend` 디렉터리에서 의존성을 설치하고 PostgreSQL을 준비합니다. 아래 URL의 host, port, 계정은 로컬
환경에 맞게 바꿉니다.

```powershell
python -m pip install -e .
python -m pip install --group dev
$env:DATABASE_URL = "postgresql+psycopg://epick:password@localhost:5432/epick_local"
$env:API_CURSOR_SIGNING_KEY = "local-development-only-cursor-key"
python -m uvicorn app.main:app --reload
```

- liveness: `GET /health`
- readiness: `GET /health/ready`
- 공개 API: `/api/v1/*` — OIDC Gate 전에는 인증 dependency가 fail-closed이며, 테스트만 dependency
  override로 principal을 넣습니다.

`MIGRATION_DATABASE_URL`은 API 컨테이너 환경변수가 아닙니다. migration 전용 계정과 runbook에서만
사용합니다. API runtime에는 `DATABASE_URL`과 `API_CURSOR_SIGNING_KEY`만 주입합니다.

## 검증

PostgreSQL test database와 관리자 연결 권한이 준비된 상태에서 실행합니다.

```powershell
python -m ruff check app tests
python -m pytest tests/api -q
python -m pytest tests/contract -q
python -m pytest tests/integration/db -q
```

CI도 같은 경로를 API/contract와 PostgreSQL integration으로 나누어 실행합니다.

## 현재 API 범위와 활성화 전 Gate

- 추천 API의 `result_origin=SYNTHETIC`은 개발·통합 검증용 결과입니다. 실제 W3/W4 결과를 대신하거나
  외부 엔진이 완료됐다는 의미가 아닙니다.
- Job 또는 삭제 요청의 `202 Accepted`는 Job/Command/Outbox의 원자적 수락만 의미합니다. Linux worker의
  실제 dispatch, W2/W3/W4 처리, 삭제 target ACK를 의미하지 않습니다.
- 공개 API에는 W2/W3 transport event, W3 ACK, private command/outbox, lease/fence/epoch, checkpoint
  payload를 노출하지 않습니다.
- 실제 W3 ACK 및 W2→W3 replay/gap/snapshot, Linux queue worker/egress 검증, OIDC issuer/JWKS/role
  mapping, 미식별 Company resolve는 별도 계약 또는 운영 Gate가 닫힌 뒤 adapter·migration·회귀 테스트를
  추가합니다.

## W3 Core Decision private consumer

이 consumer는 public API와 분리된 Worker EC2/컨테이너에서만 실행합니다. 별도 env 파일에는 다음 이름만
배치하고 실제 URL·DB password·principal ID는 저장소에 기록하지 않습니다.

```text
WORKER_DATABASE_URL
AWS_DEFAULT_REGION
W3_CORE_DECISION_QUEUE_URL
W3_CORE_DECISION_DLQ_URL
W3_CORE_DECISION_EXPECTED_PRODUCER=w3
W3_CORE_DECISION_EXPECTED_SENDER_ID
W3_CORE_DECISION_BATCH_SIZE=10
W3_CORE_DECISION_WAIT_SECONDS=20
W3_CORE_DECISION_VISIBILITY_SECONDS=120
```

`W3_CORE_DECISION_EXPECTED_SENDER_ID`에는 SQS `SenderId`의 콜론 앞 stable role principal ID만 넣습니다.
본문의 `producer` 값은 인증 근거로 사용하지 않습니다. W3 send role은 main queue의 `SendMessage`만,
W1 worker role은 main queue의 `ReceiveMessage`, `DeleteMessage`, `ChangeMessageVisibility`,
`GetQueueAttributes`와 DLQ의 `GetQueueAttributes`만 허용합니다. Queue policy도 W3 send role만 허용해야
합니다. DB URL은 Secrets Manager에서 root-only env 파일로 전달하고 실제 값이나 Secret JSON을 출력하지
않습니다.

이미지 배포 전 무변경 preflight와 실행 명령은 다음과 같습니다.

```bash
python scripts/preflight_w1_core_decision_runtime.py
docker compose -f infra/w1-runtime.compose.yml --profile w3-core-decision up -d
```

preflight는 DB read, main queue/DLQ 속성 접근, redrive target 일치와 principal 설정만 확인하며 메시지를
수신·삭제·전송하지 않습니다. W1 isolated 검증 완료 후에도 실제 W3 relay 공동 시험 전 상태는
`W1_ISOLATED_COMPLETE / JOINT_CT12_PENDING`입니다.
