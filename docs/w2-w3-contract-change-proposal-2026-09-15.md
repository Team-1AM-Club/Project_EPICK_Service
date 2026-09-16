# W2 → W3 계약 매핑·변경 제안서

문서 버전: `w2-w3-mapping-review/2026-09-15.1`  
대상: PM / W1 / W2 / W3 / W4  
상태: **변경안 검토용. 기존 W3 코드·Schema 변경 없음. 팀 승인 전.**

전달된 W2 샘플은 기존 `w3-restriction/0.1-draft`에 그대로 연결되지 않는다.
W2 원본을 보존하는 입력 어댑터와 별도의 상태 갱신 처리를 제안한다. 단순 필드명 치환으로
연결하면 이벤트 누락을 잘못 판단하거나 다른 SourceVersion의 키를 사용할 수 있다.
이번 문서는 변경 범위·영향·검증 계획을 제시하며 구현이나 통합 완료를 의미하지 않는다.

## 1. 확인한 자료와 확인하지 못한 사항

이번 검토 ZIP의 `received/`에는 PM이 제공한 원본을 변경 없이 포함했다.
원본·비교 기준 파일의 SHA-256은 `review-manifest.json`에 기록했다.

| 자료 | 확인한 내용 | 한계 |
| --- | --- | --- |
| `received/source-event-payload.schema.json` | JSON Schema 2020-12, `$id`의 W2 v1, payload의 `schema_version=w2.source.v1` | **payload 전용**. envelope·command/result·replay/snapshot의 Schema가 아님 |
| `received/w2/source-event-*.json` 4개 | 버전 complete/partial, 관측 변경, 제한 active의 전체 envelope+payload | envelope는 `schema_version=1.0`. 정식 envelope Schema와 승인 기록은 미제공 |
| `received/w2/invalid-source-event-*.json` 2개 | payload의 private `job_id`, transport `event_id/revision` 혼입 사례 | payload와 envelope의 경계 검증용 사례 |
| `received/w2/source-collection-*.json` 4개 | private command 및 complete/partial/failure result, 삭제 epoch·작업 fence의 필드 | private DTO의 타입/승인/멱등·삭제 의미와 실제 구현 여부는 미확인 |
| W3 `contracts/restriction/v0.1-draft/` 및 `src/w3_knowledge/restriction/` | 현재 후보의 DTO·제한 전용 순번·ACK/색인/복구 처리 | 로컬 후보이며 W1/W2/W4 공식 계약 채택 전 |

자료의 버전 표시는 확인했지만 팀의 정식 채택을 증명하지 않는다. 이번 검토에서는 JSON
구문·필드 비교 및 기존 W3 입력 DTO의 호환성만 확인한다. W2 JSON Schema 엔진을 통한
전체 validation, 실제 W2 연결, runtime 전체 테스트는 실행하지 않는다.

## 2. 먼저 결정해야 할 세 가지

1. **두 순번의 범위:** envelope revision과 restriction_revision의 각각의 정렬 단위·연속성.
2. **제한 대상:** restriction의 source_version_id가 있으면 해당 버전만 제한하는지,
   Source 전체 제한의 참고 정보인지. null의 정확한 의미.
3. **재생 기준:** outbox 게시 시각·보관 경계와 replay/snapshot의 일관된 cursor 계약.

W3 권고안은 §3의 이중 순번 처리와 §4의 정확한 버전 참조다. 이 세 의미를 확인하기 전에는
공용 DTO와 핵심 상태 모델을 변경하거나 W2 연결 완료로 표시하지 않는다.

## 3. 이벤트 순번 매핑 — 단순 rename 불가

| 샘플 | envelope revision | payload restriction_revision | 대상 버전 |
| --- | --- | --- | --- |
| version-available | 1 | 없음 | UUID 끝자리 `0007` |
| version-partial | 2 | 없음 | UUID 끝자리 `0009` |
| observation-changed | 3 | 없음 | null |
| restriction-changed | 4 | 1 | UUID 끝자리 `0007` |

위 표는 전달된 사실이다. **같은 Source의 세 이벤트 종류가 envelope 순번을 공유하는 것으로
보이지만**, 전체 생산 규칙은 W2 확인이 필요하다. 기존 W3는 제한 이벤트만 받고 하나의
revision으로 누락·제한 상태를 함께 처리한다.

