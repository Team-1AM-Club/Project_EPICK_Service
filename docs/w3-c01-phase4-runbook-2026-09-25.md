# W3 C-01 Phase 4 운영·공동 검증 runbook — 2026-09-25

이 문서는 W3 owner 브랜치의 C-01 실행 방법이다. 계약은 `w3-c01/0.2-candidate/r2`를 유지한다. W2가 public Source event와 replay/snapshot을 생산하고 W3는 소비·색인·상태·signal을 담당한다. W1은 동일 run ID의 공동 T058을 조율한다.

## 이미지와 설정

```powershell
docker build -f deploy/Dockerfile -t epick-w3-c01:<sha> .
$env:W3_C01_IMAGE = "<registry>/epick-w3-c01@sha256:<digest>"
docker compose -f deploy/c01.compose.yml config
docker compose -f deploy/c01.compose.yml up -d
```

Compose는 포트를 host에 publish하지 않고 `epick-private` internal network에서 8764만 expose한다. W1/W2 검증 컨테이너를 같은 network에 연결한다. root filesystem은 read-only이고 non-root `w3`(UID/GID 10001)로 실행하며 SQLite만 `c01-data:/var/lib/w3`에 보존한다. private CA가 필요하면 읽기 전용 파일 mount를 Compose override에 추가하고 컨테이너 경로를 `W3_SOURCE_AUTHORITY_CA_FILE`로 넣는다.

필수 설정 이름은 `W3_C01_IMAGE`, `W3_C01_MAX_TTL_SECONDS`, `W3_W2_TOKEN`, `W3_OPERATOR_TOKEN`, `W3_W4_TOKEN`, `W3_SOURCE_AUTHORITY_ENDPOINT`, `W3_SOURCE_AUTHORITY_TOKEN`이다. 선택 설정은 `W3_C01_DB_PATH`, `W3_SOURCE_AUTHORITY_CONNECT_TIMEOUT_SECONDS`, `W3_SOURCE_AUTHORITY_READ_TIMEOUT_SECONDS`, `W3_SOURCE_AUTHORITY_TOTAL_TIMEOUT_SECONDS`, `W3_SOURCE_AUTHORITY_CA_FILE`이다. 세 역할 token은 서로 달라야 한다. 원격 Authority는 HTTPS 인증서 검증을 사용하며 평문 HTTP는 loopback만 허용한다.

Source Authority 호출은 내부 재시도 없이 connect/read/total timeout으로 제한된다. 기본값은 각각 2/2/5초다. 전달 재시도와 replay/snapshot page 진행은 W2가 durable cursor를 다시 조회해 수행한다. Compose의 process restart도 `on-failure:3`으로 제한한다.

## health와 inspect

- `GET /health` — 인증 없이 SQLite와 schema version을 확인한다.
- `GET /c01/v1/status/{source_id}` — W2/operator/W4 bearer로 cursor, restriction revision, generation, index key, `reason`, `index_ack`를 확인한다.
- `GET /c01/v1/signals` — operator/W4 bearer로 미전달 signal을 확인한다.

예시 URL은 private network 내부 `http://c01:8764`다. 명령 이력에는 실제 bearer를 직접 적지 않고 승인된 secret injection을 사용하는 실행 도구로 호출한다.

## recovery·re-index·expire

- `POST /c01/v1/replay` — W2 역할. W2가 확정한 F/H와 최대 500개 연속 event를 보낸다.
- `POST /c01/v1/snapshot` — W2 역할. 명시적이고 완전한 atomic snapshot만 보낸다. replay 실패의 자동 우회가 아니다.
- `POST /c01/v1/index` — operator 역할. 현재 status의 cursor, restriction revision과 정확한 index key에 Evidence-bound 문서를 맞춘다. restriction clear 직후에는 `INDEX_PENDING`이며 이 호출이 성공해야 READY가 된다.
- `POST /c01/v1/purge` — operator 역할, body `{}`. TTL이 지난 index body를 제거하고 `BODY_EXPIRED` signal을 기록한다. 이 경로가 expire entrypoint다.

receipt `COMMITTED`는 transport commit이고 READY/index ACK가 아니다. `index_ack=true`와 `reason=READY`를 별도로 확인한다. conflict, gap, Authority 거부/장애 중에는 re-index로 우회하지 않는다.

## SQLite backup과 restore

온라인 snapshot은 SQLite backup API를 사용한다. 출력 파일이 있으면 덮어쓰지 않는다.

```powershell
docker compose -f deploy/c01.compose.yml run --rm --entrypoint /app/.venv/bin/python c01 `
  -m w3_knowledge.c01.operator backup `
  --db /var/lib/w3/c01.sqlite `
  --output /var/lib/w3/backups/c01-<timestamp>.sqlite
```

restore는 서비스를 먼저 중단하고 새 DB 경로에만 수행한다. 기존 DB를 자동 삭제하거나 교체하지 않는다.

```powershell
docker compose -f deploy/c01.compose.yml stop c01
docker compose -f deploy/c01.compose.yml run --rm --entrypoint /app/.venv/bin/python c01 `
  -m w3_knowledge.c01.operator restore `
  --backup /var/lib/w3/backups/c01-<timestamp>.sqlite `
  --db /var/lib/w3/c01-restored.sqlite
$env:W3_C01_DB_PATH = "/var/lib/w3/c01-restored.sqlite"
docker compose -f deploy/c01.compose.yml up -d c01
```

재시작 후 health, status, cursor, restriction revision, generation, index key와 READY를 전후 count-only 증거로 비교한다. raw event, 문서 본문, DSN, bearer token은 기록하지 않는다.
