# Feature Specification: W1–W3 Actual Runtime Integration

**Feature Branch**: `007-w1-w3-runtime-integration`
**Created**: 2026-09-20
**Status**: Draft
**Input**: W1–W3 deployment integration milestones M1–M5 from
`md/deploy/W1_W3_Deployment_Integration_Response_2026-09-20.md`.

## User Scenarios & Testing

### User Story 1 - 배포 가능한 W3 runtime을 고정한다 (Priority: P1)

운영자는 검증된 W3 source revision으로 만든 immutable image를 private Worker EC2에서
기동하고, 모든 W3 process가 같은 single-host SQLite state를 사용하며 재시작 후에도
전달·revision·삭제 상태가 유지됨을 확인할 수 있다.

**Why this priority**: 실제 supplier와 relay를 연결하기 전에 배포 단위와 durable state가
고정돼야 이후 권한·삭제·재시작 증거가 같은 runtime을 가리킨다.

**Independent Test**: 고정 source로 image를 build하고 read-only/non-root container에서
`init`, 격리 `smoke`, `inspect`를 수행한 뒤 container를 재생성하여 같은 `/state/core.db`
상태가 유지되는지 확인한다.

**Acceptance Scenarios**:

1. **Given** 고정된 W3 source와 lockfile, **When** image를 build·게시하면, **Then** source
   full SHA와 ECR manifest digest가 한 배포 기록에 결속된다.
2. **Given** private Worker EC2의 하나의 named volume, **When** W3 명령 process가 순차 또는
   동시 실행되면, **Then** 모두 `/state/core.db`를 사용하고 별도 DB나 NFS를 만들지 않는다.
3. **Given** 초기화된 W3 state, **When** container를 재생성하면, **Then** 기존 counter,
   delivery metadata와 tombstone이 유지된다.

---

### User Story 2 - 실제 분석 caller를 현재 W1 권위와 결속한다 (Priority: P1)

실제 분석 pipeline은 required/optional Source 근거가 있는 `AnalysisPlan`만 W3에 공급하고,
W3는 supply·send·replay 직전에 W1의 현재 Job·Source·owner·deletion epoch를 조회해 stale,
취소 또는 삭제된 문맥을 fail-closed로 차단한다.

**Why this priority**: 실제 caller와 권위 adapter 없이 합성 plan을 live runtime에 연결하면
다른 owner 또는 과거 문맥의 판단을 전송할 수 있다.

**Independent Test**: 실제 caller/adapter revision을 고정한 뒤 valid, source mismatch,
cancel, deletion epoch mismatch, timeout/503를 주입하여 valid만 공급되고 나머지는 저장·전송
부작용 없이 차단되는지 확인한다.

**Acceptance Scenarios**:

1. **Given** W3-A/B로 고정된 actual caller와 Authority adapter, **When** current context를
   공급하면, **Then** stable idempotency key로 한 개의 pending event가 저장된다.
2. **Given** stale/cancel/delete/source mismatch context, **When** supply 또는 relay/replay를
   시도하면, **Then** SQS 전송과 신규 decision 생성 없이 거부된다.
3. **Given** Authority timeout 또는 인증 실패, **When** W3가 현재성을 확인하면, **Then**
   caller body를 대신 신뢰하지 않고 재시도 가능한 fail-closed 결과를 남긴다.

---

### User Story 3 - 삭제와 운영 lifecycle을 안전하게 처리한다 (Priority: P1)

사용자가 삭제를 확정하면 W1은 해당 owner와 deletion epoch를 W3 runtime에 내구성 있게
전달하고, W3는 해당 owner의 private event/request만 제거하면서 다른 owner와 공유 counter를
보존한다. 운영자는 relay/expire와 HELD를 관찰하고 승인되지 않은 복구를 수행하지 않는다.

**Why this priority**: deletion-first와 send-first 경쟁에서 개인 판단이 재전송되거나 다른
사용자의 공용 Source가 삭제되면 안 된다.

