# C01 / W3 0.2 candidate

정본 설명: `docs/w3-c01-handoff-2026-09-16.md`.

- fixtures: 사용자에게 받은 원본 W2 공개 event 정상4/오류2. 수정하지 않는다.
- examples: W3가 만든 합성 예제. producer가 확정한 release/recovery 자료가 아니다.
- *.schema.json: `python scripts/export_c01_contract.py`로 생성.
- received-manifest.json: 원본 파일들의 SHA256.
- payload 원본은 `src/w3_knowledge/c01/source-event-payload.schema.json`.

`--check`는 생성물 drift를 검사한다. 런타임은 jsonschema format 검사와
Pydantic 의미 검사를 함께 수행한다. examples의 고정 expires_at은 실행 시 갱신한다.
legacy 0.1-draft DB/API/신호와 혼용하지 않는다.