### P-01 권고: transport cursor와 제한 상태 revision 분리

- W2 원래 event_id·envelope·payload를 불변으로 저장한다. 내부 필드명 변경을 이유로
  event_id를 다시 생성하거나 원래 revision을 덮어쓰지 않는다.
- Source별 **event cursor**는 version/observation/restriction을 모두 수신·검증·저장한
  순서를 추적한다. 현재 연속 구간과 관측한 최대 revision을 별도로 둔다.
- **restriction revision**은 제한 상태의 최신성을 판정한다. 범위가 Source별인지,
  restriction_id별인지, 버전별인지 W2가 확정해야 한다. 현재 샘플 1개로 결정하지 않는다.
- `(producer, event_id)` 또는 승인된 전역 event ID에 대한 consumer 멱등 처리를 유지한다.
  동일 ID의 다른 내용과 같은 정렬 키의 모순은 격리한다.
- version/observation 이벤트를 가짜 RELEASED 이벤트로 변환하지 않는다. 관측 404는
  그 자체로 제한 해제나 과거 Evidence 삭제가 아니며 당시 null을 그대로 보존한다.
- 순번 4의 active 제한이 먼저 도착하면 누락 복구 동안 해당 범위의 사용을 차단한다.
  늦은 cleared나 누락 상태에서의 해제는 허용을 복구하지 못한다.

대안은 restriction 전용 독립 스트림을 W2가 제공하는 것이다. 이 경우 별도 cursor와
완전성·replay 보장이 필요하다. 기존 혼합 스트림을 필터링한 뒤 envelope 번호가 비었다는
이유로 gap을 만들거나, restriction_revision만으로 모든 Source 사건을 덮어서는 안 된다.

## 4. 필드별 매핑 및 변경 범위

| W2 필드/의미 | 현재 W3 후보 | 권고 매핑·처리 | 상태/영향 |
| --- | --- | --- | --- |
| envelope schema_version=1.0, payload=w2.source.v1 | 단일 w3-restriction/0.1-draft | 외부 W2 계약 버전과 내부/W4 계약 버전을 분리. 원본 두 버전 보존 | envelope 정본 필요 |
| event_type 3종 | restriction.changed만 허용 | 종류별 payload 검증·처리 분기. event_type과 payload branch 일치 검증 | 입력 어댑터·dispatcher 추가안 |
| producer, aggregate_type=source, aggregate_id | producer/aggregate_type 없음 | envelope 검증 후 payload.source_id와 aggregate_id 일치 확인 | envelope 검증 범위 추가안 |
| envelope revision / restriction_revision | aggregate_revision 하나 | §3의 이중 순번으로 분리 | 상태 저장·replay·응답/Signal 변경 영향 |
| UUID ID | 영문자로 시작하는 ID 정규식 | 원래 UUID를 그대로 수용. 접두사 추가·새 ID 생성으로 통과시키지 않음 | SourceRef·Evidence·IndexKey·W4 ID 경계 함께 검토 |
| active / cleared | RESTRICTED / RELEASED | 내부 의미 대응은 가능하나 cleared는 개인 권한/삭제 복구나 Claim 검증 승인이 아님 | 상태 enum 대응안 |
| accuracy_status 4종 | UNKNOWN/CONFIRMED/DISPUTED | W2 원래 값 보존. 특히 verified_in_scope를 Claim VERIFIED로 승격하거나 superseded를 자동 해제로 해석하지 않음 | enum·의미 모델 변경안 |
| reason_code=자유 문자열, 예 SOURCE_POLICY_BLOCKED | 제한된 enum | 원래 code 보존, 허용 코드/비밀정보 금지 정책은 W2와 정의. 낯선 코드를 임의의 일반 코드로 치환하지 않음 | 공용 사유 분류 확인 |
| replacement_ref=string/null | replacement_source_id | Source ID인지 version ID/URI인지 미확인. 원문 참조 보존 후 종류를 합의 | 일대일 매핑 보류 |
| restriction.source_version_id=UUID/null | Source 전체의 현재 index_key | P-02의 제한 범위 결정 후 상태 키 설계. null을 이전 버전으로 채우지 않음 | **범위 승인 필요** |
| 제한 payload에 extraction/representation/normalization 없음 | 모든 제한 이벤트에 전체 IndexKey 또는 null | 정확히 참조된 SourceVersion의 검증된 버전 기록/조회에서 해소. 모르면 사용 보류 | 버전 저장·조회 계약 필요 |
| metadata.representation=static_html | text/html/markdown | 원본 representation을 유지하는 키 확장안. static_html을 html로 조용히 치환하지 않음 | IndexKey·W4 정규화 합의 |
| changed_at / envelope occurred_at | occurred_at 및 outbox published_at | 각 시각의 의미 보존. outbox 게시 시각을 둘 중 하나로 추정하지 않음 | 보관 기준 시각 미제공 |
| version.payload.published_at=DateValue | event.published_at=게시 timestamp | **서로 다른 필드 의미**. 콘텐츠 발행일을 outbox 시각으로 사용하지 않음 | 날짜 null 보존·replay 설계 영향 |
| excerpts_only / normalized_body, body ref | full/excerpts_only/none + expires_at | 권한과 실제 보관 범위를 확인해 별도 내부 보관 요청 구성. normalized_body를 무조건 full 허용으로 승격하지 않음 | TTL·본문 접근 계약 필요 |

