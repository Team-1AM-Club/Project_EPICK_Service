# C01 / W3 0.2 candidate

유일한 현재 정본: `docs/w3-additional-reply-2026-09-16.md` (profile r2).
같은 디렉터리의 문서 SHA256 sidecar와 배포 CANONICAL_RECEIPT.json의 full commit을 확인한다.
`w3-c01-handoff-2026-09-16.md`는 r1 이력이다.

- fixtures: 사용자에게 받은 원본 W2 공개 event 정상4/오류2. 수정하지 않는다.
- examples: W3가 만든 합성 예제. producer가 확정한 release/recovery 자료가 아니다.
- *.schema.json: `python scripts/export_c01_contract.py`로 생성.
- t058-count-evidence.schema.json: 동일 W1 공동 실행에서 W3가 남기는 익명 집계 증거 규격.
- examples/t058-count-evidence.json: 비밀값·원문 식별자·event 본문이 없는 합성 T058 예제.
- received-manifest.json: 원본 파일들의 SHA256.
- payload 원본은 `src/w3_knowledge/c01/source-event-payload.schema.json`.

`--check`는 생성물 drift를 검사한다. 런타임은 jsonschema format 검사와
Pydantic 의미 검사를 함께 수행한다. examples의 고정 expires_at은 실행 시 갱신한다.
legacy 0.1-draft DB/API/신호와 혼용하지 않는다.
