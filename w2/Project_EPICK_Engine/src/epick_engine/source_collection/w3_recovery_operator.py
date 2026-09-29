"""Fail-closed operator for W2-to-W3 Source replay and explicit snapshots.

The operator deliberately has no scheduler and never opens W3's private
storage.  It reads one complete W2 Source history, releases that transaction,
then drives only W3's public C-01 recovery endpoints from W3's durable cursor.
"""

from __future__ import annotations

import argparse
import json
import os
import ssl
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Literal, NoReturn, Protocol, cast
from uuid import UUID

from epick_engine.source_collection.persistence import (
    create_database_engine,
    create_session_factory,
    database_url_from_environment,
)
from epick_engine.source_collection.w3_recovery_payloads import (
    W3RecoveryPayloadError,
    make_replay_batch,
    make_snapshot,
)
from epick_engine.source_collection.w3_recovery_store import (
    RecoveryHistory,
    SqlAlchemyRecoveryHistoryStore,
)
from epick_engine.source_collection.w3_recovery_transport import (
    W3RecoveryClient,
    W3RecoveryStatus,
)

RecoveryMode = Literal["replay", "snapshot"]
RecoveryKind = Literal["REPLAYED", "ALREADY_CURRENT", "SNAPSHOT_APPLIED"]


class W3RecoveryOperationError(RuntimeError):
    """Sanitized recovery-control failure without remote or Source details."""

    def __init__(self, code: str) -> None:
        super().__init__(f"W3 recovery operation failed: {code}")
        self.code = code


class _RecoveryArgumentParser(argparse.ArgumentParser):
    """Convert invalid operator arguments into the module's sanitized failure path."""

    def error(self, message: str) -> NoReturn:
        del message
        raise ValueError("invalid recovery arguments")


@dataclass(frozen=True, slots=True)
class RecoveryResult:
    """Committed recovery progress; this is not an index-readiness result."""

    kind: RecoveryKind
    source_id: UUID
    event_cursor: int
    restriction_revision: int


class _RecoveryHistoryStore(Protocol):
    def load_history(self, *, source_id: UUID) -> RecoveryHistory: ...


class _W3RecoveryClient(Protocol):
    def status(self, *, source_id: UUID) -> W3RecoveryStatus: ...

    def replay(self, *, source_id: UUID, batch: dict[str, object]) -> W3RecoveryStatus: ...

    def snapshot(self, *, source_id: UUID, payload: dict[str, object]) -> W3RecoveryStatus: ...