**Independent Test**: 삭제 신호 중복·재시작·일시 실패와 send 경쟁을 주입하여 W3 tombstone,
W1 currentness 차단, 다른 owner 무영향 및 durable retry를 검증한다.

**Acceptance Scenarios**:

1. **Given** W1 삭제 transaction이 확정된 상태, **When** dispatcher가 같은 owner/epoch를
   반복 전달하면, **Then** W3 결과는 idempotent하며 삭제가 재시작 후에도 유지된다.
2. **Given** 삭제가 send보다 먼저 확정, **When** relay가 실행되면, **Then** 신규 send는 없다.
3. **Given** send가 삭제보다 먼저 완료, **When** 메시지가 W1에 늦게 도착하면, **Then** W1
   currentness가 적용을 거부하고 개인 상태를 재노출하지 않는다.
4. **Given** retry가 소진된 delivery, **When** runner가 검사하면, **Then** `HELD`가 경보되고
   자동 replay는 수행되지 않는다.

---

### User Story 4 - 실제 IAM/SQS 최소 권한을 고정한다 (Priority: P1)

운영자는 actual W3 workload role이 지정된 Main Queue에만 송신할 수 있고 W1 worker만
수신·삭제·visibility 변경을 수행하며 양측이 같은 stable role ID를 검증함을 확인할 수 있다.

**Why this priority**: body의 producer 문자열은 인증이 아니며 잘못된 role 또는 과도한 AWS
권한은 private event 경계를 무효화한다.

**Independent Test**: actual role로 STS identity와 Queue attributes/send를 검사하고,
다른 role·변조 body·receive/delete/purge 시도가 거부되는지 확인한다.

**Acceptance Scenarios**:

1. **Given** actual W3 workload role, **When** preflight를 수행하면, **Then** STS Role ID,
   W3 expected ID와 W1 expected SenderId가 일치한다.
2. **Given** W3 workload, **When** AWS 권한을 검사하면, **Then** 지정 Main Queue의
   `SendMessage` 외 receive/delete/purge 또는 W1 DB/secret 접근은 허용되지 않는다.

---

### User Story 5 - actual W3에서 W1까지 공동 CT-12를 종료한다 (Priority: P1)

통합 담당자는 actual W3 supplier/outbox/relay가 보낸 판단이 SQS를 거쳐 W1 PostgreSQL에
원자 적용되고, 중복·재시작·장애·취소·삭제·명시적 retry에서도 늦은 개인 쓰기나 자동 실행이
없음을 재현 가능한 증거로 확인한다.

**Why this priority**: M1–M4는 준비 완료일 뿐 실제 서비스 연동 완료 증거가 아니다.

**Independent Test**: 양측 full SHA/image digest/role ID를 고정한 격리 환경에서 CT12-01~12를
실행하고 W3 inspect, SQS outcome, W1 row/action/command 수 및 teardown manifest를 대조한다.

**Acceptance Scenarios**:

1. **Given** M1–M4 완료 환경, **When** actual Core/Non-Core plan을 공급하면, **Then** W1에는
   승인된 판단만 한 번 적용되고 명시적 retry 전 command는 0건이다.
2. **Given** duplicate, restart, ACK-loss, SQS/W1 DB failure, **When** delivery가 반복되면,
   **Then** 동일 event는 한 번만 적용되고 retryable failure는 유실되지 않는다.
3. **Given** wrong role, modified body, cancel/delete race, **When** delivery가 발생하면,
   **Then** stale/unauthorized effect는 0건이고 다른 owner의 공용 Source는 유지된다.
4. **Given** 사용자의 명시적 retry, **When** 새 실행을 시작하면, **Then** 새 fence command가
   정확히 1건 생기고 이전 fence의 늦은 결과는 거부된다.

### Edge Cases

