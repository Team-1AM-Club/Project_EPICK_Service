# W3 추가 회신 및 W4 합의 요청 — 2026-09-16

W2의 `W3_additional_reply_request_2026-09-16.md`에 대한 구현 근거와 결정 요청이다.
현재 기준은 **`w3-c01/0.2-candidate`**다. 이전 `w3-restriction/0.1-draft`는
호환 정본이 아닌 별도 legacy이며 새 경로는 `/c01/v1`이다.

**W3가 W3→W4 계약 owner로서 아래 자료를 W4에 제안한다. W4 합의/서명은 아직 없다.**
동봉된 consumer는 W3가 작성한 실행 가능한 참조 구현이다. 실제 W4 서비스에서 통과한
테스트 또는 공동 확정 계약이라고 표시하지 않는다. 외부 메시지는 아직 전송하지 않았다.

## 전달 순서

1. W3가 이 문서와 schema/fixture/test 묶음을 W4에게 전달한다.
2. W4는 아래 A1–A5에 대해 채택 또는 변경안을 회신하고 실제 consumer 테스트 결과를 준다.
3. W3가 변경을 반영한 동일 commit 기준으로 W2에 C-01 호환성 확인을 요청한다.
4. W2와 PM은 P1–P5 제품/producer 의미를 결정한다. 운영 투입은 이 결정과 실연동 후 진행한다.

W2에게 W4의 계약을 대신 확정해 달라고 요청하는 구조가 아니다.

## 1. 정본 경로·commit

| 내용 | 경로 |
|---|---|
| 입력 및 상태 모델 | `src/w3_knowledge/c01/contracts.py` |
| 순번/복구/색인 | `src/w3_knowledge/c01/store.py` |
| 원본 producer payload schema | `src/w3_knowledge/c01/source-event-payload.schema.json` |
| envelope schema | `contracts/c01/v0.2-candidate/event.schema.json` |
| 원본 정상4/오류2 fixture | `contracts/c01/v0.2-candidate/fixtures/` |
| recovery/index/receipt/status schema | `contracts/c01/v0.2-candidate/{replay,snapshot,index-request,receipt,status,recovery}.schema.json` |
| W4 signal schema | `contracts/c01/v0.2-candidate/signal.schema.json` |
| W3 knowledge DTO | `contracts/c01/v0.2-candidate/knowledge.schema.json` (`StructureResponse`) |
| W4 ACK 요청/성공 응답 | `contracts/c01/v0.2-candidate/{delivery-receipt,delivery-response}.schema.json` |
| W4 연결 fixture | `contracts/c01/v0.2-candidate/examples/w4-consumer.json` |
| W4 consumer | `src/w3_knowledge/c01/w4.py` |
| W4 회귀 테스트 | `tests/integration/test_c01_w4_contract.py` |
| 입력·복구 회귀 테스트 | `tests/integration/test_c01.py` |
| 실제 HTTP·기존 지식 연결 테스트 | `tests/integration/test_c01_http.py`, `test_c01_guard.py` |

**이 묶음의 정확한 implementation commit·branch는 ZIP 루트 `HANDOFF_BASELINE.json`에 기록한다.**
Git 서버 게시 여부도 같은 파일에 표시한다. 로컬 commit과 GitHub push를 구분한다.
원본 파일 무변경 근거는 `received-manifest.json`, 전체 전달 파일 무결성은
루트 `handoff-manifest.json`, 실행 결과는 `docs/w3-additional-verification-2026-09-16.json`이다.

## 2. W2 P0 항목에 대한 답변

### 입력 검증

outer 필드의 `1.0`, producer `w2`, aggregate_type `source`, event_type을 먼저 파싱하고,
payload `w2.source.v1` 및 event_type별 JSON Schema branch를 검증한다.
payload 검증을 통과해도 aggregate/source UUID가 다르면 거절한다.

원본 DTO의 UUID 문자열은 그대로 받는다. 내부 key와 비교에는 동일 UUID의 대소문자를
통일한다. 원본 SHA256과 의미 비교용 projection을 구분한다. 같은 event ID의 원본을
바꾸어 재전송하면 대소문자 변경만 있어도 immutable 재전송 규칙 위반으로 충돌할 수 있다.