### P-02 제한 범위 결정안

샘플의 최신 성공 버전은 끝자리 0009이고, 제한 이벤트는 이전 버전 0007을 참조한다.
따라서 다음 중 어떤 의미인지 W2/PM의 답변이 필요하다.

| 선택 | source_version_id가 있을 때 | null일 때 | W3/W4 영향 |
| --- | --- | --- | --- |
| 버전 범위 모델 | 해당 버전에 대한 제한 | Source 전체인지 다른 의미인지 별도 정의 | Source-wide 상태와 버전별 상태를 함께 검사. Source 하나의 Signal만으로 모든 버전을 동일하게 취급할 수 없음 |
| Source-wide 모델 | Source 전체 제한의 근거/관찰 버전 참조 | 버전 미확보 상태의 Source 전체 제한 등 의미 명시 | 기존 Source-wide 차단을 유지할 수 있으나 참고 버전과 현재 색인 대상 키를 분리 |

확인 전에는 관련 자료를 사용 보류하는 것이 안전하다. 이 임시 보수 처리 자체를 정식
Source-wide 정책으로 채택했다고 해석하지 않는다. 한 제한의 cleared가 다른 활성 제한까지
해제하는지도 함께 확인해야 한다.

### P-03 정확한 IndexKey 구성안

제한이 참조하는 버전 0007을 해소해야 하는 경우, 버전 0009의 extraction 0010을 사용하면
안 된다. 제공된 version-available 샘플에서 얻는 참조 조합은 다음과 같다.

```json
{
  "source_version_id": "10000000-0000-4000-8000-000000000007",
  "extraction_revision_id": "10000000-0000-4000-8000-000000000008",
  "representation": "static_html",
  "normalization_version": "v1"
}
```

이는 **W2 자료에서 확인한 키 조합**이며 현재 W3 DTO에 이미 수용되는 입력이 아니다.
같은 SourceVersion의 재추출 revision이 여러 개라면 제한에 적용할 extraction의 선택 규칙이나
W2의 정확한 조회 응답이 추가로 필요하다. “가장 최근 이벤트의 키”를 사용하지 않는다.
P-02의 Source-wide 모델을 택하면 제한 근거 버전과 실제 색인 대상 버전의 키를 별도로 관리한다.

## 5. 공용 payload·날짜·부분 추출 처리의 수정 제안

### P-04 이전 회신문서의 공용 본문 금지 범위 정정

9월 14일 회신의 “공용 event에는 원문·발췌를 넣지 않는다”는 포괄 규칙은 이번 W2
SourceVersion payload와 충돌한다. Schema를 수정하기 전에 다음과 같이 문서 변경을 제안한다.

- restriction/observation event와 W3 usability signal에는 원문·개인 ID·비밀값을 넣지 않는다.
- source.version.available은 **허용된 공용 Evidence 발췌·공고 구역**을 포함할 수 있다.
  정책 필드와 사용 목적을 검사해야 하며, 수집 허가를 재배포 허가로 간주하지 않는다.
- command/result의 job_id·owner·purpose·삭제 epoch·fence는 private 제어 정보다.
  공용 이벤트 및 W4 공용 Signal에 복사하지 않는다.