- 같은 event ID/body의 재전송과 같은 ID/다른 body 충돌을 구분한다.
- Authority가 401/403/404/409/503 또는 timeout을 반환해도 caller body로 우회하지 않는다.
- W3 relay가 SQS 성공 후 SQLite commit 전에 종료돼도 같은 bytes 재전송으로 수렴한다.
- 삭제 dispatcher가 중복 실행되거나 더 낮은 deletion epoch를 전달해도 상태가 후퇴하지 않는다.
- `w3.retention/1.1`은 PENDING/RETRY/HELD 본문을 절대 최대 기한에, `TRANSPORT_HANDOFF` 본문을
  handoff 기한과 절대 최대 중 이른 시점에 안전하게 제거하되 delivery metadata를 즉시 없애지 않는다.
- raw SQLite DB, Docker volume 또는 filesystem/EBS snapshot을 live primary로 복원하지 않는다.
- W3 actual role과 T043 합성 sender role을 혼용하지 않는다.

## Requirements

### Functional Requirements

- **FR-001**: W1은 독립 clone의 W3 runtime 구현 SHA
  `3b23e0843a134fb341e6a256576ccf52fedbf4a8`와 locked dependencies로 재현 가능한 container
  image recipe를 제공하고 receipt HEAD `34660343f197c74cc03459a93e0160e46adbcd2b`를 별도 기록해야 한다.
- **FR-002**: W3 image는 non-root로 실행되고 root filesystem은 read-only이며 writable state는
  `/state` named volume과 제한된 tmpfs에만 존재해야 한다.
- **FR-003**: 모든 W3 runtime process는 같은 host의 `/state/core.db`를 사용해야 하며 다중 host,
  NFS 또는 독립 SQLite writer topology를 지원된 구성으로 표시해서는 안 된다.
- **FR-004**: W1은 image의 source SHA, registry repository, manifest digest, smoke와 restart
  결과를 기록하고 W3-D review 전후 차이를 추적해야 한다.
- **FR-005**: actual AnalysisPlan caller는 repository/full SHA, 파일/함수, 호출 시점,
  required/optional 권위 근거와 stable idempotency key 규칙으로 고정돼야 한다.
- **FR-006**: W1 private Authority 경계는 `job_id + source_id`에 대해 current context, owner ID,
  owner deletion epoch와 active만 인증된 caller에게 반환하고 private 본문/DB credential을 노출하지 않아야 한다.
- **FR-007**: W3 adapter는 supply/send/replay 직전에 Authority를 조회하고 인증·timeout·stale
  실패를 fail-closed로 처리해야 한다.
- **FR-008**: W1은 삭제 확정과 함께 durable deletion dispatch를 생성하고 W3 `delete_owner`에
  적어도 한 번 전달하되 owner/epoch 단위로 idempotent하게 수렴해야 한다.
- **FR-009**: 삭제 dispatcher는 다른 owner나 공유 company/Source revision counter를 제거해서는
  안 되며, W1 Source registry가 영구 폐기를 확정한 경우에만 W3 `retire_source`를 내구성 있게
  호출해야 한다. 일시 unavailable/unknown은 영구 폐기로 변환해서는 안 된다.
- **FR-010**: 운영 runner는 bounded relay retry, expire와 inspect를 실행하고 `HELD`를 경보하되
  사람의 판정 없이 자동 replay하지 않아야 한다.
- **FR-011**: runtime은 승인된 `w3.retention/1.1`의 handoff 14일, private body 절대 최대 30일,
  terminal metadata/retired counter 90일, owner tombstone 365일, quarantine backup 최대 30일을
  변경 없이 적용하고 replay가 절대 최대 기한을 연장하지 못하게 해야 한다.
- **FR-012**: W3 workload IAM role은 지정 Main Queue의 `SendMessage`만 허용하고 W1 worker role은
  수신·삭제·visibility 변경에 필요한 최소 권한만 가져야 한다.
- **FR-013**: W3 STS Role ID, W3 expected role ID와 W1 expected SenderId는 동일해야 하며 body
  producer 문자열을 인증으로 사용해서는 안 된다.
- **FR-014**: runtime secret과 Queue URL/Role ID/DB URL은 Git/image/log가 아니라 root-owned
  mode `600` 설정 또는 승인된 secret 채널로 전달해야 한다.
