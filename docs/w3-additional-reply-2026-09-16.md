# W3 C-01 canonical 계약 회신 — 2026-09-16 r2

**유일한 canonical 문서 경로: `docs/w3-additional-reply-2026-09-16.md`.**
계약 후보 `w3-c01/0.2-candidate`, 구현 profile `r2`.
**공동 채택 전**이며 본 문서는 W2의 반복 blocker 요청에 대한 W3 기술 회신이다.
이전 ZIP·루트 사본·다른 날짜 문서는 참고 이력으로만 사용한다.
W3→W4 A1–A5와 W1 계약은 별도 진행 사항으로 이 회신에서 재질의하지 않는다.

## 1. 접근 가능한 revision과 파일 무결성

저장소: https://github.com/Team-1AM-Club/Project_EPICK_Service
브랜치: `feat/w3-knowledge-validation`.
canonical 조회 경로:
https://github.com/Team-1AM-Club/Project_EPICK_Service/blob/feat/w3-knowledge-validation/docs/w3-additional-reply-2026-09-16.md

정본의 SHA-256은 같은 디렉터리의 `w3-additional-reply-2026-09-16.md.sha256`에 기록한다.
이번 배포의 **full commit SHA, commit에 고정한 canonical URL, SHA-256, 원격 push 확인 결과**는
전달 ZIP 루트 `CANONICAL_RECEIPT.json`에 기록한다. 브랜치 URL보다 그 immutable URL을 우선한다.
파일 안에 자기 자신의 hash/commit을 쓰는 순환 참조는 만들지 않는다.

이전 local commit `b6a3631bb0354824ab33d28274dc37ef989ea077`는 r1 이력이다.
이번 revision은 그 후속이며 r1을 공동 채택된 계약으로 소급 표시하지 않는다.

확인한 이전 불일치: 로컬 문서 SHA256
`dc75b808845af747a1cef8dc6b47f5a67233837cdd8f7b0f544dd6363fc32338`,
이전 W3_W4 Contract Reply ZIP의 동일 경로 SHA256
`3b5a2923c19a2852c18585253624874ade139652691fde217ce031906ab40c7a`.
로컬 LF / ZIP CRLF 208개였고 LF로 정규화하면 내용이 같았다.
별도로 전달된 모든 루트 사본을 조사한 것은 아니다. 이번 정본은 `.gitattributes -text`로
자동 줄바꿈 변환을 막고 로컬·Git blob·ZIP 동일 바이트를 배포 검사한다. 루트에 동명 문서를 복제하지 않는다.

## 2. schema / fixture / 테스트 정본

아래는 모두 위 full commit 기준이다.

| 대상 | 경로 |
|---|---|
| 입력/상태 모델 | `src/w3_knowledge/c01/contracts.py` |
| 적용/복구/색인 구현 | `src/w3_knowledge/c01/store.py` |
| producer 원본 payload schema | `src/w3_knowledge/c01/source-event-payload.schema.json` |
| 생성 schema | `contracts/c01/v0.2-candidate/{event,replay,snapshot,receipt,status,recovery,index-request}.schema.json` |
| 원본 정상4/오류2 | `contracts/c01/v0.2-candidate/fixtures/` |
| 순번 비교 재현 fixture | `contracts/c01/v0.2-candidate/examples/revision-unit-comparison.json` |
| 기존 복구 fixture | `contracts/c01/v0.2-candidate/examples/{replay,snapshot}.json` |
| W2 producer 입력을 W3 consumer에 넣는 계약 테스트 | `tests/integration/test_c01.py`, `test_c01_confirmed_contract.py` |
| HTTP 검사 | `tests/integration/test_c01_http.py` |
| 생성물 검증 | `scripts/export_c01_contract.py --check` |
| 실행 기록 | `docs/w3-canonical-verification-2026-09-16.json` |

원본 payload schema는 수정하지 않았다. confirmed replacement_ref UUID 제약은 W3 envelope
schema의 restriction branch에 더 엄격한 overlay로 표현하고 런타임에서도 검사한다.
실제 W2 producer 코드가 포함된 공동 테스트는 아니다. W2는 자기 producer 산출물을 동일
schema/fixture/test 기준으로 검증할 수 있다. 그 결과로 공동 채택을 자동 선언하지 않는다.

```powershell
uv sync --locked
uv run --locked python scripts/export_c01_contract.py --check
uv run --locked pytest tests/integration/test_c01.py tests/integration/test_c01_confirmed_contract.py -q
uv run --locked python scripts/c01_smoke.py
```