version은 버전/추출/정책/Evidence 메타데이터, observation은 최신 관측 상태,
restriction은 restriction_id별 제한 상태에 적용한다. version/observation을 가짜 cleared로
변환하지 않는다. 알 수 없는 envelope 버전/event_type/accuracy 값은 422로 거절한다.
reason_code는 producer schema대로 임의의 비어 있지 않은 문자열을 보존한다.
replacement_ref는 opaque 문자열로 보존하며 자동 교체나 URL 접근은 하지 않는다.

### 두 순번의 후보 규칙

| 항목 | scope / 시작 / 연속성 |
|---|---|
| transport event revision | Source당 1부터, version/observation/restriction 전체를 통합하여 +1 |
| restriction revision | **후보: Source당 1부터**, restriction event에 대해서만 +1 |
| W3 cursor 초기값 | 두 순번 모두 0; 미수신을 의미 |
| generation | Source당 W3 상태/색인 변경 시 증가. producer 순번이나 개인 삭제 epoch가 아님 |

동일 event ID 또는 transport 순번에 다른 원본이 오면 conflict다. restriction 순번도
다른 변경에 재사용할 수 없다. 동일 event 재전달은 idempotent하다.

늦은 cleared는 높은 순번이라는 이유만으로 적용하지 않는다. transport gap을 메우기 전까지
차단하고, restriction 순번도 연속이어야 한다. 낮은 cleared 재전달은 DUPLICATE/STALE이며
새 active를 되돌리지 않는다. conflict에서는 replay만으로 사용을 허용하지 않는다.
알려진 사실과 일치하는 원자 snapshot과 재색인이 필요하다. 이력 자체의 모순은 운영 복구 대상이다.

### 제품 의미 선택 요청 — 확정되지 않음

| ID | 결정자 | 선택지와 영향 |
|---|---|---|
| P1 | W2/W3 | restriction 순번 Source 단위(현재 후보) / restriction_id 단위. 후자면 cursor를 ID별로 바꿔야 함 |
| P2 | W2/PM | 버전 ID가 있어도 Source 전체 차단 / 해당 버전에만 차단. 이전 버전 제한 중 최신 버전 사용 여부가 달라짐 |
| P3 | W2/PM | null restriction을 Source 전체로 해석(현재 version 모드 후보) / 범위 불명으로 차단. 현재 null 의미를 공동 확정한 것이 아님 |
| P4 | PM/W2/W4 | cleared이면서 error_confirmed/superseded이면 계속 차단(현재 후보) / 별도 accuracy 정책. 과거 오류 근거 재사용 여부에 영향 |
| P5 | PM/W2/W4 | redistribution unknown을 차단(현재 후보) / 내부 W4 처리를 재배포와 구분하는 별도 권한. 원본 샘플의 READY 여부에 영향 |

active는 적용 범위 내에서 사용 차단한다. cleared는 **해당 restriction_id만** 비활성화한다.
새 C01 모델은 active/cleared 원문을 유지하며 legacy의 RESTRICTED/RELEASED로 치환하지 않는다.
외부 status.reason=RESTRICTED는 다른 active 또는 accuracy 차단이 남아 있으면 계속 유지된다.
cleared는 READY와 동의어가 아니다.

## 3. recovery·멱등·응답 유실 규칙

상세 수락 조건은 `w3-c01-handoff-2026-09-16.md`의 replay/snapshot 절과 실제 schema/test를 따른다.

- replay는 Source 단위 cursor, 오름차순/중복 없는 최대500건이다.
- 현재 후보 보관 경계는 **시간이 아닌** `retention_floor_cursor`다. N 이후 복구 가능하다는 뜻이다.
  W2가 시간 기반 보관을 한다면 producer outbox 게시 시각과 정책으로 floor를 계산해 제공해야 한다.
  콘텐츠 published_at, occurred_at 또는 W3 수신 시각을 대신 쓰지 않는다.
  기준 시각·시간대·보관 기간·floor 산출 책임은 W2 확인 항목이다.
- snapshot은 한 시점의 전체 Source state를 원자적으로 제공해야 한다. version별 최신 추출 상태,
  restriction_id별 최신 상태(해제 포함), 최신 observation 및 두 순번을 함께 준다.
- known watermark보다 낮은 snapshot, 알려진 적용/대기 제한 누락, event 변조,
  restriction 순번 재사용/역전을 거절한다.
- receipt COMMITTED는 트랜잭션 수신 완료다. SNAPSHOT_REQUIRED/CONFLICT/INCOMPLETE 상태와
  실제 index ACK를 혼동하지 않는다. snapshot 성공은 과거 이력 완전 복원이 아니므로 history_complete=false다.

