# W3 restriction 계약 제안 — 0.1-draft

상태: **W3 구현 후보 / W2·W4·PM 공동 승인 전**. 작성일: 2026-09-13.
PM의 `W3_followup_request_2026-09-13.md`에 대한 제안이다. 기존 W2 Schema를
변경하거나 이 문서가 공식 채택됐다고 선언하지 않는다. 팀이 승인한 버전·결정 기록을
추가한 다음 운영 계약으로 승격해야 한다.

## 계약 파일과 의미

| 파일 | 용도 |
| --- | --- |
| event.schema.json | W2→W3 제한 변경 이벤트 |
| replay.schema.json | W2→W3 replay batch |
| snapshot.schema.json | 신뢰된 W2의 현재 상태 snapshot |
| index-request.schema.json | 내부 색인 작업자의 문서·정확한 버전 키·보관 범위 입력 |
| status.schema.json | 현재 사용 가능 상태와 index ACK |
| consume-response.schema.json | 저장 receipt와 소비 결과, 현재 index ACK |
| recovery-response.schema.json | replay/snapshot 복구 결과 |
| signal.schema.json | W3→W4 사용 가능 상태 변경 통지 |
| delivery-receipt.schema.json | W4의 통지 저장 완료 확인 |

Schema는 JSON Schema 2020-12다. `src/w3_knowledge/restriction/contracts.py`에서
생성하며 `python scripts/export_restriction_contract.py --check`로 파일 차이를 검사한다.
JSON Schema의 필드 검증에 더해 아래 **필드 간 의미 검증**도 계약의 일부다.
날짜 실재 여부, Source 일치, 시간 관계, 정렬, ACK 일관성은 런타임에서도 검사한다.

## 이벤트

이벤트명 제안은 기존 W3 전달서와 같은 `source.restriction.changed`다.
schema_version은 정확히 `w3-restriction/0.1-draft`여야 한다.
모든 객체는 추가 필드를 거부하며 미래 버전, 숫자 문자열, boolean revision을 거부한다.
호환 변경도 새 버전과 샘플·회귀 테스트를 함께 검토한다.

| 필드 | 의미 / 불변조건 |
| --- | --- |
| event_id | producer가 생성한 전역 불변 ID. 재전송 시 전체 payload와 시간도 그대로 유지 |
| aggregate_id | 공용 Source ID. payload.source_id와 일치 |
| aggregate_revision | **Source별 restriction aggregate**의 1부터 시작하는 연속 정수. 전역/SourceVersion revision이 아님 |
| occurred_at | 제한 상태 결정 시각. UTC `YYYY-MM-DDTHH:mm:ssZ` |
| published_at | W2 outbox 최초 게시 시각. occurred_at 이상; 재전송 시 갱신 금지 |
| payload.restriction_id | Source의 현재 제한 기록을 식별하는 공용 ID |
| payload.status | RESTRICTED 또는 RELEASED. RELEASED는 모든 권한·의미 검증의 승인이 아님 |
| payload.accuracy | UNKNOWN / CONFIRMED / DISPUTED 후보 값. restriction 및 Claim 검증 상태와 별개 |
| payload.reason_code | POLICY_REVIEW / RIGHTS_RESTRICTION / ACCURACY_REVIEW / RESOLVED 후보 값 |
| payload.replacement_source_id | 대체 Source 참조 또는 null. 기존 근거를 자동 대체하지 않음 |
| payload.index_key | 당시 알려진 정확한 version/extraction/representation/normalization 조합 또는 null |

모든 필드는 required이며 허용된 null을 명시해야 한다. ID는 최대 128자의 공용 불투명
참조만 사용한다. index_key의 source_version_id·extraction_revision_id·normalization_version은
서로 대체 불가능하다. representation 후보는 text/html/markdown이다. 값이 null이면
이전 성공 버전을 채우지 않으며 `VERSION_UNAVAILABLE`로 사용을 차단한다.
실제 W2의 enum/ID 생성 규칙과 매핑은 채택 회의에서 확인한다.
revision·watermark·generation은 최대 9,223,372,036,854,775,807이다. revision/generation의
유효 시작값과 0을 허용하는 cursor/status 값은 각 Schema를 따른다.

유효 예시는 `examples/initial.json`, `restrict.json`, `release.json`이다.
`invalid/`에는 private 필드, 미래 버전, 문자열 revision, Source 불일치 예시가 있다.
모두 합성 자료이며 유효/무효 판정을 재현하는 테스트를 포함한다.