## 3. 이번에 확정 사항으로 반영한 규칙

- outer revision은 Source의 모든 public event를 통합한 연속 번호다.
- 별도 payload restriction_revision과 혼용하지 않는다.
- 버전 ID가 있는 restriction은 그 버전만, null은 Source 전체에 적용한다.
- 기존 `--restriction-scope source` 모드는 거절한다. `version`만 허용한다.
- replacement_ref는 null 또는 등록된 대체 Source UUID다. URI와 임의 문자열을 거절한다.
- 회사 식별이 미확정일 때 수집을 시작하지 않는 책임은 W2/상위 입력 경계에 있다.
  W3는 수집을 시작하거나 Company를 임의 확정하지 않는다.

replacement_ref 등록 확인은 현재 로컬 `c01_registered_sources`를 사용한다.
유효한 W2 event를 실제 수신했거나 snapshot을 수락한 Source만 등록한다. 충돌로 생긴 상태 행은
등록 근거가 아니다. 대상 Source를 아직 확인하지 못했다면 422 CONTRACT_INVALID,
내부 오류 REPLACEMENT_SOURCE_UNREGISTERED로 거절하고 receipt/cursor는 커밋하지 않는다.
replay/snapshot도 동일 검사를 수행하며 실패 시 해당 batch 전체를 rollback한다.
먼저 대상 Source의 유효한 event/snapshot을 제공한 후 같은 event ID/body로 재시도한다.
W3 내부 관측 여부가 W2 실제 registry 조회를 완전히 대체하는 것은 아니다.
W2의 등록 Source 조회/목록 계약이 제공되면 그 authoritative registry에 연결해야 한다.
특히 cold start의 순환 replacement 참조는 등록 목록/조회 연동 없이는 bootstrap이 막힐 수 있다.
이 제약은 producer 실연동의 선행조건이며 등록 확인을 건너뛰는 fallback은 없다.
이름은 그대로 보존하며 대체 Source를 자동 fetch하거나 현재 Version을 바꾸지 않는다.

r2는 scope/등록 규칙을 강화했으므로 r1 DB의 자동 열기/전환을 거절한다.
DB consumer identity는 `w3-c01/0.2-candidate/r2`다.
새 DB로 authoritative replay/snapshot을 하고 기존 W4 캐시를 무효화해야 한다.
wire 버전 표시는 아직 candidate이므로 0.2를 유지하되 **정확한 commit/profile로 호환성을 고정**한다.
r1/r2 혼용을 정식 호환으로 보장하지 않는다.

## 4. restriction_revision 확정 — 2026-09-20

**확정: (a) Source당 단일 연속 restriction 번호. W2/제품 책임자가 2026-09-20 채택했다.**
근거: `W3_Restriction_Revision_Decision_2026-09-20.md` 수신 문서.
W3-REPEAT-02의 단위 선택은 해소됐으며 다시 질의하지 않는다. per-ID 대안은 미채택 비교 이력이다.
동일 ID의 active → cleared → active는 허용하지만 적용 Source 및 Version/null 범위는 고정한다.
Version/Observation은 restriction 번호를 소비하지 않으며 outer revision은 별도 Source 순번이다.
W2 저장·adapter·재색인 ACK·W4 소비·배포·공동 검증 완료를 뜻하지 않는다.

W2는 이미 Source 단위 transport 번호를 원자적으로 할당한다. 같은 트랜잭션에서 restriction
event일 때만 별도 번호를 증가시키면 추가적인 writer 직렬화 범위가 필요하지 않다.
현재 W3의 scalar restriction watermark, gap 판정, snapshot 모델과 맞고 중간 단위 변경을 피한다.
여러 restriction ID를 병렬 생성할 필요가 있어 per-ID를 선택할 수는 있으나, 아래 다른 알고리즘과
새 snapshot/recovery 계약을 함께 채택해야 한다. 이번 코드는 (a)만 구현한다.

