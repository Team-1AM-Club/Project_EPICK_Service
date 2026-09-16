# W3 회신 — Restriction 계약 채택 판단 자료

문서 버전: `w3-adoption-review/2026-09-14.1`  
검토 대상: `w3-restriction/0.1-draft`  
대응 요청: `W3_followup_request_2026-09-14.md`  
상태: **팀 채택 검토용. W1 D-05/D-06/D-07 채택 승인 전.**

W3는 아래 파일을 현 후보의 기준본으로 지정한다. 팀 채택 시 유지해야 할 의미와 변경
가능한 표현을 구분해 회신한다. 이번 회신에서는 기존 코드·Schema를 수정하지 않았다.
W1 개인 삭제 연동은 현재 미구현이며, §3의 내용은 구현 완료 보고가 아닌 계약 변경 제안이다.

## 1. 채택 시 정본으로 삼을 경로와 버전

아래 경로는 `Project_EPICK_Service` 저장소 루트 기준이다. 이번 검토 ZIP도 같은 경로를
유지한다. 이미 전달한 `EPICK_W3_Restriction_Handoff_2026-09-13.zip`이 실행 기준본이다.
이번 ZIP은 검토자료 묶음이다. runbook의 실행 명령은 9월 13일 실행 기준본의 압축 해제
루트에서 수행하며, 검토자료 ZIP만으로 서버를 실행하는 구성은 아니다.
파일별 SHA-256 및 실행 기준 ZIP의 SHA-256은 함께 제공한
`docs/w3-adoption-baseline-2026-09-14.json`에 고정했다.
현재 원격 branch에 이 후보가 push됐다고 가정하지 않는다. 기존 원격 commit을 이번 후보의
정본 commit으로 쓰지 않으며, 채택·push 후 팀이 commit/tag를 기록한다.

| 대상 | 정본 지정 경로 | 버전 / 역할 |
| --- | --- | --- |
| 계약 의미·운영 규칙 | `contracts/restriction/v0.1-draft/README.md` | `w3-restriction/0.1-draft` |
| W2 입력 Schema | 같은 디렉터리의 `event.schema.json`, `replay.schema.json`, `snapshot.schema.json` | 동일 버전, 추가 필드 거부 |
| 색인·응답 Schema | `index-request.schema.json`, `status.schema.json`, `consume-response.schema.json`, `recovery-response.schema.json` | 동일 버전 |
| W4 Signal DTO | `contracts/restriction/v0.1-draft/signal.schema.json` | 동일 버전, 이벤트명 `w3.source.usability.changed` |
| W4 전달 확인 DTO | `contracts/restriction/v0.1-draft/delivery-receipt.schema.json` | 동일 버전 |
| 유효 입력 fixture | `contracts/restriction/v0.1-draft/examples/` | initial / restrict / release / replay / snapshot / index-request |
| 무효 입력 fixture | `contracts/restriction/v0.1-draft/invalid/` | private-field / future-version / revision-string / source-mismatch |
| runbook·현재 가용 상태 | `docs/w3-restriction-handoff.md` | 2026-09-13 실행 후보 전달서 |
| 기존 검증 증거 | `docs/w3-restriction-verification.json` | 이전 실행 결과. 이번 문서 작업에서 재실행한 결과가 아님 |
| 이번 공동 검토 fixture | `docs/fixtures/w3-adoption-review-v1.json` | `w3-adoption-review/2026-09-14.1`, 제품 endpoint 입력이 아닌 검토용 시나리오 |

Wire 타입은 위 JSON Schema와 계약 README의 의미 규칙을 함께 따른다. Pydantic 원본은
`src/w3_knowledge/restriction/contracts.py`다. JSON Schema만으로 표현하지 못하는 Source
일치·시간 관계·revision 정렬·ACK 일관성도 검증해야 한다. 두 표현이 다르면 임의로 한쪽을
선택하지 말고 채택을 보류해 수정안을 검토한다. 이후 변경은 새 계약 버전과 회귀 fixture로 관리한다.

## 2. 규칙별 채택 판단표

여기서 **필수 원칙**은 W3가 호환성과 안전을 위해 유지하도록 요청하는 의미다. 이미 팀이
모든 필드·enum을 승인했다는 뜻은 아니다. **후보 선택**은 같은 원칙을 만족하는 대체 설계가
가능하다는 뜻이며, 현재 0.1-draft에 다른 값을 그대로 보내도 수용된다는 뜻이 아니다.