- **FR-015**: 공동 CT-12는 actual W3 supplier/outbox/relay, actual AWS SQS, W1 consumer와
  PostgreSQL을 사용해야 하며 W1 합성 sender만으로 완료를 주장해서는 안 된다.
- **FR-016**: 공동 CT-12는 Core, Non-Core, duplicate, same-ID mutation, restart/ACK-loss,
  wrong principal, SQS/DB failure, cancel, delete, explicit retry와 stale late effect를 검증해야 한다.
- **FR-017**: 명시적 retry 전 W1 command/outbox는 0건이어야 하고 retry 후 새로운 fence command는
  1건이어야 한다.
- **FR-018**: 종료 증거는 양측 source SHA/image digest/role identity, W3 inspect counts, SQS outcome,
  W1 row/action/command counts와 cleanup 결과를 포함해야 한다.
- **FR-019**: M2 actual cutover는 W3-A/B, M3 dispatcher closure는 W3-C, M4는 M1/M2, M5는
  M1–M4와 W3-F가 충족되기 전 READY 또는 완료로 표시해서는 안 된다. W3-E retention policy는
  `w3.retention/1.1` 구현 pin으로 VERIFIED 상태다.
- **FR-020**: W2 CT-15, W4 Question Core CT-12와 Neo4j 저장소 선택은 이 기능의 완료 조건이 아니다.

### Key Entities

- **W3 Runtime Deployment**: pinned source, immutable image digest, runtime configuration과 single-host
  state volume을 결속하는 배포 단위다.
- **Authority Snapshot**: W1이 인증된 현재 상태에서 반환하는 context, owner, deletion epoch와 active 판정이다.
- **Deletion Dispatch**: W1 삭제 확정에서 생성되어 W3 `delete_owner`로 전달되는 owner/epoch 기반 내구성 작업이다.
- **W3 Delivery State**: W3 SQLite의 PENDING, RETRY, TRANSPORT_HANDOFF, HELD 또는 DELETED 상태다.
- **Joint CT-12 Evidence Manifest**: 양측 revision, identity, scenario outcome, count와 teardown을 결속하는 증거다.

## Success Criteria

- **SC-001**: 동일 image digest의 container 재생성 10회에서 `/state/core.db` state 유실은 0건이다.
- **SC-002**: stale/cancel/delete/source mismatch 및 Authority 장애 각 20회에서 SQS send와 신규 W1
  판단 부작용은 0건이다.
- **SC-003**: deletion duplicate/restart/race 100회에서 삭제 owner의 재전송·재노출과 다른 owner의
  데이터 삭제는 각각 0건이다.
- **SC-004**: actual W3 workload의 허용되지 않은 receive/delete/purge/W1 DB·secret 접근 성공은 0건이다.
- **SC-005**: CT12-01~12에서 승인된 event의 중복 적용은 0건이고 retryable failure 유실은 0건이다.
- **SC-006**: 모든 공동 시나리오에서 명시적 retry 전 자동 command는 0건이며 retry 후 새 fence command는
  정확히 1건이다.
- **SC-007**: cancel/delete 이후 stale late effect와 개인 상태 재노출은 0건이다.
- **SC-008**: 공동 검증 종료 시 W3/W1/SQS 증거 count가 일치하고 disposable DB/queue/state cleanup이
  증거 manifest에 기록된다.

## Assumptions and External Gates

- W3 implementation pin `3b23e084...`, receipt HEAD `34660343...`와 기존 wire contract
  `w3.private.core-decision/0.1-candidate`는 변경하지 않는다.
- W1 W3 inbound consumer와 T043 격리 검증은 완료됐으며 이 기능은 actual W3 runtime 연결에 집중한다.
- M1은 즉시 착수 가능하지만 최종 digest 확정 전 W3-D review가 필요하다.
- M2 actual cutover는 W3-A/B, M3 dispatcher closure는 W3-C, M5는 W3-F 공동 실행자가 필요하다.
- 실제 분석 caller의 소유 서비스는 현재 미정이며 구현에서 추측하지 않는다. Production retention
  숫자는 `w3.retention/1.1`로 확정됐으므로 다른 환경값으로 재정의하지 않는다.
