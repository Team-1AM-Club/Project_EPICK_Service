# PM 회신 — W3 restriction 계약 후보 및 실행 환경

작성일: 2026-09-13. 대상: PM / W2 수집 / W4 매칭 담당.
대응 문서: `W3_followup_request_2026-09-13.md`.

**기존 Lab과 별도로 실행 가능한 W3 consumer·SQLite FTS 검색·W4 통지 어댑터를 구현했다.**
계약은 `w3-restriction/0.1-draft` 후보이며 팀 승인 전이다. 실제 W2 outbox와 기존 W4
서비스를 연결한 제품 통합 완료를 선언하지 않는다.

## 요청 항목별 회신

| PM 요청 | 제공 내용 | 상태 |
| --- | --- | --- |
| P0-1 공식 이벤트 계약 | [계약 후보](../contracts/restriction/v0.1-draft/README.md), JSON Schema 9종, 유효 6종·무효 4종 예시 | 후보 구현 완료 / 채택 승인 대기 |
| P0-2 전달·ACK·재시도 | HTTP 역할별 endpoint, 저장 receipt/색인 ACK/W4 전달 확인의 의미, 실패 코드·재시도 제안 | 로컬 transport 구현 / 운영 인프라·worker 합의 대기 |
| P0-3 replay·snapshot | 실제 replay batch 소비, high watermark 차단, 만료 경계, stale/conflict snapshot 거부·복구 | W3 구현·검증 완료 / W2 producer 연결 대기 |
| P0-4 lifecycle | Source 전체 신규 사용 차단, audit 분리, FTS 제거, 재색인 후 해제, W4 generation 통지 | W3 경로 구현 / 승인 주체·W1 영향·제품 표시 합의 대기 |
| P1-5 실행 환경 | 실제 HTTP 프로세스·SQLite DB·FTS5, W4 지속 저장 어댑터, health·fixture·실행 명령 | 아래 명령으로 재현 가능 / 공유 서버 배포는 미실행 |
| P1-6 검증 증거 | 계약·실제 저장·HTTP·W4 adapter 테스트, 실행 결과 JSON | 로컬 검증 완료 / W2→기존 W4 앱 통합은 미실행 |

## 저장소와 실행 위치

- 저장소: Team-1AM-Club/Project_EPICK_Service, 작업 branch `feat/w3-knowledge-validation`.
- W3 실행 모듈: `src/w3_knowledge/restriction/http.py`.
- 상태/검색: `src/w3_knowledge/restriction/store.py` — 실제 SQLite FTS5를 사용한다.
- W4 소비 어댑터: `src/w3_knowledge/restriction/w4.py`.
- 기존 구조화 모듈 연결: `src/w3_knowledge/restriction/guard.py`의 `structure_guarded`.
- 테스트: `tests/integration/test_restriction_*.py`.
- 문서·소스가 있는 현재 전달본 기준이다. 원격 branch의 최신 여부와는 구분한다.

## 가장 빠른 검증

Python 3.12 이상, uv, SQLite FTS5가 필요하다. 외부 API 키·Docker·broker는 필요 없다.
저장소 루트 또는 전달 ZIP의 압축 해제 루트에서:

```powershell
uv sync --locked
uv run --locked python scripts/restriction_smoke.py
```

이 명령은 별도 W3 서버 프로세스를 localhost 임의 포트에 띄우고, 실제 HTTP 요청으로
이벤트·색인·replay·snapshot을 처리한다. W4 어댑터는 별도 SQLite 파일에 통지와 cache를
저장한다. 임시 역할 토큰은 실행 중 생성해 자식 프로세스 환경에만 주입한다.
테스트 종료 시 서버를 종료하며 DB와 결과는 `.runtime/smoke-<id>/`에 남긴다.
외부 W2·W4 서비스나 모델 호출은 하지 않는다.

출력의 report 파일에서 `passed=23, total=23, error=null`을 확인한다.
`transport=real-http-subprocess`, `storage=sqlite-fts5`, `team_contract_approved=false`를 기록한다.
명시적인 결과 폴더를 지정하려면:

