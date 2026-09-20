"""Run-bound W1 execution seam. The host owns transactions, auth and persistence.

Only synthetic acceptance runs are enabled. This adapter does not implement the
team's PostgreSQL repository or declare the draft status policy approved.
"""

import os
from copy import deepcopy
from dataclasses import dataclass
from typing import Protocol
from uuid import UUID

from .c01_contract import C01ServerContext, C01ServiceOutput
from .handoff_contract import ServiceRequest
from .service_adapter import execute_service
from .synthetic_policy import content_hash


@dataclass(frozen=True)
class EpisodeVersionBinding:
    episode_id: str
    version: int
    episode_version_id: UUID


@dataclass(frozen=True)
class RunBinding:
    owner_user_id: UUID
    run_id: UUID
    project_id: UUID
    question_id: UUID
    question_version_id: UUID
    snapshot_id: UUID
    lease_token: UUID
    context_sha256: str
    request: dict
    episode_versions: tuple[EpisodeVersionBinding, ...]


class RunStore(Protocol):
    def acquire(self, *, owner_user_id: UUID, run_id: UUID) -> RunBinding | None:
        """Short atomic transaction: ENGINE PENDING -> RUNNING, freeze revision/lease.

        None means an already completed identical run; missing/forbidden/busy must
        raise a safe host exception. Never keep the DB transaction open for LLMs.
        """
        ...

    def load_context(self, binding: RunBinding) -> dict | None:
        """Owner/run/lease/snapshot/question-scoped current data; preserve original_narrative."""
        ...

    def authorize(self, binding: RunBinding, **action) -> bool:
        """Current lease, owner, consent, deletion and provider policy; strict bool."""
        ...

    def publish(self, binding: RunBinding, publication: dict) -> bool:
        """One fenced transaction: recheck RUNNING/lease/revisions/access/Source epochs;
        store full body + dependencies + candidates, mark LIMITED. Roll back all on
        failure. Returns False if changed, cancelled, revoked, or lease expired.
        Stored-result reads must recheck the same fences and Source validity.
        """
        ...

    def fail(self, binding: RunBinding, code: str) -> None:
        """Mark FAILED only if this exact lease still owns RUNNING; never revive cancellation."""
        ...


class BridgeError(RuntimeError):
    pass


def validate_binding(binding, context, owner, run):
    if any(
        not isinstance(getattr(binding, key), UUID)
        for key in (
            "owner_user_id",
            "run_id",
            "project_id",
            "question_id",
            "question_version_id",
            "snapshot_id",
            "lease_token",
        )
    ) or any(not isinstance(e.episode_version_id, UUID) for e in binding.episode_versions):
        raise BridgeError("W1_TRUSTED_UUID_REQUIRED")
    if binding.owner_user_id != owner or binding.run_id != run:
        raise BridgeError("W1_RUN_SCOPE_MISMATCH")
    request = ServiceRequest.model_validate(binding.request).model_dump()
    context = C01ServerContext.model_validate(context).model_dump()
    if (
        context["project"] != {"owner_id": str(owner), "project_id": str(binding.project_id)}
        or context["snapshot"]["snapshot_id"] != str(binding.snapshot_id)
        or request["project_id"] != str(binding.project_id)
        or request["request_id"] != str(run)
        or content_hash(context) != binding.context_sha256
    ):
        raise BridgeError("W1_INPUT_FENCE_MISMATCH")
    keys = [(e.episode_id, e.version) for e in binding.episode_versions]
    expected = {(e["episode_id"], e["version"]) for e in context["episodes"]}
    if (
        len(keys) != len(set(keys))
        or set(keys) != expected
        or len({e.episode_version_id for e in binding.episode_versions}) != len(keys)
    ):
        raise BridgeError("W1_EPISODE_VERSION_MAP_INVALID")
    if context["data_kind"] != "SYNTHETIC":
        raise BridgeError("W1_REAL_DATA_NOT_ENABLED")
    return context, request


class BoundBackend:
    def __init__(self, store, binding):
        self.store, self.binding = store, deepcopy(binding)

    def load_context(self, *, user_id, project_id):
        b = self.binding
        if user_id != str(b.owner_user_id) or project_id != str(b.project_id):
            return None
        context = self.store.load_context(b)
        if context is None:
            return None
        value, _ = validate_binding(b, context, b.owner_user_id, b.run_id)
        return value

    def authorize(self, **action):
        return self.store.authorize(self.binding, **action)


