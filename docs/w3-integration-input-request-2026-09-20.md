# W3 실제 연결 입력 요청

상태: **INPUTS_REQUIRED / LOCAL_LIFECYCLE_IMPLEMENTATION_AVAILABLE / LIVE_INTEGRATION_NOT_STARTED**

기준 정책: `w3.retention/1.1`

W3 구현 pin: `0c4f01f9537a3129c976fae5e63111a7982c5da6`

실제 값이 제공되지 않은 endpoint, factory, 담당자, role, queue, registry 경로를 W3가 임의로
만들지 않는다. 아래 값이 확보되면 실제 adapter와 caller 연결을 시작한다.

## 1. 분석 caller

| 항목 | 필요한 값 |
|---|---|
| 제공자·담당자 | 저장소, 팀/담당자, 연락 경로 |
| source pin | 연결 대상 full commit SHA와 파일·함수 경로 |
| 호출 시점 | 분석 요청 생성·변경·취소 중 어느 시점에 `CoreRuntime.supply`를 호출하는지 |
| 요청 identity | 변경 불가능한 `analysis_request_id` UUID |
| 최초 발급 시각 | 신뢰된 UTC `analysis_request_issued_at`; 재시도·replay로 갱신 금지 |
| 분석 계획 | 신뢰된 `DecisionContext`, `required_sources`, `optional_sources` 산정 경로 |
| 멱등성 | stable idempotency key의 생성·보존·재전달 규칙 |
| 현재성 | 종료·삭제·알 수 없음·30일 초과 요청과 동일 identity 내용 변경을 거부하는 근거 |
| 실패 처리 | timeout, transient failure, definitive cancellation의 재시도·차단 규칙 |

W3 내부 입력은 다음 형태다. 이 구조는 W3→W1 event wire를 변경하지 않는다.

```json
{
  "context": {
    "job_id": "uuid",
    "company_id": "uuid",
    "source_id": "uuid",
    "analysis_input_version": "non-blank version"
  },
  "analysis_request_id": "uuid",
  "analysis_request_issued_at": 0,
  "required_sources": ["uuid"],
  "optional_sources": ["uuid"]
}
```

## 2. Authority

| 항목 | 필요한 값 |
|---|---|
| 호출 경로 | 기존 인증 경로의 endpoint 또는 `module:factory`와 source pin |
| 인증 | workload identity/service principal 방식과 허용 principal |
| 요청·응답 | job/source 입력, context·owner_id·owner_epoch·active 출력 schema |
| 의미 | `active=false`, not-found, deleted, cancelled, unknown의 구분 또는 모두 definitive reject인지 |
| 요청 결속 | analysis request identity·최초 발급 시각·내용 digest가 현재 authoritative record와 같음을 보장하는 경로 |
| Source 상태 | active/retired/unknown Source 판정과 영구 종료 시각 제공 경로 |
| 시간 한도 | connect/read/전체 timeout 및 transient error 목록 |

W3는 supply와 매 relay/replay 직전에 전체 context, owner, epoch, active를 재검사한다. 조회 실패는
허용으로 바꾸지 않는다. definitive reject는 본문을 차단하고 transient error는 bounded retry로 처리한다.

## 3. 사용자 삭제 dispatcher

로컬 consumer, strict schema, command ledger와 receipt outbox는 구현·검증됐다. 실제 연결에는 아래
인프라 입력과 W1 계약 채택이 필요하다. 세부 전달은
`docs/w3-lifecycle-dispatch-handoff-2026-09-20.md`를 따른다.

| 항목 | 필요한 값 |
|---|---|
| source pin | W3 `0c4f01f9537a3129c976fae5e63111a7982c5da6`; W1 producer의 채택 SHA는 미제공 |
| 전달 방식 | 전용 private Standard SQS 두 개의 실제 URL과 queue policy |
| schema | W1 owner deletion target type 확장, Source `target_ref = Source.id`, W3 lifecycle receipt의 W1 채택 pin |
| epoch | higher-epoch 판정의 authoritative source와 등록 전 삭제 규칙 |
| 인증 | W1 sender와 W3 consumer의 stable Role ID, workload 설정 전달 경로 |
| W1 처리 | W3 receipt consumer, idempotent 최종 수용 상태와 오류 운영 규칙 |

