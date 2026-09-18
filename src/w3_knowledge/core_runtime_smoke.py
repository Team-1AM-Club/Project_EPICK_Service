"""Explicit local diagnostic, synthetic data only; never constructs an AWS client."""

import json
from pathlib import Path
from uuid import UUID

from .core_decision import DecisionContext
from .core_runtime import AnalysisPlan, Authorization, CoreRuntime


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
    runtime = CoreRuntime(directory / "outbox.db", retention_seconds=60, max_attempts=3)
    event = runtime.supply(
        AnalysisPlan(context=context, required_sources=[context.source_id], optional_sources=[]),
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
    runtime.backup(directory / "redacted-backup.db")
    if runtime.delete_owner(UUID(int=4), deletion_epoch=2) != 1:
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
        "deleted_state": runtime.inspect()[0]["state"],
        "aws_calls": 0,
    }
    (directory / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report
