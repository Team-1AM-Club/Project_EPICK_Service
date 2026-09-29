# CT15 B1: scoped inspection and bounded transport controls

Updated 2026-09-20. These are local W2 preparations, not a joint CT15 receipt.
Deployment and infrastructure remain W1-owned. No AWS calls, source push or image
build/publish were performed as part of this work.

## Pinned W1 compatibility gate

Service `dc3a7b45e1297d5a5a66ee6a00cbec55f073f97d` provides
`backend/scripts/run_w1_w2_ct15_guarded_harness.py` with `seed`, `inspect`,
`cancel-primary`, `delete-primary`, `redrive-primary`. It requires the W1-owned
synthetic opt-in, run ID and isolated seed DB. W2 does not receive that DB login.
The separate `run_w1_w2_ct15_synthetic.py` is an inbound drain probe.

The seed creates DB bindings and returns synthetic command data; it does **not**
create a normal collection-dispatch outbox message. W2's explicit synthetic
`stage` is the intended local preparation boundary, not collection dispatch proof.

The exported AND stored seed command currently use `resume_stage=fetch` with
`policy_revision=null`. W2's existing parser rejects that combination. Do not
patch only the W2 copy: W1 does not compare the full supplied command to its
stored command, so such a fix-up could produce misleading green evidence.
W1 must supply a corrected pin (initial policy stage with null policy revision,
consistent exported/stored commands) and its regression evidence first.

The claimed `backend/docs/runbooks/w2-commit-gate-e2e.md` is absent at that pin;
the independently inspected complete tree contains no alternative tracked runbook.
The source snapshot and Git blob identity are in `tests/fixtures/w1_ct15_pin/`.
The negative contract test confirms rejection, not interoperability. T089 remains
blocked; there is no placeholder canonical E2E file or `E2E_READY` assertion.

## Run inspection

Use the existing CT15 operator's additive command:

```text
python -m epick_engine.source_collection.commit_gate_operator inspect-run --input <private-scope-file>
```

Input is strict JSON, bounded to 4096 bytes:

```text
{
  "schema_version": "w2.ct15.run-inspection.scope.v1",
  "run_id": "ct15-<synthetic-run>",
  "source_id": "<shared-source-uuid>",
  "primary": {"owner_ref": "<uuid>", "job_id": "<uuid>", "command_id": "<uuid>"},
  "secondary": {"owner_ref": "<different-uuid>", "job_id": "<different-uuid>", "command_id": "<different-uuid>"}
}
```

The scope comes from W1's controlled fixture, not arbitrary production IDs. Both
command locks are acquired in deterministic order in one transaction. The output
uses [ct15-run-inspection.schema.json](ct15-run-inspection.schema.json):
primary/secondary/total counts only for those two commands, with no global count,
payload, owner/job/command/source IDs or hashes. The synthetic run ID is included.
Source verification is explicit: matched, absent, or erased/unverifiable after
terminal payload erasure; erased metadata must not be presented as verified.
The older single-command `inspect` schema is unchanged.

W1 and W2 count names describe different stores. Compare semantic expectations,
not raw equality of every table count: W1 operation/outbox counts are not W2 ACK
counts. W1 result-effect and W2 visible-result observations must be interpreted
at the agreed scenario checkpoint, including terminal deletion.

## Transport controls

All ordinary CT15 settings/preflight still apply, plus the exact additional
opt-in `W2_CT15_TRANSPORT_CONTROLS=true`. Run only one bounded invocation:

```text
python -m epick_engine.source_collection.ct15_transport_controls <mode> --run-id <ct15-run> --command <private-valid-collection-command-file>
```

The command file is at most 16 KiB and passes the unchanged W2 parser. Supply the
exact command for the intended synthetic owner/job. It is neither a W1 gate file
nor a `{command,result}` pair. A wrong scope fails closed, and only the chosen
command's pending staged/ACK delivery is selected; other owners' pending rows
are not drained. No new queue or W1 SenderId is manufactured.

| Mode | Effect |
| --- | --- |
| `duplicate` | Send the same persisted W2 wire twice, with the same ID/body. |
| `conflict` | Send the original staged result and a valid different result/digest with the same message ID; never mutate the persisted original or an ACK. |
| `drop-ack` | Interrupt before the ACK send; rollback the delivery marker so a fresh normal relay can send the original ACK. A pending staged result is sent normally; `control_applied=false` means the ACK fault has not occurred. |
| `ack-send-uncertain` | Interrupt after the queue accepts the ACK but before delivery-marker commit; a fresh relay sends the exact same persisted wire. |
| `retain` | Process one genuine authenticated W1 gate but suppress its receipt deletion to allow ordinary queue redelivery. |
| `retain-finalize` | Suppress deletion only for a genuine FINALIZE. Other valid gate actions are processed normally. |

Controls must run without competing W2 consumer/relay processes for the case.
The receive path cannot choose an SQS message by command: a wrong scoped delivery
is not deleted and is not processed. Isolate/schedule the scenario before calling
it; do not loop until a desired message happens to arrive.

Retention adds no visibility override, queue permission or DLQ-policy change.
The existing SqsGateQueue receive call specifies a **60-second per-message
visibility timeout**, overriding the queue default for that receive. Schedule
W1's approved action inside that window, not an assumed queue-default window.
W2 never sends
a copied W1 gate under its own identity or spoofs a system SenderId. The real
queue's later redelivery/DLQ result must be collected in the actual environment.

`drop-ack` and `ack-send-uncertain` deliberately return `SEND_FAILED` and exit 1.
That is the planned fault, not a successful transport handoff. Construct a fresh
normal relay invocation to recover; do not declare successful recovery from the
fault status alone. This tests the durable failure boundary, not an actual OS
process kill. An operational restart can use the same persisted DB afterward.

Evidence JSON records the synthetic run, control mode/applied flag, counts,
message IDs and SHA-256 of wire bytes. It contains no body, receipt, DSN or token.
A control that was not triggered returns exit 2 and must not count as a passed
scenario (including EMPTY or a normally processed non-target message). Exit 0
only means a control was triggered with successful local processing, not a joint
test pass. Send count
means confirmed SDK handoffs, not W1 application acceptance. No DLQ-completion
status or joint CT15 pass is synthesized by these commands.

## FINALIZE after PURGE: two different cases

- An exact previously applied FINALIZE replay reuses its original APPLIED ACK,
  while the W2 state remains PURGED and private payloads remain erased. W1 must
  apply its own currentness/idempotency boundary to that historical ACK.
- A previously unseen FINALIZE cannot cause a new FINALIZED transition after
  PURGE; W2 rejects it without deleting the receipt.

This work preserves the adopted store semantics. It does not reinterpret an ACK
replay as the current visibility of a result, and does not implement T067's wider
account/Project deletion.

## Remaining gates

Corrected W1 seed/runbook, actual isolated environment, source/image delivery pin,
workload identity, joint test scheduling and CT15-01~09/teardown evidence are still
required. Unit/PG transport doubles are not real queue evidence. See the parent
feature validation record for executed commands and results.
