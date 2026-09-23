# W1–W2 T067 연동 진행 증거 (2026-09-23)

판정: **진행 중; 공동 삭제 E2E 및 W1–W2 READY 아님.** 이 문서는 W2의
`W2_W1_T067_Private_Deletion_Handoff_2026-09-23.md` 요청 1–3을 대조하기 위한
비밀 없는 경로·pin 목록이다. 기존 CT15 증거를 새 T067 검증으로 재표기하지 않는다.

## 계약 및 격리 검증

| 항목 | 확인한 값과 경로 |
| --- | --- |
| W2 정본 | `Team-1AM-Club/Project_EPICK_Engine` `feat/crawler` full SHA `dbd698f801783ac6ad9d4840e49c841ec8f54c09` |
| W2 원본 스키마 | 해당 SHA의 `contracts/w2-private/private-deletion-command.schema.json`, `private-deletion-ack.schema.json` |
| W1 pin | `backend/contracts/w2/v1/private-deletion-manifest.json` 및 동일 디렉터리의 command/ACK 스키마; 원본 SHA의 JSON 객체와 각각 일치 확인 |
| W1 payload/ACK | `backend/app/runtime/w2_private_deletion_command.py`, `w2_private_deletion_ack.py`; `backend/tests/contract/test_w2_private_deletion_command.py`, `test_w2_private_deletion_ack.py` **20 passed** |
| W1 추가 경계 | `backend/app/runtime/w2_private_deletion_boundary.py`와 `backend/tests/contract/test_w2_private_deletion_boundary.py`. 계정/Project의 W1 owner·target·epoch 결속, 고정 target ID 기반 재전송 본문, ACK 결속·완료 후 중복 ACK·미발송 target 거부를 검사한다. 새 11건과 기존 20건 **31 passed**; Ruff check 통과. 전용 DB target/migration·실제 발송·ACK 적용은 아직 없다. |
| W1 넓은 contract suite | **247 passed, 1 skipped, 8 failed, 2 errors**. 실패 8건은 이 격리 worktree에 없는 W3 `specs/007`·runbook·clone 증거 경로, 오류 2건은 로컬 W1 PostgreSQL 자격 증명 불일치다. 전체 suite green으로 표기하지 않는다 |
| 실제 W2 parser 대조 | W1 직렬화 payload를 W2 정본 SHA의 `PrivateDeletionCommand.from_mapping`에 전달하여 deletion/owner/epoch 동일성 확인 (`W1_W2_PARSER_OK True True 7`) |
| 실제 W2 ACK 대조 | W2 정본 SHA의 `PrivateDeletionAcknowledgement` `DUPLICATE` 결과를 W1 ACK parser·정확한 deletion/owner/epoch binding에 전달하여 `W2_W1_ACK_OK True` 확인 |
| W2 삭제·runtime 회귀 | 정본 SHA의 계약·PostgreSQL 삭제·Alembic·runtime/commit-gate 집중 테스트 **150 passed**. PostgreSQL은 loopback test 컨테이너의 UUID 격리 schema만 사용. W2 인계의 `161 passed`와 달리 `test_foundation_boundary.py`는 이 checkout의 형제 `specs/001-official-source-collection/contracts/examples.json` reference bundle이 없어 수집 실패하여 제외했으며, 통과로 계산하지 않는다 |
| Alembic | 별도 빈 로컬 test DB에 `upgrade head` 실행; `alembic_version=0009_private_deletion_receipt`, `private_deletion_owner_states`·`private_deletion_receipts` 2개 테이블 확인 |
| 새 로컬 이미지 | 승인 base `python@sha256:78387bc3881b8273120a12ebe6c1ab22b018ccc2c9adf565ae1ac9b536e184ea`, revision label=`dbd698f801783ac6ad9d4840e49c841ec8f54c09`, `linux/amd64`; **로컬 전용** manifest digest `sha256:75328f723c8505175efb2dc8f43790092663de0960cf5fb2df6553f4ad755adc`. 이 digest에서 W2 consumer import와 image 내 head=`0009_private_deletion_receipt` 확인. ECR push·운영 배포 없음 |

## W1-01 / 기존 CT15 독립 대조 경로

W1-01 full SHA는 `187cbcbcb1ca8cd5c1fc5abe8c8e1820b627db4b`이고,
W1 실행 당시의 parser/lookup 결과는
`md/deploy/W1_W2_Integration_Completion_Response_2026-09-21.md`의
`W1-01` 표와 `W1-01 재검증` 절에 있다. W1 기록은 `test_runtime_workers.py`
28 passed, W2 parser/lookup 340 passed, CT15 pin 2 passed다. 이 수치를 W2의
독립 재현으로 간주하지 않는다.

같은 문서의 `2026-09-23 최종 W1 실행 회신 — same-run CT15 및 Core collection`
절은 `run_id=ct15-live-20260923-03`, W1 image
`sha256:ea4d5541e725d0978e5ac0b91431254fff5e02012d08c8b17d599ab13e2707c2`,
W2 image
`sha256:fe43bdae579964271d65d1f62e384921fccf43cd09bddb62395292d7e952a6fc`,
CT15-01–09 count-only, ACK-loss, DLQ 증가분과 Linux restart 기록을 담고 있다.
이는 W2 `0008` 기준의 과거 실행이며, 이 문서의 새 `0009` 이미지 공동 검증을
대신하지 않는다. 원시 credential, queue URL, DSN, payload/receipt 본문은
공유하지 않는다.

## W1 연동의 남은 경계

W1의 deletion target에는 아직 W2 전용 store/실제 dispatcher가 없고,
W2가 내부 생성한 `request_deduplication_id`를 W1이 계정/Project 범위별로
완전하게 조회·입증할 승인된 경로가 없다. W2의 `request_deduplications` 행에는
`project_id`도 없다. 따라서 W1은 현재 임의의 빈 목록이나 추정 ID로 삭제
명령을 자동 발행하지 않는다. 위 payload 직렬화기와 W1 경계 어댑터는
**이미 인증·인가 및 scope 검증된 ID 목록**을 입력받는 fail-closed 구성 요소다.
W1 경계 검증은 추가했지만 실제 dispatcher, purge callback, target ACK 적용,
epoch 순서의 DB 보장과 W1/W2 공동
계정·Project 삭제/중복/stale/purge 실패/늦은 replay/다른 owner/공유 Source
검증은 아직 미완료다.

배포 및 공동 증거 확정 전에는 테스트 IAM/queue/profile과 위 격리 test DB를
정리하지 않는다. 정리 시각·대상은 양측 기록 이후 별도로 적는다.
