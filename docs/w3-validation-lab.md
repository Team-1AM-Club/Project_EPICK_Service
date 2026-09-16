# W3 로컬 통합 검증 환경 (LAB ONLY)

이 환경은 합성 이벤트로 restriction, 색인 불일치, replay, snapshot 재동기화,
보관기간 경계를 재현하기 위한 독립 실험실이다. W2 이벤트 스키마나 D-05 ACK
계약으로 채택된 인터페이스가 아니다. 실제 서비스의 consumer/검색/권한 포트에
연결되지 않으며 운영 연동 완료를 증명하지 않는다.

## 설계와 작업

- SQLite에 consumer별 이벤트 ID, 원래 payload, Source 상태를 보존한다.
- 중복 이벤트는 멱등 처리하고, 역순 이벤트는 기록하되 현재 상태를 되돌리지 않는다.
- revision gap 동안 사용과 ACK를 차단한다. 보관된 연속 이벤트 replay 또는
  최신 snapshot으로만 회복한다. snapshot 복구 이후 색인을 다시 확인한다.
- 색인 키는 실험용 `(version, extraction, normalization)`이다. 모든 값이 맞고
  restriction이 해제된 경우에만 실험용 성공 ACK를 제공한다.
- replay 보관기간과 원문 보관기간은 별개다. 경계 시각부터 만료로 처리한다.
- 시나리오의 시간은 합성 초 단위 정수다. 실제 시스템 시간·대기·외부 API는 사용하지 않는다.

팀 확인이 필요한 사항: 공식 이벤트 필드/정렬 단위, ACK 전달 방식과 키,
restriction 해제 권한, replay 보관기간, snapshot 인증/일관성, 원문 및 tombstone
보관기간, 실제 인덱스 무효화와 W4 캐시 정책.

## 실행

Python 3.11 이상이면 외부 패키지, API 키, Docker 없이 실행할 수 있다.
저장소 루트에서:

```powershell
python src/w3_knowledge/lab.py --scenario examples/w3-lab-scenario.json
```

전달 ZIP을 풀었다면 압축 해제 폴더에서:

```powershell
python lab.py --scenario scenario.json
```

성공 결과는 `"lab_only": true, "passed": 27, "total": 27`이다.
종료 코드는 성공 0, 예상 결과 불일치 1, 잘못된 입력/상태 2이다.
각 단계의 번호·연산·성공 여부를 JSON으로 출력하며 원문 내용은 출력하지 않는다.
기본 DB는 실행마다 새로 만드는 메모리 SQLite다.
`--db lab.sqlite`를 추가하면 이벤트와 consumer 상태가 파일에 남는다.
같은 파일로 전체 시나리오를 재실행하면 초기 상태가 달라 기대값이 실패할 수 있으므로,
전체 재검증은 기본 메모리 DB 또는 새로운 DB 파일을 사용한다.

## 재현 항목과 기대 결과

| 항목 | 재현 방법 / 판정 |
| --- | --- |
| 제한 적용 | 정상 색인 뒤 blocked=true 이벤트를 소비하면 RESTRICTED, ACK false |
| 중복·역순 | 동일 이벤트는 DUPLICATE, 과거 revision은 STALE; 현재 제한을 되돌리지 않음 |
| 충돌 | 동일 event ID의 다른 payload, 동일 Source/revision의 모순된 상태를 거부 |
| 색인 불일치 | 버전·추출·정규화 키 중 하나라도 다르면 INDEX_MISMATCH, ACK false |
| 색인 실패 | SQL의 기대 키 유지, INDEX_FAILED; 성공 재시도 후에만 ACK 가능 |
| 이벤트 누락 | revision gap을 발견하면 EVENT_GAP; 보관기간 내 연속 replay로 복구 |
| replay 만료 | 누락 이벤트가 만료되면 SNAPSHOT_REQUIRED; 과거 snapshot으로 복구 불가 |
| snapshot 복구 | HISTORY_UNAVAILABLE 표시, 색인 재확인 필요 |
| 보관 범위 | FULL은 원문, EXCERPT는 발췌만, NONE은 본문 미보관; 발췌로 원문을 생성하지 않음 |
| 원문 만료 | 만료 경계부터 읽기 불가 및 BODY_UNAVAILABLE, ACK false |
| 버전 변경 | 기존 버전에 속한 보관 본문 무효화; 예전 본문 반환 방지 |