| 항목 | 현재 draft 규칙 | 채택 필수 여부 | 변경 가능 범위 | 영향 받는 W1/W2/W4 계약 |
| --- | --- | --- | --- | --- |
| 이벤트명 | `source.restriction.changed` | 의미 필수 / 이름 후보 | 이름·envelope 구조 변경 가능. producer와 consumer를 같은 버전으로 함께 변경 | W2 게시, W3 소비, W1 변경 영향 |
| event ID·불변 payload·dedup | 전역 event_id, 재전송 시 시간 포함 전체 값 유지, `(consumer_id,event_id)` 멱등 | 필수 원칙 | ID 형식·이름공간·저장 방식 가능. 수정 사실은 새 이벤트로 전달하며 기존 ID를 재사용하지 않음 | W2 outbox, W3 receipt, W1/W4 재처리 |
| Source-wide aggregate_revision | Source별 restriction 흐름이 1부터 연속 증가 | 정렬·누락 검출은 필수 / 범위·연속 번호 방식은 후보 | 제한 대상별 revision 등으로 변경 가능하나 대상 간 영향·전체 Source 차단·snapshot watermark·누락 검출을 함께 재설계. 버전 번호/시각으로 단순 대체 불가 | W2 정렬, W3 복구, W1 영향 범위, W4 상태 |
| RESTRICTED / RELEASED | 공용 Source 현재 사용 제한 / 제한 해제 | 차단·해제 의미 필수 / enum 후보 | 명칭·상태 확장 가능. 해제는 개인 권한·삭제 복구·Claim 검증 승인을 뜻하지 않음. 미지원 상태는 사용 보류 | W1 권한·삭제, W2 payload, W3/W4 사용 검사 |
| accuracy / reason code | accuracy와 restriction을 분리, 사유는 제한된 코드 | 의미 분리·민감정보 제외 필수 / enum 후보 | 코드 집합·필수 여부는 W2와 조정. 자유 형식 개인 정보·비밀값은 공용 event에 추가하지 않음 | W2 분류, W1/W4 표시 |
| stale | 과거 기록 보존, 현재 상태는 되돌리지 않음 | 필수 원칙 | 저장 방식·관측 지표 가능. **stale 수신 자체가 무조건 사용 차단은 아님**; 최신 상태가 READY면 그대로 유지 | W2 재전송, W3 이력, W4 역순 처리 |
| conflict | 같은 ID의 변경·같은 revision의 모순은 격리하고 사용 차단 | 필수 원칙 / 복구 절차 후보 | 오류 코드·승인 절차 가능. 현 후보는 현재보다 높은 신뢰된 snapshot으로 해소 | W1 운영, W2 정정, W3 복구, W4 보류 |
| gap·replay·snapshot | gap 차단, 보관 구간 replay, stale snapshot 거부, 복구 실패 시 차단 유지 | 필수 원칙 / 전송·보관 수치는 후보 | pull/push, batch 크기, 기간·타임아웃 가능. 누락 상태를 확인 없이 정상으로 간주하거나 과거 snapshot으로 제한을 되돌릴 수 없음 | W2 outbox/snapshot, W3 복구, W1/W4 상태 |
| 전체 IndexKey readback·ACK | SourceVersion/extraction/representation/normalization 및 restriction revision을 확인하고 실제 색인 쓰기 후 ACK | 정확한 버전·성공 확인 필수 / DTO 표현 후보 | 키 필드명·색인 backend·ACK 전송 방식 가능. 수신 receipt나 본문 hash 하나로 대체 불가 | W2 수집/색인 구분, W1 대기 상태, W4 사용 검사 |
| history_complete | snapshot 복구 시 false. 재색인 성공 후에도 false 유지 가능 | 이력 공백 명시 필수 / 표현 후보 | boolean 대신 구조화된 limitation 가능. false는 개인 삭제나 현재 사용 허가를 나타내지 않으며, 현 후보에서는 false이면서 READY가 가능 | W1/W4 한계 표시, W2/W3 audit |
| generation | Source별 사용 가능 상태 순번. 색인 실패·성공·TTL도 증가시킴 | 최신성 비교·역행 방지 필수 / 이름·표현 후보 | stream epoch+sequence 등으로 대체 가능. restriction_revision만으로 대체하면 같은 revision의 색인/TTL 변화 누락 | W3 Signal, W4 cache, W1 재분석 |
| 원문·공용/개인 경계 | 공용 event에 원문·개인 ID·비밀값 없음 | 필수 원칙 | private 경로의 필드·인증·전송은 별도 합의. 공용 Schema를 개인 삭제용으로 재사용하지 않음 | W1 private lifecycle, W2 공용 게시, W3/W4 분리 |

