# W1 → W3 actual runtime 선행물·M1 review 통합 요청

> **폐기된 기준선:** 이 문서는 이전 `3b23e084...` image review 요청의 실행 기록이다.
> 현재 implementation pin은 `66a0e3e1b087bf7f9d1b6d7730934ea27f55e94b`, receipt/docs HEAD는
> `bad8671b2ea8d02bdce2157120b94d2b7edf09d8`이다. 아래 image는 publish/deploy하지 않으며,
> T020에서 현재 pin으로 rebuild한 뒤 W3-D review를 다시 받아야 한다.

- 작성일: 2026-09-20
- 요청자: W1
- 수신자: W3 runtime 담당자, 통합 조정자
- 목적: W3-D image review와 W3-A~F 실제 서비스 연동 선행물을 한 번의 형식화된 회신으로 수령
- 현재 판정: **W1 M1 재기준 로컬 구현·검증 완료 / W3-D review 및 이후 ECR manifest digest 미완료**
- 종료 목표: 이 회신 패키지 수령 후 W1이 추가 계약 질문 없이 M1~M4를 완성하고, W3-F에 지정된 실제 실행자와 M5 공동 CT-12를 수행할 수 있어야 함

## 1. 고정 입력과 산출물

| 항목 | 값 또는 경로 |
| --- | --- |
| W3 implementation full SHA | `3b23e0843a134fb341e6a256576ccf52fedbf4a8` |
| W3 receipt HEAD full SHA | `34660343f197c74cc03459a93e0160e46adbcd2b` |
| adopted contract | `w3.private.core-decision/0.1-candidate` |
| adopted policy | `w3.retention/1.1` (`retention_seconds=1209600`) |
| image recipe | `backend/infra/w3-runtime.Dockerfile` |
| build context exporter | `backend/scripts/export_w3_runtime_context.py` |
| exported provenance | `.runtime/w3-build-context/.w3-build-provenance.json` (local, ignored) |
| runtime topology | `backend/infra/w3-runtime.compose.yml` |
| M1 preflight | `backend/scripts/preflight_w3_actual_runtime.py` |
| ECR policy template | `backend/infra/w3-runtime-ecr-policy.template.json` |
| 로컬 image tag | `epick-w3-core-runtime:3b23e084` |
| 로컬 OCI image ID | `sha256:6b7b1bcd14c7f83d7618885e4ab761ee02962c6e8012e12338eaff9f44628697` |
| 목표 ECR repository | `epick-staging-w3-core-runtime` |
| W1 기준 revision | `6352347748f3cfb664672e3a5b35c92e14570aca` |

로컬 digest는 현재 recipe의 재현·검토 식별자이며 ECR manifest digest를 대신하지 않는다.
W3-D가 확인되기 전에는 이 image를 production-ready 또는 M1 완료로 표시하지 않는다.

## 2. 구현 경계

- Python `3.12.14` base image를 digest로 고정했다.
- `uv.lock`과 `uv sync --frozen --no-dev --no-editable`로 runtime dependency를 설치한다.
- OCI revision label은 W3 implementation SHA와, 별도 receipt label은 W3 receipt HEAD와 일치한다.
- build context는 implementation commit의 Git archive에서 allowlist로만 추출하며 `.git`, `.venv`,
  `.env`와 working-tree 파일을 포함하지 않는다.
- process는 고정 UID/GID `10001:10001`로 실행한다.
- root filesystem은 read-only이며 writable 경계는 local named volume `/state`와
  bounded tmpfs `/tmp`뿐이다.
- SQLite 경로는 `/state/core.db` 하나로 고정한다. host bind·NFS·복수 host 공유는 없다.
- entrypoint는 `python -m w3_knowledge.core_runtime_cli`이며 `init`, `smoke`, `inspect`,
  `relay`, `expire`, `backup` one-shot command가 동일 image와 동일 state volume을 사용한다.
- local smoke는 `SyntheticAuthority`만 사용하며 actual runtime이나 상시 서비스에 연결하지
  않는다. AWS 호출 수는 0이었다.
- raw SQLite/volume snapshot을 live runtime으로 복원하는 경로는 제공하지 않는다.

