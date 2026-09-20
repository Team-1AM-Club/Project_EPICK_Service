# W3 lifecycle dispatcher 전달

상태: **LOCAL_VERIFIED / W1_INFRA_AND_ADOPTION_PENDING / LIVE_E2E_NOT_RUN**

W3 구현 pin: `66a0e3e1b087bf7f9d1b6d7730934ea27f55e94b`

보존 정책: `w3.retention/1.1`

## 구현된 경계

- W1→W3 private Standard SQS command를 한 건씩 소비한다.
- AWS SDK workload credential을 사용하고, 시작 시 W3 실제 STS Role ID를 확인한다.
- SQS `SenderId`의 stable Role ID가 승인된 W1 Role ID와 같은지 확인한다.
- owner 삭제와 Source 영구 종료 명령을 strict schema로 검증한다.
- 상태 변경, command digest, 결과 receipt, receipt outbox를 동일 SQLite transaction에 기록한다.
- 원문 command body는 저장하지 않는다. 동일 `command_id`에 다른 내용이 오면 terminal conflict로 거부한다.
- receipt SQS `SendMessage`가 HTTP 200과 `MessageId`를 반환한 뒤에만 원본 command를 ACK한다.
- receipt 송신이나 로컬 DB 처리가 불확실하면 원본 command를 삭제하지 않는다.
- 재시작·redelivery 시 저장된 receipt의 동일 바이트를 재전송한다.
- 재시작 시 미전달 receipt를 모두 처리하기 전에는 신규 command를 수신하지 않는다.
- 동일 `command_id`의 payload 충돌은 receipt 없이 terminal reject한다.

## 계약 파일

| 방향 | 파일 | 핵심 결속 |
|---|---|---|
| W1→W3 owner 삭제 | `contracts/core-runtime/owner-deletion-command.schema.json` | `command_id`, `owner_id`, `owner_deletion_epoch`, `target_type`, `target_ref` |
| W1→W3 Source 종료 | `contracts/core-runtime/source-retirement-command.schema.json` | `command_id`, `company_id`, `source_id`, 최초 `retired_at`, `target_type`, `target_ref == source_id` |
| W3→W1 결과 | `contracts/core-runtime/lifecycle-receipt.schema.json` | `command_id`, `target_ref`, operation, `APPLIED/DUPLICATE/STALE`, 적용 epoch 또는 효력 시각 |

각 schema의 정상 예시는 `contracts/core-runtime/examples/`에 있다. `retire_source`는
`SourceRetirementCommand`를 입력으로 받아 `command_id`가 없는 호출을 허용하지 않는다.

## 처리 의미

### owner 삭제

- 더 높은 epoch: `APPLIED`; 해당 owner의 private body를 같은 transaction에서 제거한다.
- 동일 epoch: `DUPLICATE`.
- 더 낮은 epoch: `STALE`.
- tombstone의 최초 삭제 시각은 이후 중복·상위 epoch 처리로 연장하지 않는다.

### Source 종료

- 처음 받은 authoritative `retired_at`: `APPLIED`.
- 같은 `retired_at`: `DUPLICATE`.
- 이미 고정된 값과 다른 `retired_at`: `STALE`; 최초 시각을 유지한다.
- 종료 후 새 supply를 거부하고, counter 삭제 기산점은 최초 `retired_at`이다.
- `target_ref`는 W1의 `Source.id`, 즉 `source_id`와 같은 값만 허용한다.

`TRANSPORT_HANDOFF`는 receipt queue에 전달했다는 뜻이며 W1의 최종 수용을 뜻하지 않는다.
전달 완료 lifecycle metadata는 정책의 terminal metadata 기간 뒤 command ledger와 receipt outbox에서
함께 제거된다.

## 실행 진입점

```text
python -m w3_knowledge.core_runtime_cli lifecycle-once \
  --db /state/core.db \
  --retention-seconds 1209600 \
  --consume
```

필수 설정 이름:

- `AWS_DEFAULT_REGION`
- `W3_CORE_DECISION_EXPECTED_ROLE_ID`: 실행 중인 W3 workload의 stable Role ID
- `W3_LIFECYCLE_EXPECTED_W1_ROLE_ID`: 승인된 W1 sender의 stable Role ID
- `W3_LIFECYCLE_COMMAND_QUEUE_URL`: W1→W3 private Standard queue
- `W3_LIFECYCLE_RECEIPT_QUEUE_URL`: W3→W1 private Standard queue

`--consume`이 없으면 명령은 실패한다. 실제 값은 저장소나 전달문서에 기록하지 않는다.

## 검증 근거

- 전체 테스트: `274 passed, 1 skipped`
- lifecycle/retention 관련 테스트: `25 passed`
- Ruff lint: 통과
- Ruff format check: 통과
- core-decision export: 12 files, drift 0
- C-01 export: 20 files, drift 0
- restriction export: 19 files, mismatch 0
- live provider 1건: 명시적 허용·설정이 없어 미실행

검증에는 같은 command 재전달, payload 충돌, Source 최초 종료 시각, SQLite outbox 실패 전체 롤백,
재시작 후 동일 receipt bytes, receipt 송신 불확실 시 비ACK, DB 오류 시 비ACK, 발신자 불일치
거부, ACK 순서, metadata 만료가 포함된다.

## W1이 제공·확정할 항목

1. W1 owner deletion command의 `target_type`에 `W3_CORE_RUNTIME`을 채택하고, Source
   `target_ref = Source.id` 의미를 포함해 위 schema를 pin한다.
2. 두 Standard queue URL, queue policy, W1/W3 stable Role ID와 workload 설정 전달 경로를 확정한다.
3. W3 receipt schema를 pin하고 W1 측 idempotent receipt consumer와 최종 수용 상태를 구현한다.
4. 이 W3 full SHA를 image에 고정한 뒤 Queue/Role을 주입해 end-to-end 증적을 실행한다.

현재 저장소에는 실제 Queue/Role 값이 없으므로 AWS 수신·송신과 end-to-end는 실행하지 않았다.