- 현재 샘플의 redistribution_permission과 body_storage_permission은 unknown이다.
  발췌 보관 허가만으로 모든 외부 표시·본문 보관을 자동 허용하지 않는다.

이 규칙은 이전 회신을 조용히 수정한 결과가 아니라 이번 자료에 따른 변경 제안이다.

### 날짜·관측·부분 추출

- published_at/valid_from/valid_to의 unknown/null을 보존한다. collected_at으로 대체하지 않는다.
- 관측 이벤트의 source_version_id=null과 representation=null을 이전 성공 값으로 덮지 않는다.
  현재 Source 조회로 과거 observation snapshot을 대체하지 않는다.
- partial에는 우대 구역 누락만 명시되어 있다. 미제공 우대 조건을 Claim으로 만들지 않으며
  기업 근거 사용 범위는 기존 W4 정책 및 필수 구역 합의에 따른다.
- source-collection-result-partial의 code는 PARTIAL_REQUIRED_SECTION이지만 missing_sections는
  preferred다. 코드 명칭과 실제 영향의 정합성을 W2에 확인하고 임의 자격 미달로 처리하지 않는다.
- 기존 W3 전달서에서는 일부 미확보 관측의 acquisition_status가 null일 수 있다고 설명했으나,
  이번 payload Schema는 해당 필드를 필수 enum으로 제한한다. 새 Schema가 이전 설명을
  대체하는지, null 관측용 별도 표현이 있는지 확인한다.

## 6. W1 private lifecycle 연계 — 기존 제안 C-01과의 관계

command 샘플의 owner_deletion_epoch=0, execution_fence, input_version은 늦은 개인 작업을
차단하는 데 사용할 수 있는 **후보 연결점**이다. 아래 의미는 자료만으로 확정되지 않았다.

| 필드 | 확인된 사실 | 확인할 의미 / W3 제안 |
| --- | --- | --- |
| owner_deletion_epoch | command에 정수 0이 존재 | 누가 증가시키는지, owner 전체/개별 리소스 삭제 범위, 비교·존재 확인·복구 규칙을 W1이 정의 |
| execution_fence | 합성 문자열 존재 | 생성·검증 주체와 낡은 작업 commit 거부를 원자적으로 보장하는 방법 확인 |
| input_version | command/result에 존재 | 해당 개인 입력 버전의 범위·현재 버전 비교 규칙 확인 |
| result_version | result에 존재 | 결과 멱등/역순 기준 확인. W3 공용 generation과 별개 |

owner 삭제 epoch만으로 개별 Episode/Project 삭제까지 표현된다고 가정하지 않는다.
collection result에는 command의 epoch/fence가 그대로 실리지 않으므로 W1이 command_id 등으로
원래 실행 문맥을 조회·검사하는지, result 계약에 추가 확인 정보가 필요한지 결정해야 한다.
W3 Source 제한 해제나 replay는 개인 삭제 상태를 복구하지 못해야 한다.
private 스키마와 승인 절차를 확인하기 전에는 이 샘플을 공용 consumer의 새 필드로 추가하지 않는다.

## 7. W4 ACK·Signal 영향

| 현재 유지할 원칙 | 변경 검토가 필요한 부분 |
| --- | --- |
| 저장 receipt와 실제 색인 ACK를 분리 | transport cursor와 restriction revision을 응답에 각각 표현하는 방식 |
| mismatch·실패·gap 중 사용 차단 | 제한이 버전 범위인 경우 status/Signal 대상 식별과 cache 키 |
| 실제 색인 쓰기·정확한 키 확인 후 사용 허용 | UUID 및 static_html을 포함한 IndexKey·Signal의 버전 변경 |
| W4 generation으로 같은 제한 revision의 색인·TTL 변화도 구분 | generation은 W2 envelope revision 또는 owner_deletion_epoch로 대체하지 않음 |
| 현재 W3 상태 및 W1 개인 권한/삭제 상태 재검사 | private fence·epoch와 공개 usability의 결합은 별도 W1/W4 계약 |

현재 Signal의 required_revision을 어떤 W2 순번으로 해석할지도 새 계약에서 명확히 해야 한다.
이중 순번을 기존 한 필드에 섞어 넣지 않는다. 입력 어댑터만으로 해결되지 않는 출력 변경은
W4와 함께 새 버전으로 검토하고 기존 0.1-draft를 덮어쓰지 않는다.