## 3. 재현된 검증 결과

| 검증 | 결과 |
| --- | --- |
| W3 고정 핵심·보존정책 테스트 | `32 passed` |
| W1 M1 contract/runtime 테스트 | `24 passed` |
| Docker locked build | 성공 (`3b23e084...` archive, Python `3.12.14`, frozen lock) |
| non-root image metadata | `10001:10001` 확인 |
| read-only rootfs + bounded tmpfs smoke | `LOCAL_VERIFIED_NOT_DEPLOYED`, `aws_calls=0` |
| container 재생성 후 `/state/core.db` inspect | policy/migration-blocker/delivery state 보존 확인 |
| W1 M1 preflight | dual SHA/image ID/UID/rootfs/mount/SQLite/policy/restart 모두 `ok` |
| opt-in Docker persistence test | `1 passed` |
| migration blockers | 없음 (`[]`) |

CI에 W3 외부 checkout이 없는 경우에는 실제 수신 패키지 byte 대조만 skip하고,
합성 receipt로 source SHA·handoff hash 일치/변조 거부를 항상 검증한다. 실제 W3 패키지
대조는 W3 checkout이 있는 검토 환경에서 별도로 유지한다.

## 4. W3-D에서 반드시 확인할 사항

W3는 아래 항목을 원본 W3 source와 대조해 판정해 주기 바란다.

1. image entrypoint와 각 CLI subcommand 인자 조합이 W3가 보증한 사용법과 일치하는가.
2. 모든 stateful command가 같은 `/state/core.db`를 사용해도 되는가.
3. 같은 private host·같은 local volume에서 one-shot process를 순차 실행하는 경계가
   W3 SQLite transaction/lock 책임과 일치하는가.
4. non-root UID, read-only rootfs, `/state` RW volume, `/tmp` tmpfs 구성이 W3 동작에 충분한가.
5. init 재실행 거부, restart inspect, raw-state live restore 금지가 W3 운영 의미와 일치하는가.

## 5. W3-D 필수 회신 형식

```text
W3-D status: VERIFIED | REJECTED
reviewed W3 implementation SHA: <40-char full SHA>
reviewed W3 receipt HEAD SHA: <40-char full SHA>
reviewed W1 delivery SHA: <40-char full SHA>
reviewed local image ID: sha256:<64 hex>
reviewed contract revision: w3.private.core-decision/0.1-candidate
reviewed policy revision: w3.retention/1.1
entrypoint: ACCEPTED | REJECTED — <근거>
state path /state/core.db: ACCEPTED | REJECTED — <근거>
same-host one-shot process model: ACCEPTED | REJECTED — <근거>
non-root/read-only/writable-boundary: ACCEPTED | REJECTED — <근거>
restart and restore semantics: ACCEPTED | REJECTED — <근거>
required corrections: NONE | <파일·항목·이유>
review evidence path: <W3 repository-relative path or immutable review reference>
```

`VERIFIED`는 모든 세부 항목이 `ACCEPTED`이고 `required corrections: NONE`이며 위 dual SHA,
contract/policy revision, local image ID가 모두 일치할 때만 허용한다.
W1은 `VERIFIED` 회신을 받은 뒤 implementation-SHA tag로 ECR에 게시하고 registry manifest digest를
고정해 M1을 닫는다. 실제 credential, Role ID, Queue URL, DB URL 또는 private payload는 이
회신에 포함하지 않는다.

## 6. W3가 함께 준비해야 하는 W3-A~F 선행물

W3-D만 회신하면 W1–W3 서비스 연동을 끝낼 수 없다. W3는 아래 항목을
같은 full SHA에서 재현 가능한 코드·계약·테스트·증거로 준비해야 한다. 단순히
`예정`, `협의 필요`, `W1 선행 필요`로 표시한 항목은 `VERIFIED`로 보지 않는다.

W3가 대조할 W1 입력은 이미 아래 경로에 제공되어 있다. 이 회신은 채택된
Core Decision wire/schema/revision/SQS ACK 계약을 재협상하는 요청이 아니다.