위 W1 영향 항목을 D-05/D-06/D-07의 어느 항목에 반영할지는 W1이 최종 확인한다.
현재 제공된 자료만으로 해당 세 문서의 세부 조항 이름이나 승인 상태를 새로 정의하지 않는다.

## 3. W1 개인 삭제와 공용 Source restriction의 경계 — 변경 제안

### 3.1 현재 상태와 유지할 원칙

프로젝트 공통 기준의 공용/개인 분리와 “완전 삭제 요청은 개인 Snapshot 보존보다 우선”을
따른다(`Docs/00_SHARED_CONTEXT.md` §4). W1 개인 삭제는 **W1 private lifecycle**로 유지한다.
한 사용자의 경험·프로젝트 삭제를 전체 사용자의 공용 Source 제한으로 바꾸지 않는다.

현재 W3 runtime은 공용 Source 상태와 검색 색인을 다룬다. W1 개인 삭제 이벤트 수신,
개인 범위 tombstone, 개인 결과 쓰기 차단, 삭제 완료 receipt는 **미구현**이다.
기존 W4Cache도 Source별 캐시 어댑터이므로 개인 데이터 캐시의 삭제 보장을 대신하지 못한다.
기존 계약서의 audit 보존 설명은 공용 metadata 범위이며 개인 완전 삭제 요청을 무시할 근거가 아니다.

### 3.2 최소 교환 정보 제안

아래는 필요한 의미의 목록이다. 필드명·enum·endpoint를 확정하거나 현재 공용 DTO에 추가하지 않는다.

| 경계 | 최소 정보·의무 제안 | 목적 |
| --- | --- | --- |
| W1→개인 파생 데이터 보유자(W3/W4 해당 시) | private 계약 버전, 불변 알림 ID, 인증된 범위의 불투명 resource/scope 참조, 범위 종류·하위 리소스 포함 의미, 단조 증가 lifecycle revision, 삭제 상태 | 정확한 대상만 차단하고 중복·역순을 처리. 본문·개인 이름·이메일은 알림에 불필요 |
| 작업/결과→W1 현재 상태 검사 | 작업이 참조한 scope와 lifecycle revision, W1의 현재 존재·사용 가능 상태 및 revision | 오래된 작업·checkpoint·결과 쓰기·캐시 반환 차단. 인증 실패·조회 실패·존재하지 않음도 허용으로 해석하지 않음 |
| W3/W4→W1 private 처리 확인 | 알림 ID, consumer 식별, 대상 scope, 적용 lifecycle revision, 사용 차단 완료와 물리 삭제 진행/완료를 구분한 결과 | 알림 수신과 삭제 완료를 분리하고 재시도·운영 확인에 사용 |
| W1→복구/재생 작업 | 현재 삭제·접근 상태를 확인할 수 있는 권위 있는 조회 또는 동등한 폐기 세대 정보 | backup/replay/snapshot이 오래된 개인 상태를 되살리지 않도록 복구 전에 재검사 |

W3가 해당 개인 데이터를 보관하지 않는 경로에는 개인 식별 정보를 복제하지 않는다.
이 경우 W1/W4가 private 표시·결과 경로를 차단하고 W3에는 공용 Source 요청만 전달한다.
W3가 향후 개인 파생 색인을 보유하면 그때 private invalidation 소비 책임을 명시한다.

### 3.3 복원 방지 조건 제안

1. W1이 삭제 상태를 기준 저장소에 commit한 시점부터 신규 사용·결과 저장을 차단한다.
   전파 완료 전에도 현재 상태 검사를 거치며, 이미 실행 중인 작업의 결과 commit 직전에도
   참조 lifecycle revision을 비교한다. 개인 쓰기 검사와 commit은 경쟁 조건에 안전해야 한다.
2. 삭제된 scope는 같은 식별자로 자동 재생성하지 않는다. 새로운 사용자 생성 행위는 새
   식별자로 구분한다. 늦은 ACTIVE 알림·높은 공용 generation·RELEASED·snapshot은 삭제를 해제하지 못한다.
3. 개인 결과의 사용 조건은 **공용 Source 사용 가능 AND W1 개인 scope 존재·권한·lifecycle 유효**다.
   W3의 `index_ack=true`는 이 중 공용 조건만 확인한다. 여러 Source/scope를 참조하면 모두 검사한다.