## 8. PM/W2에 요청할 확인 자료

| 우선순위 | 확인·제공 요청 | 이유 |
| --- | --- | --- |
| P0 | envelope 정본 Schema, event_type→payload branch 매핑, producer/ID 불변 규칙 | payload oneOf만으로 event_type과 내용 일치나 envelope를 검증할 수 없음 |
| P0 | revision과 restriction_revision의 범위·연속성·다중 restriction 정렬 규칙 | 잘못된 gap·해제 방지 |
| P0 | source_version_id 유무별 제한 범위 및 cleared 예시 | 이전 버전 제한과 Source 전체 제한을 구분 |
| P0 | replay/snapshot Schema·cursor·원자성, outbox 보관 기준 시각·경계 | 제공된 이벤트에는 기존 W3가 요구하는 outbox published_at이 없음 |
| P0 | 전체 키 해소 조회와 재추출 선택 규칙, replacement_ref 종류 | 오래된 버전과 최신 extraction 혼합 방지 |
| P1 | command/result 스키마, owner_deletion_epoch/fence의 권위·commit 검사 | 개인 삭제·늦은 작업 복원 방지 |
| P1 | null 관측 허용 범위, partial 코드 의미, 공개 발췌 재사용 정책 | 이전 문서와 새 Schema의 차이 해소 |

Schema에 DateValue.status와 value의 조합, Evidence와 enclosing SourceVersion 일치,
locator end>start 등의 모든 의미 조건이 표현된 것은 아니다. 승인 후 어댑터 검증에는
이 조건을 별도로 포함해야 한다. 예시 content_hash의 반복 a/b 값도 실제 원문 무결성 검증
통과 증거로 쓰지 않는다.

## 9. 승인 후 구현·검증 순서

| 단계 | 변경할 부분 | 완료 판정 |
| --- | --- | --- |
| I-01 | 버전 고정 W2 envelope/payload 어댑터, UUID·이벤트 종류 검증 | 정상 4종 수용, private/transport 혼입 거부, 원본 ID 보존 |
| I-02 | event cursor와 제한 상태를 분리한 저장·멱등·복구 | 혼합 revision 1→2→3→4가 가짜 gap 없이 진행, 누락·충돌·역순은 차단/보존 |
| I-03 | 승인된 제한 범위와 정확한 SourceVersion/extraction/representation 키 해소 | 0007 제한에 0009/0010 키를 혼합하지 않음, 미확인 범위는 허용하지 않음 |
| I-04 | 실제 색인·ACK·W4 status/Signal 호환 버전 | 정확한 키의 성공 후만 허용, 기존 cache와 늦은 신호로 제한을 되돌리지 않음 |
| I-05 | 승인된 private 경계 연결 및 W2→W3→W4 공동 검증 | 삭제/fence/epoch·partial·날짜 미상·복구 시나리오를 실제 계약으로 검증 |

SQLite 저장·FTS·트랜잭션·캐시 차단에서 재사용 가능한 부분은 유지한다. 입력 형식만 치환해
기존 restriction 전용 reducer에 넣는 방식은 제안하지 않는다. 기존 어댑터가 완성되기 전
모든 자료를 현재 W3 서버에 직접 POST하는 방식도 사용하지 않는다.

이번 검토의 실행 근거는 `compatibility-check.json`이다. JSON 구문 확인과 기존 W3 DTO의
직접 수용 불가만 기록하며 W2 Schema 전체 통과·새 어댑터 구현·실서비스 연결로 표현하지 않는다.

## 10. PM에게 보낼 회신

> 전달해 주신 W2 payload Schema와 샘플 10개를 기준으로 W3 매핑·변경안을 정리했습니다.
> 핵심은 전체 Source 이벤트 revision과 restriction_revision의 분리, 제한 적용 범위 확인,
> 정확한 SourceVersion의 IndexKey 해소입니다. 버전 이벤트의 허용된 발췌 전달을 반영해
> 기존 공용 event 금지 문구의 수정안도 포함했습니다.
> envelope/replay/snapshot 정본, 순번·제한 범위 및 cleared 예시 확인을 요청드립니다.
> 기존 W3 코드와 Schema는 변경하지 않았으며, 위 의미를 합의한 뒤 어댑터와 상태 모델을
> 별도 버전으로 구현하겠습니다.