def publication_for(binding, result):
    result = C01ServiceOutput.model_validate(result).model_dump()
    if (
        result["request_id"] != str(binding.run_id)
        or result["project_id"] != str(binding.project_id)
        or result["snapshot"]["snapshot_id"] != str(binding.snapshot_id)
    ):
        raise BridgeError("W1_RESULT_SCOPE_MISMATCH")
    request = ServiceRequest.model_validate(binding.request).model_dump()
    selector = request["question"]
    if any(result["question_scope"][key] != value for key, value in selector.items()):
        raise BridgeError("W1_RESULT_QUESTION_MISMATCH")
    inference_modes = {
        result["inference"][stage].get("mode") for stage in ("extraction", "judgment")
    }
    synthetic_acceptance = (
        inference_modes == {"SIMULATED_LLM"}
        and os.environ.get("W4_RECOMMENDATION_SYNTHETIC_ACCEPTANCE") == "YES"
    )
    if inference_modes != {"LLM"} and not synthetic_acceptance:
        raise BridgeError("W1_ACTUAL_INFERENCE_REQUIRED")
    result_version = "w4-" + content_hash(
        {
            "run_id": str(binding.run_id),
            "question_version_id": str(binding.question_version_id),
            "context_sha256": binding.context_sha256,
            "result": result,
        }
    )
    mapping = {
        (e.episode_id, e.version): str(e.episode_version_id) for e in binding.episode_versions
    }
    statuses = {
        "DIRECT_MATCH": "DIRECT_MATCH",
        "PARTIAL_MATCH": "PARTIAL_RELEVANCE",
        "NEEDS_CONFIRMATION": "NEEDS_VERIFICATION",
    }
    candidates, seen = [], set()
    for number, candidate in enumerate(result["candidates"], 1):
        key = candidate["episode_id"], candidate["episode_version"]
        if key not in mapping or key in seen or candidate["rank"] != number:
            raise BridgeError("W1_RESULT_CANDIDATE_MISMATCH")
        seen.add(key)
        reasons = " / ".join(candidate["reasons"])
        questions = " / ".join(item["question"] for item in candidate["confirmation_items"])
        candidates.append(
            {
                "episode_version_id": mapping[key],
                "match_status": statuses[candidate["status"]],
                "short_reason": reasons or "원문 근거를 추가로 확인해야 합니다.",
                "strength_summary": reasons or None,
                "limitation_summary": questions
                or "합성 검증 결과이며 문항·정답·순위 정책은 팀 검토 전입니다.",
                "validation_status": "LIMITED",
                "result_version": result_version,
                "internal_rank": number,
            }
        )
    if len(candidates) > request["top_k"]:
        raise BridgeError("W1_RESULT_LIMIT_EXCEEDED")
    limitations = [
        "W4_SYNTHETIC_ACCEPTANCE_ONLY",
        "W4_POLICY_REVIEW_PENDING",
        "W4_MODEL_NOT_PRODUCTION_SELECTED",
    ]
    if synthetic_acceptance:
        limitations.append("W4_FIXED_SYNTHETIC_CLIENT_NO_NETWORK")
    return {
        "schema_version": "w4-w1-publication/0.1-draft",
        "run_id": str(binding.run_id),
        "result_origin": "ENGINE",
        "input_data_kind": "SYNTHETIC",
        "run_status": "LIMITED",
        "result_status": "LIMITED",
        "limited_analysis": True,
        "limitations": limitations,
        "result_version": result_version,
        "candidates": candidates,
        "full_result": result,
        "source_dependencies": deepcopy(result["company_context"]["dependencies"]),
    }


class W1ExecutionAdapter:
    """Implements RecommendationExecutionPort.execute(owner_user_id, run_id)."""

    def __init__(self, *, store, extraction_factory, judgment_factory, c01_consumer):
        self.store, self.extraction_factory, self.judgment_factory = (
            store,
            extraction_factory,
            judgment_factory,
        )
        self.c01_consumer = c01_consumer

    def execute(self, *, owner_user_id: UUID, run_id: UUID) -> None:
        if not isinstance(owner_user_id, UUID) or not isinstance(run_id, UUID):
            raise BridgeError("W1_TRUSTED_UUID_REQUIRED")
        binding = self.store.acquire(owner_user_id=owner_user_id, run_id=run_id)
        if binding is None:
            return
        binding = deepcopy(binding)
        try:
            backend = BoundBackend(self.store, binding)
            context, request = validate_binding(
                binding, self.store.load_context(binding), owner_user_id, run_id
            )
            result = execute_service(
                request,
                user_id=str(owner_user_id),
                backend=backend,
                extraction_factory=self.extraction_factory,
                judgment_factory=self.judgment_factory,
                c01_consumer=self.c01_consumer,
                c01_split=True,
            )
            if result["server_context_version"] != context["context_version"]:
                raise BridgeError("W1_RESULT_FENCE_MISMATCH")
            publication = publication_for(binding, result)
            # The host publish transaction must also fence its local Source epochs.
            self.c01_consumer.check_current(context["company_knowledge"])
            if not self.store.publish(binding, publication):
                self.c01_consumer.invalidate_scope(str(owner_user_id), str(binding.project_id))
                raise BridgeError("W1_PUBLICATION_FENCE_CHANGED")
        except Exception:
            self.store.fail(binding, "W4_EXECUTION_FAILED")
            raise
