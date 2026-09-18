# W3 Core supplier / relay / deletion implementation

승인 범위: W1 채택 wire는 변경하지 않고 실제 실행 가능한 내부 공급·전송·삭제 경로와 로컬 검증 제공.
Authority: W1 41dd0c21692c7f59c87962cf1e7d2746bac73303. 새 receipt API 없음.

## 설계
- trusted analysis plan의 required/optional source 집합에서 명시적 source dependency를 판정한다. unknown/중복분류는 거부한다. C01 confidence로 Core 추론하지 않는다.
- trusted authority port는 현재 context, owner와 epoch, active 상태를 제공한다. production 공급자 연결 없이는 live READY 아님.
- 기존 immutable event/SQLite allocator 재사용. 원자 publish+owner metadata, 삭제 후에도 회사/source counter 유지.
- SQS SendMessage만 사용. 동일 event body 재전송, exponential backoff, 횟수 제한 후 HELD. HANDOFF는 domain acceptance가 아님.
- relay는 SQLite write lock 아래에서 권한 재확인/제한된 timeout 전송/상태기록. 삭제 commit 이후 새 전송 없음. 이미 전송된 delivery의 차단은 W1 currentness 책임.
- 공개되지 않은 pending도 owner 삭제 시 body와 request 제거. 최소 해시 tombstone·공용 counter만 유지.
- 전송본문 보관기간은 운영자가 명시 설정하며 정책확정값을 발명하지 않는다. 만료 후 idempotency hash 유지로 재생산 차단.
- 지원 backup API는 복원본 송신을 차단한다. 임의 filesystem backup restore를 지원한다고 주장하지 않는다. 최신 삭제대장/높은 counter 없이는 재활성화 기능 없음.
- 기존 producer row는 owner 결속 미확인이므로 relay에 자동 편입하지 않는다.

## Tasks
- [x] RED tests: explicit plan, retry unchanged body, restart, failure backoff, deletion/counter, retention/replay, backup blocked.
- [x] atomic persistence hook/counter + core_runtime.py + SQS adapter.
- [x] CLI synthetic smoke, live runner with explicit trusted authority plugin/config; no live send during this task.
- [x] SDK Stubber integration, full245pass/1skip, ruff78files, independent review (backup interruption / optimized smoke fixes).
- [x] W1 §9 honest readiness JSON and runbook. Fixed revision/ZIP distribution evidence is in HANDOFF_RECEIPT.json.
