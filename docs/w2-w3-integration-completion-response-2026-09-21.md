# W2 → W3 연동 완료 요청 회신 — 2026-09-21

전체 판정: **W3_LOCAL_READY / W2_TRANSPORT_ADAPTER_BLOCKED / LIVE_JOINT_E2E_NOT_RUN**

이 회신은 W2 `feat/crawler`의 full SHA
`2cc9153b8401c04c93a2f5a069904ee32ac79cb2`와 W3
`feat/w3-knowledge-validation`의 구현 full SHA
`35933e8037dc899f646b7b9af36909ae0e4b8f34`를 기준으로 한다.

## W3-01 / LOCAL_VERIFIED

- 정본 경로: `src/w3_knowledge/c01`, `src/w3_knowledge/restriction`,
  `src/w3_knowledge/core_runtime.py`
- W3 full SHA: `35933e8037dc899f646b7b9af36909ae0e4b8f34`
- remote branch: `origin/feat/w3-knowledge-validation`
- 포함 범위: C-01 r2 consumer, restriction revision/replay/snapshot/index ACK,
  `w3.retention/1.1`, authoritative Source 확인 port와 fail-closed 처리
- image digest: **NOT_BUILT / NOT_PROVIDED**
- 실행 결과: 전체 `279 passed, 1 skipped`; Ruff check/format PASS; C-01 20개,
  core decision 12개, restriction 19개 생성물 drift 0
- 실제 환경 여부: **아니오**
- 미완료 담당/다음 산출물: W1 배포 담당이 immutable image와 manifest digest를 제공한다.

문서 commit은 위 구현 SHA 뒤에 추가되므로 실행 image의 source pin은 위 SHA를 사용한다.

## W3-02 / BLOCKED_W2_TRANSPORT_ADAPTER

- 정본 경로: `scripts/verify_w2_w3_pin.py`,
  `docs/w2-w3-pin-verification-2026-09-21.json`
- fixture SHA-256:
  `1a3e8986e08adf8247de9b9b686335ab3d96751d32bd7ab56cbcf2176517787e`
- 실행 명령:

```powershell
.runtime\w2-engine-2cc9153\.venv\Scripts\python.exe scripts\verify_w2_w3_pin.py `
  --w2-checkout .runtime\w2-engine-2cc9153 `
  --w2-sha 2cc9153b8401c04c93a2f5a069904ee32ac79cb2 `
  --w3-sha 35933e8037dc899f646b7b9af36909ae0e4b8f34 `
  --output docs\w2-w3-pin-verification-2026-09-21.json
```

- 결과: W2 내부 `SourceEvent` 4건 생성 성공; W2 outbox 재구성은 version 2건과
  observation 1건 성공; restriction 1건은 `REJECTED_NOT_DELIVERABLE`
- 직접 호환 결과: W2 내부 wire 4건 모두 W3 합의 envelope 검증 거부
- 검증용 명시 변환 결과: outer `schema_version=1.0`, `producer=w2`,
  `aggregate_type=source`, `revision=aggregate_revision` 및 payload schema를 명시하면
  4건 검증·소비 성공, 최종 cursor 4, restriction revision 1
- 실제 환경 여부: **아니오**. DB producer→queue/HTTP→W3 process 경로는 실행하지 않았다.
- 차단 근거:
  - W2 `SourceEvent`는 outer `schema_version=w2.source.v1`, `aggregate_revision`을 사용하고
    `producer`, `aggregate_type`이 없다.
  - W2 observation/restriction payload 모델에는 payload `schema_version`이 없다.
  - W2 `_DELIVERABLE_EVENT_TYPES`에 `source.restriction.changed`가 없다.
  - `PublicSourceEventPublisher`는 Protocol만 있고 W3 전송 구현이 없다.
- 미완료 담당/다음 산출물: W2가 합의 envelope adapter, restriction deliverable 등록,
  실제 publisher 구현과 채택 full SHA를 제공한다. 이후 양쪽 SHA를 고정해 duplicate/gap/
  conflict/replay/snapshot/cleared 재색인을 공동 실행한다.

