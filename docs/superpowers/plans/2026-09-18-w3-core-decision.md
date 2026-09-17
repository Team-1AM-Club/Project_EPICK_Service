# W3 Core Decision implementation plan

Goal: W1 검토용 COMPANY_KNOWLEDGE 생산 계약·durable producer·fixture·검증을 제공한다.
Architecture: 기존 C01/구조화 경로와 독립된 후보 모듈. 명시적 W3 판단을 입력받아
SQLite transaction에서 revision과 immutable event를 함께 저장한다. 실제 W1 queue는 연결하지 않는다.
Tech stack: 기존 Python/Pydantic/SQLite/pytest, 추가 의존성 없음.
Authority: W1 Service afec08a9602132e5e433b523b0b6804850440524의 core_decision_binding.py와 dispatch schema.

## 설계와 범위
- candidate v1, producer=w3, decision_owner=W3, scope=COMPANY_KNOWLEDGE 고정.
- job/company/source UUID와 opaque analysis_input_version을 결속한다. question_version_id는 필수 null.
- CORE_REQUIRED/true 또는 NON_CORE_OPTIONAL/false만 허용한다. non-core로 W2 Core command 생성 금지.
- W1 current context는 신뢰된 caller가 별도 제공한다. event 자기 주장을 인증 근거로 쓰지 않는다.
- decision_version은 (company_id,source_id) 단위로 생산자가 원자 증가하며 Job/입력 교체 시에도 초기화하지 않는다. W1 decision 식별에 job_id가 없으므로 다른 Job에서도 revision을 재사용하지 않는다. input_version 문자열을 숫자로 변환하지 않는다.
- idempotency key는 job/source 단위, 변경 입력 재사용은 거부. 같은 요청 재시도는 동일 event 반환.
- 오래된 입력/잘못된 principal은 publish 전에 거부. W1도 수신 transaction에서 currentness를 재확인해야 한다.
- 후보 전달: W1 소유 private queue와 IAM principal w3 제안. queue/ARN/실제 principal·보존기간은 W1 채택 전 미연결.
- W1 DB decision ID는 W1 발급. producer event는 decision row/pin 생성 완료나 CT12 통과를 주장하지 않는다.

## Tasks
- [x] tests/integration/test_core_decision.py: producer 재시작·idempotency 충돌·동시 revision·stale input·owner/source mismatch·pin 호환성 실패를 먼저 확인.
- [x] src/w3_knowledge/core_decision.py: strict event/context 모델, current binding 검사, SQLite durable producer 구현.
- [x] scripts/export_core_decision_contract.py: schema/정상·음성 fixture export 및 --check; W1 pinned validator snapshot+manifest로 호환성 재현.
- [x] docs/w3-w1-core-decision-handoff-2026-09-18.md: 의미·소유권·전달제안·CT12 시나리오·미완료 경계 기록.
- [x] focused26 / 전체223pass·1skip, ruff74files, exporter12 drift없음, independent review 재검토완료.
- 배포 기록: 실제 commit/push 및 전달 ZIP 결과는 외부 HANDOFF_RECEIPT.json에서 확인한다.

검증 예: `python -m pytest tests/integration/test_core_decision.py -q -p no:cacheprovider`.
실제 W1 소비/DB 원자성/인증/queue E2E는 W1 수신 구현 후 공동 검증한다.
