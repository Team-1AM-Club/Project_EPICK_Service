# W3 실제 공급·SQS relay·보관/삭제 구현 인계

상태: **LOCAL_VERIFIED / DEPLOYMENT_AND_AUTHORITY_INTEGRATION_PENDING / JOINT_CT12_NOT_RUN**.
W1 회신 기준 `41dd0c21692c7f59c87962cf1e7d2746bac73303`을 적용했다.
채택된 event shape·revision·SQS ACK를 다시 제안하지 않는다. 기존 wire는 변경하지 않았다.
이 문서의 실제 full SHA·원격 확인·정본 hash와 최종 테스트 수는 ZIP `HANDOFF_RECEIPT.json`에 기록한다.

## 1. 구현 범위와 남은 연결

| 구성 | 제공 코드 | 실제 상태 |
|---|---|---|
| 판단 공급 | `CoreRuntime.supply(AnalysisPlan, Authority, key, now=...)` | 필수/선택 Source 의존관계가 명시된 분석 계획에서 Core/Non-Core를 생산하고 원자 저장 |
| 인증된 현재 문맥 | `Authority.current(job_id, source_id)` port | 구현자가 연결할 경계 제공. 실제 분석 앱의 인증/현재성 조회 구현은 이 저장소에 없어 미연결 |
| SQS relay | `CoreRuntime.relay_once`, `SqsTransport`, CLI | 실제 boto3 SendMessage 호출 경로 구현, SDK Stubber 검증. 실제 AWS 발송/배포 미실행 |
| 삭제/보관 | `delete_owner`, `expire`, `backup`, `inspect` | SQLite 실행 테스트 완료. 실제 사용자 삭제 신호 수신 어댑터/운영 스케줄러는 미연결 |
| W1 수신 | W1 소유 코드 | W1이 독립 검증 완료를 회신. 이번 테스트에서 실제 W1 DB/queue는 실행하지 않음 |

`analysis_supplier=READY`, `relay=READY`, deployed full SHA/role 정보를 허위로 채우지 않는다.
코드 commit은 배포 revision과 다르다. 현재 §9 회신 값은 `contracts/core-runtime/readiness.json`이다.

## 2. 공급·권한 경계

신뢰된 분석 caller가 `AnalysisPlan(context, required_sources, optional_sources)`를 전달한다.
context.Source가 required에만 있으면 CORE_REQUIRED / REQUIRED_ANALYSIS_DEPENDENCY,
optional에만 있으면 NON_CORE_OPTIONAL / OPTIONAL_ANALYSIS_CONTEXT다.
둘 다 있거나 어느 쪽에도 없으면 SOURCE_DEPENDENCY_UNRESOLVED로 저장하지 않는다.
이는 분석 계획 의존관계의 기계적 판정이며, W3가 원문/모델 confidence로 필수성을 새로 추정하는 기능이 아니다.
실제 분석 서비스가 어떤 Source를 required로 지정할지 결정하는 연결은 해당 caller가 제공해야 한다.

Authority는 `Authorization(context, owner_id, owner_epoch, active)`를 **신뢰된 현재 데이터**에서 반환한다.
호출자 body의 current/owner/principal을 그대로 돌려주는 실제 어댑터는 허용하지 않는다.
공급 및 매번 전송 직전에 context 전체·owner·epoch·active를 검사한다.
실제 서비스에서는 분석 앱의 인증된 작업 문맥 및 삭제/취소 상태를 연결한다. W1의 새 receipt API를 요구하지 않는다.
신뢰된 CLI 운영자는 `--authority package.module:factory`로 이를 주입한다.
local smoke의 SyntheticAuthority는 합성 예시이며 live 환경에 사용하면 안 된다.

WIRE owner는 W3, transport producer는 w3로 유지한다. 개인 owner_id/epoch는 event wire에 추가하지 않는다.
owner ID는 로컬에서 SHA256 key로 변환해 metadata에 결속한다. 이 해시도 가명 metadata이며 익명이라고 주장하지 않는다.