### 공개 경계

공용 이벤트·통지는 위 공용 참조·상태·시간만 포함한다. 원문·발췌 본문, 개인 이름/이메일,
Project/Job/Episode ID, 토큰·비밀값, 자유 형식 사유를 넣지 않는다. 스키마 검사만으로
ID 문자열의 내용까지 개인정보 여부를 증명할 수 없으므로 producer도 분류를 책임진다.
`POST /v1/index`의 text는 **내부 operator 권한 데이터**이며 공용 event가 아니다.
로그·오류 응답에는 요청 본문이나 토큰을 출력하지 않는다.

## 전달 및 권한

로컬 후보는 HTTP push/pull 방식이며 broker topic/routing key는 없다.
consumer group은 단일 W3 `w3-restriction`, W4 통지 구독자는 단일 논리 W4 group이다.
각 DB는 하나의 W3 consumer identity에 묶인다. 여러 W4 인스턴스가 같은 group으로
동작하려면 같은 W4 지속 저장소를 공유해야 한다. 서로 다른 구독자별 offset은 미구현이다.

| 요청 | 역할 | 의미 |
| --- | --- | --- |
| GET /health | 무인증 | 프로세스/DB 기본 상태·계약 버전 확인 |
| POST /v1/events | W2 | 제한 이벤트 저장·소비 |
| POST /v1/replay | W2 | replay batch 주입 |
| POST /v1/snapshot | W2 | 신뢰된 현재 상태 복구 |
| POST /v1/index | operator | 내부 문서 색인·재처리 |
| POST /v1/purge | operator | `{}` 입력, 만료 본문/FTS 행 논리 삭제 |
| GET /v1/status/{source_id} | W2/operator/W4 | 현재 ACK·이유·restriction revision·generation |
| GET /v1/search?q=... | operator/W4 | 현재 사용 가능한 FTS 검색 결과, 최대 100개 |
| GET /v1/signals | operator/W4 | 미확인 통지 최대 500개를 생성 순서대로 조회 |
| POST /v1/signals/ack | W4 | 로컬 영구 저장한 signal_id의 전달 완료 확인 |

서버는 127.0.0.1에만 바인딩한다. 요청은 `Authorization: Bearer <role token>`을 사용한다.
W3_W2_TOKEN / W3_OPERATOR_TOKEN / W3_W4_TOKEN을 서로 다른 값으로 서버 환경에
주입한다. JSON POST만 허용하며 최대 크기는 2,000,000 bytes다. 연결 읽기 timeout은 5초다.
운영 환경은 TLS·서비스 identity·인증 교체·네트워크 주소를 합의해야 하며 이 HTTP 서버를
외부에 그대로 노출하지 않는다. 테스트용 Bearer token은 사람의 제한 해제 승인 증거가 아니다.

## 순서·멱등·충돌

- `(consumer_id,event_id)`로 소비 멱등성을 보장한다. 저장 receipt와 상태·outbox는 같은
  SQLite transaction에 기록한다. DB 장애 시 503이며 성공 확인을 보내지 않는다.
  상태를 읽기 전 BEGIN IMMEDIATE로 DB 쓰기 잠금을 확보하여 같은 DB를 사용하는
  다른 connection의 제한 변경과 색인 작업이 상태를 덮어쓰지 않게 한다. W4 cache도
  같은 방식으로 generation 확인과 갱신을 하나의 transaction에서 수행한다.
- 순서는 Source별 aggregate_revision으로 판정한다. 같은 revision에서 이벤트 ID가 달라도
  payload snapshot이 같으면 과거 중복으로 처리한다. 과거 시간으로 현재 상태를 덮지 않는다.
- 동일 ID의 payload/time 변경 또는 동일 Source/revision의 모순은 CONFLICT다. 현재 사용과
  검색을 차단하고 충돌 이력을 저장한다. 무조건 재시도해서 풀리지 않으며 W2/operator가
  사실을 확인한 뒤 **현재 revision보다 높고 모든 관측 revision 이상인 snapshot**으로 복구한다.
- gap은 Source 전체의 신규 사용을 차단하고 FTS 문서를 제거한다. 이후 연속 replay 또는
  신뢰된 snapshot으로만 복구한다. 초기 이벤트가 revision 1보다 높으면 gap으로 취급한다.
- history와 snapshots는 불변 공용 metadata다. stale 이벤트도 보존한다. 검색/본문 노출과
  audit 보존을 분리하며 일반 search API는 history를 반환하지 않는다.