| 실패/재시도 | 규칙 |
|---|---|
| W2 event 응답 유실 | 같은 event_id와 동일 body 재전송. 새 ID를 만들지 않음. 현재 status를 재확인 |
| replay 응답 유실 | 같은 페이지 재전송 가능. 이미 반영한 event는 중복 처리. 재색인 필요 상태를 보수적으로 유지 |
| snapshot 응답 유실 | 동일 snapshot body 재전송. 같은 cursor의 다른 body로 교체 금지. 재전송도 index 폐기 |
| index 응답 유실 | status에서 index_ack와 두 순번/키 확인 후 필요하면 동일 요청 재시도. generation은 추가로 증가할 수 있음 |
| W4 신호 처리 전 실패 | ACK하지 않음. 다음 pull에서 같은 signal 재전달 |
| W4 저장 후 ACK 전 실패 | 재시작 후 같은 signal을 DUPLICATE 처리하고 ACK. 유효한 캐시를 중복 신호만으로 지우지 않음 |
| ACK 응답 유실 | 같은 signal_id로 ACK 반복. 이미 ACK됐어도 `{delivered:true}` |
| 낮은 generation | STALE 처리 후 해당 signal ACK 가능. 현재 캐시 상태를 되돌리지 않음 |
| 같은 generation의 다른 내용 | CONFLICT. 캐시 삭제, ACK하지 않고 운영 조사 |
| 401/403/422/409 | 동일 잘못된 요청 무한 재시도 금지. 인증/계약/충돌 원인 해결 |
| timeout/5xx | 같은 식별자로 재시도. 간격·횟수·DLQ는 호스트 worker 정책으로 공동 결정 |

자동 retry worker/DLQ를 구현한 것으로 표현하지 않는다. `drain`은 한 페이지 처리 시도이며
실패를 호출자에게 반환한다. delivery ACK는 W4 로컬 영속 반영 **후** 보내고 성공 body도 검증한다.
acknowledge 자체는 처리 결과의 증명이 아니다. 역할 토큰을 가진 W4가 이 순서를 지킬 책임이 있다.

## 4. W3 → W4 합의 요청

| ID | W4에게 확인할 내용 | 후보 및 실행 근거 |
|---|---|---|
| A1 | signal/knowledge/receipt DTO 채택 | 위 schema 4종 및 w4-consumer.json |
| A2 | 두 순번과 generation 사용 | required_event_cursor/required_restriction_revision 분리, 단일 required_revision 없음 |
| A3 | restriction/gap/replay/snapshot 캐시 처리 | 차단 즉시 캐시 삭제, 복구해도 새 READY와 generation의 결과만 수용 |
| A4 | 재전달/실패/ACK | 위 표; ACK 유실·consumer restart·동일 신호 중복 회귀 테스트 |
| A5 | IndexKey/보관/권한 | static_html 유지, 정확한 추출키, TTL 및 아래 scope 제약 |

knowledge DTO는 기존 `StructureResponse`를 그대로 schema로 내보냈다. 독립적인 새 HTTP knowledge
endpoint를 추가한 것은 아니다. W3 호스트는 처리 시작 때 확보한 `(source_id, generation)`과
guarded StructureResponse를 함께 전달한다. W4 참조 consumer는
`put_knowledge(source_id, generation, response)`로 이를 수용한다.

consumer는 READY 신호가 존재하고 generation이 일치해야 저장한다. Response는 COMPLETED/LIMITED,
Evidence ID는 중복 없어야 하며 Claim/Requirement의 Evidence 참조가 존재해야 한다.
모든 SourceRef의 Source/Version이 해당 신호의 IndexKey와 일치해야 한다.
Requirement의 root/children/순환 및 노드별 Evidence 참조도 기존 조건 트리 검증기로 확인한다.
W4는 개별 Claim/Requirement의 verification_status·usage_status를 계속 확인해야 한다.
이 검사만으로 Claim의 사실성 또는 W1 사용자 권한을 보증하지 않는다.

참조 cache는 Source당 한 항목이다. 여러 Source의 결과를 합친 실제 추천은 모든 의존 Source의
키/generation과 W1 scope를 추적해야 한다. 이를 이미 구현했다고 주장하지 않는다.
W4는 실제 서비스 결과나 구체적인 수정안을 회신해야 공동 확정으로 표시할 수 있다.