## 3. 저장·재시도·ACK

기존 `w3_core_decision_outbox`와 신규 `core_delivery`, `w3_core_counters`를 같은 DB transaction으로 저장한다.
revision은 회사/Source 단위이며 본문 purge 뒤에도 counter를 보존한다.
기존 DB counter는 남아 있는 event 최대값에서 backfill한다. 이미 유실된 과거 revision을 복원할 수는 없다.
기존 legacy producer row는 owner metadata가 없으므로 relay에 자동 편입하지 않는다.

생산·relay·삭제는 같은 SQLite DB의 BEGIN IMMEDIATE로 직렬화한다.
여러 프로세스는 같은 로컬 DB를 사용해야 한다. 여러 호스트의 별도 DB/NFS 분산 allocator는 지원하지 않는다.
relay는 write lock을 잡고 bounded network 호출을 하므로 긴 전송은 다른 쓰기를 지연시킬 수 있다.
CLI AWS client timeout은 connect 3초/read 5초, SDK 전체 시도 1회다. authority adapter에도 제한 시간을 적용해야 한다.

상태: PENDING → TRANSPORT_HANDOFF 또는 RETRY → HELD.
- SendMessage HTTP 200과 MessageId 확인만 TRANSPORT_HANDOFF로 기록한다. **W1 수락이 아니다.**
- retry 간격은 2,4,8…최대300초, 횟수 기본8(설정1~100). 소진하면 HELD로 두고 자동 재시도 중단.
- `replay`는 최신 권한을 다시 확인하고 같은 event ID/body/revision으로 재예약한다. 새 판단을 생성하지 않는다.
- 전송 후 local commit 전 crash는 원본 event를 재전송한다. W1 inbox dedupe로 수렴해야 한다.
- W1 DB 장애가 SQS 수신 뒤 발생하면 W1이 delete하지 않고 SQS가 redelivery한다. W3가 W1 outcome을 추측해 새 event를 발행하지 않는다.
- 예외 전문은 저장/출력하지 않는다. inspect는 event ID/digest/state/횟수/시간만 제공한다.

## 4. 보관·개인 삭제·backup

`--retention-seconds`는 필수 운영 설정이다. 제품 보관 기간을 임의 확정하지 않았다.
expire는 TRANSPORT_HANDOFF의 마지막 published_at + 설정기간 경계(포함)부터 본문/request를 제거한다.
PENDING/RETRY/HELD는 확인 없이 기간만으로 제거하지 않는다. 필요 시 최신 권한 확인 또는 owner 삭제로 차단한다.
만료된 동일 key는 EVENT_RETIRED로 막아 ID를 새로 만들어 재생산하지 않는다.
replay를 명시적으로 실행하면 재전송 성공 시 보관 기간이 다시 시작한다.

`delete_owner(owner_id, deletion_epoch)`는 신뢰된 삭제 dispatcher 전용이다.
관측 owner epoch보다 큰 삭제 epoch만 적용하며, 등록 전 삭제도 tombstone을 남겨 이후 공급을 차단한다.
owner event/request를 지우고 DELETED metadata로 바꾼다. 공용 회사/Source counter와 다른 owner는 유지한다.
삭제 tombstone 및 idempotency/digest metadata는 현재 자동 만료하지 않는다. 종료 보관정책은 운영 채택 필요다.
계정 owner ID의 재사용은 지원하지 않는다. 동일 owner의 새 epoch로 임의 재활성화하지 않는다.

전송과 삭제의 순서는 DB lock으로 정한다. 삭제가 먼저 commit되면 새 send는 없다.
전송이 먼저 끝난 경우 이미 SQS에 있는 메시지를 W3가 회수하지 않으며, W1의 삭제 currentness 검사가 차단한다.
SQLite secure_delete로 논리 삭제된 페이지를 처리하지만 OS/storage snapshot까지 물리 삭제됐다고 주장하지 않는다.