기본 JSON은 대표 흐름을 27단계로 실행한다. consumer별 멱등성·재시작,
각 색인 키의 불일치, FULL/EXCERPT/NONE, 충돌 거부 등은
`tests/acceptance/test_validation_lab.py`에서 추가 검증한다.

## 임시 인터페이스

이벤트는 Source의 합성 상태 snapshot이다. 공식 restriction.changed 이벤트와
동일한 구조라고 가정하면 안 된다. 허용 필드는 다음 7개뿐이며 추가 필드는 거부한다.

```json
{
  "event_id": "demo-2",
  "source_id": "demo-source",
  "revision": 2,
  "blocked": true,
  "version": "v1",
  "extraction": "ex1",
  "normalization": "norm1"
}
```

revision은 Source별 1부터 시작하는 연속 양의 정수다.
version은 SourceVersion에 대응시키기 위한 실험용 키다. Evidence/Claim 계약은
이 도구의 범위가 아니다. ack는 외부로 전송하는 메시지가 아니라 실험 상태의 사용 가능 여부다.
본문 정책을 아직 적용하지 않은 Source는 본문 없이 색인/이벤트 흐름만 검증할 수 있다.
retain으로 본문 정책을 적용한 뒤에는 본문 부재도 ACK 차단 사유가 된다.

각 단계는 op, 함수 인자, expect로 구성한다. expect가 객체이면 지정한 필드만 비교하고,
그 외에는 값 전체를 비교한다.

| op | 주요 인자 | 역할 |
| --- | --- | --- |
| publish | event, at | 합성 producer journal에 저장만 수행 |
| consume | event, now | journal 저장 후 consumer에 적용 |
| query | source_id, now | 현재 상태와 ACK 조회, now 지정 시 본문 만료 확인 |
| index | source_id, key, fail, now | 색인 성공/실패 모의 주입; fail 기본값 false |
| replay | source_id, now, retention | 보관기간 내 journal 재소비 |
| snapshot | event, now | 신뢰된 호출자가 제공한 최신 상태로 복구 |
| retain | source_id, full, excerpt, scope, now, ttl | 지정한 범위의 본문 보관 |
| read_body | source_id, now | 제한·gap·만료 검사 후 본문 읽기 |

## 보관 정책 검증의 범위

- 시간은 테스트용 초 단위 값이다. 실제 시계나 백그라운드 만료 작업은 없다.
  본문 만료 검증 시 query/index에도 반드시 now를 전달한다.
- 예시의 retention=10, ttl=10은 경계값 재현용이며 운영 보관기간 제안이 아니다.
- replay는 `now-retention < at <= now`만 허용한다. 경계 시각은 만료다.
  journal/receipt는 실제로 삭제하지 않으므로 replay 가능 기간을 검증하는 기능이다.
- 본문은 `now >= expires`이면 SQLite 행을 논리 삭제한다. DB 페이지·백업의 완전 삭제,
  tombstone 보관기간, 외부 색인/캐시 삭제를 검증하지 않는다.
- restriction은 읽기와 ACK를 차단한다. 제한 이벤트가 본문 영구 삭제를 뜻한다고
  가정하지 않는다. 삭제/보관 전환은 공식 정책 합의 후 실제 어댑터에서 구현해야 한다.

## 팀 합의 후 남은 연동 작업

1. 수집 담당: 공식 이벤트 필드·revision 정렬 단위·재전송 규칙·replay API와 기간,
   최신 snapshot 조회의 인증/일관성을 확정한다.
2. W3 및 매칭 담당: 색인 키, D-05 ACK 형식/전송/재시도, 제한 해제 권한,
   검색 인덱스 무효화와 W4 캐시 차단 경로를 합의한다.
3. 정책 담당과 구현 담당: 원문·발췌·tombstone·백업 보관기간 및 삭제 범위를 확정한다.
4. W3 구현 담당: 공식 계약 어댑터와 실제 consumer/index 연결을 추가하고,
   W2→W3→W4 통합 테스트에서 같은 시나리오를 다시 검증한다.

현재 제공 범위는 로컬 합성 검증 환경이다. 이 결과가 실제 연동 완료나 운영 정책 충족을
의미하지 않는다.