## receipt, index ACK, W4 전달 확인은 서로 다름

`receipt=COMMITTED`는 **W3의 이벤트 기록이 저장됐다**는 뜻이다. APPLIED, DUPLICATE,
STALE, GAP, CONFLICT를 outcome으로 구분한다. GAP/CONFLICT도 저장될 수 있으므로
receipt만 보고 W2→W3 처리가 모두 끝났다고 판단하지 않는다.

`index_ack=true`는 조회 시점에 제한 해제, gap/충돌 없음, 정확한 색인 키와 restriction
revision 반영, 사용 가능한 본문 보관기간이 모두 충족됐다는 뜻이다. 실제 FTS 쓰기 후
색인 metadata를 다시 읽어 판정한다. 검색 결과는 검증된 Claim이라는 의미가 아니며
별도의 Claim/Evidence 검증과 W1 권한 검사를 통과해야 한다.

| reason | index_ack | 대응 |
| --- | --- | --- |
| READY | true | 해당 generation·키에 한해서 현재 사용 가능 |
| UNKNOWN_SOURCE / VERSION_UNAVAILABLE | false | 정확한 입력/버전 확보 |
| RESTRICTED | false | 승인된 변경 전까지 신규 사용 금지 |
| EVENT_GAP / CONFLICT | false | replay 또는 검증된 snapshot 복구 |
| INDEX_PENDING | false | operator가 정확한 문서를 색인 |
| INDEX_MISMATCH | false | 키/revision을 수정해 재색인. W2 수집 실패로 바꾸지 않음 |
| INDEX_FAILED | false | 실제 저장 장애 해결 후 재색인 |
| BODY_EXPIRED | false | 허용된 보관 정책에 따라 자료를 다시 확보·색인 |

제한 적용 시 검색 문서는 transaction 안에서 제거한다. 제한 해제·새 revision·snapshot
복구 후에는 재색인이 필요하다. index 실패 때 event history/현재 Source 상태는 보존한다.
성공 시점 이후의 제한·실패·만료가 기존 ACK를 무효화하므로 소비자는 현재 상태를 재확인한다.
index ACK가 true여도 W4에 통지가 전달됐다는 뜻은 아니다.

W4는 통지를 자기 DB에 저장하고 cache 무효화를 commit한 **후** signal_id를 ACK한다.
W3 outbox는 ACK 전까지 보존하므로 네트워크 실패 후 동일 통지를 다시 읽을 수 있다.
순서는 restriction_revision 대신 Source별 `generation`으로 비교한다. 같은 restriction
revision에서 색인 실패/성공·TTL 만료가 생겨도 generation은 증가한다. 높은 generation은
cache를 비우고 적용, 낮은 값은 무시, 같은 값의 다른 내용은 충돌로 차단한다.
실제 W4 앱은 `W4Cache.get`의 현재 W3 재검사를 모든 캐시 사용 경로에 적용해야 한다.
W3 조회 실패나 generation 불일치이면 캐시를 반환하지 않는다. 이미 외부로 전송한
응답의 회수나 실행 중 외부 모델 호출 취소는 이 어댑터가 보장하지 않는다.

### 실패와 재시도 제안

- HTTP 200: 응답의 outcome/reason을 처리한다. 단순 transport 성공과 사용 승인을 구분한다.
- 401/403: 인증/역할 오류. 자동 재시도 금지, 운영자 확인.
- 413/422: 크기/계약 오류. 같은 입력 자동 재시도 금지, producer 수정.
- 409: 충돌/오래된 snapshot/앞선 replay cursor. 현재 상태를 조회한 뒤 복구 주체에 전달.
- 503 또는 응답 유실: 같은 event_id와 payload로 재전송 가능. index는 동일 요청으로 재처리.
  W4는 자기 저장 commit 후 ACK 재전송 가능.
- 채택 제안: 호출 timeout 5초, 실패 후 1/2/4/8/16초 대기하여 최대 5회 재시도 후 운영자
  알림과 W2 DLQ/미처리 큐로 이동. **이 스케줄/DLQ는 W2/운영 구현 영역**이며 로컬 후보는
  자동 재시도 worker를 실행하지 않는다. W4 CLI도 1회 최대 500건 처리 후 종료한다.

## replay·snapshot

W2는 `POST /v1/replay`로 Source 하나의 batch를 보낸다. after_revision은 조회 시작
cursor, high_watermark는 W2가 확인한 최신 revision, events는 중복 없는 revision 오름차순,
최대 500건이다. payload Source가 batch Source와 같아야 한다. cursor가 W3 현재 revision보다
앞서면 CURSOR_AHEAD로 거부한다. 이전 cursor의 재전송은 허용한다.