Evidence ID는 producer Evidence UUID와 W3가 산출한 Evidence ID의 역할이 다르다. 이 예제는
기존 W3 지식 DTO를 소비하는 검증이며, 의미 검증이나 producer Evidence ID와의 자동 동일화가 아니다.
SourceVersion 참조와 수집 Evidence 발췌 해시는 W3 입력/색인/guard 경계에서 검증한다.

## 5. IndexKey·보관 책임

IndexKey는 SourceVersion UUID, extraction UUID, representation 원문(static_html 포함),
normalization version 원문이다. operator는 현재 두 순번 및 정확한 키로 색인을 요청한다.
receipt schema, index ACK인 Status schema, W4 delivery receipt/response schema를 각각 사용한다.

| scope | C01 후보 동작 / 책임 |
|---|---|
| excerpts_only | 허용된 수집 Evidence 발췌와 해시 일치 시 색인. operator가 expires_at 지정, W3가 TTL 상한·권한 검증 |
| full/normalized_body | **미지원**, 요청 거절. 전체 본문 처리 필요 시 body_storage와 redistribution 허가 및 별도 구현/합의 필요 |
| none | 저장하지 않는 정책 상태를 의미. index API의 none 옵션은 미지원; 정책 차단/삭제로 live index를 비움 |

W2는 정책 판정과 version 자료를 제공하고 W3는 허가를 추정하지 않는다. TTL 운영값은 PM/W3/W4 결정이다.
W4는 본문 포함 파생 캐시의 보관·삭제 책임도 갖는다. 최신 status 재검증 실패 시 캐시를 반환하지 않는다.
유휴 상태의 실제 삭제 scheduler, 감사 metadata, outbox, SQLite WAL/백업 물리 삭제는 별도 운영 정책이다.

## 6. W1 private deletion 연계 제안 — 구현/합의 전

확정된 W1→W2 command/result epoch·fence를 W3 public Source restriction과 동일시하지 않는다.
**public W2 event에는 job/owner/project/deletion 정보를 추가하지 않는다.**

W1에게 아래 private 계약을 요청한다. 예시 값이나 wire schema를 확정값으로 만들지 않았다.

- 인증된 서비스 주체, 삭제 대상의 opaque scope 종류/ID, immutable signal ID,
  삭제 epoch 또는 scope revision, effective_at, 삭제 종류(사용 금지/삭제 요청).
- owner/project/job 각각의 삭제 대상 데이터와 W3 보관 여부; public Source의 공유 데이터는 별도.
- 실행 시작 및 결과 commit 직전 확인할 W1 현재 epoch/fence 조회 또는 검증 방법.
- receipt(차단 반영)와 purge 완료(물리 보관 영역별 삭제)를 구분한 ACK 및 재시도 규칙.

W3 제안 동작은 다음과 같다.

1. private scope가 삭제되면 즉시 해당 scope 결과/파생 캐시 사용을 차단하고 작업 commit을 거절한다.
2. private index/history/replay payload를 식별해 삭제한다. 최소 tombstone의 보관 가능 범위는 W1/PM이 결정한다.
3. 재전달된 과거 result, public Source의 cleared/READY, replay/snapshot으로 private scope를 되살리지 않는다.
4. W4에 private scope 무효화를 별도로 전파한다. 공용 Source 전체를 불필요하게 삭제하거나 제한하지 않는다.
5. 개인정보가 없어야 하는 public W3 영역에 private 데이터가 들어온 사고는 별도 정정/삭제 절차로 처리한다.

현재 C01 Store는 public Source 전용이고 owner/project/job 인덱스나 private signal endpoint가 없다.
위 동작은 **제안이며 미구현/미검증**이다. W1 규격을 받은 뒤 추가 구현과 공동 테스트가 필요하다.

## 완료 판단

로컬 schema/fixture/참조 consumer/회귀 테스트/정본 commit은 이번 묶음으로 제시한다.
**W4 실제 consumer 채택과 P1–P5/A1–A5 회신 전에는 공동 확정 완료가 아니다.**

W4에게 보낼 문구:

> W3가 W2 public event를 소비한 뒤 전달할 C01 후보 계약을 공유합니다.
> 이 문서 A1–A5의 signal/knowledge/ACK, 두 순번·generation, 캐시 복구 및 재시도 규칙을 확인해 주세요.
> schema·fixture·참조 consumer 테스트와 commit 기준을 포함했습니다.
> 실제 W4 적용 테스트 결과 또는 변경안을 회신해 주시면 W3가 반영한 뒤 W2와 입력 호환성을 확인하겠습니다.