`LifecycleSqsWorker`는 검증된 command만 `CoreRuntime.apply_lifecycle_command`로 연결한다. 사용자
삭제는 상태·보관기한보다 우선하고 상태 변경과 receipt outbox를 한 transaction에 기록한다.

## 4. Source registry

- 영구 retired와 일시 제한·접근 불가·unknown을 구분하는 authoritative 경로
- `company_id`, `source_id`, 영구 종료 시각 및 동일 Source ID 재사용 금지 보장
- 조회 인증, timeout, not-found/error 의미와 source pin
- 영구 종료를 `CoreRuntime.retire_source`에 전달하는 adapter/재처리 규칙
- W2 `40ca63447287432d5a127f6001fc984dc8980dd5`의 인증된 내부 GET을 운영 환경에서
  접근 가능하게 하는 실제 endpoint·신뢰된 token 전달 경로
- C-01 consumer의 event/replay/snapshot/index 전 조회에 적용할 connect/read/전체 timeout

`command_id`가 결속된 Source 종료, receipt outbox, retired marker와 counter purge는 구현되어 있다.
실제 W1 producer·queue·role 입력 없이는 실환경 완료로 표시하지 않는다.

## 4.1. W2 public event adapter

W2 `40ca63447287432d5a127f6001fc984dc8980dd5`에서 아래 adapter와 outbox 전달 경로가
제공됐다. W3는 해당 SHA의 실제 wire 4건을 로컬 수용했고 합성 양방향 HTTP를 통과했다.
운영 및 PostgreSQL 공동 E2E에는 다음 입력이 남았다.

- 승인된 로컬 또는 운영 PostgreSQL과 W2 migration·producer 합성 fixture
- W2 outbox operator의 실행 환경 및 W3 C-01 endpoint로 가는 네트워크·인증 설정
- W2 SourceAuthority ASGI의 실행 환경 및 W3에서 접근할 endpoint·인증 설정
- duplicate/gap/conflict/replay/snapshot/cleared 재색인 공동 실행·재시작 증거

새 검증 근거는 `docs/w2-w3-local-http-pin-verification-2026-09-21.json`과
`docs/w2-w3-local-e2e-contract-response-2026-09-21.md`에 기록한다. 이전 W2 SHA의 불일치
증거는 이력으로 유지한다.

## 5. W1 실행·배포 입력

- workload host와 동일 SQLite DB를 공유하는 영구 volume
- 새 W3 full SHA를 고정한 image recipe, registry와 manifest digest
- `AWS_DEFAULT_REGION`, main/lifecycle command/lifecycle receipt queue URL, stable Role ID의 안전한 전달 경로
- 전용 main queue `SendMessage`와 queue policy 증거
- 5분 expire runner, 15분 DB 논리 삭제 SLO, HELD·scheduler 실패 경보
- metadata-only 로그 30일 lifecycle
- redacted/quarantined backup과 복제본의 deadline 기반 lifecycle 및 24시간 삭제 SLO
- 공동 CT12 실행 창, W1/W3 담당자, teardown 담당자

## 6. 제공 후 검증 순서

1. source pin과 schema를 fixture로 고정한다.
2. caller·Authority·삭제·registry adapter 정상/거부/timeout 테스트를 실행한다.
3. synthetic local 연결 후 격리 preflight를 실행한다.
4. 새 W3 full SHA로 immutable image를 빌드하고 source SHA와 manifest digest를 기록한다.
5. 실제 workload identity/config를 확인한 뒤 공동 CT12-01~12를 실행한다.
6. 미실행 항목은 `PENDING`/`NOT_RUN`으로 유지한다.
