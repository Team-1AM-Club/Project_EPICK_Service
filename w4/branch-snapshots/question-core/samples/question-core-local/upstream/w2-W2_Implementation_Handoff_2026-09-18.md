# W2 → W1 독립 산출물 인계 및 채택 요청

작성일: 2026-09-18 (Asia/Seoul)

상태: **T082~T085 W2 독립 산출물 완료 / W1 채택 요청**
수신: W1 Platform 담당자 및 담당 Agent

이 문서는 Engine 저장소에 함께 전달되는 사본이다. 경로는 모두 저장소 루트 기준이며, 검증 기록과 checksum은 이 디렉터리에 있다.

## 1. 완료 범위와 계약 상태

W2가 확정 W1 계약을 반영하고, 별도 private staged-result·ACK 후보를 준비했습니다. W1 채택·실제 queue 연결·공동 CT15 및 전체 W2 구현 완료를 의미하지 않습니다. 기존 worker의 same-transaction 권한 경로는 유지하며 새 경로로 자동 교체하지 않습니다.

- Service 검토 pin: `afec08a9602132e5e433b523b0b6804850440524`.
- 기존 Engine 기준 commit: `0865ecdfe4748dad5679bc82b9f7386dc663675e`. 이 값은 신규 산출물의 commit SHA가 아니다. 실제 인계 revision은 이 파일이 포함된 `feat/crawler`의 full SHA를 별도로 전달한다.
- W2 후보 상태: `W2_UNADOPTED_PROPOSAL_QUEUE_DISCONNECTED`.
- 접근 가능한 Git commit의 full SHA와 아래 파일별 checksum을 함께 검증해야 한다. Git push만으로 W1 계약 채택·배포를 뜻하지 않는다.

이미 해결된 direct registration 분리, uppercase decision owner, numeric pin 결속, partial action 및 nullable policy 규칙을 다시 결정해 달라는 요청이 아닙니다.

## 2. 산출물 위치

아래 Engine 경로는 `Project_EPICK_Engine/` 기준입니다.

| 산출물 | 경로 | 상태 |
| --- | --- | --- |
| 확정 dispatch·lookup·result codec | `src/epick_engine/source_collection/w1_transport.py` | T082 완료 |
| W1 gate codec 및 W2 proposal builder/digest | `src/epick_engine/source_collection/commit_gate_contracts.py` | T083 완료 |
| W1 pinned snapshot·provenance | `tests/fixtures/w1_private_contract/manifest.json` | pinned snapshot, 운영 환경 확인 아님 |
| W2 정상·음성 fixture·고정 digest vector | `tests/fixtures/w2_commit_gate_proposal/` | 제안 지위 유지 |
| private store | `src/epick_engine/source_collection/commit_gate_store.py` | T084 완료, 실제 PostgreSQL 검증 |
| additive migration | `migrations/versions/0004_private_commit_gate.py`, `migrations/env.py` | 0003→0004 isolated upgrade 검증; destructive downgrade 미지원 |
| test-only inspection | `tests/support/commit_gate_inspection.py` | owner/command별 검사, 운영 endpoint 아님 |
| private DB regression | `tests/integration/source_collection/test_private_commit_gate.py` | 실제 PostgreSQL 35개 |

계약 설명과 JSON Schema:

- [W2 proposal 규칙](w2-commit-gate-proposal.md)
- [ACK schema](source-collection.commit-gate-ack.schema.json)
- [staged-result schema](source-collection.staged-result.schema.json)
- [검증 기록](verification.md)
- [artifact SHA-256 manifest](artifacts.sha256) — Engine 저장소 루트 상대 경로와 실제 file bytes 기준.

W1은 제공받은 full SHA에서 이 문서와 manifest에 기록된 파일을 가져와 checksum을 검증해 주십시오. 이 문서 안에는 자기 자신의 commit SHA를 고정하지 않는다.

