# W2 private staged-result·commit-gate ACK proposal

상태: **W2 미채택 proposal / queue·runtime 미연결** (2026-09-18).
사용자 승인 독립 산출물이며 W1 채택 또는 양측 canonical transport를 뜻하지 않는다.
T059/T060, R2-03 및 공동 CT15 Gate는 닫지 않는다.

## 정본과 제안의 경계

W1 command 정본은 Service pin `afec08a9602132e5e433b523b0b6804850440524`의
`backend/contracts/w1/v1/private-w2-commit-gate.schema.json`이다.
Engine `tests/fixtures/w1_private_contract/manifest.json`과 upstream
`commit-gate-manifest.json` snapshot이 Git blob SHA·UTF-8/LF checksum을 보존한다.
PREPARE / FINALIZE / ABORT / PURGE의 immutable operation·command·job·owner·fence·
original deletion epoch·digest binding을 codec에서 보정하지 않는다.
operation revision은 연속 도착을 가정하지 않는다.
PURGE의 epoch는 original epoch보다 커야 하며 non-PURGE에서는 epoch 필드 자체가
금지된다(명시 null도 거부). 파싱 성공은 slot·lease·실행·commit 권한이 아니다.
정상 형식의 다른 binding/revision은 context-free codec으로 판단할 수 없으며
store의 immutable binding·revision·상태 검사가 필요하다.

다음 두 JSON Schema는 W2 제안이며 PRIVATE producer `w2`를 고정한다.

- `source-collection.staged-result.schema.json`:
  `schema_version` / `message_type` = `w2.private.staged-result.proposal.v1`.
  caller-persisted `message_id` / `occurred_at`, 원본 `command` / `result`,
  `result_digest`를 전달한다. 기존 CollectionResult union 또는 result queue에 넣지 않는다.
- `source-collection.commit-gate-ack.schema.json`:
  `schema_version` / `message_type` = `w2.private.commit-gate-ack.proposal.v1`.
  W1 command의 operation ID/revision, action, command/job/owner, fence, original
  epoch, digest 및 PURGE epoch를 유지하며 outcome은 `APPLIED` / `DUPLICATE` /
  `REJECTED`로 구분한다. W1 `issued_at`은 ACK 발생 시각이 아니므로 ACK에는 caller의
  `occurred_at`을 사용한다. 원 command의 `message_id` 대신 caller의 ACK ID를 사용한다.

두 builder는 delivery ID·시각을 생성하지 않는다. caller가 persistence 후 재전달 때
동일 ID·시각을 재사용해야 한다. ACK builder는 저장/commit/송신을 하지 않으며
transaction commit 뒤 송신 가능한 outbox 기록의 책임은 store에 있다.
`REJECTED` 또는 `DUPLICATE`를 W1 normalized 성공 ACK로 매핑하지 않는다.
실제 parser/routing·결과 callback·queue relay는 W1 채택 이후 별도 통합 작업이다.
`execution_lease_id`와 `operation_id`는 staged-result에 제공되지 않는다.
lease를 추정·생성하거나 W1 내부 조회를 가정하지 않는다.

W2 store의 현재 구현은 새 상태 전이에 `APPLIED` ACK를 기록하고, 재전달·동일
operation/revision 중복에는 최초 기록된 ACK의 ID/시각/outcome을 그대로 반환한다.
따라서 replay 응답을 새 `DUPLICATE` ACK로 바꾸지 않는다. 잘못된 binding·stale·
허용되지 않는 전이·저장 실패는 안전한 `CommitGateRejected`로 거부하며 `REJECTED`
ACK outbox를 자동 생성하지 않는다. `DUPLICATE`/`REJECTED`는 후보 schema의 허용
outcome이지 이 store가 각각 자동 발행한다는 약속이 아니다. Terminal 뒤의 과거
`APPLIED` ACK도 역사적 처리 증거일 뿐 현재 가시성·권한·새 FINALIZE 성공이 아니다.
W1 raw adapter는 이를 자신의 현재 operation/revision과 대조해야 한다.

## Digest 후보 규칙

`staged_result_digest(command, result)`는 `sha256:<lowercase 64 hex>`를 반환한다.
기존 Python 모델을 strict 재검증해 `model_copy` 변조, bool-as-int 및 비유한 수치를
거부한다. command/result의 command ID, job ID, source ID, input version을 먼저
결속하고 command fence가 `[1-9][0-9]*`인 W1 canonical positive decimal인지 검사한다.
Domain의 alpha fence를 변환하지 않으며 기존 same-transaction path는 바꾸지 않는다.

대상은 정확히 다음 객체의 모델 JSON 출력이다.

```python
{"command": command.model_dump(mode="json"), "result": result.model_dump(mode="json")}
```

JSON은 `sort_keys=True`, `separators=(",", ":")`, `ensure_ascii=False`,
`allow_nan=False`로 직렬화한 UTF-8 bytes다. 배열 순서를 보존하며 Unicode 정규화는
하지 않는다. envelope ID/시각·lease ID·operation ID는 대상에서 제외한다.
command/result 자체의 ID·입력/결과 버전·owner·fence·epoch 등은 그대로 포함된다.

Engine `tests/fixtures/w2_commit_gate_proposal/digest-vector.json`은 codec 구현 전
별도 Node `crypto` 및 recursive sorted-key JSON으로 고정한 합성 partial 입력이다.
한글, emoji 및 NFC/NFD 문자열을 포함하며 기대값은
`sha256:430a2e14098d3dd309f6bfb0a23f75e0e6fb5f6e80cc00ea8ff0dd81ecab0360`이다.
테스트는 production 출력으로 기대값을 역산하지 않는다.

## 검증 범위와 한계

JSON Schema의 구조·enum·추가 필드·UUID/RFC3339 검증과 모델의 strict parsing을
함께 사용한다. JSON Schema는 command/result cross-field equality, digest 재계산,
PURGE epoch 간 대소 비교를 표현하지 못하므로 모델 검사가 추가로 필수다.
JSON Schema integer는 수학적 정수 `1.0`을 수용할 수 있으나 codec의 strict JSON
integer는 float를 거부한다. UUID 및 시각 lexical 검증은 기존 W1 helper를 재사용한다.
오류는 payload를 echo/log하지 않는다.

fixture manifest는 정상, structural rejection, schema-valid semantic rejection을
분리한다. ACK fixture 각각은 독립적인 단일-message 예시이며 한 delivery stream이
아니다. APPLIED/DUPLICATE/REJECTED는 의미를 합치지 않는다.
Offline 테스트는 durable transaction, W1 currentness, 실제 queue 왕복, owner deletion
완료 또는 W1+W2 공동 원자성의 증거가 아니다.
