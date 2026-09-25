# W3 → W1/W2: Phase 4 C-01 정본·T058 준비 회신 — 2026-09-25

## 판정

W3 owner 브랜치 `feat/w3-knowledge-validation`에 Phase 4 C-01 구현·테스트·운영 패키징을 채택했다.

- W3 artifact full SHA: `87ac87f95c9f4f90ec95d94f1f92f9ca7e430b3c`
- W1 요청 기준 SHA: `b68c45afafa8b5dc99757388eb47940b4852cae6`
- W3 상태: `W3_OWNER_READY_JOINT_RUN_PENDING`
- T058 상태: `NOT_RUN`

이 SHA는 T042/T054/T055 및 W3 측 증거 형식을 포함한다. T058 완료를 뜻하지 않는다. 같은 W1 Job/run ID에서 W2 FINALIZED, W3 READY, W3 재시작 후 READY가 함께 확인되어야 공동 Gate를 닫는다. W1 내부 로컬 후보 `9f49a0b…`는 사용하지 않았다.

## 채택 파일과 책임

| 작업 | 파일 | 보장 내용 |
|---|---|---|
| T042 | `tests/integration/test_c01_joint_runtime.py` | Authority 거부·장애, clear/re-index, replacement, restart, duplicate/gap/conflict/replay/snapshot, private field 차단 |
| T054 | `deploy/Dockerfile`, `.dockerignore` | `uv.lock` 고정 설치, non-root `w3` UID/GID 10001, read-only 배포 전제, `/health` 점검 |
| T055 | `deploy/c01.compose.yml` | durable SQLite volume, W2/operator/W4 분리 token, internal network, TTL·Authority timeout·CA 설정, 제한된 restart |
| 운영 | `src/w3_knowledge/c01/operator.py` | SQLite online backup, 무결성·schema 확인 restore, 기존 파일 덮어쓰기 거부 |
| HTTP | `src/w3_knowledge/c01/http.py`, `src/w3_knowledge/restriction/http.py` | 명시적 `127.0.0.1`/`0.0.0.0` bind; 기본값은 loopback 유지 |
| 운영 회귀 | `tests/integration/test_c01_operability.py` | 컨테이너 bind, READY 상태 backup/restore, 잘못된 DB·덮어쓰기 거부 |
| 배포 회귀 | `tests/integration/test_c01_deploy_artifacts.py` | Docker/Compose/runbook 및 T058 익명 증거 schema 검증 |
| runbook | `docs/w3-c01-phase4-runbook-2026-09-25.md` | health/inspect/replay/snapshot/re-index/expire/backup/restore 절차 |
| T058 형식 | `contracts/c01/v0.2-candidate/t058-count-evidence.schema.json` | 동일 job/run의 pin·익명 참조·집계·restart 전후 상태 |

## 기존 계약 pin

기존 `w3-c01/0.2-candidate/r2` event/receipt/status 의미와 W2 Source Authority의 `registered=true` fail-closed 경계를 변경하지 않았다.

| 계약 | 경로 | SHA256 |
|---|---|---|
| C-01 event | `contracts/c01/v0.2-candidate/event.schema.json` | `1d5462ba05149cb825bb9f50ceba35c9abdf4a382b23e2b746d5202cb86d9126` |
| C-01 receipt | `contracts/c01/v0.2-candidate/receipt.schema.json` | `d51a89780aeee29227cf0a089233f039c5f97d034c78890f490e62a7e700cd99` |
| C-01 status | `contracts/c01/v0.2-candidate/status.schema.json` | `a4ba653fd9f4bd149117a97b3a4843b2567f02f0be6017634a42ac5f3972641d` |
| Source Authority adapter | `src/w3_knowledge/c01/source_authority_http.py` | `e093b35caa8c6209c63507b31476a2de6c8e81548acd1f2c7174fccf957d0b4a` |

`python scripts/export_c01_contract.py --check` 결과는 `files=20`, `drift=[]`이다.

## 이미지·스토리지·운영 경계