Store entry point는 `stage_private_result`, `apply_commit_gate`, `read_finalized_result`입니다. 호출 측 root transaction을 사용하며 내부 commit은 하지 않습니다. 초기 row가 없어도 command 단위 transaction advisory lock으로 경쟁을 직렬화합니다. STAGED/PREPARED는 비가시, FINALIZED만 owner별로 반환합니다. ABORT/PURGE는 결과와 staged 제출 본문을 제거하고 anti-resurrection용 최소 metadata를 유지합니다. 삭제 선도착과 terminal 이후 더 높은 revision/epoch PURGE도 검증했습니다. 오류는 private payload 없는 전용 예외이며, JSONB/serializer 실패 시 원자 rollback을 확인했습니다. 실제 queue relay는 없습니다.

## 3. W1에 요청하는 채택 결정

기존 기존 요청서 R3의 R3-02에 아래 실제 후보를 제출합니다. 항목별 `채택 / 수정 필요 / 미결정`과 근거·담당자·다음 제공 조건을 답변해 주십시오. W2 후보 제출 대기를 W1 답변 누락으로 분류하지 않습니다.

1. **Digest:** `{"command": ..., "result": ...}`의 모델 JSON을 sorted-key·compact·UTF-8·`ensure_ascii=False`·`allow_nan=False`로 직렬화하여 SHA-256을 계산합니다. 배열 순서와 Unicode를 그대로 보존합니다. envelope ID/시각, operation ID, lease ID는 제외합니다. 고정 합성 vector로 W1 재계산 결과를 확인해 주십시오.
2. **Lease:** W2 command에는 `execution_lease_id`가 없습니다. W1 내부의 인증된 binding 조회 또는 명시적인 private 전달 계약 중 확보 경계를 정해 주십시오. W2는 lease를 생성·추정하지 않습니다.
3. **ACK outcome:** 후보 enum은 `APPLIED` / `DUPLICATE` / `REJECTED`입니다. 현재 store는 새 적용에 APPLIED를 기록하고, 중복에는 원본 ACK의 ID/시각/outcome을 그대로 재사용하며, 거부에는 전용 예외를 반환합니다(별도 DUPLICATE/REJECTED ACK 자동 발행 없음). Terminal 뒤의 과거 APPLIED도 현재 가시성·새 성공 전이를 뜻하지 않습니다. W1 normalized 성공 ACK로 전달할 조건과 거부·stale·재시도의 처리 경계를 정해 주십시오. 실패를 성공으로 변환하지 마십시오.
4. **Routing/parser:** staged-result, gate command, ACK와 기존 collection result의 producer/consumer·parser·queue 연결을 명시해 주십시오. 기존 result queue에 자동 연결하거나 새 queue 생성을 확정하지 않았습니다.
5. **Result/checkpoint:** 별도 staged-result를 W1 normalized COMMIT_READY 및 concrete result/checkpoint callback에 연결하는 책임과 시점을 확정해 주십시오. 기존 `CollectionResult` union에는 COMMIT_READY를 추가하지 않았습니다. 기존 result가 gate를 우회하지 않도록 W1 raw adapter와 callback 검증이 필요합니다.

## 4. 새로 확인된 QUESTION_MATCHING 계약 충돌

지정 Service pin의 `backend/app/runtime/core_decision_binding.py:48–54`는 QUESTION_MATCHING pin의 `company_id`를 null로 요구합니다. 같은 파일 75행은 command와 pin의 company 동일성을 무조건 요구하지만, command schema는 company UUID를 필수로 요구합니다. 이 세 조건을 동시에 만족할 수 없어 W2는 fail-closed로 유지했습니다.

W1은 W4와 의도한 company 결속 규칙을 확인한 뒤 runtime·schema·정상/음성 fixture가 일치하는 접근 가능한 수정 full SHA를 제공해 주십시오. W2가 단독으로 검증을 완화하지 않습니다. 이 항목은 기존 회신 전체가 누락됐다는 주장이 아니라 pinned artifact 검토에서 확인한 구체적 불일치입니다.

## 5. 남아 있는 W1 자체 회신과 W3/W4 조율

