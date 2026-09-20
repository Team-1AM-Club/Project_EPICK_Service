# W1 → W3 M1 image·state layout review 요청

- 작성일: 2026-09-20
- 요청자: W1
- 수신자: W3 runtime 담당자, 통합 조정자
- 목적: W3-D(`image/entrypoint/state-layout review`) 판정
- 현재 판정: **로컬 구현·검증 완료, W3-D 및 ECR manifest digest 미확정**

## 1. 고정 입력과 산출물

| 항목 | 값 또는 경로 |
| --- | --- |
| W3 source full SHA | `c7e6788168c048941bdabe7ed8cb01007edeecec` |
| W3 handoff SHA-256 | `917ff83a93e29740f7e92452be61e3cefb7ff964531e1ed2847971814671ea24` |
| image recipe | `w3/Dockerfile` |
| build context allowlist | `w3/.dockerignore` |
| runtime topology | `backend/infra/w3-runtime.compose.yml` |
| M1 preflight | `backend/scripts/preflight_w3_actual_runtime.py` |
| ECR policy template | `backend/infra/w3-runtime-ecr-policy.template.json` |
| 로컬 OCI image digest | `sha256:441d97ad515e0fcceb1cca39fcbaa96a16a6144dfad9366c2f5cc6fd1e3a3fe9` |
| 목표 ECR repository | `epick-staging-w3-core-runtime` |
| W1 전달 revision | 이 문서를 포함하는 W1 commit의 full SHA를 별도 전달 |

로컬 digest는 현재 recipe의 재현·검토 식별자이며 ECR manifest digest를 대신하지 않는다.
W3-D가 확인되기 전에는 이 image를 production-ready 또는 M1 완료로 표시하지 않는다.

## 2. 구현 경계

- Python `3.12.14` base image를 digest로 고정했다.
- `uv.lock`과 `uv sync --frozen --no-dev --no-editable`로 runtime dependency를 설치한다.
- OCI revision label은 위 W3 source full SHA와 일치한다.
- process는 고정 UID/GID `10001:10001`로 실행한다.
- root filesystem은 read-only이며 writable 경계는 local named volume `/state`와
  bounded tmpfs `/tmp`뿐이다.
- SQLite 경로는 `/state/core.db` 하나로 고정한다. host bind·NFS·복수 host 공유는 없다.
- entrypoint는 `python -m w3_knowledge.core_runtime_cli`이며 `init`, `smoke`, `inspect`와
  향후 승인된 one-shot command가 동일 image와 동일 state volume을 사용한다.
- local smoke는 `SyntheticAuthority`만 사용하며 actual runtime이나 상시 서비스에 연결하지
  않는다. AWS 호출 수는 0이었다.
- raw SQLite/volume snapshot을 live runtime으로 복원하는 경로는 제공하지 않는다.

## 3. 재현된 검증 결과

| 검증 | 결과 |
| --- | --- |
| Docker locked build | 성공 |
| non-root image metadata | `10001:10001` 확인 |
| read-only rootfs + bounded tmpfs smoke | `LOCAL_VERIFIED_NOT_DEPLOYED`, `aws_calls=0` |
| container 재생성 후 `/state/core.db` inspect | count-only 결과 동일 |
| 같은 volume 재초기화 거부 | `CORE_RUNTIME_COMMAND_FAILED`로 fail-closed |
| W1 M1 preflight | source/digest/UID/rootfs/mount/SQLite/restart 모두 `ok` |
| 신규 contract/runtime tests | `33 passed` |
| opt-in Docker persistence test | `1 passed` |

전체 backend contract/runtime 회귀에서는 이번 변경과 무관하게 로컬 Python 환경의
`boto3` 미설치로 기존 T043/T060 loader 테스트 10개가 import 단계에서 실패했다. 신규 M1
대상 테스트와 실제 Docker 검증은 모두 통과했으며, 이 환경 의존 실패를 W3-D 성공 근거로
계상하지 않는다.

## 4. W3-D에서 반드시 확인할 사항

W3는 아래 항목을 원본 W3 source와 대조해 판정해 주기 바란다.

1. image entrypoint와 각 CLI subcommand 인자 조합이 W3가 보증한 사용법과 일치하는가.
2. 모든 stateful command가 같은 `/state/core.db`를 사용해도 되는가.
3. 같은 private host·같은 local volume에서 one-shot process를 순차 실행하는 경계가
   W3 SQLite transaction/lock 책임과 일치하는가.
4. non-root UID, read-only rootfs, `/state` RW volume, `/tmp` tmpfs 구성이 W3 동작에 충분한가.
5. init 재실행 거부, restart inspect, raw-state live restore 금지가 W3 운영 의미와 일치하는가.

## 5. 필수 회신 형식

```text
W3-D status: VERIFIED | REJECTED
reviewed W3 source SHA: <40-char full SHA>
reviewed W1 delivery SHA: <40-char full SHA>
reviewed local image digest: sha256:<64 hex>
entrypoint: ACCEPTED | REJECTED — <근거>
state path /state/core.db: ACCEPTED | REJECTED — <근거>
same-host one-shot process model: ACCEPTED | REJECTED — <근거>
non-root/read-only/writable-boundary: ACCEPTED | REJECTED — <근거>
restart and restore semantics: ACCEPTED | REJECTED — <근거>
required corrections: NONE | <파일·항목·이유>
review evidence path: <W3 repository-relative path or immutable review reference>
```

`VERIFIED`는 모든 세부 항목이 `ACCEPTED`이고 `required corrections: NONE`일 때만 허용한다.
W1은 `VERIFIED` 회신을 받은 뒤 commit-SHA tag로 ECR에 게시하고 registry manifest digest를
고정해 M1을 닫는다. 실제 credential, Role ID, Queue URL, DB URL 또는 private payload는 이
회신에 포함하지 않는다.
