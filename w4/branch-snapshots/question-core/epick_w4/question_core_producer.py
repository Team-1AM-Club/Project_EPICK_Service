"""Host-only W4 producer. No route accepts a client/model-supplied job_id.

The W1 context port and W4 policy port must be installed by the trusted host.
The CT-12 HTTP/plan adapters live in question_core_http/question_core_runtime.
REAL data stays disabled; synthetic runtime enablement is separate from wire adoption.
"""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Annotated, Literal, Protocol
from uuid import uuid4

from pydantic import AwareDatetime, ConfigDict, Field, ValidationError

from .c01_contract import UUIDText
from .handoff_contract import Identifier, Record
from .question_core_contract import QuestionCoreError, canonical_json, digest, require


def utc_now():
    return datetime.now(timezone.utc)


class JobContext(Record):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )
    context_key: Identifier
    job_id: UUIDText
    question_version_id: UUIDText
    source_id: UUIDText
    analysis_input_version: Annotated[str, Field(min_length=1, max_length=64, pattern=r"\S")]
    authorization_revision: Identifier
    current_decision_version: Annotated[int, Field(ge=0)]
    data_kind: Literal["SYNTHETIC", "REAL"]
    processing_allowed: bool
    question_current: bool
    source_active: bool
    revoked: bool
    valid_until: AwareDatetime


class PolicyDecision(Record):
    model_config = ConfigDict(
        extra="forbid", strict=True, frozen=True, revalidate_instances="always"
    )
    decision_version: Annotated[int, Field(gt=0)]
    decision_code: Literal["CORE_REQUIRED", "NON_CORE_OPTIONAL"]
    reason_code: Literal["QUESTION_EVIDENCE_REQUIRED", "SUPPLEMENTARY_CONTEXT"]
    policy_revision: Identifier
    approved: bool


class W1ContextPort(Protocol):
    def load(self, context_key: str) -> JobContext:
        """Load authenticated, current W1 context; never copy request/model fields."""


class W4PolicyPort(Protocol):
    def load(self, context: JobContext) -> PolicyDecision:
        """Load explicitly approved W4 policy, not LLM support/rank/C01 usability."""


@dataclass(frozen=True)
class PreparedDecision:
    submission_key: str
    context_key: str
    message_id: str
    decision_id: str
    body: str = field(repr=False)
    body_digest: str
    context_json: str = field(repr=False)
    policy_json: str = field(repr=False)
    schema_sha256: str
    prepared_at: str


def _binding(context):
    return context.model_dump(mode="json", exclude={"current_decision_version", "valid_until"})