## W3-03 / LOCAL_EXECUTION_CONTRACT_READY

- 정본 경로:
  - HTTP entrypoint: `src/w3_knowledge/c01/http.py`
  - route/role 처리: `src/w3_knowledge/restriction/http.py`
  - 원자 상태·복구: `src/w3_knowledge/c01/store.py`
  - schema: `contracts/c01/v0.2-candidate`
- 로컬 구현 endpoint: `POST /c01/v1/events`, `/replay`, `/snapshot`, `/index`,
  `GET /c01/v1/status/{source_id}`, `/signals`, `POST /signals/ack`
- transport receipt: event transaction commit 뒤 응답의 `receipt=COMMITTED`
- index ACK: exact IndexKey, event cursor, restriction revision, TTL이 일치할 때만 상태의
  `index_ack=true`; SQLite `indexed` 상태로 보존된다. receipt와 같은 의미가 아니다.
- replay: `H=high_watermark`를 required cursor로 보존한다. 현재 cursor가
  `F=retention_floor_cursor`보다 작으면 `SNAPSHOT_REQUIRED`를 반환하고 index를 지운다.
- snapshot: Source cursor, restriction watermark, Version/Restriction/Observation 전체를 한
  transaction에서 검증·교체하고 checkpoint hash를 기록한다. stale 또는 같은 cursor의 다른
  body는 적용하지 않는다.
- W4 ACK: `POST /c01/v1/signals/ack`의 `signal_id` 전달 완료만 나타내며 W2 transport receipt와
  index ACK에서 분리된다.
- 실제 환경 여부: **아니오**
- 미완료 담당/다음 산출물: W1/W2가 실제 Queue/HTTP 경로와 인증 정보를 제공한 뒤 같은 계약을
  concrete transport에 연결한다.

## W3-04 / LOCAL_PORT_READY_W2_ENDPOINT_PENDING

- 정본 경로: `src/w3_knowledge/c01/authority.py`, `src/w3_knowledge/c01/http.py`
- 최소 interface: `SourceAuthority.is_registered(source_id: UUID) -> bool`
- 주입 방식: C-01 시작 시 필수 `--source-authority module:factory`
- 호출 시점: event DB mutation 전, replay 전, snapshot 전, index 전. `replacement_ref`가 있으면
  원 Source와 대체 Source를 모두 조회한다.
- timeout/fail-closed: concrete adapter가 connect/read/전체 timeout을 적용한다. 예외·timeout은
  `SOURCE_AUTHORITY_UNAVAILABLE` 503, literal `True` 이외는 `SOURCE_NOT_REGISTERED` 422이며
  이벤트·신호·index를 기록하지 않는다.
- fallback: W3가 관측한 Source를 등록 근거로 사용하지 않는다. 기존 로컬 등록 테이블에는 더
  이상 쓰지 않고 실제 factory가 없으면 시작하지 않는다.
- 검증: unknown, timeout, replay/snapshot/index 재검사, replacement Source 거부, HTTP 오류
  비노출 테스트 통과
- 실제 환경 여부: **아니오**
- 미완료 담당/다음 산출물: W2가 authoritative Source 조회 경로, 인증, timeout 분류와 adapter
  source pin을 제공한다.

## W3-05 / INPUTS_REQUIRED

- runtime 위치: 미확정
- image registry/digest: 미확정
- 영구 DB/volume: 미확정
- IAM role/stable Role ID/SenderId: 미확정
- 실제 SQS/HTTP 경로: 미확정
- 모니터링·teardown 담당: 미확정
- 공동 실행 시간 창: 미확정
- 실제 환경 여부: **아니오**
- 미완료 담당/다음 산출물: W1 배포 담당이 위 값을 안전한 채널로 전달하고 W2/W3 담당자가
  공동 E2E 결과, restart/recovery 증거와 최종 ACK 로그를 남긴다.

따라서 현재 완료 판정은 **W3 로컬 계약·구현 완료, W2 adapter와 실제 인프라 연동 대기**다.
실제 W2 producer 산출물과 transport로 공동 E2E가 통과하기 전에는 W2–W3 연동 완료로 표시하지
않는다.