**반복 요청 주의: T059 retry 실행 재개 증거와 실제 W2 연결 환경 인계는 아직 닫히지 않았습니다. 기존 성공 출력의 반복 제출만으로 완료하지 마십시오.** 자세한 수용 기준은 r3의 해당 ID를 유지하며 중복 계약을 새로 만들지 않습니다.

| 요청 | 필요한 회신 |
| --- | --- |
| R3-01 | 새 retry command relay → 새 fence claim → 실제 실행 재개, 대기/슬롯 반납·재개·선택적 retry의 검증 구분 및 증거 |
| R3-03 | 접근 가능한 private endpoint/TLS·CA, queue별 설정 주입·최소 권한, 합성 owner와 격리 환경 준비·정리 책임 |
| R3-04 | 접근 가능한 CT15 runbook 정확한 경로·full SHA 또는 별도 파일. 지정 경로는 검토한 두 revision에서 404였음 |
| R3-05 | **W1이 W3·W4 각각에 Core Decision inbound 정본을 요청하고 회신을 취합** |

R3-05는 W3/W4 → W1의 schema·정상/음성 fixture·접근 가능한 pin, input/owner/stale/dedup 규칙, producer 준비 상태 및 CT-12 실행 조건에 한정합니다. W1은 각 담당자의 요청/회신 상태와 W2 영향도 함께 알려 주십시오. W2 Source Event↔W3 ACK나 W3→W4 usability 계약을 이 inbound 정본으로 바꾸어 해석하지 않습니다.

비밀값·token·실제 DB DSN은 MD/Git/채팅/fixture에 기재하지 마십시오. 변수명, 안전한 전달 주체·경로와 준비 상태만 회신해 주십시오.

## 6. 검증 결과 및 전달 조건

T082/T083/T084의 작업별 독립 review와 수정 재검토가 완료됐습니다. 재현 명령·환경 준비는 [quickstart](verification.md), 상세 RED/GREEN과 실패 이력은 위 검증 기록을 따릅니다.

| 직접 실행한 검증 | 실제 결과 |
| --- | --- |
| gate·transport·runtime focused | 418 passed |
| 전체 unit·contract (DB 승인 env 포함) | **1006 passed, skip 0** |
| 새 private gate + 기존 삭제·복구 | **47 passed, 6 failed** — 기존 T067 미구현 6건 |
| 기존 foundation·atomic persistence | **36 passed** |
| Ruff check / format `src tests migrations` | 통과 / 65 files already formatted |
| mypy | 13 source files 통과 |

독립 review가 발견한 copied non-PURGE null 재검증 결함과 JSONB 오류의 private 예외 내용 노출은 각각 회귀 테스트로 수정·재검토했습니다. 이 결과는 W2 local synthetic 데이터 및 실제 local PostgreSQL 검증이며, 실제 W1/W2 queue 왕복이나 공동 CT15 증거가 아닙니다. 임시 UUID schema/data는 테스트 teardown에서 정리했습니다.

기존 T067의 계정/Project 삭제 6개 테스트는 `PrivateDeletionCommand`/`process_private_deletion` 미구현으로 EXPECTED RED 상태입니다. 이 신규 operation-local store로 T067 전체 삭제를 완료 처리하지 않습니다. 실제 W1 cancel/delete 이후 W2에 gate가 도착하기 전 읽기 차단, 전체 operation 열거, terminal 메타데이터 보유기간·최종 삭제 및 backup 재노출 방지는 공동 검증 범위입니다.

T085 최종 독립 전체 review는 **Approved**, actionable Critical/Important/Minor finding 없음입니다. Reviewer가 현재 코드·회귀 assertion·migration·문서 경계를 대조하고 manifest의 artifact 전부 checksum, W1 snapshot 36개 및 proposal fixture/schema를 독립 확인했습니다. 테스트 재실행은 Main이 수행했으며 reviewer가 같은 suite를 다시 실행한 것으로 표시하지 않습니다.

W1은 §3 채택 결정, §4 QUESTION_MATCHING 수정, §5 미완료 인계 및 W3/W4 조율을 항목별로 회신해 주십시오. 실제 delivery revision의 full SHA는 이 문서를 전달할 때 별도로 명시합니다.