4. scope에는 Episode만이 아니라 상위 Project/계정 등 삭제 범위가 미치는 하위 파생 결과를
   포함해야 한다. 대상 확장·참조 관계·표시 상태의 권위는 W1이 가진다. 공유 Source 자체는
   다른 사용자가 계속 사용할 수 있다.
5. tombstone/폐기 세대는 복원 방지에 필요한 최소 metadata로 설계한다. 보관기간·백업 복구
   절차는 W1/운영과 합의한다. 기간 만료 후에도 “상태 없음=허용”이나 공용 event로 개인 scope를
   생성하는 경로를 허용하지 않는다. 개인 본문을 삭제 방지 이력 명목으로 영구 보존하지 않는다.

**변경안 C-01:** W1 소유 private lifecycle 계약과 결과 commit/표시 검사 경계를 먼저 합의한다.
공용 `event.schema.json`과 `signal.schema.json`에는 개인 삭제 필드를 추가하지 않는다.
구현과 통합 검증은 승인 후 별도 작업이다.

## 4. W4 usability signal 정본 DTO와 소비 조건

정본 지정안은 `contracts/restriction/v0.1-draft/signal.schema.json`이다. 아래는 해당 DTO에
맞는 합성 예시이며 개인 삭제 신호가 아니다. 같은 값은 공동 검토 fixture에 포함했다.

```json
{
  "schema_version": "w3-restriction/0.1-draft",
  "event_type": "w3.source.usability.changed",
  "signal_id": "review-signal-5",
  "source_id": "demo-source",
  "generation": 5,
  "restriction_revision": 3,
  "required_revision": 3,
  "usable": true,
  "reason": "READY",
  "index_key": {
    "source_version_id": "demo-version",
    "extraction_revision_id": "demo-extraction",
    "representation": "text",
    "normalization_version": "demo-normalization"
  },
  "history_complete": false
}
```

현재 후보의 모든 필드는 required다. index_key만 null을 허용하며 usable=true이면 null이면
안 된다. usable과 reason=READY는 일치해야 하고, usable=true이면 두 restriction revision이
같아야 한다. reason 허용값 전체는 Schema를 따른다. history_complete=false는 이력 공백 표시다.

| 조건 | 기존 W4 앱이 지켜야 할 동작 | 현재 제공 여부 |
| --- | --- | --- |
| 새 generation | 같은 Source의 cache를 비우고 상태를 영구 저장한 뒤 signal_id 전달 ACK | W4Cache 어댑터 제공. 기존 앱 연결은 대기 |
| 같은 generation·동일 내용 | 멱등 처리. 상태·cache를 과거로 변경하지 않음 | 제공 |
| 낮은 generation | 무시. 최신 제한·무효화를 되돌리지 않음 | 제공 |
| 같은 generation·다른 내용 | 충돌로 cache 차단. 임의 last-write-wins 금지 | 제공 |
| cache 읽기 | 인증된 현재 W3 status와 Source ID·generation 일치 및 index_ack=true 확인. 조회 실패 시 반환 금지 | 제공. status 호출 함수는 호스트가 연결 |
| READY 통지 | 이전 결과 자동 부활 금지. 현재 근거로 계산한 결과만 해당 generation에 저장 | 새 통지는 cache 삭제. 실제 재계산은 W4 앱 책임 |
| 여러 Source/개인 scope 결과 | 모든 Source 현재 상태와 §3의 private lifecycle 조건을 함께 확인 | 앱/계약 추가 연결 필요 |
| W3 DB 초기화·백업 복구 | 기존 W4 cache 무효화 후 전체 동기화. 새 DB generation을 이전 stream과 비교해 허용하지 않음 | runbook 의무. 자동 epoch 프로토콜 미구현 |

**변경안 C-02:** DB 복구 시 수동 동기화 대신 stream epoch를 DTO에 넣을지 공동 결정한다.
epoch 없는 현 후보를 그대로 채택하면 DB 복구 runbook의 cache 초기화를 필수 운영 조건으로 둔다.
공용 generation과 W1 lifecycle revision은 별개이며 하나의 값으로 대체하지 않는다.

## 5. W2 producer 조건과 consumer 응답 의미

저장 receipt, 색인 ACK, W4 전달 확인은 서로 다른 완료 기준이다. 아래 HTTP 코드는 현재
로컬 구현 기준이다. broker로 전환하면 같은 의미를 transport ACK와 별도 결과 메시지에 매핑해야 한다.

