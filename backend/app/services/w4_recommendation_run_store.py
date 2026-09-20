from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Literal
from uuid import UUID, uuid4

from jsonschema import Draft202012Validator, FormatChecker
from sqlalchemy.orm import Session

from app.db.session import set_local_owner_context
from app.models.recommendation_execution import (
    RecommendationExecutionBinding,
    RecommendationPublication,
    RecommendationSourceDependency,
)
from app.models.recommendations import RecommendationCandidate
from app.repo.recommendation_execution import RecommendationExecutionRepository

CONTRACT_VERSION = "w1-w4-recommendation/1.0"
ALLOWED_ACTIONS = frozenset({"PROCESS", "SEND_TO_PROVIDER", "RETURN_TO_CALLER"})
SAFE_FAILURE_CODES = frozenset(
    {
        "W4_EXECUTION_FAILED",
        "W4_CONTEXT_NOT_CURRENT",
        "W4_POLICY_DENIED",
        "W4_SCHEMA_INVALID",
        "W4_TIMEOUT",
    }
)


class W4RecommendationStoreError(RuntimeError):
    def __init__(self, code: str, *, retryable: bool = False) -> None:
        super().__init__(code)
        self.code = code
        self.retryable = retryable


def canonical_sha256(value: object) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@lru_cache
def _publication_validator() -> Draft202012Validator:
    relative = Path("contracts") / "w4" / "v1" / "publication.schema.json"
    candidates = (
        Path(__file__).resolve().parents[2] / relative,
        Path.cwd() / relative,
        Path("/app") / relative,
    )
    path = next((candidate for candidate in candidates if candidate.is_file()), None)
    if path is None:
        raise RuntimeError("W4 publication schema is not installed")
    return Draft202012Validator(
        json.loads(path.read_text(encoding="utf-8")),
        format_checker=FormatChecker(),
    )


