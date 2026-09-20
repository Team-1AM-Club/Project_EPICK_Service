# W3 보관·삭제 정책 승인 요청안

> **SUPERSEDED:** PM이 2026-09-20 `w3.retention/1.1`로 수정 승인했다.
> 이 문서의 `w3.retention/1.0`을 운영 정책으로 사용하지 않는다. 승인된 정본은
> `docs/superpowers/specs/2026-09-20-w3-retention-1.1-design.md`다.

- 상태: `PROPOSED / PM_APPROVAL_PENDING`
- 제안 정책 revision: `w3.retention/1.0`
- 작성일: 2026-09-20 (Asia/Seoul)
- 적용 대상: W3 Core Decision runtime의 private event/request, 전달 metadata, 삭제 tombstone, revision counter, 운영 로그, 지원 backup
- 승인자: PM

## 1. 승인 요청 요약

다음 기간과 처리 규칙을 `w3.retention/1.0`으로 승인해 주시기를 요청합니다.
승인 전에는 production 기본값으로 간주하지 않으며, 승인 후 구현·테스트·배포가 완료된 시점부터 적용합니다.

| 대상 | 권고 기간 | 기산점 | 만료 동작 | 산정 근거 |
|---|---:|---|---|---|
| `TRANSPORT_HANDOFF`된 private event/request 본문 | 14일 | SQS `SendMessage` 성공을 기록한 `published_at` | 경계 시각 포함(`now >= published_at + 14일`) 즉시 본문과 원 요청을 제거하고 metadata state를 `EXPIRED`로 변경 | SQS 장애·재처리 조사 기간을 확보하면서 private payload의 장기 보관을 막는다. SQS 최대 보관기간과 같은 상한을 사용해 양쪽 복구 창을 일치시킨다. |
| `PENDING`·`RETRY` private event/request 본문 | 30일 | 최초 W3 event의 `occurred_at` | 자동 전송을 중단하고 본문·원 요청을 제거한 뒤 `DELIVERY_EXPIRED`로 종결, 운영 경보 발생 | 아직 전달되지 않은 자료를 즉시 없애지 않되, 외부 장애로 무기한 보관되는 것을 방지한다. 일반 전송 재시도 8회보다 충분히 긴 수동 복구 창이다. |
| `HELD` private event/request 본문 | 30일 | `HELD`로 최초 전환된 시각 | 본문·원 요청 제거, `DELIVERY_EXPIRED` 종결, 미해결 운영 경보 유지 | 자동 재시도가 끝난 자료에는 명시적인 운영 결정을 요구하며 무기한 보관하지 않는다. |
| 종료 delivery metadata(event ID, digest, request hash, 상태, 횟수, 시각) | 90일 | `EXPIRED`·`DELIVERY_EXPIRED`·`DELETED`·`BLOCKED` 중 하나로 전환된 시각 | delivery metadata 행 제거 | 공동 CT12, 중복·충돌·삭제 이행 조사에 필요한 제한된 감사 창을 제공한다. payload는 포함하지 않는다. |
| owner 삭제 tombstone(owner hash, deletion epoch) | 365일 | 신뢰된 삭제 명령이 성공한 시각 | tombstone 제거. 제거 뒤 같은 owner ID 재사용은 금지 | 삭제 직후 지연·중복 명령이나 오래된 공급이 자료를 되살리는 것을 막는다. 가명 hash도 개인정보 가능성이 있으므로 영구 보관하지 않는다. |
| Company·Source별 monotonic revision counter | 해당 Source 운영기간 + 종료 후 90일 | Source 비활성화가 확정된 시각 | counter 제거. 같은 Source ID 재사용 금지 | 재전송과 DB 재기동 후 revision 역행을 방지하는 공용 무결성 metadata다. Source 종료 뒤 감사 창을 지난 후 제거한다. |
| metadata-only 운영 로그 | 30일 | 로그 발생 시각 | 로그 저장소 정책으로 자동 삭제 | 장애 분석과 최소한의 운영 추적에 충분한 기간으로 제한한다. event/request 본문, owner ID, 예외 전문은 기록하지 않는다. |
| W3 지원 backup(본문 제거·quarantine된 backup) | 30일 | backup 생성 완료 시각 | backup 객체와 복제본 삭제 | counter·tombstone 점검 및 단기 복구 검증 창을 제공한다. 지원 backup에는 복구 가능한 private payload가 없어야 한다. |

14일 기준은 AWS가 문서화한 SQS `MessageRetentionPeriod` 상한인 1,209,600초와 일치한다.
근거: <https://docs.aws.amazon.com/AWSSimpleQueueService/latest/APIReference/API_CreateQueue.html>

## 2. 기산점과 경계 규칙