| 연결/상황 | W2 producer 조건 | 현재 W3 응답·상태 | 성공/실패 의미와 W2 후속 처리 |
| --- | --- | --- | --- |
| 최초 게시 | 인증된 W2, 정확한 버전·필수 필드·Source 일치·불변 ID/시각. Source별 연속 revision | receipt=COMMITTED, outcome=APPLIED, 통상 HTTP 200 | 저장·상태 반영 성공. index_ack가 false이면 색인 완료 아님 |
| 동일 이벤트 재전송 | 동일 event_id와 원래 내용·published_at 유지 | DUPLICATE 또는 아직 gap이면 GAP | 중복 처리는 정상. 수신만으로 최신 사용 가능을 추론하지 않음 |
| 낮은 revision | 원래 snapshot을 그대로 전달 | STALE 또는 기존 ID면 DUPLICATE, 최신 상태 유지 | 과거 이력 저장/중복 처리. 현재 상태를 롤백하거나 자동 전체 재수집하지 않음 |
| payload 충돌 | 기존 ID를 수정하지 말고 원인 확인 후 정정 절차 | HTTP 409, outcome=CONFLICT, index_ack=false, 기록 receipt는 COMMITTED일 수 있음 | 저장 확인은 업무 성공이 아님. 격리·신뢰된 새 snapshot 복구 |
| revision gap | 누락 이벤트와 최신 high watermark를 제공할 수 있어야 함 | outcome=GAP / reason=EVENT_GAP, index_ack=false | 누락 구간 replay. receipt를 이유로 gap 복구 작업을 닫지 않음 |
| replay batch | 같은 Source, 중복 없는 오름차순 최대 500건, after_revision≤W3 현재 revision, published_at 기준 retention_start | REPLAYED 또는 SNAPSHOT_REQUIRED. cursor가 앞서면 409 CURSOR_AHEAD | high watermark까지 도달해야 복구 완료. batch가 남으면 이어서 보내고 만료/복구 불가능 구간은 snapshot 전환 |
| snapshot | 권위 있는 현재 상태를 Source별 원자적으로 읽고 as_of≥published_at. 알려진 최대 revision 이상 | SNAPSHOT_APPLIED, history_complete=false, 재색인 전 index_ack=false. stale/모순은 409 | 현재 상태 복구와 과거 이력 복원을 구분. snapshot 불필요한 반복은 재색인을 무효화할 수 있음 |
| 색인 실패/mismatch | 정확한 전체 키·자료 제공. 장애를 수집 실패로 재분류하지 않음 | INDEX_FAILED는 503, INDEX_MISMATCH는 상태 응답, 모두 index_ack=false | W3 operator가 장애 해결/키 수정 후 재색인. W2 receipt와 분리 추적 |
| 응답 유실·503 | 같은 ID·내용으로 재전송 가능하도록 outbox 유지 | 미저장일 수도, 이미 COMMITTED일 수도 있음 | 현재 상태·receipt 확인과 멱등 재시도. 실패만 보고 새 ID 생성 금지 |
| 계약/인증 오류 | 미래 버전·unknown/private 필드 금지, 역할별 인증 | 401/403/413/422 | 같은 잘못된 입력의 자동 반복 금지. 계약·인증·크기 수정 또는 운영 검토 |
| W4 통지 확인 | W2가 W4 저장 완료를 대신 ACK하지 않음 | W4가 영구 저장 후 `/v1/signals/ack`, delivered=true | 통지 전달 완료. 개인 삭제 완료나 모델 처리 완료를 뜻하지 않음 |

현재 코드는 응답의 reason이 INDEX_FAILED이면 이벤트 재수신 응답도 HTTP 503일 수 있다.
따라서 HTTP 코드 하나로 receipt·outcome·index_ack를 합치지 않는다.
재시도 간격·DLQ·운영 endpoint·인증 교체·실제 보관기간은 후보 runbook의 제안이며 W2/운영
합의 대상이다. W3 로컬 서버는 W2의 지속 재시도 worker를 제공하지 않는다.

## 6. 같은 fixture로 확인할 채택 기준

공동 검토 파일은 `docs/fixtures/w3-adoption-review-v1.json`이다. 공용 입력은 기존 유효/
무효 fixture를 그대로 참조하며, private 자료는 모두 합성 scope ID다. private 단계의 필드는
**결정용 표기**이며 현재 서버에 POST할 수 있는 JSON Schema/endpoint가 아니다.

