# W1 → W2: T050 삭제 v2 운영 입력 회신

대상: W2의 `w2-w1-t050-deletion-runtime-inputs-2026-09-27.md` 및 `w2-w1-t050-private-deletion-local-handoff-2026-09-27.md`. 이 회신은 운영 경계와 남은 공동 검증을 구분한다. T050/T058 완료·production 삭제 라우팅 승인이 아니다. W2 로컬 구현 기준은 `76ae299101c7b2b8cb1b0d21a02ffe077a2f8ab5`, W1 작업 기준은 이 문서를 포함하는 `codex/008-phase4-us2` 커밋이다. W1 운영 입력의 기계 판독 가능한 정본은 `backend/infra/w2-deletion-runtime-inputs.template.json`이다. 현재 W1 manifest의 W2 소스 pin은 `3700b8dc324b4a365b19214550bd66b74171870b`로 잠정 상태다. 이는 W2 인계 SHA `76ae299...`의 이미지·배포 pin 완료를 뜻하지 않으며, W2 최종 inspect 구현 SHA와 immutable 이미지 digest를 받은 뒤 함께 갱신해야 한다.

## W2 요청 1 — 전용 큐와 권한

W1이 전용 command queue와 DLQ, SQS SSE, redrive 5회, W1 발행/W2 수신·삭제·visibility 변경 권한을 소유한다. W2는 W1의 설정 템플릿에 명시된 SSM 경로에서 main URL과 DLQ ARN을 읽는다. W2 workload role의 identity policy 및 queue policy 소유자는 W1이다. W2는 정확한 `200 ACKNOWLEDGED` 전에 메시지를 삭제하지 않는다. 실제 URL·ARN·계정번호는 이 문서에 넣지 않는다. 배포 시 approved SSM 및 IAM에서 실제 값을 대조해야 한다. W2 consumer는 수신 visibility 60초 고정·자동 연장 없음이므로 공동 실행에서 처리시간, 재전달 및 DLQ를 확인해야 한다.

## W2 요청 2 — private ACK callback

W1이 private HTTPS ALB/CA와 인증된 `POST /internal/v1/w2-private/deletion/ack`를 제공한다. W2는 승인된 SSM origin/CA 경로 및 Secrets Manager bearer 경로에서 읽고 `X-EPICK-Service-Principal: w2`를 전송한다. W1이 bearer rotation을 소유하며 순서는 W2 consumer 중지 → secret 갱신 → W1 callback 재시작 → W2 consumer 재시작이다. W2 실행 보안 그룹만 내부 ALB 443에 진입한다. W1은 이전 staging 확인에서 CA 검증, readiness 200, 무인증 401, 잘못된 principal 403, 존재하지 않는 target의 409를 관찰했다. 이것은 실제 삭제 ACK 성공 증거가 아니다. 실제 주소·토큰·인증서는 문서/로그/fixture에 남기지 않는다.

## W2 요청 3 — 반복 409, DLQ 및 재발행

담당자는 **W1: 큐·DLQ·W1 삭제 authority·재시도 승인**, **W2: consumer·W2 영속 receipt·ACK 전송 증거**다. W2는 `200 ACKNOWLEDGED` 외 응답에서 SQS 삭제·성공 기록을 하지 않는다. 503/timeout은 메시지를 보존하여 재시도한다. 401/403은 인증 설정을 중단·수정하며 메시지를 보존한다. 409의 여섯 W1 코드 및 422는 실패 메시지를 보존하고, 반복 수신으로 DLQ에 도달하면 다음 순서로 수동 조사한다.