class QuestionCoreProducer:
    def __init__(
        self, *, contract, store, contexts: W1ContextPort, policies: W4PolicyPort, clock=utc_now
    ):
        self.contract, self.store = contract, store
        self.contexts, self.policies, self.clock = contexts, policies, clock

    def _current(self, context_key):
        try:
            context = JobContext.model_validate(self.contexts.load(context_key))
            require(context.context_key == context_key, "CORE_CONTEXT_KEY_MISMATCH")
            require(context.data_kind == "SYNTHETIC", "CORE_REAL_DATA_NOT_ENABLED")
            require(
                context.processing_allowed
                and context.question_current
                and context.source_active
                and not context.revoked,
                "CORE_CONTEXT_NOT_CURRENT",
            )
            require(context.valid_until > self.clock(), "CORE_CONTEXT_EXPIRED")
            policy = PolicyDecision.model_validate(self.policies.load(context))
        except ValidationError:
            raise QuestionCoreError("CORE_TRUSTED_CONTEXT_INVALID") from None
        except QuestionCoreError as error:
            if error.code in {
                "CORE_CONTEXT_KEY_MISMATCH",
                "CORE_REAL_DATA_NOT_ENABLED",
                "CORE_CONTEXT_NOT_CURRENT",
                "CORE_CONTEXT_EXPIRED",
                "CORE_CONTEXT_AUTH_FAILED",
                "CORE_CONTEXT_NOT_FOUND",
                "CORE_CONTEXT_REQUEST_INVALID",
                "CORE_CONTEXT_RESPONSE_INVALID",
                "CORE_POLICY_PLAN_INVALID",
                "CORE_CURRENTNESS_UNAVAILABLE",
            }:
                raise
            raise QuestionCoreError("CORE_CURRENTNESS_UNAVAILABLE") from None
        except Exception:  # noqa: BLE001 - host adapter errors must not expose private data.
            raise QuestionCoreError("CORE_CURRENTNESS_UNAVAILABLE") from None
        require(policy.approved, "CORE_POLICY_NOT_APPROVED")
        expected = (
            "QUESTION_EVIDENCE_REQUIRED"
            if policy.decision_code == "CORE_REQUIRED"
            else "SUPPLEMENTARY_CONTEXT"
        )
        require(policy.reason_code == expected, "CORE_POLICY_REASON_MISMATCH")
        require(context.valid_until > self.clock(), "CORE_CONTEXT_EXPIRED")
        return context, policy

    def prepare(self, *, submission_key, context_key):
        # Identifiers are host-owned retry handles, not a public request payload.
        require(
            isinstance(submission_key, str)
            and 1 <= len(submission_key) <= 200
            and all(c.isalnum() or c in "-_.:" for c in submission_key),
            "CORE_SUBMISSION_KEY_INVALID",
        )
        context, policy = self._current(context_key)
        existing = self.store.get_submission(submission_key)
        if existing is not None:
            require(existing.context_key == context_key, "CORE_SUBMISSION_CONFLICT")
            require(
                self.store.inspect(existing.message_id)["state"] != "blocked",
                "CORE_SUBMISSION_BLOCKED",
            )
            self.check_current(existing, current=(context, policy))
            return existing
        require(policy.decision_version > context.current_decision_version, "CORE_REVISION_NOT_NEW")
        now = self.clock().isoformat()
        message = self.contract.build(
            {
                "message_id": str(uuid4()),
                "decision_id": str(uuid4()),
                "occurred_at": now,
                "job_id": context.job_id,
                "question_version_id": context.question_version_id,
                "source_id": context.source_id,
                "analysis_input_version": context.analysis_input_version,
                "decision_version": policy.decision_version,
                "decision_code": policy.decision_code,
                "reason_code": policy.reason_code,
                "is_core": policy.decision_code == "CORE_REQUIRED",
            }
        )
        prepared = PreparedDecision(
            submission_key,
            context_key,
            message["message_id"],
            message["decision_id"],
            canonical_json(message).decode("utf-8"),
            digest(message),
            canonical_json(context.model_dump(mode="json")).decode("utf-8"),
            canonical_json(policy.model_dump()).decode("utf-8"),
            self.contract.source.schema_sha256,
            now,
        )
        # Store may return a concurrently committed identical submission.
        saved = self.store.save(prepared)
        self.check_current(saved, current=(context, policy))
        return saved

    def check_current(self, prepared, *, current=None):
        context, policy = current or self._current(prepared.context_key)
        require(prepared.schema_sha256 == self.contract.source.schema_sha256, "CORE_SCHEMA_CHANGED")
        try:
            before = JobContext.model_validate_json(prepared.context_json)
            old_policy = PolicyDecision.model_validate_json(prepared.policy_json)
        except ValidationError:
            raise QuestionCoreError("CORE_STORED_DECISION_INVALID") from None
        require(_binding(before) == _binding(context), "CORE_CONTEXT_CHANGED")
        require(old_policy == policy, "CORE_POLICY_CHANGED")
        require(
            context.current_decision_version >= before.current_decision_version,
            "CORE_REVISION_REGRESSED",
        )
        require(
            policy.decision_version > before.current_decision_version
            and policy.decision_version >= context.current_decision_version,
            "CORE_REVISION_STALE",
        )
        message = self.contract.validate(prepared.body)
        require(
            canonical_json(message).decode("utf-8") == prepared.body
            and digest(message) == prepared.body_digest,
            "CORE_STORED_BODY_CHANGED",
        )
        expected = {
            "message_id": prepared.message_id,
            "decision_id": prepared.decision_id,
            "occurred_at": prepared.prepared_at,
            "job_id": context.job_id,
            "question_version_id": context.question_version_id,
            "source_id": context.source_id,
            "analysis_input_version": context.analysis_input_version,
            "decision_version": policy.decision_version,
            "decision_code": policy.decision_code,
            "reason_code": policy.reason_code,
            "is_core": policy.decision_code == "CORE_REQUIRED",
        }
        require(all(message[k] == v for k, v in expected.items()), "CORE_EVENT_CONTEXT_MISMATCH")
        require(
            json.loads(prepared.context_json)["context_key"] == prepared.context_key,
            "CORE_CONTEXT_KEY_MISMATCH",
        )
        require(context.valid_until > self.clock(), "CORE_CONTEXT_EXPIRED")
        return prepared.body