| W1 제공 입력 | 경로 |
| --- | --- |
| Authority request candidate | `specs/007-w1-w3-runtime-integration/contracts/authority-request.schema.json` |
| Authority response candidate | `specs/007-w1-w3-runtime-integration/contracts/authority-response.schema.json` |
| deletion command candidate | `specs/007-w1-w3-runtime-integration/contracts/deletion-command.schema.json` |
| joint CT-12 시나리오 | `specs/007-w1-w3-runtime-integration/contracts/joint-ct12-scenarios.md` |
| W3 image/runtime topology | `backend/infra/w3-runtime.Dockerfile`, `backend/infra/w3-runtime.compose.yml` |

| ID | W3가 제공할 필수 선행물 | W1이 바로 사용할 수 있는 완료 기준 |
| --- | --- | --- |
| W3-A 실제 caller | `AnalysisPlan` 생성 및 `CoreRuntime.supply` 실제 호출자의 repository, full SHA, 파일, class/function, 호출 시점, input/output type, required/optional Source 분류의 권위 근거, stable idempotency key 생성 규칙 | 합성 caller가 아닌 실제 서비스 코드와 테스트가 push된 full SHA로 제공됨. W3 저장소 밖 서비스라면 W3는 소유 조직·정확한 repository·revision을 식별해 통합 조정자 확인을 포함해야 함 |
| W3-B actual Authority adapter | W1 candidate Authority request/response 수용 여부, adapter 구현 파일·factory import path·full SHA, supply/relay/replay 매 시점 재조회 위치, connect/read/total timeout, 401/403/404/409/503/timeout의 retryable/terminal mapping, fail-closed 테스트 | W1이 `backend/contracts/w1/v1/` 계약과 private endpoint를 구현한 후 import path와 env 이름만으로 adapter를 실행할 수 있고, stale/cancel/delete에서 SQS send가 0건임을 재현할 수 있음 |
| W3-C deletion adapter | W1 durable deletion delivery가 W3 `delete_owner(owner_id, deletion_epoch)`를 호출할 실제 interface(CLI 또는 승인된 private adapter), 인증 경계, command identity/idempotency, success/already-applied/stale/retryable 결과 mapping, SQLite lock 책임, restart/duplicate 테스트, count-only inspect 증거 | W1 worker가 결과와 exit/error contract을 추측하지 않고 durable ACK/retry를 구현할 수 있음. 다른 owner 및 공유 Source 카운터 보존 결과가 포함됨 |
| W3-D image review | 제4절의 image/entrypoint/state/lock/read-only/restart/restore 항목을 제5절 형식으로 판정한 근거 | 모든 항목 `ACCEPTED`, correction `NONE`, 검토한 W1/W3 full SHA와 local digest 일치 |
| W3-E operations semantics | `retention_seconds`, relay/expire/inspect 실행 주기, max attempts/`HELD` 전이, alert 조건, manual replay 승인, tombstone/idempotency metadata 보관, quarantine backup, raw-state live restore 금지의 최종 의미·테스트·runbook. 보관 기간의 제품/개인정보 승인 revision | W1이 값과 상태 전이를 추정하지 않고 systemd/Compose runner·monitor·금지 규칙을 구현할 수 있음. 정책 승인은 W3 단독 권한이 아니므로 W3가 승인 주체의 revisioned disposition을 패키지에 함께 연결해야 함 |
| W3-F joint CT-12 | 실제 W3 caller/supplier/outbox/relay 실행 담당 role, 실행 entrypoint/command, 필수 env 이름(값 제외), 사전 조건, CT12-01~12 담당 분할, W3 inspect/outbox evidence 위치·count schema, 장애 주입/재시작 방법, 참여 가능 milestone/window | M1~M4 후 W1이 추가 질문 없이 공동 실행을 예약하고, actual W3 producer 경로와 count 대조·teardown까지 실행할 수 있음 |

W3-A·B·C에 코드 변경이 필요하면 검토 가능한 branch가 아니라 **remote에 push된
full commit SHA**로 제공한다. W3-E의 기술 의미는 W3가 확정하되, 보관 기간은
제품/개인정보 승인자의 별도 승인 근거가 있어야 한다.