```powershell
uv run --locked python scripts/restriction_smoke.py --output-dir .runtime/pm-check-01
```

이전에 DB/결과가 생성된 폴더에는 덮어쓰지 않는다. 새 이름으로 재실행한다.
결과 공유 시 `result.json`만 전달하면 된다. 운영 토큰이나 DB를 첨부하지 않는다.

## W2/W4가 직접 호출할 서버 실행

아래 값은 각자의 로컬 셸에서 생성한다. 실제 값을 메신저나 Git에 공유하지 않는다.
`.env.example`은 환경 변수 이름 예시이며 자동 로딩하지 않는다.

```powershell
$env:W3_W2_TOKEN = [guid]::NewGuid().ToString('N')
$env:W3_OPERATOR_TOKEN = [guid]::NewGuid().ToString('N')
$env:W3_W4_TOKEN = [guid]::NewGuid().ToString('N')
New-Item -ItemType Directory -Force .runtime | Out-Null
uv run --locked python -m w3_knowledge.restriction.http --db .runtime/w3.sqlite --port 8763
```

서버는 환경 변수를 설정한 셸을 점유한다. 다른 셸의 클라이언트에는 해당 역할의 값만
안전한 로컬 환경 설정으로 주입한다. health는 `http://127.0.0.1:8763/health`다.
공유 원격 endpoint는 제공하지 않으며, 팀 테스트 서버 배포/주소/인증은 별도로 정해야 한다.

예: 이미 W3_W2_TOKEN을 주입한 클라이언트 셸에서 초기 이벤트 전송:

```powershell
$headers = @{ Authorization = "Bearer $env:W3_W2_TOKEN" }
$body = Get-Content -Raw -Encoding UTF8 contracts/restriction/v0.1-draft/examples/initial.json
Invoke-RestMethod -Method Post -Uri http://127.0.0.1:8763/v1/events -Headers $headers -ContentType application/json -Body $body
```

initial → index → restrict → release → reindex 흐름의 상세 JSON은 smoke 스크립트에 있다.
정적 `index-request.json`은 revision 3·2026-09-14 만료 예시이므로 초기 revision 1에
그대로 보내거나 만료 뒤 사용하면 성공하지 않는다. 실행 가능한 smoke는 revision과
expires_at을 현재 흐름·시각에 맞춰 구성한다.

W4_W3 token이 아니라 **W3_W4_TOKEN**을 주입한 W4 셸에서:

```powershell
uv run --locked python -m w3_knowledge.restriction.w4 --url http://127.0.0.1:8763 --db .runtime/w4.sqlite
```

이 명령은 통지를 최대 500개 처리하고 종료한다. 실행 실패 시 같은 명령을 재실행할 수
있다. 지속 polling/backoff/DLQ worker는 호스트가 담당하며 정책은 계약 제안에 적었다.

## 기존 지식 구조화·W4 앱에 붙이는 경계

기존 `structure` 함수 자체의 호출 규약은 바꾸지 않았다. 실제 호스트는
`structure_guarded(request, ports, store, keys, config)`를 호출해야 한다.
keys는 Source ID별 **실제 전체 IndexKey**이며 기존 SourceInput에 없는 extraction ID를
추정해서 채우지 않는다. 기존 policy/retained-text 포트가 허용한 경우에만 현재 restriction,
SourceVersion 및 전체 키를 추가 검사한다. 처리 중 generation이 바뀌면 전체 응답을 보류한다.
내부 Artifact의 Source ID 또는 SourceVersion이 바깥 SourceInput과 다르면 구조화 사용을
거부하여 다른 Source의 제한을 우회하지 못하게 한다.
외부 모델 호출 자체의 중간 취소는 이 wrapper의 보장이 아니다.