retention_start는 **W2 최초 published_at 기준의 제외 경계**다.
`event.published_at > retention_start`인 이벤트만 replay 대상으로 소비한다.
기존 Lab의 10초는 운영값이 아니다. W2는 누락 구간을 연속 전달하며 500건을 넘으면
상태의 현재 revision을 다시 조회해 다음 batch를 보낸다. high watermark에 못 미치면
SNAPSHOT_REQUIRED와 차단 상태가 유지된다. 다음 batch가 남았다면 먼저 이어서 replay하고,
누락분이 보관기간 밖이거나 복구 불가능하면 snapshot으로 전환한다.

snapshot은 `as_of`와 마지막 restriction event snapshot을 한 번에 제공한다. W2는
그 시각에 Source별 원자적·일관된 상태를 읽어야 한다. as_of는 event.published_at 이상이다.
W3는 이미 관측한 가장 높은 revision보다 오래된 snapshot을 거부한다. 동일 revision의
모순이나 event ID 재사용도 충돌이다. 성공하면 기존 이력을 덮지 않고 history_complete=false를
남기며 재색인 전 ACK를 차단한다. snapshot 재전송도 재색인을 무효화하므로 불필요한 반복을
피하고 상태 조회로 완료 여부를 확인한다.

replay/snapshot 둘 다 불가능하면 차단 상태를 유지한다. W2가 원천 상태 복구, W3 operator가
상태 확인·재색인, W4가 사용자에게 근거 사용 보류 및 재분석 선택을 제공한다. 정확한 화면
문구와 W1 Job 상태는 제품팀과 합의한다. 현재 상태를 과거 audit의 대체물로 사용하지 않는다.

## lifecycle·보관 범위

제한 적용/해제의 승인 주체는 W1/정책·수집 운영팀과 합의해야 한다. 이 구현은 인증된 W2가
승인된 snapshot만 게시한다고 전제하며 개인 승인자 ID를 공용 event에 포함하지 않는다.

현재 Source 제한은 모든 SourceVersion과 그 Evidence/Claim의 **새로운 정상 사용**을
차단한다. 공고의 다른 Source까지 차단할지, 기존 Project/Snapshot에 어떻게 표시할지는
W1/W4의 참조 그래프·제품 정책 영역이다. 기존 Snapshot 사실과 audit을 삭제하지 않지만
그 안의 제한 근거를 현재 추천/검색에 그대로 노출해서는 안 된다.

index 입력은 `retention_scope=full|excerpts_only|none`과 절대 UTC expires_at을 요구한다.
scope=none이면 문서는 빈 배열이어야 한다. full/excerpts_only 모두 제공된 text만 저장하며
원문을 fetch하거나 발췌에서 전문을 복원하지 않는다. 호출자가 실제 승인 범위를 보장해야 한다.
허용 범위/기간 변경은 새 index 요청으로 적용한다. version 키가 다르면 거부한다.

`now >= expires_at`부터 상태·검색·통지 조회에서 만료를 검사하고 FTS/색인 metadata를
논리 삭제한다. operator는 purge endpoint를 주기적으로 호출할 수 있다. 별도 스케줄러는
포함하지 않는다. local history/conflict/snapshot/outbox receipt metadata는 기간 제한 없이
보관한다. W2 outbox 물리 삭제, tombstone TTL, DB/WAL/백업 완전 삭제는 **미구현·정책 합의 대상**이다.
실제 운영 보관기간을 확정했다고 해석하면 안 된다.

## 채택 시 닫을 결정

1. W2/PM: Source restriction revision 범위, enum/ID/발생·게시 시각, 공식 버전 채택.
2. W2/W3/운영: HTTP 유지 또는 broker 전환, 환경별 주소·서비스 인증·timeout/retry/DLQ.
3. W2/W3: replay 기간·batch 원자성, snapshot 권한·승인 증거·복구 책임.
4. W1/W4/PM: Source→공고/Project/Snapshot 영향, 제한 해제 승인, cache/재분석 UX.
5. 운영/정책: 원문·발췌·audit/tombstone/outbox·백업의 기간과 삭제 범위.

각 항목은 **PENDING**이다. W3의 계약 후보와 실제 로컬 실행 경로는 제공되지만,
W2 outbox 및 기존 W4 앱을 연결한 제품 handoff 완료는 별도 검증으로 남는다.
