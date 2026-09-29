"""Deterministic W1 failure-state helper for integration and contract tests.

This is deliberately a test-only aggregate.  It does not model the production
Job, queue, slot manager, or worker.  In particular, ``job_revision`` is W1
aggregate state and never substitutes for a W2 source ``result_version``.
"""

from __future__ import annotations

from collections.abc import Mapping
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from epick_engine.source_collection.contracts import (
    CollectionStage,
    CompletionKind,
    Failure,
)


class W1JobStatus(StrEnum):
    """The W1 lifecycle values needed by the synthetic failure cases."""

    QUEUED = "QUEUED"
    RUNNING = "RUNNING"
    WAITING_USER = "WAITING_USER"
    PAUSED_RATE_LIMIT = "PAUSED_RATE_LIMIT"
    SUCCEEDED = "SUCCEEDED"
    FAILED_RETRYABLE = "FAILED_RETRYABLE"
    FAILED_FINAL = "FAILED_FINAL"
    CANCELLED = "CANCELLED"


DecisionAction = Literal["retry", "continue_limited", "stop"]
_DECISION_SCHEMA_VERSION = "w1.collection-decision.v2"
_UNSET = object()


@dataclass
class _SourceState:
    name: str
    source_id: UUID
    is_core: bool
    result_version: int
    core_decision_revision: int | None
    outcome: str | None = None
    decision: DecisionAction | None = None
    dispatched: bool = False