| 동작 | (a) Source 단위 — 구현됨 | (b) restriction_id 단위 — 설계 비교 |
|---|---|---|
| transport 비교 key | (source_id, revision), Source 전체 cursor | 동일 |
| dedup | event_id + canonical 원본 SHA; 동일 event 원본만 중복 | 동일 |
| 제한 순번 identity | (source_id, restriction_revision) | (source_id, restriction_id, restriction_revision) |
| 최신 상태 저장 | (source_id, restriction_id)별 최신 payload | 동일 |
| 정상 변경 | R = Source.R + 1 | R = map[restriction_id].R + 1; 첫 ID는 1 |
| 같은 R 다른 event | conflict | 같은 ID에서만 conflict; 서로 다른 ID의 R=1은 정상 |
| 제한 gap | Source.required_R > Source.R | ID별 required_R[id] > R[id]; max(R[id]) 하나로 판정 금지 |
| snapshot | 전체 ID 상태 + scalar R와 transport T | 전체 ID 목록 + ID별 R/map + transport T |
| snapshot merge | 알려진 ID 누락/변조 금지, T·R 비감소, 알려진 제한 순서와 합치 | 알려진 ID 누락 금지, T와 각 ID.R 비감소; ID들 사이 R 크기는 비교하지 않음 |

공통 처리 순서:
1. schema/Source/등록 참조 검증 후 event ID와 transport revision 충돌을 검사한다.
2. 같은 event 재전달이면 상태를 되돌리지 않고 DUPLICATE를 반환한다.
3. transport gap은 대기 저장하고 전체 Source 사용을 차단한다. 모든 event 종류를 순서대로 drain한다.
4. restriction event가 적용될 차례에 위 단위의 다음 R인지 검사한다.
   **더 큰 transport revision으로 과거 restriction R을 다시 발행하면 STALE로 숨기지 않고 conflict**다.
5. snapshot에 포함된 과거의 미관측 transport event가 뒤늦게 도착하면 STALE로 무시한다.
   이미 알고 있는 event ID의 변경은 이 경우에도 conflict다.
6. replay는 transport T 순서대로 복구한다. restriction 단위와 별개로 이벤트를 필터링해 누락시키지 않는다.

(a)의 snapshot은 최신 ID 상태들 사이의 restriction R이 transport 순서와 함께 증가해야 한다.
서로 다른 ID가 같은 R을 재사용하면 거절한다.
(b)는 이 검사를 전역에 적용하면 안 된다. per-ID 이력별로 검사하고 producer가 complete ID 집합을
원자적으로 제공해야 한다. 삭제된 ID를 tombstone 없이 목록에서 빼면 과거 제한이 복구될 수 있다.

### 교차 재현 시퀀스

모든 Version 정책은 테스트용 allowed, restriction accuracy는 verified_in_scope다.
A는 V1, B는 Source 전체(null), C는 V2에 적용한다.

| transport T | 변경 | (a) R | (b) ID별 R | W3 기대 결과 |
|---|---|---:|---:|---|
| 1 | V1 available | — | — | INDEX_PENDING |
| 2 | A active(V1) | 1 | A:1 | RESTRICTED |
| 3 | B active(null) | 2 | B:1 | RESTRICTED |
| 4 | A cleared(V1) | 3 | A:2 | B 때문에 RESTRICTED |
| 5 | V2 available | — | — | B 때문에 RESTRICTED |
| 6 | B cleared(null) | 4 | B:2 | INDEX_PENDING |
| 7 | A active(V1) | 5 | A:3 | V2에는 영향 없음, INDEX_PENDING |
| 8 | C active(V2) | 6 | C:1 | RESTRICTED |
| 9 | A cleared(V1) | 7 | A:4 | C 때문에 RESTRICTED |
| 10 | C cleared(V2) | 8 | C:2 | INDEX_PENDING, 재색인 후 READY |

fixture는 두 envelope 목록과 각 단계 기대값을 제공한다.
(a)는 실제 SQLite consumer 테스트로 검증한다.
(b)는 같은 의미를 표현한 설계 fixture이며 per-ID consumer 구현/검증 완료가 아니다.
(b)의 첫 B:1을 현재 (a) consumer에 넣으면 conflict가 되는 호환성 테스트도 포함한다.

### 변경/이행 제약

W2 producer는 Source T와 (a) R을 동일 transaction/outbox 생성에서 할당하고 rollback 시 번호를
소모하지 않아야 한다. T는 모든 public event에서 증가하고 R은 restriction에서만 증가한다.
하나의 restriction_id의 적용 대상(Source/Version)을 재사용해 바꾸는 정책은 별도 합의 없이는 금지해야 한다.
event ID와 이미 발행한 원본은 수정하지 않는다.