- 의존성: `pyproject.toml` + `uv.lock`, image build는 `uv sync --frozen --no-dev` 사용.
- 실행: `/app/.venv/bin/python -m w3_knowledge.c01.http`, 사용자 `w3` UID/GID 10001.
- 이미지: Compose의 `W3_C01_IMAGE`는 registry digest 형식을 요구한다.
- DB: 기본 `/var/lib/w3/c01.sqlite`, named volume `c01-data:/var/lib/w3`.
- backup/restore: `python -m w3_knowledge.c01.operator backup|restore`. restore는 정지 상태에서 새로운 DB 경로로만 수행한다.
- network: host port publish 없이 `epick-private` internal network의 8764만 expose한다.
- 인증: `W3_W2_TOKEN`, `W3_OPERATOR_TOKEN`, `W3_W4_TOKEN`을 서로 분리한다.
- Authority: `W3_SOURCE_AUTHORITY_ENDPOINT`, `W3_SOURCE_AUTHORITY_TOKEN`, `W3_SOURCE_AUTHORITY_CA_FILE`.
- timeout: `W3_SOURCE_AUTHORITY_CONNECT_TIMEOUT_SECONDS`, `W3_SOURCE_AUTHORITY_READ_TIMEOUT_SECONDS`, `W3_SOURCE_AUTHORITY_TOTAL_TIMEOUT_SECONDS`.
- policy: `W3_C01_MAX_TTL_SECONDS`, `--restriction-scope version`.
- retry: Authority 내부 재시도는 하지 않고 timeout으로 fail closed한다. Compose process restart는 `on-failure:3`이다.

정확한 명령은 runbook에 고정했다. HTTP 운영 경로는 `GET /health`, `GET /c01/v1/status/{source_id}`, `GET /c01/v1/signals`, `POST /c01/v1/replay`, `POST /c01/v1/snapshot`, `POST /c01/v1/index`, `POST /c01/v1/purge`다. receipt `COMMITTED`와 `reason=READY`/`index_ack=true`는 별도 판정한다.

## 검증 결과

### 실행 명령과 결과

```text
python -m pytest -p no:cacheprovider --basetemp=.runtime/pytest-phase4-focused-20260925a tests/integration/test_c01_joint_runtime.py tests/integration/test_c01_operability.py tests/integration/test_c01_deploy_artifacts.py -q
16 passed

python -m pytest -p no:cacheprovider --basetemp=.runtime/pytest-phase4-full-debug-20260925a -q -x
304 passed, 1 skipped

uv run ruff check .
All checks passed

uv run ruff format --check .
140 files already formatted

python scripts/export_c01_contract.py --check
files=20, drift=[]

docker compose -f deploy/c01.compose.yml config --quiet
PASS

docker build -f deploy/Dockerfile -t epick-w3-c01:phase4-test .
PASS

docker run --rm epick-w3-c01:phase4-test --help
PASS
```

live provider test 1개는 `--run-live`와 실제 외부 자격 증명이 없어 skip했다. 프로젝트에는 별도 type checker가 설정되어 있지 않아 type 결과는 `NOT_CONFIGURED`다.

### 요구 사례 대응표

| 요구 사례 | 검증 테스트 |
|---|---|
| Authority false/503/timeout | `test_authority_false_unavailable_and_timeout_never_commit[false|503|timeout]` |
| restriction clear 후 정확한 key 재색인 | `test_restriction_clear_requires_exact_reindex_before_ready` |
| Source replacement와 Authority | `test_replacement_source_must_be_authoritative` |
| 재시작 후 cursor/revision/generation/index key/READY | `test_restart_preserves_ready_cursor_restriction_generation_and_key` |
| duplicate/gap/conflict/replay 단조성 | `test_duplicate_gap_replay_and_conflict_are_monotonic` |
| snapshot 복구와 re-index | `test_snapshot_recovery_requires_reindex_and_keeps_history_incomplete` |
| private Job/owner 필드 차단 | `test_private_job_owner_fields_never_enter_public_c01` |
| durable backup/restore | `test_online_backup_restores_durable_ready_state` |

## T058 count-only 증거

공동 실행 산출물은 `t058-count-evidence.schema.json`을 따른다. 필수 항목은 다음과 같다.

- W1 joint job ID와 joint run ID
- W1/W2/W3 commit pin 및 event/receipt/status/Authority SHA256
- Source/version의 SHA256 익명 참조
- events received, receipts committed, indexes accepted, READY source, error 수
- cursor, required cursor, restriction revision, required revision, generation, reason, index ACK, history complete, index key hash
- 실행 전, 실행 후, 재시작 후 상태
- Source 미등록, Authority 장애, gap, conflict, index 실패의 분류별 수

원시 event 본문, 원문 Source/version ID, Job owner, 개인 데이터, DSN, bearer token은 schema와 예제에서 제외했다.

## 아직 필요한 공동 입력

W2 T050의 새 clean SHA와 실제 public event/outbox·Source Authority endpoint, W1의 동일 Job/run ID 하네스는 이 W3 저장소에서 확인할 수 없다. 따라서 현재 정확한 fixture 기대값과 실제값 사이의 계약 불일치는 발견되지 않았지만, 실제 관통 호환성은 미확인이다. W2 SHA와 W1 실행 창이 준비되면 W3는 위 artifact SHA와 증거 schema로 공동 T058을 실행하고 restart 이후 상태까지 대조한다.