1. 모든 시각은 UTC로 저장하고 비교한다.
2. 기간은 초 단위의 고정 경과시간으로 계산한다. 달력상의 날짜 변경이나 Asia/Seoul 자정에 맞추지 않는다.
3. 만료 경계는 포함한다. 즉 `now >= start_at + retention`이면 만료 대상이다.
4. replay가 명시적으로 승인되어 같은 event를 다시 전송한 경우 private event/request의 14일 기산점은 마지막 성공 `published_at`으로 갱신한다.
5. replay는 event ID, body, revision을 바꾸지 않으며 새로운 판단으로 취급하지 않는다.
6. 정책 revision 변경은 이미 생성된 row에도 적용한다. 단, 새 정책 적용 직후 이미 만료된 row는 최초 purge 작업에서 제거하고 그 실행 결과를 증적으로 남긴다.

## 3. 삭제 우선순위와 예외

신뢰된 사용자 삭제 명령은 위 기간보다 우선한다. 관측 owner epoch보다 높은 deletion epoch가 확인되면 상태가 `PENDING`, `RETRY`, `HELD`, `TRANSPORT_HANDOFF`인지와 관계없이 해당 owner의 event/request 본문을 즉시 제거하고 state를 `DELETED`로 변경한다.

다음 항목만 본문 삭제 후 제한적으로 남길 수 있다.

- owner hash와 deletion epoch: 오래된 공급의 재생성을 막기 위해 365일 보관한다.
- event ID, digest, request hash 및 삭제 상태: 삭제 이행·중복 조사 목적으로 90일 보관한다.
- Company·Source revision counter: 다른 owner의 revision 역행 방지를 위해 Source 운영기간 동안 보관한다.

법적 보존 명령이나 사고 조사로 기간 연장이 필요한 경우 PM 단독 판단으로 자동 연장하지 않는다. 대상, 사유, 승인자, 시작·종료 시각, 해제 조건을 별도 기록한 예외 revision을 발행해야 한다. 예외 상태에서도 접근은 지정 운영자에게만 허용하며, 사용자 삭제 대상의 private event/request 본문을 backup으로 우회 보존하지 않는다.

`PENDING`·`RETRY`·`HELD`는 전달 확인 전까지 14일 성공 보관기간의 적용 대상이 아니지만, 30일 절대 상한의 적용 대상이다. 전송 실패 상태라는 이유로 무기한 보관하지 않는다.

지원 backup은 생성 전에 event/request 본문을 메모리 snapshot에서 제거하고 quarantine한다. raw SQLite DB 또는 filesystem snapshot은 지원 backup이 아니며 production 복원에 사용하지 않는다. 이미 외부 storage snapshot에 포함된 삭제 대상의 물리 제거 시점은 storage lifecycle과 복제 정책으로 별도 증명해야 한다.

## 4. 정책 revision과 적용 시점

- 승인 대상 revision: `w3.retention/1.0`
- 승인 시각: PM이 문서 또는 이 revision을 명시해 승인한 시각
- 효력 발생 시각: 승인 이후 아래 적용 조건을 모두 만족한 production 배포의 완료 시각
  1. runtime 설정에 정책 revision과 승인된 기간을 고정
  2. `PENDING`·`RETRY`·`HELD` 절대 상한과 terminal metadata/tombstone 만료 구현
  3. UTC 기산점과 terminal transition 시각을 저장하도록 DB migration 완료
  4. 만료·사용자 삭제·replay·backup lifecycle 테스트 통과
  5. 운영 scheduler, 경보, 삭제 결과 증적 경로 연결

승인과 효력 발생 사이에는 현재 runtime의 임의 기간을 production 정책으로 사용하지 않는다. 배포 완료 시 기존 데이터에 대한 dry-run 집계와 최초 purge 결과를 기록한다.

기간이나 기산점·예외·삭제 동작을 바꾸면 patch가 아니라 새 정책 revision을 발행한다. 문구 보정처럼 실행 의미가 바뀌지 않는 수정만 `1.0.x`로 관리한다.

## 5. 승인 후 구현 영향

현재 코드는 다음 범위까지만 구현되어 있다.

- `TRANSPORT_HANDOFF`의 마지막 `published_at + retention_seconds`에서 본문/request 제거
- 신뢰된 높은 deletion epoch에 의한 owner 본문/request 즉시 제거
- 본문이 제거되고 quarantine된 지원 backup 생성

`w3.retention/1.0` 승인 후에는 아래 작업이 추가로 필요하다.

- 단일 `retention_seconds`를 정책 revision 기반 대상별 설정으로 확장
- `created_at`, `held_at`, `terminal_at`, `deleted_at`, `source_retired_at`, `backup_created_at` 기산점 저장
- `PENDING`·`RETRY`·`HELD` 30일 절대 상한 처리와 운영 경보
- terminal delivery metadata 90일, owner tombstone 365일, Source 종료 counter 90일 purge
- backup 저장소 lifecycle 30일 및 복제본 삭제 증적
- inspect 결과에 payload 없이 적용 policy revision과 다음 만료시각 노출

## 6. PM 승인 회신 형식

```text
승인 결과: APPROVED | CHANGES_REQUESTED
정책 revision: w3.retention/1.0
승인 시각: <ISO 8601 UTC>
승인자: <name/role>
변경 요청: <없음 또는 대상별 수정 기간·규칙>
```
