"""Opt-in, bounded CT15 faults around the real queue port, never a W1 producer.

Retaining a genuine receipt lets SQS redeliver it under its existing visibility
policy. No captured body or receipt is written to disk or forged as W1 traffic.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from collections.abc import Sequence
from pathlib import Path
from typing import Literal

from sqlalchemy.orm import sessionmaker

from epick_engine.source_collection.commit_gate_contracts import (
    CommitGateAckProposal,
    StagedResultProposal,
    parse_commit_gate_command,
    staged_result_digest,
)
from epick_engine.source_collection.commit_gate_operator import (
    Ct15Settings,
    SqsGateQueue,
    create_ct15_engine,
    create_private_authority_client,
    create_sqs_client,
    preflight,
)
from epick_engine.source_collection.commit_gate_runtime import (
    MAX_OUTBOUND_MESSAGE_BYTES,
    Queue,
    QueueDelivery,
    _sender_matches,
    _strict_json_object,
    _wire_body,
    consume_once,
    relay_once,
)
from epick_engine.source_collection.contracts import CollectionCommand
from epick_engine.source_collection.ct15_inspection import CT15_RUN_ID_PATTERN
from epick_engine.source_collection.w1_transport import _parse_wire, _revalidate_model

type ControlMode = Literal[
    "duplicate", "conflict", "drop-ack", "ack-send-uncertain", "retain", "retain-finalize"
]
MODES = ("duplicate", "conflict", "drop-ack", "ack-send-uncertain", "retain", "retain-finalize")


class Ct15ControlError(ValueError):
    """Fixed diagnostic without private queue data or operator configuration."""


class ControlledQueue:
    """One processing iteration only; construction is not environment authorization."""

    def __init__(
        self,
        queue: Queue,
        *,
        command: CollectionCommand,
        run_id: str,
        mode: str,
        approved: bool,
        expected_sender_id: str,
    ) -> None:
        if (
            approved is not True
            or mode not in MODES
            or re.fullmatch(CT15_RUN_ID_PATTERN, run_id) is None
            or not expected_sender_id
            or ":" in expected_sender_id
        ):
            raise Ct15ControlError("explicit bounded synthetic control required")
        try:
            self._command = _revalidate_model(command, CollectionCommand, label="CT15 command")
        except Exception:
            raise Ct15ControlError("validated synthetic command required") from None
        self._queue = queue
        self._run_id = run_id
        self._mode = mode
        self._sender = expected_sender_id
        self._received = False
        self._sent = False
        self._receipt: str | None = None
        self._retain = False
        self._applied = False
        self._send_count = 0
        self._received_count = 0
        self._suppressed = 0
        self._messages: list[dict[str, str]] = []

    def _bound(self, raw: dict[str, object]) -> None:
        if (
            raw.get("command_id") != str(self._command.command_id)
            or raw.get("job_id") != str(self._command.job_id)
            or raw.get("authenticated_owner_ref") != str(self._command.authenticated_owner_ref)
        ):
            raise Ct15ControlError("synthetic control scope mismatch")

    def _record(self, body: str, raw: dict[str, object], direction: str) -> None:
        self._messages.append(
            {
                "direction": direction,
                "message_id": str(raw["message_id"]),
                "wire_sha256": "sha256:" + hashlib.sha256(body.encode("utf-8")).hexdigest(),
            }
        )

    def receive(self) -> Sequence[QueueDelivery]:
        if self._received or self._mode not in {"retain", "retain-finalize"}:
            raise Ct15ControlError("control allows only one inbound iteration")
        self._received = True
        deliveries = self._queue.receive()
        if not deliveries:
            return deliveries
        if len(deliveries) != 1:
            raise Ct15ControlError("control allows only one received message")
        delivery = deliveries[0]
        if not _sender_matches(delivery.sender_id, self._sender):
            # Keep the genuine transport attributes for the ordinary consumer's
            # authentication failure. Do not bless or remember this receipt.
            return deliveries
        try:
            raw = _strict_json_object(delivery.body)
            gate = parse_commit_gate_command(raw)
            self._bound(raw)
        except Exception:
            raise Ct15ControlError("synthetic gate control input rejected") from None
        self._receipt = delivery.receipt_handle
        self._retain = self._mode == "retain" or gate.action == "FINALIZE"
        self._received_count += 1
        self._record(delivery.body, raw, "received")
        return deliveries

    def delete(self, receipt_handle: str) -> None:
        if self._receipt is None or receipt_handle != self._receipt:
            raise Ct15ControlError("control receipt unavailable")
        self._receipt = None
        if self._retain:
            self._suppressed += 1
            self._applied = True
            return
        self._queue.delete(receipt_handle)

    def send(self, body: str) -> None:
        if self._sent or self._mode in {"retain", "retain-finalize"}:
            raise Ct15ControlError("control allows only one outbound iteration")
        self._sent = True
        try:
            raw = _strict_json_object(body, max_bytes=MAX_OUTBOUND_MESSAGE_BYTES)
            if raw.get("message_type") == "w2.private.staged-result.proposal.v1":
                stage = _parse_wire(raw, StagedResultProposal, label="CT15 staged result")
                if stage.command != self._command:
                    raise Ct15ControlError("synthetic control scope mismatch")
                is_ack = False
            else:
                _parse_wire(raw, CommitGateAckProposal, label="CT15 ACK")
                self._bound(raw)
                is_ack = True
            conflict_body = None
            if self._mode == "conflict":
                if is_ack:
                    raise Ct15ControlError("conflict control only accepts staged result")
                changed = stage.result.model_copy(
                    update={"message_ko": stage.result.message_ko + "\nCT15 synthetic conflict"}
                )
                # Validate the different result and recompute its digest. This
                # tests message-identity conflict, not merely malformed JSON.
                raw["result_digest"] = staged_result_digest(stage.command, changed)
                raw["result"] = changed.model_dump(mode="json")
                _parse_wire(raw, StagedResultProposal, label="CT15 conflict")
                conflict_body = _wire_body(raw)
        except Exception:
            raise Ct15ControlError("synthetic outbound control input rejected") from None

        if self._mode == "drop-ack" and is_ack:
            self._applied = True
            self._record(body, raw, "ack_not_sent")
            raise Ct15ControlError("synthetic transport interruption")
        self._queue.send(body)
        self._send_count += 1
        self._record(body, raw, "sent")
        if self._mode == "ack-send-uncertain" and is_ack:
            self._applied = True
            raise Ct15ControlError("synthetic transport interruption")
        if self._mode in {"duplicate", "conflict"}:
            second = conflict_body if conflict_body is not None else body
            self._queue.send(second)
            self._send_count += 1
            self._record(second, raw, "sent")
            self._applied = True

    def evidence(self) -> dict[str, object]:
        return {
            "schema_version": "w2.ct15.transport-control.v1",
            "run_id": self._run_id,
            "mode": self._mode,
            "control_applied": self._applied,
            "send_count": self._send_count,
            "received_count": self._received_count,
            "delete_suppressed_count": self._suppressed,
            "messages": [dict(item) for item in self._messages],
        }


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="W2 isolated CT15 bounded transport controls")
    parser.add_argument("mode", choices=MODES)
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--command", type=Path, required=True)
    args = parser.parse_args(argv)
    engine = None
    try:
        if os.environ.get("W2_CT15_TRANSPORT_CONTROLS") != "true":
            raise Ct15ControlError("explicit synthetic controls approval required")
        settings = Ct15Settings.from_environment(os.environ)
        if args.command.stat().st_size > 16_384:
            raise Ct15ControlError("synthetic command exceeds bound")
        with args.command.open("rb") as command_file:
            data = command_file.read(16_385)
        raw = _strict_json_object(data.decode("utf-8"))
        command = _parse_wire(raw, CollectionCommand, label="CT15 command")
        client = create_sqs_client(settings)
        queue = ControlledQueue(
            SqsGateQueue(client, settings),
            command=command,
            run_id=args.run_id,
            mode=args.mode,
            approved=True,
            expected_sender_id=settings.expected_w1_sender_id,
        )
        engine = create_ct15_engine(settings)
        preflight(engine, client, settings)
        private_authority_client = create_private_authority_client(settings)
        sessions = sessionmaker(engine, expire_on_commit=False)
        outcome = (
            consume_once(
                sessions,
                queue,
                settings.expected_w1_sender_id,
                private_authority_client=private_authority_client,
            )
            if args.mode in {"retain", "retain-finalize"}
            else relay_once(
                sessions,
                queue,
                command_id=command.command_id,
                authority_client=private_authority_client,
            )
        )
        evidence = queue.evidence()
        print(json.dumps({"status": outcome.status, **evidence}, sort_keys=True))
        if outcome.status not in {"APPLIED", "SENT", "EMPTY"}:
            return 1
        return 0 if evidence["control_applied"] else 2
    except Exception:
        print('{"status":"CT15_CONTROL_FAILED"}')
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