**지원 backup은 본문 복원용이 아니다.** 메모리에서 모든 event/request를 제거하고 quarantine한 뒤
디스크 임시 파일에 쓴다. 중단돼도 디스크에 미격리 본문을 복사하지 않는다.
복원본에서는 supply/relay/replay가 RESTORE_QUARANTINED로 차단되며 해제 CLI는 없다.
counter/가명 tombstone 점검용 backup이다. 누락된 private event는 복구하지 않는다.
임의 raw DB/filesystem snapshot 복원은 지원 절차가 아니다. 이를 live primary로 복원하면 삭제/counter가
되돌아갈 수 있으므로 금지한다. 운영 재구성에는 최신 삭제대장·counter 기준 확인과 별도 복구 절차가 필요하다.

## 5. 실행 절차

로컬 진단(네트워크 호출 없음, 새 디렉터리 필요):
```sh
uv sync --locked
uv run --locked python -m w3_knowledge.core_runtime_cli smoke --directory .runtime/core-relay-smoke
uv run --locked pytest tests/integration/test_core_runtime.py tests/integration/test_core_decision.py -q
```

운영 명령 형식(실제 값은 별도 설정, 아래 이름은 예시):
```sh
python -m w3_knowledge.core_runtime_cli init --db /state/core.db --retention-seconds <approved-seconds>
python -m w3_knowledge.core_runtime_cli supply --db /state/core.db --retention-seconds <approved-seconds> --authority app.authority:create --plan /private/analysis-plan.json --idempotency-key <stable-key>
python -m w3_knowledge.core_runtime_cli relay-once --db /state/core.db --retention-seconds <approved-seconds> --authority app.authority:create --send
python -m w3_knowledge.core_runtime_cli expire --db /state/core.db --retention-seconds <approved-seconds>
python -m w3_knowledge.core_runtime_cli inspect --db /state/core.db --retention-seconds <approved-seconds>
```
`app.authority:create`는 예시 경로이며 이 저장소에 구현돼 있지 않다.
운영 작업 runner가 relay-once/expire를 주기 실행한다. HELD를 운영 관찰에 포함한다.
delete-owner는 `--owner-id`와 `--deletion-epoch`, replay는 `--event-id`와 `--authority`,
backup은 `--destination`을 추가한다. 삭제 명령은 검증된 삭제 신호에서만 호출한다.

AWS 설정 변수:
`AWS_DEFAULT_REGION`, `W3_CORE_DECISION_QUEUE_URL`, `W3_CORE_DECISION_EXPECTED_ROLE_ID`.
SDK 기본 workload credential chain을 쓰며 명령/문서에 access key를 넣지 않는다.
live relay는 STS GetCallerIdentity의 assumed-role UserId prefix를 expected Role ID와 비교한다.
W1은 같은 stable Role ID를 자신의 EXPECTED_SENDER_ID로 검사한다. SQS 권한은 전용 main queue SendMessage만.
실제 IAM policy와 queue policy 적용 여부는 이 프로그램이 증명하지 않는다.

## 6. W1에 전달할 상태와 공동 검증

W1 §9 JSON의 실제 deployed SHA, role ARN/RoleId, joint window는 아직 없다.
`contracts/core-runtime/readiness.json`은 이를 null/환경대기로 표시하며 READY라고 제출하지 않는다.
실제 분석 caller/Authority/삭제 신호 연결, workload 배포 및 role/queue 설정 후 값을 채운다.

CT12-01/12는 실제 plan으로 Core/Non-Core 공급, CT12-02는 replay,
CT12-07은 SQS/W1 DB 장애 주입, CT12-08/09는 W1 cancel/delete와 본 모듈 삭제 dispatcher를 함께 실행한다.
잘못된 role/변조 event 음성 주입은 격리 harness에서만 수행하고 정상 supplier 검증을 완화하지 않는다.
W1 row/action/command 수와 SQS outcome은 W1이, event digest/metadata는 W3 inspect가 제공한다.
실제 W1 공동 CT12-01~12 및 배포는 이번 작업에서 실행하지 않았다.