class W4RecommendationRunStoreService:
    """Transaction-scoped implementation of W4's RunStore protocol."""

    def __init__(self, session: Session, *, lease_seconds: int) -> None:
        self.session = session
        self.repository = RecommendationExecutionRepository(session)
        self.lease_seconds = lease_seconds

    def acquire(self, *, owner_user_id: UUID, run_id: UUID) -> dict[str, object] | None:
        # The worker login still traverses the owner-scoped RLS policies on the
        # canonical user/run tables. Scope only this transaction to the owner
        # supplied by the opaque dispatch reference before looking up any
        # owner data; the exact run/binding checks below remain authoritative.
        set_local_owner_context(self.session, owner_user_id)
        owner = self.repository.lock_owner(owner_user_id=owner_user_id)
        run = self.repository.lock_run(owner_user_id=owner_user_id, run_id=run_id)
        binding = self.repository.lock_binding(owner_user_id=owner_user_id, run_id=run_id)
        if owner is None or run is None or binding is None:
            raise W4RecommendationStoreError("W4_RUN_NOT_FOUND")
        if binding.execution_status == "PUBLISHED" and run.status == "LIMITED":
            return None
        now = datetime.now(UTC)
        live_lease = (
            binding.execution_status == "RUNNING"
            and binding.lease_expires_at is not None
            and binding.lease_expires_at > now
        )
        if live_lease:
            raise W4RecommendationStoreError("W4_RUN_BUSY", retryable=True)
        if binding.execution_status not in {"PENDING", "RUNNING"}:
            raise W4RecommendationStoreError("W4_RUN_NOT_ACQUIRABLE")
        if (
            owner.account_status != "ACTIVE"
            or owner.deleted_at is not None
            or owner.deletion_epoch != binding.owner_deletion_epoch
            or run.result_origin != "ENGINE"
            or run.result_status != "PENDING"
            or run.status not in {"PENDING", "RUNNING"}
        ):
            raise W4RecommendationStoreError("W4_RUN_NOT_FOUND")
        binding.execution_status = "RUNNING"
        binding.lease_token = uuid4()
        binding.lease_expires_at = now + timedelta(seconds=self.lease_seconds)
        binding.attempt_no += 1
        binding.acquired_at = now
        binding.failure_code = None
        binding.failed_at = None
        run.status = "RUNNING"
        self.session.flush()
        return self._binding_document(binding)

    def load_context(self, *, run_id: UUID, lease_token: UUID) -> dict[str, object] | None:
        binding = self._current_binding(run_id=run_id, lease_token=lease_token)
        if binding is None:
            return None
        return dict(binding.context_body)

    def authorize(
        self,
        *,
        run_id: UUID,
        lease_token: UUID,
        action: Literal["PROCESS", "SEND_TO_PROVIDER", "RETURN_TO_CALLER"],
        owner_user_id: UUID,
        project_id: UUID,
        context_version: str,
    ) -> bool:
        if action not in ALLOWED_ACTIONS:
            return False
        binding = self._current_binding(run_id=run_id, lease_token=lease_token)
        if (
            binding is None
            or binding.owner_user_id != owner_user_id
            or binding.project_id != project_id
            or binding.context_body.get("context_version") != context_version
        ):
            return False
        set_local_owner_context(self.session, binding.owner_user_id)
        owner = self.repository.lock_owner(owner_user_id=binding.owner_user_id)
        run = self.repository.lock_run(
            owner_user_id=binding.owner_user_id,
            run_id=binding.run_id,
        )
        return bool(
            owner is not None
            and run is not None
            and self.repository.run_inputs_are_current(binding=binding, owner=owner, run=run)
        )

    def publish(
        self,
        *,
        run_id: UUID,
        lease_token: UUID,
        publication: dict[str, object],
    ) -> bool:
        if list(_publication_validator().iter_errors(publication)):
            raise W4RecommendationStoreError("W4_SCHEMA_INVALID")
        binding = self.repository.get_binding_by_lease(
            run_id=run_id,
            lease_token=lease_token,
            for_update=True,
        )
        if binding is None:
            return False
        set_local_owner_context(self.session, binding.owner_user_id)
        existing = self.repository.get_publication(run_id=run_id)
        if existing is not None:
            return (
                existing.binding_id == binding.id
                and existing.lease_token == lease_token
                and existing.result_version == publication["result_version"]
                and existing.content_sha256 == canonical_sha256(publication)
            )
        owner = self.repository.lock_owner(owner_user_id=binding.owner_user_id)
        run = self.repository.lock_run(
            owner_user_id=binding.owner_user_id,
            run_id=binding.run_id,
        )
        if (
            owner is None
            or run is None
            or not self._lease_is_live(binding)
            or canonical_sha256(binding.context_body) != binding.context_sha256
            or not self.repository.run_inputs_are_current(binding=binding, owner=owner, run=run)
            or publication.get("run_id") != str(run_id)
        ):
            return False
        dependencies = publication["source_dependencies"]
        assert isinstance(dependencies, list)
        normalized_dependencies = [self._normalize_dependency(item) for item in dependencies]
        if not all(
            self.repository.source_is_current(
                source_id=item["source_id"],
                source_version_id=item["source_version_id"],
            )
            for item in normalized_dependencies
        ):
            return False

        candidates = publication["candidates"]
        assert isinstance(candidates, list)
        episode_ids = {
            item.episode_version_id for item in self.repository.list_episodes(binding_id=binding.id)
        }
        if len(candidates) > run.requested_candidate_limit:
            raise W4RecommendationStoreError("W4_RESULT_LIMIT_EXCEEDED")
        seen_episode_ids: set[UUID] = set()
        for number, item in enumerate(candidates, start=1):
            episode_version_id = UUID(str(item["episode_version_id"]))
            if (
                episode_version_id not in episode_ids
                or episode_version_id in seen_episode_ids
                or item["internal_rank"] != number
                or item["result_version"] != publication["result_version"]
            ):
                raise W4RecommendationStoreError("W4_RESULT_CANDIDATE_MISMATCH")
            seen_episode_ids.add(episode_version_id)
            self.repository.add_candidate(
                RecommendationCandidate(
                    owner_user_id=binding.owner_user_id,
                    run_id=binding.run_id,
                    question_id=binding.question_id,
                    snapshot_id=binding.snapshot_id,
                    candidate_no=number,
                    episode_version_id=episode_version_id,
                    match_status=str(item["match_status"]),
                    short_reason=str(item["short_reason"]),
                    strength_summary=item.get("strength_summary"),
                    limitation_summary=item.get("limitation_summary"),
                    internal_rank=number,
                    validation_status="LIMITED",
                    result_version=str(publication["result_version"]),
                )
            )

        now = datetime.now(UTC)
        stored = RecommendationPublication(
            binding_id=binding.id,
            run_id=binding.run_id,
            owner_user_id=binding.owner_user_id,
            lease_token=lease_token,
            result_version=str(publication["result_version"]),
            schema_version=str(publication["schema_version"]),
            engine_source_revision=binding.engine_source_revision,
            input_data_kind="SYNTHETIC",
            processing_status=str(publication["full_result"].get("processing_status", "UNKNOWN")),
            limited_analysis=True,
            limitations=list(publication["limitations"]),
            full_result=dict(publication["full_result"]),
            content_sha256=canonical_sha256(publication),
        )
        self.repository.add_publication(stored)
        self.session.flush()
        for dependency in normalized_dependencies:
            self.repository.add_dependency(
                RecommendationSourceDependency(
                    publication_id=stored.id,
                    source_id=str(dependency["source_id"]),
                    source_version_id=str(dependency["source_version_id"]),
                    extraction_revision_id=dependency["extraction_revision_id"],
                    representation=dependency["representation"],
                    normalization_version=dependency["normalization_version"],
                    knowledge_generation=dependency["knowledge_generation"],
                    restriction_revision=dependency["restriction_revision"],
                    dependency_digest=canonical_sha256(dependency["raw"]),
                )
            )
        binding.execution_status = "PUBLISHED"
        binding.published_at = now
        binding.lease_expires_at = now
        run.status = "LIMITED"
        run.result_status = "LIMITED"
        run.result_origin = "ENGINE"
        run.limited_analysis = True
        run.limitations = list(publication["limitations"])
        run.completed_at = now
        self.session.flush()
        return True

    def fail(self, *, run_id: UUID, lease_token: UUID, code: str) -> bool:
        if code not in SAFE_FAILURE_CODES:
            code = "W4_EXECUTION_FAILED"
        binding = self.repository.get_binding_by_lease(
            run_id=run_id,
            lease_token=lease_token,
            for_update=True,
        )
        if binding is None or binding.execution_status != "RUNNING":
            return False
        set_local_owner_context(self.session, binding.owner_user_id)
        run = self.repository.lock_run(
            owner_user_id=binding.owner_user_id,
            run_id=binding.run_id,
        )
        if run is None or run.status in {"CANCELLED", "SUCCEEDED", "LIMITED"}:
            return False
        now = datetime.now(UTC)
        binding.execution_status = "FAILED"
        binding.failure_code = code
        binding.failed_at = now
        binding.lease_expires_at = now
        run.status = "FAILED"
        run.result_status = "FAILED"
        run.completed_at = now
        self.session.flush()
        return True

    def _current_binding(
        self, *, run_id: UUID, lease_token: UUID
    ) -> RecommendationExecutionBinding | None:
        binding = self.repository.get_binding_by_lease(
            run_id=run_id,
            lease_token=lease_token,
        )
        if binding is None or not self._lease_is_live(binding):
            return None
        return binding

    @staticmethod
    def _lease_is_live(binding: RecommendationExecutionBinding) -> bool:
        return bool(
            binding.execution_status == "RUNNING"
            and binding.lease_token is not None
            and binding.lease_expires_at is not None
            and binding.lease_expires_at > datetime.now(UTC)
        )

    def _binding_document(self, binding: RecommendationExecutionBinding) -> dict[str, object]:
        episodes = self.repository.list_episodes(binding_id=binding.id)
        return {
            "owner_user_id": str(binding.owner_user_id),
            "run_id": str(binding.run_id),
            "project_id": str(binding.project_id),
            "question_id": str(binding.question_id),
            "question_version_id": str(binding.question_version_id),
            "snapshot_id": str(binding.snapshot_id),
            "lease_token": str(binding.lease_token),
            "lease_expires_at": binding.lease_expires_at.isoformat().replace("+00:00", "Z"),
            "context_sha256": binding.context_sha256.removeprefix("sha256:"),
            "request": dict(binding.request_body),
            "episode_versions": [
                {
                    "episode_id": str(item.episode_id),
                    "version": item.episode_version,
                    "episode_version_id": str(item.episode_version_id),
                }
                for item in episodes
            ],
        }

    @staticmethod
    def _normalize_dependency(item: object) -> dict[str, object]:
        if not isinstance(item, dict):
            raise W4RecommendationStoreError("W4_SOURCE_DEPENDENCY_INVALID")
        index = item.get("index_key") if isinstance(item.get("index_key"), dict) else item
        try:
            source_id = UUID(str(item["source_id"]))
            source_version_id = UUID(str(index["source_version_id"]))
        except (KeyError, TypeError, ValueError) as error:
            raise W4RecommendationStoreError("W4_SOURCE_DEPENDENCY_INVALID") from error
        return {
            "source_id": source_id,
            "source_version_id": source_version_id,
            "extraction_revision_id": _optional_text(index.get("extraction_revision_id")),
            "representation": _optional_text(index.get("representation")),
            "normalization_version": _optional_text(index.get("normalization_version")),
            "knowledge_generation": item.get("knowledge_generation"),
            "restriction_revision": _optional_text(item.get("restriction_revision")),
            "raw": item,
        }


def _optional_text(value: object) -> str | None:
    return value if isinstance(value, str) and value else None