(a)→(b)는 필드 이름이 같아도 의미가 달라 **breaking change**다. 단순 rename이나 누적 R을
ID별 R로 복사하면 안 된다. producer가 ID별 authoritative 상태/순번을 재구성해 새 버전 snapshot을
제공하고, consumer의 watermark를 map으로 변경하고, 이전 이력/DB와 분리해야 한다.
Source-wide B도 하나의 restriction ID이며 별도 전역 비교 대상으로 섞지 않는다.
W3→W4에 전달하는 순번 의미 변경은 별도 A1–A5 채택 절차로 전달해야 한다.

## 5. replay retention: 정확한 소비자 제약

표기: T=consumer의 durable event_cursor, H=이미 확인한 required_event_cursor,
F=producer retention_floor_cursor, A=request after_cursor.

F는 **F보다 큰 revision을 복구 제공할 수 있는 경계**다. F 자체의 event 보관을 요구하지 않는다.

- producer는 A < F인 새로운 replay 요청에 전체 범위를 제공할 수 없으므로
  SNAPSHOT_REQUIRED와 Source/F/current high watermark를 알려야 한다. 이것은 producer API의 제안이다.
- W3 수신 `POST /c01/v1/replay`는 먼저 A > T이면 409 CURSOR_AHEAD로 거절한다.
- 그 외 T < F이면 HTTP 200, outcome=SNAPSHOT_REQUIRED, index_ack=false다.
  이 200은 복구 성공이 아닌, 복구 필요 상태의 반영 완료다.
- **T = F는 snapshot 필수가 아니다.** T 이후부터 replay하면 된다.
- 재시도 페이지의 A가 F보다 작아도 W3가 이미 T >= F까지 적용했다면 추가 snapshot은 필요 없다.
  판정 기준은 오래된 요청 A가 아닌 아직 미복구인 durable T다.
- required H는 max(기존 H, batch.high_watermark)로 유지한다. 낮은 페이지 H로 이미 아는 gap을 없애지 않는다.
- 같은 Source, A < event.T <= page.H, 오름차순/중복 없음, 500건 이하.
  페이지가 끝나도 durable T < H 또는 restriction gap이면 INCOMPLETE, index_ack=false.
- batch 적용은 BEGIN IMMEDIATE 단일 transaction이다. SQL/참조 검증 실패 시 전체 rollback한다.

snapshot 최소 필요 조건:
1. 미복구 T < F로 replay가 불가능함.
2. conflict 때문에 replay만으로 신뢰할 수 있는 상태를 확정할 수 없음.
3. 초기 동기화/DB 교체에서 producer가 전체 상태로 시작하도록 합의한 경우.

snapshot은 동일 원자적 시점의 Source T, restriction R, Version별 최신 상태,
restriction_id별 최신 상태(해제 상태 포함), 최신 observation 및 complete=true를 포함한다.
각 event.T <= snapshot.T이고 as_of는 포함 event의 occurred_at보다 이르면 안 된다.
W3 현재 시각과의 ±N초 오차 검사는 하지 않으며 aware timestamp로 비교한다.

수락 시 snapshot.T >= max(T,H), snapshot.R >= 알려진 적용/요구 R.
알려진 적용 상태뿐 아니라 gap 대기 event도 비교한다. ID 누락, 원본 변경,
restriction R 재사용/역전, 같은 snapshot cursor에 다른 body는 conflict다.
snapshot에 포함된 event identity도 저장한다. 동일 snapshot 재시도도 index를 폐기한다.
history_complete=false로 유지하며 gap/conflict 해소만으로 READY를 주지 않는다.
정책/제한이 허용되고 정확한 IndexKey 재색인을 마쳐야 index ACK를 해제한다.

### 최소 window와 시간 요구

**안전성에 필요한 고정 숫자 window/시간은 없다.** 과거 event가 하나도 없어도 위 complete atomic
snapshot이 제공되면 차단을 유지하면서 안전하게 복구할 수 있다. 대신 복구 가용성은 보장되지 않는다.
snapshot을 제공하지 않는다면 consumer의 마지막 ACK 이후 모든 event를 보관해야 하며,
무한 장애 시간을 허용하면서 유한 시간 보관으로 복구를 보장할 수는 없다.