W4 앱은 `W4Cache.apply`로 최신 통지를 저장하고, `put`에 사용한 generation을 기록한 뒤,
`get(source_id, current_status)`의 실시간 재검사를 거쳐야 한다. `current_status`에는
인증된 W3 status 조회 함수를 연결한다. W4 기존 서비스를 대신 수정한 것은 아니며
현재 adapter를 기존 추천/cache 반환 경로에 채택하는 작업은 W4 담당과 공동 진행한다.
여러 Source를 참조한 추천은 모든 Source의 상태를 확인한 뒤 반환해야 한다.

W3 DB를 초기화/복구할 때는 기존 W4 cache를 함께 무효화하고 전체 상태를 재동기화해야 한다.
새 DB의 generation을 기존 DB의 연속 번호로 간주하지 않는다. 다중 노드·서로 다른 구독자·
DB 복구 세대의 운영 설계는 별도 배포 검토 대상이다.

## 검증 범위와 명령

```powershell
uv run --locked pytest -q
uv run --locked ruff check src tests scripts
uv run --locked ruff format --check src tests scripts
uv run --locked python scripts/export_restriction_contract.py --check
```

- 계약: unknown/private 필드·미래 버전·타입 강제 변환·잘못된 날짜·Source 불일치를 거부한다.
- 실제 저장/FTS: 제한 적용·해제, 재시작·중복·역순·충돌, gap/replay·만료·snapshot 복구,
  4개 키 mismatch, 실제 SQL trigger로 유발한 쓰기 실패·재처리, TTL 논리 삭제를 검증한다.
  별도 DB connection에서 동시에 제한·색인 또는 W4 통지를 처리하는 경우도 검증한다.
  전체 SQL transaction이 rollback되어 잠금이 풀리는 장애와 그 사이의 제한 변경도 재현한다.
- HTTP/W4: 역할별 인증, 안전한 오류, 지속 outbox ACK, W4 역순·중복·충돌·cache 차단을 검증한다.
- 구조화 연결: 기존 권한과 전체 키 검사, 추출 중 제한 변경 시 반환 보류를 검증한다.
  이 두 연결 테스트는 외부 의미 추출 포트에 테스트 대역을 사용한다. HTTP/SQLite/FTS 경로와
  별도 서버 smoke에는 consumer/search/cache 성공을 흉내 내는 mock을 사용하지 않는다.
  전체 rollback 경쟁 테스트는 실제 SQL 결과를 바꾸지 않는 관찰 함수로 실행 순서만 제어한다.
- 실제 시계 만료 검증은 smoke에서 수행하며 보관 경계·오래된 replay는 고정 시각 테스트도 있다.
- `.github/workflows/w3-validation.yml`에 동일 검사와 결과 JSON 업로드를 추가했다.
  **GitHub Actions 실행 성공은 아직 확인하지 않았다.** 로컬 통과와 구분한다.

실제 검증 결과는 함께 제공하는 `w3-restriction-verification.json`을 참고한다.
외부 모델 live 테스트는 기존대로 명시적 설정/실행을 요구하며 이번 범위에서 실행하지 않았다.

## PM/W2/W4의 다음 결정

1. 위 draft를 검토해 이벤트 ID·revision 범위·enum·ACK 의미를 채택하거나 수정 의견을 회신한다.
2. W2는 승인된 이벤트 게시·replay batch·원자적 snapshot 제공 경로를 연결한다.
3. W4는 제안 어댑터를 실제 cache/추천 반환 경로에 연결하고 W1의 참조/승인 정책을 반영한다.
4. 공동 환경에서 W2 outbox→W3→기존 W4 앱의 같은 시나리오를 실행한다.

T066은 W3 계약 후보가 제공되어 검토를 시작할 수 있지만 공식 채택/연결은 대기다.
T069는 W3 실행 환경과 로컬 증거가 제공됐으며 실제 W2 consumer 연결 검증은 대기다.
T073 제품 handoff는 W4 앱/캐시·W1 영향 정책과 공동 검증 완료 전까지 열어 둔다.
이 번호들은 PM이 인용한 W2 작업이며 W3가 임의로 완료 처리하지 않는다.
