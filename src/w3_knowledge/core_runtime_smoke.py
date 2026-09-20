"""Explicit local diagnostic, synthetic data only; never constructs an AWS client."""

import json
from pathlib import Path
from uuid import UUID

from .core_decision import DecisionContext
from .core_runtime import AnalysisPlan, Authorization, CoreRuntime
from .retention import POLICY_REVISION, RetentionPolicy


def run(directory: Path):
    directory.mkdir(parents=True, exist_ok=False)
    context = DecisionContext(
        job_id=UUID(int=1),
        company_id=UUID(int=2),
        source_id=UUID(int=3),
        analysis_input_version="synthetic-analysis",
    )

    class SyntheticAuthority:
        def current(self, job_id, source_id):
            return Authorization(context=context, owner_id=UUID(int=4), owner_epoch=1, active=True)

    class LocalTransport:
        def __init__(self):
            self.bodies = []

        def send(self, body):
            self.bodies.append(body)
            if len(self.bodies) == 1:
                raise TimeoutError("synthetic timeout")
            return "synthetic-transport-id"

    authority, transport = SyntheticAuthority(), LocalTransport()
    policy = RetentionPolicy(
        handoff_body_seconds=60,
        private_body_max_seconds=600,
        terminal_metadata_seconds=600,
        owner_tombstone_seconds=600,
        retired_counter_seconds=600,
        backup_seconds=600,
    )
    runtime = CoreRuntime(directory / "outbox.db", policy=policy, max_attempts=3)
    event = runtime.supply(
        AnalysisPlan(
            context=context,
            analysis_request_id=UUID(int=5),
            analysis_request_issued_at=100,
            required_sources=[context.source_id],
            optional_sources=[],
        ),
        authority,
        "synthetic-1",
        now=100,
    )
    if runtime.relay_once(authority, transport, now=100) != "RETRY":
        raise RuntimeError("SMOKE_RETRY_FAILED")
    if runtime.relay_once(authority, transport, now=103) != "TRANSPORT_HANDOFF":
        raise RuntimeError("SMOKE_HANDOFF_FAILED")
    runtime.replay(event.message_id, authority, now=104)
    if runtime.relay_once(authority, transport, now=104) != "TRANSPORT_HANDOFF":
        raise RuntimeError("SMOKE_REPLAY_FAILED")
    runtime.backup(directory / "redacted-backup.db", now=104)
    if runtime.delete_owner(UUID(int=4), deletion_epoch=2, now=105) != 1:
        raise RuntimeError("SMOKE_DELETION_FAILED")
    if runtime.relay_once(authority, transport, now=105) != "IDLE":
        raise RuntimeError("SMOKE_DELETION_REPLAY_FAILED")
    if len(transport.bodies) != 3 or len(set(transport.bodies)) != 1:
        raise RuntimeError("SMOKE_BODY_CHANGED")
    report = {
        "status": "LOCAL_VERIFIED_NOT_DEPLOYED",
        "joint_ct12": "NOT_RUN",
        "same_body_retry": len(set(transport.bodies)) == 1,
        "send_success_semantics": "TRANSPORT_HANDOFF_NOT_W1_ACCEPTANCE",
        "policy_revision": POLICY_REVISION,
        "deleted_state": runtime.inspect()[0]["state"],
        "aws_calls": 0,
    }
    (directory / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