운영 권고는 최대 예상 consumer 중단 시간 + backlog 재처리 시간 + 배포/장애 여유를 수용하는
window를 W2가 정하는 것이다. W3가 임의로 24시간/7일을 필수 계약으로 정하지 않는다.
W2의 publish timestamp, timezone, 기간은 W2 운영 결정이다. W3는 **정확한 F/H와 atomic snapshot**에
의존하고 콘텐츠 published_at이나 occurred_at을 outbox retention 시각으로 대체하지 않는다.
cursor의 ±1 허용 오차는 없다. 시간 오차로 안전하지 않은 floor를 조정해 받아들이지 않는다.

## 6. P4/P5 상태 행렬 — 제품 결정 전 release blocker

다음은 현재 구현 사실이며 최종 제품 정책은 PM/W2/W4가 정한다.
같은 적용 범위의 restriction은 cleared, 다른 active/gap/conflict 없음,
Version 자체 accuracy 문제 없음, collection/excerpt 허가 등 다른 조건은 모두 충족했다고 가정한다.

| restriction accuracy | redistribution allowed | unknown | denied |
|---|---|---|---|
| unverified | INDEX_PENDING → 재색인 READY | POLICY_BLOCKED | POLICY_BLOCKED |
| verified_in_scope | INDEX_PENDING → 재색인 READY | POLICY_BLOCKED | POLICY_BLOCKED |
| error_confirmed | RESTRICTED | RESTRICTED | RESTRICTED |
| superseded | RESTRICTED | RESTRICTED | RESTRICTED |

12개 조합을 실제 Store/index 테스트로 검증한다.
P4의 차단과 P5가 동시에 있으면 restriction 검사 우선이라 RESTRICTED를 출력한다.
유효 범위의 active는 항상 RESTRICTED다. 다른 버전의 restriction은 현재 버전을 차단하지 않는다.
READY는 색인 사용 가능성이며 unverified Claim을 VERIFIED로 바꾸지 않는다.
개별 Claim 의미 검증/usage 규칙은 기존 지식 처리 경계에 남아 있다.

| 정책 | 보존 정보 / 필요한 기존 필드 | 변경 영향 / 차단 |
|---|---|---|
| P4 cleared+오류 accuracy | restriction_status, accuracy_status, reason_code, source_version_id, restriction_revision | 현재 계속 차단. 완화 결정 시 기존과 결과가 달라져 재평가·캐시 무효화·재색인 필요. **release blocker** |
| P5 redistribution unknown | Version policy.redistribution_permission, policy_version, policy_decision_id 및 Evidence 해시 | 현재 차단. 내부 W4 사용을 재배포와 구분하려면 명시적 권한 정책과 책임 필요. **release blocker** |

원본 event의 정책/accuracy 의미를 allowed 또는 verified로 바꾸어 저장하지 않는다.
본문은 별도 보관 조건에 따라 제거한 projection/해시로 남기므로 원본 전체 event archive는 아니다.
현재 필드만으로 위 행렬은 표현할 수 있다. 새 기본값/추가 권한 필드가 필요하면 PM/W2/W4가
정의해야 하며 W3가 임의로 wire field를 추가하지 않는다.

## 7. 공동 채택 상태와 W2가 지금 할 수 있는 검증

가능: 3종 event 분기, UUID/등록참조 거절, scope, Source R 후보 시퀀스,
중복/늦은 event/gap/replay/snapshot/floor, 정책 조합과 index ACK를 정본 commit에서 검증.
불가: per-ID runtime이 이미 구현됐다는 주장, W2 registry 실제 조회 완료 주장,
P4/P5 제품 승인이나 W4 consumer acceptance 완료 주장.

| 채택을 막는 항목 | 소유자 / 선행조건 |
|---|---|
| restriction R 단위 최종 결정 | **해소(2026-09-20)**. W2/제품이 Source 단일 연속 순번 채택 |
| P4/P5 최종 정책 | PM/W2/W4. 위 행렬 수락 또는 구체적 변경값 결정 |
| replay F/H·원자 snapshot 생산 | W2. producer 계약 및 실제 운영 보관/복구 구현 |
| authoritative replacement Source 등록 확인 | W2 registry 조회/목록 계약 + W3 연결. 현재 로컬 관측 목록은 보수적 후보 |
| end-to-end producer 계약 검사 | W2/W3, 같은 commit/profile에서 실제 producer 산출물로 실행 |

이미 확정된 transport revision·Version/null scope·replacement 의미·Company 전제는 다시 선택지로 묻지 않는다.
W4 A1–A5와 W1 요청은 이 문서의 제외 범위를 유지한다.