| ID | 순서·검토 항목 | 합의할 기대 결과 | 증거 상태 |
| --- | --- | --- | --- |
| A01 | initial → index → restrict → duplicate/late initial → release → reindex | 제한 중 검색/cache 차단, 과거 이벤트로 복구 불가, 해제 후 재색인 | 기존 W3 로컬 검증 대상 |
| A02 | revision 누락 → 기간 내 replay / 만료 replay → stale/최신 snapshot | gap 차단, stale 거부, snapshot 뒤 이력 공백·재색인 | 기존 W3 로컬 검증 대상 |
| A03 | READY gen5 → RESTRICTED gen6 → 늦은 gen5 → 모순된 gen6 | 낮은 값 무시, 같은 값 충돌 시 cache 차단 | DTO 유효 예시 + 기존 어댑터 회귀 범위 |
| A04 | private scope ACTIVE rev10 → W1 DELETE rev11 → 공용 RELEASED/snapshot | 공용 Source가 READY여도 삭제된 scope의 데이터·표시·cache는 복구되지 않음 | **제안 / private 통합 미실행** |
| A05 | 개인 작업 rev10 시작 → DELETE rev11 commit → 늦은 작업 결과·ACTIVE rev10 도착 | 개인 결과 commit·표시 차단. 삭제 상태를 역행시키지 않음 | **제안 / 미실행** |
| A06 | 두 개인 scope가 같은 Source 참조 → 한 scope 삭제 | 삭제한 scope만 개인 사용 차단. 다른 scope의 권한·공용 Source 상태는 유지 | **제안 / 미실행** |
| A07 | 삭제 뒤 backup/replay 복구 또는 tombstone 조회 실패 | W1 현재 상태 미확인/부재는 차단. 이전 개인 scope 자동 생성 금지 | **제안 / 미실행** |
| A08 | 새 W3 DB에서 generation 재시작 | 기존 cache 초기화·재동기화, 또는 승인한 epoch 설계로 세대 구분 | 현 runbook 운영 조건 / 공동 복구 검증 대기 |

정적 index-request 예시는 만료 시각이 2026-09-14T00:00:00Z로 고정되어 있다. 현재 실행에는
기존 `scripts/restriction_smoke.py`를 사용한다. 이 스크립트는 별도 합성 실행 입력의 TTL을
현재 시각에 맞춘다. 기준 fixture 파일을 날짜에 맞춰 덮어쓰지 않는다.

기존 130개 테스트 통과·라이브 1개 생략·실제 서버 23단계 통과는 9월 13일 전달 기록이다.
이번 회신은 문서와 검토 fixture를 작성한 작업이며 W1 개인 삭제나 팀 통합의 새 통과 증거가 아니다.

## 7. 회신 요청 및 채택 완료 조건

| 결정 | 회신할 주체 | 현재 상태 |
| --- | --- | --- |
| 정본 경로·기준 hash 및 필수 원칙 수용, 후보 명칭/enum/revision 범위 선택 | PM / W1 / W2 / W4 | PENDING |
| C-01 private lifecycle·대상 확장·늦은 결과 commit 차단·처리 확인 | W1 주도, 개인 파생 데이터 보유 W3/W4 참여 | PENDING |
| W4 Signal DTO·현재 상태 검사·다중 Source/개인 결과 조건 | W4 / W3 / W1 | PENDING |
| C-02 DB 복구 시 runbook 유지 또는 epoch 계약 추가 | W3 / W4 / 운영 | PENDING |
| W2 outbox 게시·복구·receipt/index ACK 분리·운영 수치 | W2 / W3 / 운영 | PENDING |

각 팀이 같은 fixture ID에 대해 수용/변경안/미결정을 기록한 뒤 채택 범위를 확정한다.
계약 채택 완료와 실제 통합 구현·실행 완료는 별도로 기록한다. private 시나리오는 기대 동작을
합의할 수 있지만, 승인 후 구현·통합 테스트 전에는 실행 완료로 표시하지 않는다.

PM에게 보낼 짧은 회신:

> W3 restriction 후보의 정본 경로·버전, 필수 원칙과 변경 가능한 형식, W2 응답 의미,
> W4 Signal/cache 조건을 정리했습니다. 개인 삭제는 W1 private lifecycle로 유지하며,
> 복원 방지에 필요한 최소 정보와 결과 commit 검사를 변경안 C-01로 제안합니다.
> 공용 Schema는 수정하지 않았고, private 연동은 미구현 상태로 명시했습니다.
> 첨부한 공통 검토 fixture의 기대 결과와 후보 계약의 채택 범위 확인을 요청드립니다.