class W1FailureFake:
    """Fixture-driven W1 decision state machine with an inspectable ledger."""

    def __init__(self, case: Mapping[str, Any]) -> None:
        self.case_name = self._required_text(case, "name")
        self.job_id = UUID(self._required_text(case, "job_id"))
        self.command_id = UUID(self._required_text(case, "command_id"))
        self.input_version = self._required_positive_int(case, "input_version")
        self.job_revision = self._required_positive_int(case, "job_revision")
        self.result_revision = self._required_positive_int(case, "result_revision")
        self._recorded_at = self._parse_utc(self._required_text(case, "recorded_at"))
        self.status = W1JobStatus.QUEUED
        self.completion_kind = CompletionKind.NONE
        self._sources = self._load_sources(case)
        self._source_names = {source.name: source.source_id for source in self._sources.values()}
        self._failures: list[Failure] = []
        self._ledger: list[dict[str, Any]] = []
        self._dispatches: list[UUID] = []
        self._active_slots: set[UUID] = set()
        self._processed_result_commands: set[UUID] = set()
        self._command_deliveries = 0
        self._logical_source_results = 0
        self._analysis_start_count = 0
        self._idempotency: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}

    @classmethod
    def from_case(cls, case: Mapping[str, Any]) -> W1FailureFake:
        """Build a fake and replay the fixed, no-I/O fixture events."""

        fake = cls(case)
        events = case.get("events", [])
        if not isinstance(events, list):
            raise ValueError("failure case events must be a list")
        for event in events:
            if not isinstance(event, Mapping):
                raise ValueError("failure case event must be an object")
            fake._replay_event(event)
        return fake

    def record_source_result(
        self,
        source_id: UUID | str,
        *,
        outcome: str,
        failure: Mapping[str, Any] | None = None,
        command_id: UUID | str | None = None,
        core_decision_revision: int | None | object = _UNSET,
    ) -> bool:
        """Record one logical source result; duplicate command delivery is ignored."""

        source = self._source(source_id)
        if self.status is W1JobStatus.CANCELLED:
            parsed_command_id = self._parse_uuid(command_id) if command_id is not None else None
            if parsed_command_id is not None:
                self._command_deliveries += 1
            self._append_event(
                "source_result_after_cancel_ignored",
                source_id=str(source.source_id),
                command_id=str(parsed_command_id) if parsed_command_id is not None else None,
            )
            return False
        if core_decision_revision is not _UNSET:
            if core_decision_revision is None:
                updated_core_decision_revision = None
            elif isinstance(core_decision_revision, int) and core_decision_revision >= 1:
                updated_core_decision_revision = core_decision_revision
            else:
                raise ValueError("core_decision_revision must be a positive integer or null")
            if outcome != "success" and updated_core_decision_revision is None:
                raise ValueError("failure source result requires core_decision_revision")
            source.core_decision_revision = updated_core_decision_revision
        if outcome != "success" and source.core_decision_revision is None:
            raise ValueError("failure source result requires core_decision_revision")

        parsed_command_id = self._parse_uuid(command_id) if command_id is not None else None
        if parsed_command_id is not None:
            self._command_deliveries += 1
            if parsed_command_id in self._processed_result_commands:
                self._append_event(
                    "source_result_duplicate_ignored",
                    source_id=str(source.source_id),
                    command_id=str(parsed_command_id),
                )
                return False
            self._processed_result_commands.add(parsed_command_id)

        source.decision = None
        if outcome == "success":
            source.outcome = outcome
            source.dispatched = False
            self._active_slots.discard(source.source_id)
        else:
            source.outcome = outcome
            source.dispatched = False
            self._active_slots.discard(source.source_id)
            self._failures.append(self._failure(source, outcome, failure))
        self.result_revision += 1
        self._logical_source_results += 1
        self._append_event(
            "source_result_recorded",
            source_id=str(source.source_id),
            outcome=outcome,
            source_result_version=source.result_version,
            result_revision=self.result_revision,
        )
        self._recompute_status()
        return True

    def submit_decision(
        self,
        decision: Mapping[str, Any] | None = None,
        *,
        action: DecisionAction | None = None,
        source_id: UUID | str | None = None,
        expected_input_version: int | None = None,
        expected_result_version: int | None = None,
        expected_job_revision: int | None = None,
        idempotency_key: str | None = None,
    ) -> dict[str, Any]:
        """Accept one user decision without dispatching work automatically.

        ``expected_job_revision`` is test-only optimistic-concurrency evidence.
        It is intentionally separate from the public v2 decision request's W1
        aggregate ``expected_result_version``.  Tests may omit it when only the
        public request fields are under examination.
        """

        values: dict[str, Any] = (
            dict(decision) if decision is not None else {"schema_version": _DECISION_SCHEMA_VERSION}
        )
        if decision is None:
            values.update(
                {
                    "action": action,
                    "source_id": source_id,
                    "expected_input_version": expected_input_version,
                    "expected_result_version": expected_result_version,
                    "expected_job_revision": expected_job_revision,
                    "idempotency_key": idempotency_key,
                }
            )
        else:
            action = values.get("action") if action is None else action
            source_id = values.get("source_id") if source_id is None else source_id
            expected_input_version = (
                values.get("expected_input_version")
                if expected_input_version is None
                else expected_input_version
            )
            expected_result_version = (
                values.get("expected_result_version")
                if expected_result_version is None
                else expected_result_version
            )
            expected_job_revision = (
                values.get("expected_job_revision")
                if expected_job_revision is None
                else expected_job_revision
            )
            idempotency_key = (
                values.get("idempotency_key") if idempotency_key is None else idempotency_key
            )

        if values.get("schema_version") != _DECISION_SCHEMA_VERSION:
            return self._conflict("INVALID_SCHEMA_VERSION")
        if action not in ("retry", "continue_limited", "stop"):
            return self._conflict("INVALID_ACTION")
        if not isinstance(expected_input_version, int) or expected_input_version < 1:
            return self._conflict("INVALID_INPUT_VERSION")
        if not isinstance(expected_result_version, int) or expected_result_version < 1:
            return self._conflict("INVALID_RESULT_VERSION")
        if not isinstance(idempotency_key, str) or not idempotency_key:
            return self._conflict("INVALID_IDEMPOTENCY_KEY")
        if expected_job_revision is not None and (
            not isinstance(expected_job_revision, int) or expected_job_revision < 1
        ):
            return self._conflict("INVALID_JOB_REVISION")

        if action == "stop" and source_id is not None:
            return self._conflict("STOP_REQUIRES_NULL_SOURCE")
        if action != "stop" and source_id is None:
            return self._conflict("SOURCE_REQUIRED")

        source: _SourceState | None = None
        if source_id is not None:
            try:
                source = self._source(source_id)
            except ValueError:
                return self._conflict("SOURCE_NOT_IN_JOB")

        fingerprint = {
            "action": action,
            "source_id": str(source.source_id) if source is not None else None,
            "expected_input_version": expected_input_version,
            "expected_result_version": expected_result_version,
            "expected_job_revision": expected_job_revision,
        }
        prior = self._idempotency.get(idempotency_key)
        if prior is not None:
            if prior[0] != fingerprint:
                return self._conflict("IDEMPOTENCY_CONFLICT")
            self._append_event("decision_replayed", idempotency_key=idempotency_key)
            return deepcopy(prior[1])

        if expected_input_version != self.input_version:
            return self._conflict("STALE_INPUT_VERSION")
        if expected_job_revision is not None and expected_job_revision != self.job_revision:
            return self._conflict("STALE_JOB_REVISION")
        if expected_result_version != self.result_revision:
            return self._conflict("STALE_RESULT_VERSION")
        if self.status in {W1JobStatus.CANCELLED, W1JobStatus.FAILED_FINAL, W1JobStatus.SUCCEEDED}:
            return self._conflict("JOB_NOT_DECIDABLE")

        if action == "stop":
            self.status = W1JobStatus.CANCELLED
            self.completion_kind = CompletionKind.NONE
            self._active_slots.clear()
            self.job_revision += 1
            self.result_revision += 1
        else:
            assert source is not None
            if source.outcome in (None, "success", "policy_denied"):
                return self._conflict("SOURCE_NOT_ELIGIBLE")
            if source.decision is not None or source.dispatched:
                return self._conflict("SOURCE_DECISION_ALREADY_RECORDED")
            source.decision = action
            self.job_revision += 1
            self.result_revision += 1
            self._recompute_status()

        receipt = {
            "status_code": 202,
            "accepted": True,
            "job_id": str(self.job_id),
            "job_revision": self.job_revision,
            "input_version": self.input_version,
            "result_revision": self.result_revision,
            "status": self.status.value,
            "dispatch_count": len(self._dispatches),
        }
        self._idempotency[idempotency_key] = (fingerprint, receipt)
        self._append_event(
            "decision_accepted",
            action=action,
            source_id=str(source.source_id) if source is not None else None,
            idempotency_key=idempotency_key,
        )
        return deepcopy(receipt)

    def advance_to(
        self,
        status: W1JobStatus | str,
        *,
        input_version: int | None = None,
    ) -> None:
        """Move the test aggregate to a fixed status, optionally changing input."""

        next_status = W1JobStatus(status)
        if input_version is not None and input_version < 1:
            raise ValueError("input_version must be positive")
        changed = next_status is not self.status
        if input_version is not None and input_version != self.input_version:
            self.input_version = input_version
            changed = True
        if changed:
            self.status = next_status
            self.job_revision += 1
        if next_status is W1JobStatus.CANCELLED:
            self._active_slots.clear()
        self._append_event("advanced", status=next_status.value, input_version=self.input_version)

    def dispatch_ready(self) -> tuple[UUID, ...]:
        """Dispatch only explicitly retry-selected sources once every core choice exists."""

        if self.status is not W1JobStatus.QUEUED or self._has_undecided_core_failure():
            self._append_event("dispatch_not_ready")
            return ()
        ready = tuple(
            source.source_id
            for source in self._sources.values()
            if source.decision == "retry" and not source.dispatched and source.outcome != "success"
        )
        if not ready:
            self._append_event("dispatch_not_requested")
            return ()
        for source_id in ready:
            self._sources[source_id].dispatched = True
            self._dispatches.append(source_id)
            self._active_slots.add(source_id)
        self.status = W1JobStatus.RUNNING
        self._append_event("sources_dispatched", source_ids=[str(source_id) for source_id in ready])
        return ready

    def redeliver(self, command_id: UUID | str | None = None) -> bool:
        """Record a broker delivery without consuming its future source result."""

        parsed_command_id = (
            self._parse_uuid(command_id) if command_id is not None else self.command_id
        )
        self._command_deliveries += 1
        self._append_event("command_delivered", command_id=str(parsed_command_id))
        return True

    def cancel(self) -> None:
        """Cancel the test aggregate and release its synthetic slots."""

        if self.status is not W1JobStatus.CANCELLED:
            self.status = W1JobStatus.CANCELLED
            self.completion_kind = CompletionKind.NONE
            self._active_slots.clear()
            self.job_revision += 1
        self._append_event("cancelled")

    def request_analysis(self) -> bool:
        """Start one synthetic analysis only after every eligible source resolves."""

        if self.status is W1JobStatus.CANCELLED:
            self._append_event("analysis_not_started", reason="job_cancelled")
            return False
        if self._analysis_start_count != 0:
            self._append_event("analysis_not_started", reason="already_started")
            return False
        if not self._analysis_eligible():
            self._append_event("analysis_not_started", reason="sources_unresolved")
            return False
        self._analysis_start_count = 1
        self._append_event("analysis_started")
        return True

    def snapshot(self) -> dict[str, Any]:
        """Return a JSON-ready aggregate snapshot and deterministic ledger."""

        return {
            "case": self.case_name,
            "job_id": str(self.job_id),
            "status": self.status.value,
            "input_version": self.input_version,
            "job_revision": self.job_revision,
            "result_revision": self.result_revision,
            "completion_kind": self.completion_kind.value,
            "sources": [
                {
                    "name": source.name,
                    "source_id": str(source.source_id),
                    "is_core": source.is_core,
                    "result_version": source.result_version,
                    "core_decision_revision": source.core_decision_revision,
                    "outcome": source.outcome,
                    "decision": source.decision,
                    "dispatched": source.dispatched,
                }
                for source in self._sources.values()
            ],
            "failures": [failure.model_dump(mode="json") for failure in self._failures],
            "required_actions": self._required_actions(),
            "dispatch_count": len(self._dispatches),
            "active_slots": len(self._active_slots),
            "command_deliveries": self._command_deliveries,
            "logical_commands": len(self._processed_result_commands),
            "logical_source_results": self._logical_source_results,
            "analysis_eligible": self._analysis_eligible(),
            "analysis_start_count": self._analysis_start_count,
            "ledger": deepcopy(self._ledger),
        }

    def _replay_event(self, event: Mapping[str, Any]) -> None:
        operation = event.get("op")
        if operation == "record_source_result":
            self.record_source_result(
                self._required_text(event, "source"),
                outcome=self._required_text(event, "outcome"),
                failure=self._mapping_or_none(event.get("failure")),
                command_id=event.get("command_id"),
                core_decision_revision=(
                    event["core_decision_revision"] if "core_decision_revision" in event else _UNSET
                ),
            )
            return
        if operation == "advance_to":
            self.advance_to(
                self._required_text(event, "status"),
                input_version=event.get("input_version"),
            )
            return
        if operation == "redeliver":
            self.redeliver(event.get("command_id"))
            return
        if operation == "cancel":
            self.cancel()
            return
        raise ValueError(f"unsupported failure fixture event: {operation!r}")

    def _recompute_status(self) -> None:
        if self.status is W1JobStatus.CANCELLED:
            return
        if any(source.outcome == "policy_denied" for source in self._sources.values()):
            self.status = W1JobStatus.FAILED_FINAL
            self.completion_kind = CompletionKind.NONE
            return
        if self._sources and all(source.outcome == "success" for source in self._sources.values()):
            self.status = W1JobStatus.SUCCEEDED
            self.completion_kind = CompletionKind.COMPLETE
            return
        if self._analysis_eligible():
            self.status = W1JobStatus.SUCCEEDED
            self.completion_kind = CompletionKind.PARTIAL
            return
        self.completion_kind = CompletionKind.NONE
        if any(
            source.outcome == "rate_limited" and source.decision is None
            for source in self._sources.values()
        ):
            self.status = W1JobStatus.PAUSED_RATE_LIMIT
            return
        if self._has_undecided_core_failure():
            self.status = W1JobStatus.WAITING_USER
            return
        if any(source.decision == "retry" for source in self._sources.values()):
            self.status = W1JobStatus.QUEUED
            return
        self.status = W1JobStatus.FAILED_RETRYABLE

    def _has_undecided_core_failure(self) -> bool:
        return any(
            source.is_core
            and source.outcome not in (None, "success", "policy_denied")
            and source.decision is None
            for source in self._sources.values()
        )

    def _analysis_eligible(self) -> bool:
        return (
            self.status is not W1JobStatus.CANCELLED
            and any(source.outcome == "success" for source in self._sources.values())
            and all(
                source.outcome == "success" or source.decision == "continue_limited"
                for source in self._sources.values()
            )
        )

    def _required_actions(self) -> list[dict[str, Any]]:
        if self.status in {W1JobStatus.CANCELLED, W1JobStatus.FAILED_FINAL, W1JobStatus.SUCCEEDED}:
            return []
        actions: list[dict[str, Any]] = []
        for source in self._sources.values():
            if source.outcome in (None, "success", "policy_denied") or source.decision is not None:
                continue
            if source.is_core:
                actions.append(
                    {
                        "action": "core_failure_decision",
                        "source_id": str(source.source_id),
                        "choices": ["continue_limited", "stop", "retry"],
                        "expected_result_version": self.result_revision,
                    }
                )
            else:
                actions.append(
                    {
                        "action": "user_retry",
                        "source_id": str(source.source_id),
                        "expected_result_version": self.result_revision,
                    }
                )
        return actions

    def _failure(
        self,
        source: _SourceState,
        outcome: str,
        value: Mapping[str, Any] | None,
    ) -> Failure:
        defaults: dict[str, tuple[CollectionStage, str, list[str], str]] = {
            "failure": (
                CollectionStage.PARSE,
                "EXTRACTION_FAILED",
                ["job_description"],
                "Source extraction failed.",
            ),
            "rate_limited": (
                CollectionStage.FETCH,
                "RATE_LIMITED",
                ["job_description"],
                "Source is rate limited.",
            ),
            "timeout": (
                CollectionStage.FETCH,
                "FETCH_TIMEOUT",
                ["job_description"],
                "Source fetch timed out.",
            ),
            "policy_denied": (
                CollectionStage.POLICY,
                "SOURCE_POLICY_BLOCKED",
                ["collection_permission"],
                "Policy denied collection.",
            ),
        }
        default_stage, default_code, default_missing, default_impact = defaults.get(
            outcome,
            defaults["failure"],
        )
        payload = dict(value or {})
        stage = CollectionStage(payload.get("stage", default_stage.value))
        missing_sections = payload.get("missing_sections", default_missing)
        if not isinstance(missing_sections, list) or not all(
            isinstance(section, str) and section for section in missing_sections
        ):
            raise ValueError("failure missing_sections must be non-empty strings")
        assert source.core_decision_revision is not None
        return Failure(
            source_id=source.source_id,
            stage=stage,
            code=str(payload.get("code", default_code)),
            missing_sections=missing_sections,
            impact=str(payload.get("impact", default_impact)),
            core_decision_revision=source.core_decision_revision,
        )

    def _source(self, source_id: UUID | str) -> _SourceState:
        if isinstance(source_id, str) and source_id in self._source_names:
            parsed_source_id = self._source_names[source_id]
        else:
            parsed_source_id = self._parse_uuid(source_id)
        try:
            return self._sources[parsed_source_id]
        except KeyError as error:
            raise ValueError("source is not part of this job") from error

    def _conflict(self, code: str) -> dict[str, Any]:
        self._append_event("decision_rejected", code=code)
        return {
            "status_code": 409,
            "accepted": False,
            "code": code,
            "job_revision": self.job_revision,
            "input_version": self.input_version,
            "result_revision": self.result_revision,
            "status": self.status.value,
        }

    def _append_event(self, event: str, **details: Any) -> None:
        timestamp = self._recorded_at + timedelta(seconds=len(self._ledger))
        self._ledger.append(
            {
                "at": timestamp.isoformat().replace("+00:00", "Z"),
                "event": event,
                **details,
            }
        )

    @staticmethod
    def _mapping_or_none(value: Any) -> Mapping[str, Any] | None:
        if value is None:
            return None
        if not isinstance(value, Mapping):
            raise ValueError("failure must be an object")
        return value

    @staticmethod
    def _parse_uuid(value: UUID | str) -> UUID:
        return value if isinstance(value, UUID) else UUID(value)

    @staticmethod
    def _parse_utc(value: str) -> datetime:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError("fixture timestamps must include a timezone")
        return parsed.astimezone(UTC)

    @classmethod
    def _load_sources(cls, case: Mapping[str, Any]) -> dict[UUID, _SourceState]:
        raw_sources = case.get("sources")
        if not isinstance(raw_sources, list) or not raw_sources:
            raise ValueError("failure case sources must be a non-empty list")
        sources: dict[UUID, _SourceState] = {}
        for raw_source in raw_sources:
            if not isinstance(raw_source, Mapping):
                raise ValueError("failure case source must be an object")
            source = _SourceState(
                name=cls._required_text(raw_source, "name"),
                source_id=UUID(cls._required_text(raw_source, "source_id")),
                is_core=raw_source.get("is_core") is True,
                result_version=cls._required_positive_int(raw_source, "result_version"),
                core_decision_revision=cls._optional_positive_int(
                    raw_source,
                    "core_decision_revision",
                ),
            )
            if source.source_id in sources or any(
                item.name == source.name for item in sources.values()
            ):
                raise ValueError("failure case source identifiers and names must be unique")
            sources[source.source_id] = source
        return sources

    @staticmethod
    def _required_text(value: Mapping[str, Any], key: str) -> str:
        parsed = value.get(key)
        if not isinstance(parsed, str) or not parsed:
            raise ValueError(f"{key} must be a non-empty string")
        return parsed

    @staticmethod
    def _required_positive_int(value: Mapping[str, Any], key: str) -> int:
        parsed = value.get(key)
        if not isinstance(parsed, int) or parsed < 1:
            raise ValueError(f"{key} must be a positive integer")
        return parsed

    @staticmethod
    def _optional_positive_int(value: Mapping[str, Any], key: str) -> int | None:
        parsed = value.get(key)
        if parsed is None:
            return None
        if not isinstance(parsed, int) or parsed < 1:
            raise ValueError(f"{key} must be a positive integer or null")
        return parsed