1. 영향받은 삭제 route만 중단하고 DLQ 메시지를 보존한다. DLQ 전체 redrive, 무검증 `SendMessage`, 비밀·개인 payload 출력은 금지한다.
2. W2가 해당 `deletion_id`의 영속 receipt와 ACK 결과, count-only DLQ 상태를 W1에 전달한다. W1은 callback 오류 코드·상관관계, request/target/outbox의 owner·scope·epoch·상태와 현재 소유권을 승인된 운영 접근으로 대조한다.
3. 양측에서 이미 ACK 완료 또는 stale epoch·owner/scope 불일치가 확인되면 재발행하지 않고 격리·에스컬레이션한다. W2 receipt/ACK가 불명확해도 재발행하지 않는다.
4. 원인이 수정 가능하고 W2 receipt와 W1 currentness가 일치할 때만 지정된 W1 운영자가 승인된 트랜잭션에서 해당 target에 `record_target_failure` 후 `retry_target`을 **1회** 실행한다. 이 경로는 같은 target/`deletion_id`와 같은 v2 payload를 새 outbox에 담는다. W1 릴레이의 기존 정확한 body·owner·epoch 검증 및 activation preflight를 거쳐 전용 큐로 발행한다. DLQ 본문 자체를 다시 밀어 넣지 않는다.
5. 새 outbox ID, 동일 `deletion_id`, W2 receipt/ACK 최종 상태, DLQ 원본의 처분을 양측 기록에 맞춘 후 영향 route를 재개한다. 이 승인된 운영 호출·공동 검증이 준비되지 않았으면 DLQ는 격리 상태로 둔다.

W1 코드 근거: `DeletionOrchestrationService.record_target_failure/retry_target`, `OutboxRelay._serialize_w2_private_deletion_v2`, `w2_deletion_manual_retry.stage_reconciled_w2_deletion_retry`, `scripts/retry_w2_deletion_after_dlq.py`, 그리고 삭제 전용 DB 계정에 W2 삭제 outbox만 허용하는 migration `046_w2_deletion_outbox_rls`이다. W1 callback은 409/503 코드와 삭제 ID의 SHA-256 참조만 진단 로그에 남기고 원문 ID·토큰은 남기지 않는다. 격리 PostgreSQL에서 W1 관련 migration·삭제 경로·callback·relay·수동 재시도 회귀 **59건이 통과**했다. 이는 실제 운영 409 → DLQ → 수동 재발행의 공동 실행 증거가 아니며, 해당 경로는 아직 운영에서 활성화하지 않았다.

## W2 요청 4 — health/inspect

인증된 HTTP health/inspect endpoint는 **요구하지 않는다**. W2 삭제 consumer에 대해서는 CLI `preflight` 결과, 프로세스 생존, main/DLQ 접근 및 DB 연결에 더해 count-only `pending_deletion_count`, `pending_ack_count`가 필요하다. 두 값은 음이 아닌 정수여야 하며 owner ID, payload, token, URL은 출력하지 않는다. 현재 W2 삭제 operator에는 `preflight|consume-once|run`만 있어 이 count-only inspect 인터페이스는 **아직 없다**. W2가 전용 count-only CLI 또는 동등한 승인된 운영 검사로 제공하고 W1과 출력 의미·이름을 맞춰야 한다. 기존 commit-gate의 `inspect-run` 또는 general source runtime helper를 삭제 consumer의 구현으로 간주하지 않는다.

## 공동 검증 전 남은 조건

- W2 최종 source SHA, DB head `0012_private_ack_wire_digest`, immutable 이미지 digest를 독립 pin하고 격리 환경에 배포한다. 현재 W1 manifest의 잠정 pin과 W2 인계 SHA가 다르므로 활성화 증거로 사용하지 않는다. W1도 현재 작업 변경을 포함한 immutable 이미지를 빌드·pin해야 한다.
- W2 count-only inspect와 W1 수동 재시도 운영 호출을 격리 환경에서 확인한다. W1의 로컬 격리 DB 회귀 59건은 통과했지만 실제 activation proof를 이용한 운영 호출 증거는 아직 없다.
- 양측에서 `APPLIED`·`DUPLICATE`·`STALE`, 409/503/timeout, 60초 visibility 경계, restart/redelivery/DLQ와 W1/W2 count·receipt·ACK 일치를 함께 기록한다.
- 공동 결과를 확인하기 전에는 production 삭제 라우팅을 활성화하지 않는다. RenderedCollector/robots gate는 별도 미완료 항목이다.
