# W3 실제 연결 입력 요청

상태: **INPUTS_REQUIRED / LOCAL_RETENTION_IMPLEMENTATION_AVAILABLE / LIVE_INTEGRATION_NOT_STARTED**  
기준 정책: `w3.retention/1.1`  
W3 baseline: `c7e6788168c048941bdabe7ed8cb01007edeecec` 이후 정책 구현 branch

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

| 항목 | 필요한 값 |
|---|---|
| source pin | 실제 command consumer의 저장소 SHA와 파일·함수 경로 |
| 전달 방식 | endpoint/queue/in-process adapter와 인증 방식 |
| schema | command ID, owner ID, deletion epoch, target type/ref의 정확한 의미 |
| epoch | higher-epoch 판정의 authoritative source와 등록 전 삭제 규칙 |
| 멱등성 | 중복·지연·순서 역전 command 처리와 ACK/error 규칙 |
| 재처리 | dispatcher 실패·runtime 재시작 뒤 redelivery 방식 |

검증된 높은 epoch 삭제만 `CoreRuntime.delete_owner(..., now=trusted_clock)`로 연결한다. 사용자 삭제는
상태·보관기한보다 우선하고 transaction 안에서 본문을 제거한다.

## 4. Source registry

- 영구 retired와 일시 제한·접근 불가·unknown을 구분하는 authoritative 경로
- `company_id`, `source_id`, 영구 종료 시각 및 동일 Source ID 재사용 금지 보장
- 조회 인증, timeout, not-found/error 의미와 source pin
- 영구 종료를 `CoreRuntime.retire_source`에 전달하는 adapter/재처리 규칙

로컬 retired marker와 counter purge는 구현되어 있다. 실제 registry adapter 없이는 실환경 완료로 표시하지 않는다.

## 5. W1 실행·배포 입력

- workload host와 동일 SQLite DB를 공유하는 영구 volume
- 새 W3 full SHA를 고정한 image recipe, registry와 manifest digest
- `AWS_DEFAULT_REGION`, main queue URL, stable expected Role ID의 안전한 전달 경로
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