## 7. 필수 회신 패키지 구조

W3는 아래와 동등한 구조를 W3 저장소의 하나의 push된 full SHA로 제공한다.
파일명은 달라질 수 있지만 각 항목의 권위 위치와 SHA-256 manifest는 필수다.

```text
docs/w1-w3-actual-runtime-prerequisites.md
contracts/w1-w3/authority-adoption.json
contracts/w1-w3/deletion-adapter.json
contracts/w1-w3/operations-policy.json
contracts/w1-w3/joint-ct12-executor.json
tests/...                     # W3-A/B/C/E 재현 테스트
evidence/...                  # secret-free 테스트 결과·count-only 예시
HANDOFF_RECEIPT.json
artifacts.sha256
```

`HANDOFF_RECEIPT.json`은 최소한 다음을 포함한다.

```json
{
  "schema_version": "w3-w1.actual-runtime-prerequisites.v1",
  "w3_full_sha": "<40-char full SHA>",
  "remote_branch": "<branch>",
  "remote_head": "<same full SHA>",
  "pushed": true,
  "contracts": {
    "core_decision": "w3.private.core-decision/0.1-candidate",
    "authority": "ACCEPTED | REJECTED",
    "deletion": "ACCEPTED | REJECTED",
    "operations": "ACCEPTED | REJECTED",
    "joint_ct12": "ACCEPTED | REJECTED"
  },
  "gates": {
    "W3-A": "VERIFIED | BLOCKED",
    "W3-B": "VERIFIED | BLOCKED",
    "W3-C": "VERIFIED | BLOCKED",
    "W3-D": "VERIFIED | REJECTED",
    "W3-E": "VERIFIED | BLOCKED",
    "W3-F": "VERIFIED | BLOCKED"
  },
  "artifact_manifest": "artifacts.sha256",
  "limitations": []
}
```

실제 secret·token·DB URL·Queue URL·Role ARN/Role ID·owner ID·AnalysisPlan 본문은 패키지에
포함하지 않는다. 필요한 설정은 환경 변수 **이름**, 설정 위치, 형식, 안전한
별도 전달 절차만 제공한다.

## 8. 회신 판정 규칙과 W1의 다음 동작

- `PREREQUISITES_COMPLETE_FOR_W1`: W3-A~F가 모두 `VERIFIED`이고 artifact hash·full SHA·test
  재현이 일치할 때만 허용한다.
- `PARTIAL`: 하나라도 `BLOCKED`/`REJECTED`이면 이 상태다. 누락 항목은 정확한 책임
  영역, 해제 조건, 다음 full SHA 제공 milestone을 함께 적는다.
- W1은 `PARTIAL`을 받고 미정 계약을 추측해 구현하지 않는다.
- 완전한 패키지를 받으면 W1은 W3-D를 닫고 ECR digest를 고정한 뒤, W3-A/B로
  M2, W3-C/E로 M3, M1/M2로 M4를 순서대로 완성한다.
- M5는 W3-F의 실제 실행자가 참여하는 actual-producer CT-12이므로 W1 단독으로
  대체하지 않는다. 단, 패키지가 완전하면 공동 실행 직전까지 추가 W3 계약
  질문이 발생하지 않아야 한다.

W3의 최종 회신 문서 상단에는 아래 요약을 그대로 포함한다.

```text
overall status: PREREQUISITES_COMPLETE_FOR_W1 | PARTIAL
W3 full SHA: <40-char full SHA>
artifact manifest SHA-256: <64 hex>
W3-A: VERIFIED | BLOCKED — <evidence path or exact unblock condition>
W3-B: VERIFIED | BLOCKED — <evidence path or exact unblock condition>
W3-C: VERIFIED | BLOCKED — <evidence path or exact unblock condition>
W3-D: VERIFIED | REJECTED — <evidence path or correction>
W3-E: VERIFIED | BLOCKED — <evidence path or exact unblock condition>
W3-F: VERIFIED | BLOCKED — <evidence path or exact unblock condition>
W1 can proceed without another contract clarification: YES | NO
```