class W3SourceRecovery:
    """Recover exactly one W3 Source from W3's reported durable state."""

    def __init__(
        self,
        *,
        store: _RecoveryHistoryStore,
        client: _W3RecoveryClient,
    ) -> None:
        self._store = store
        self._client = client

    def recover(self, *, source_id: UUID, mode: RecoveryMode) -> RecoveryResult:
        """Replay from W3's cursor or apply an explicitly requested full snapshot."""

        if not isinstance(source_id, UUID):
            raise W3RecoveryOperationError("INVALID_SOURCE_ID")
        if mode not in {"replay", "snapshot"}:
            raise W3RecoveryOperationError("INVALID_MODE")

        history = self._store.load_history(source_id=source_id)
        self._validate_history(history, source_id=source_id)
        status = self._client.status(source_id=source_id)
        self._validate_status(status, source_id=source_id, history=history, response=False)

        if mode == "replay":
            return self._replay(source_id=source_id, history=history, status=status)
        return self._snapshot(source_id=source_id, history=history, status=status)

    @staticmethod
    def _validate_history(history: RecoveryHistory, *, source_id: UUID) -> None:
        """Validate all local public history before making any W3 HTTP request."""

        if not isinstance(history, RecoveryHistory) or history.source_id != source_id:
            raise W3RecoveryOperationError("LOCAL_HISTORY_UNSAFE")
        try:
            # This validates every event/wire and F=0, without retaining an open DB session.
            make_replay_batch(history, after_cursor=0)
        except W3RecoveryPayloadError:
            raise W3RecoveryOperationError("LOCAL_HISTORY_UNSAFE") from None

    @staticmethod
    def _validate_status(
        status: W3RecoveryStatus,
        *,
        source_id: UUID,
        history: RecoveryHistory,
        response: bool,
    ) -> None:
        """Reject a malformed, foreign, conflicted, or ahead W3 state."""

        if (
            not isinstance(status, W3RecoveryStatus)
            or status.source_id != source_id
            or type(status.event_cursor) is not int
            or type(status.required_event_cursor) is not int
            or type(status.restriction_revision) is not int
            or type(status.required_restriction_revision) is not int
            or status.event_cursor < 0
            or status.required_event_cursor < 0
            or status.restriction_revision < 0
            or status.required_restriction_revision < 0
            or not isinstance(status.reason, str)
            or type(status.history_complete) is not bool
            or type(status.index_ack) is not bool
            or status.index_ack != (status.reason == "READY")
            or (status.outcome is not None and not isinstance(status.outcome, str))
            or (not response and status.outcome is not None)
        ):
            raise W3RecoveryOperationError("REMOTE_STATUS_UNSAFE")
        if status.reason == "CONFLICT" or status.outcome == "CONFLICT":
            raise W3RecoveryOperationError("REMOTE_CONFLICT")
        if (
            status.event_cursor > history.high_watermark
            or status.required_event_cursor > history.high_watermark
        ):
            raise W3RecoveryOperationError("REMOTE_CURSOR_UNSAFE")
        if (
            status.restriction_revision > history.restriction_revision
            or status.required_restriction_revision > history.restriction_revision
        ):
            raise W3RecoveryOperationError("REMOTE_RESTRICTION_UNSAFE")

    @staticmethod
    def _is_complete(status: W3RecoveryStatus, history: RecoveryHistory) -> bool:
        return (
            status.event_cursor == history.high_watermark
            and status.required_event_cursor == history.high_watermark
            and status.restriction_revision == history.restriction_revision
            and status.required_restriction_revision == history.restriction_revision
        )

    def _replay(
        self,
        *,
        source_id: UUID,
        history: RecoveryHistory,
        status: W3RecoveryStatus,
    ) -> RecoveryResult:
        """Post at most one page per durable W3 status observation."""

        posted = False
        while True:
            cursor = status.event_cursor
            if cursor == history.high_watermark:
                if not self._is_complete(status, history):
                    raise W3RecoveryOperationError("REMOTE_STATUS_INCOMPLETE")
                return RecoveryResult(
                    kind="REPLAYED" if posted else "ALREADY_CURRENT",
                    source_id=source_id,
                    event_cursor=history.high_watermark,
                    restriction_revision=history.restriction_revision,
                )

            receipt = self._client.replay(
                source_id=source_id,
                batch=make_replay_batch(history, after_cursor=cursor),
            )
            posted = True
            self._validate_status(receipt, source_id=source_id, history=history, response=True)
            if receipt.outcome == "SNAPSHOT_REQUIRED":
                raise W3RecoveryOperationError("SNAPSHOT_REQUIRED")
            if receipt.outcome not in {"INCOMPLETE", "REPLAYED"}:
                raise W3RecoveryOperationError("UNEXPECTED_REPLAY_OUTCOME")
            if receipt.event_cursor <= cursor:
                raise W3RecoveryOperationError("REPLAY_NO_PROGRESS")

            if receipt.outcome == "REPLAYED":
                if not self._is_complete(receipt, history):
                    raise W3RecoveryOperationError("REPLAY_RECEIPT_UNSAFE")
                return RecoveryResult(
                    kind="REPLAYED",
                    source_id=source_id,
                    event_cursor=history.high_watermark,
                    restriction_revision=history.restriction_revision,
                )

            # W3 did not declare completion.  Do not send another page until its
            # public status confirms durable progress and becomes the next cursor.
            refreshed = self._client.status(source_id=source_id)
            self._validate_status(refreshed, source_id=source_id, history=history, response=False)
            if refreshed.event_cursor < receipt.event_cursor or refreshed.event_cursor <= cursor:
                raise W3RecoveryOperationError("REPLAY_NO_PROGRESS")
            status = refreshed

    def _snapshot(
        self,
        *,
        source_id: UUID,
        history: RecoveryHistory,
        status: W3RecoveryStatus,
    ) -> RecoveryResult:
        """Apply one explicit, complete snapshot only when local watermarks dominate."""

        # _validate_status already established current and required W3 watermarks
        # are not ahead of local H/R.  Snapshot selection is never automatic.
        del status
        receipt = self._client.snapshot(source_id=source_id, payload=make_snapshot(history))
        self._validate_status(receipt, source_id=source_id, history=history, response=True)
        if (
            receipt.outcome != "SNAPSHOT_APPLIED"
            or not self._is_complete(receipt, history)
            or receipt.history_complete is not False
            or receipt.index_ack is not False
        ):
            raise W3RecoveryOperationError("SNAPSHOT_RECEIPT_UNSAFE")
        return RecoveryResult(
            kind="SNAPSHOT_APPLIED",
            source_id=source_id,
            event_cursor=history.high_watermark,
            restriction_revision=history.restriction_revision,
        )


def main(argv: Sequence[str] | None = None) -> int:
    """Run one explicit recovery command with sanitized stdout on every failure."""

    parser = _RecoveryArgumentParser(description="Recover one W2 Source in W3")
    parser.add_argument("--source-id", type=UUID, required=True)
    parser.add_argument("--mode", choices=("replay", "snapshot"), default="replay")

    engine = None
    try:
        args = parser.parse_args(argv)
        ca_file = os.environ.get("EPICK_W3_CA_FILE")
        ssl_context = ssl.create_default_context(cafile=ca_file) if ca_file else None
        client = W3RecoveryClient(
            endpoint=os.environ.get("EPICK_W3_EVENT_ENDPOINT", ""),
            bearer_token=os.environ.get("EPICK_W3_W2_TOKEN", ""),
            ssl_context=ssl_context,
        )
        engine = create_database_engine(database_url_from_environment())
        recovery = W3SourceRecovery(
            store=SqlAlchemyRecoveryHistoryStore(
                session_factory=create_session_factory(engine),
            ),
            client=client,
        )
        result = recovery.recover(
            source_id=args.source_id,
            mode=cast(RecoveryMode, args.mode),
        )
        print(
            json.dumps(
                {
                    "status": result.kind,
                    "source_id": str(result.source_id),
                    "event_cursor": result.event_cursor,
                },
                sort_keys=True,
            )
        )
        return 0
    except Exception:
        # Configuration, DB, TLS, and HTTP diagnostics may include credentials or Source data.
        print('{"status":"W3_RECOVERY_FAILED"}')
        return 1
    finally:
        if engine is not None:
            engine.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
