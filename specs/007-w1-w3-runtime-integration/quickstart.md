# Quickstart: W1–W3 M1–M5 Validation

> Historical 2026-09-20 pin instructions. The independent W3 clone now has implementation
> `0c4f01f9537a3129c976fae5e63111a7982c5da6` and receipt/runtime HEAD
> `402f7a63bf8f8d601cc6ada1ce685280f47320ce`. Do not run the older image build
> commands below as a current release procedure. See `specs/008-end-to-end-service/baseline-evidence.md`
> for the verified current source; a new image digest has not been recorded.

This is a validation guide, not an instruction to bypass milestone gates. At the current state only
M1 may run. Commands and script names below are the target interface produced during implementation;
actual secret values must remain in root-owned runtime files or the approved secret channel.

## 1. Baseline verification

From the repository root:

```powershell
git rev-parse HEAD
git -C w3/Project_EPICK_Service rev-parse HEAD
git -C w3/Project_EPICK_Service cat-file -t 66a0e3e1b087bf7f9d1b6d7730934ea27f55e94b
python backend/scripts/verify_w3_runtime_provenance.py --w3-root w3/Project_EPICK_Service
```

Expected:

- W3 implementation is `66a0e3e1b087bf7f9d1b6d7730934ea27f55e94b` and receipt HEAD is
  `bad8671b2ea8d02bdce2157120b94d2b7edf09d8`.
- The implementation commit is an ancestor of receipt HEAD and runtime paths have no drift.
- No runtime env, Queue URL, Role ID, token, owner ID or database URL is printed.

## 2. M1 — image and single-host state

```powershell
python backend/scripts/export_w3_runtime_context.py --source w3/Project_EPICK_Service --commit 66a0e3e1b087bf7f9d1b6d7730934ea27f55e94b
docker build --pull --tag epick-w3-core-runtime:66a0e3e1 --file backend/infra/w3-runtime.Dockerfile .runtime/w3-build-context
docker compose --file backend/infra/w3-runtime.compose.yml config
docker run --rm --read-only --tmpfs /tmp:rw,noexec,nosuid,nodev,size=16m `
  epick-w3-core-runtime:66a0e3e1 `
  smoke --directory /tmp/smoke
python -m pytest backend/tests/contract/test_w3_runtime_deployment.py -q
```

Then create one local named state volume, initialize `/state/core.db`, recreate the container and
run `inspect` from the replacement container. Expected M1 evidence:

- image runs as non-root with read-only rootfs;
- only `/state` is persistent writable storage;
- `smoke` reports `LOCAL_VERIFIED_NOT_DEPLOYED` and zero AWS calls;
- state/counters survive container recreation;
- ECR output records source full SHA and manifest digest;
- W3-D review is attached before final M1 closure.

## 3. Gate check before later milestones

Validate `runtime-readiness.schema.json`. Do not proceed when these are absent:

- M2 W1 work: may run now; actual cutover requires W3-A and W3-B verified;
- M3 W1 work: W3-C transport/ACK semantics and implementation SHA are verified; actual cutover still requires secret-channel Queue/Role values and live E2E evidence;
- M4: M1/M2 complete and actual workload identity assigned;
- M5: M1–M4 complete and W3-F executor/window confirmed.

## 4. M2 — actual caller and Authority

Run W1 contract/service work now. Run actual caller/client integration only after W3-A/B:

```powershell
python -m pytest backend/tests/contract/test_w3_authority_contract.py -q
python -m pytest backend/tests/integration/db/test_w3_authority_currentness.py -q
python -m pytest backend/tests/runtime/test_w3_authority_adapter.py -q
```

Exercise current, source mismatch, cancel, deletion epoch mismatch, 401/403/404/409/503 and timeout.
Expected: only current input returns a valid snapshot; all other cases are fail-closed and emit no
SQS message or new W1 decision.

## 5. M3 — deletion, Source retirement, and operations

Run W1 persistence and lifecycle work against adopted W3-C and `w3.retention/1.1`:

```powershell
python -m pytest backend/tests/integration/db/test_w3_deletion_dispatch.py -q
python -m pytest backend/tests/runtime/test_w3_deletion_worker.py -q
python -m pytest backend/tests/runtime/test_w3_runtime_operations.py -q
```

Run duplicate deletion, dispatcher restart, temporary W3 failure, deletion-first/send-first races,
another-owner preservation, retry exhaustion/HELD and approved expiry. Expected: one current W3
deletion acknowledgement per target/epoch, durable retry on failure, no automatic HELD replay and
no removal of another owner or shared revision counter.

## 6. M4 — actual IAM/SQS preflight

On the private Worker EC2 with root-owned mode `600` env files:

```sh
docker run --rm --network host --env-file /opt/epick/worker/w3-runtime.env \
  "$W3_IMAGE" python scripts/preflight_w3_actual_runtime.py

docker run --rm --network host --env-file /opt/epick/worker/w3-core-decision.env \
  "$BACKEND_IMAGE" python scripts/preflight_w1_core_decision_runtime.py
```

Expected:

- W3 STS Role ID equals the configured W3 expected role ID;
- W1 configured SenderId equals that stable role ID;
- W3 can send only to the designated Main Queue;
- W3 cannot receive/delete/purge or access W1 DB/secrets;
- W1 can receive/delete/change visibility but cannot assume W3 send identity.

Store raw role/queue values outside Git and redact them from evidence.

## 7. M5 — joint CT-12

Follow [contracts/joint-ct12-scenarios.md](contracts/joint-ct12-scenarios.md). Fix both repositories'
full SHAs, both image digests and the workload identity before starting. Run CT12-01~12 without
changing the environment between positive and negative cases.

Expected final summary:

```json
{
  "status": "JOINT_CT12_COMPLETE",
  "actual_w3_producer": true,
  "scenarios_passed": 12,
  "automatic_commands_before_retry": 0,
  "new_fence_commands_after_retry": 1,
  "stale_late_effects": 0,
  "counts_reconciled": true,
  "cleanup_recorded": true
}
```

The summary is illustrative and contains no actual identity or secret. Completion requires the
underlying W3 inspect, SQS, W1 PostgreSQL and teardown evidence—not this JSON shape alone.
