from __future__ import annotations

from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import Mock
from uuid import uuid4

from app.models.recommendation_execution import RecommendationExecutionBinding
from app.models.recommendations import RecommendationRun
from app.services.w4_recommendation_run_store import W4RecommendationRunStoreService


def _fixture():
    owner_id = uuid4()
    run = RecommendationRun(
        id=uuid4(),
        owner_user_id=owner_id,
        project_id=uuid4(),
        question_id=uuid4(),
        question_version_id=uuid4(),
        snapshot_id=uuid4(),
        analysis_policy_version="w4-test-v1",
        result_origin="ENGINE",
        result_status="PENDING",
        status="PENDING",
        requested_candidate_limit=3,
        limitations=[],
    )
    context = {"context_version": f"w1-engine-{run.id}"}
    binding = RecommendationExecutionBinding(
        id=uuid4(),
        run_id=run.id,
        owner_user_id=owner_id,
        project_id=run.project_id,
        question_id=run.question_id,
        question_version_id=run.question_version_id,
        snapshot_id=run.snapshot_id,
        execution_status="PENDING",
        attempt_no=0,
        owner_deletion_epoch=2,
        context_sha256="sha256:" + "a" * 64,
        contract_version="w1-w4-recommendation/1.0",
        engine_source_revision="df41433218918e4167784243dc9b88e5a858278d",
        request_body={"schema_version": "w4-service-input/0.1"},
        context_body=context,
    )
    owner = SimpleNamespace(
        id=owner_id,
        account_status="ACTIVE",
        deleted_at=None,
        deletion_epoch=2,
    )
    return owner, run, binding


def _service(owner, run, binding):
    service = W4RecommendationRunStoreService(Mock(), lease_seconds=300)
    repository = Mock()
    repository.lock_owner.return_value = owner
    repository.lock_run.return_value = run
    repository.lock_binding.return_value = binding
    repository.list_episodes.return_value = []
    repository.run_inputs_are_current.return_value = True
    service.repository = repository
    return service, repository


def test_acquire_creates_one_bounded_lease_and_returns_opaque_binding() -> None:
    owner, run, binding = _fixture()
    service, _ = _service(owner, run, binding)

    document = service.acquire(owner_user_id=owner.id, run_id=run.id)

    assert document["run_id"] == str(run.id)
    assert document["context_sha256"] == "a" * 64
    assert binding.execution_status == "RUNNING"
    assert binding.attempt_no == 1
    assert binding.lease_expires_at > datetime.now(UTC)
    assert run.status == "RUNNING"


def test_authorize_requires_exact_owner_project_context_and_current_fence() -> None:
    owner, run, binding = _fixture()
    service, repository = _service(owner, run, binding)
    service.acquire(owner_user_id=owner.id, run_id=run.id)
    repository.get_binding_by_lease.return_value = binding

    assert service.authorize(
        run_id=run.id,
        lease_token=binding.lease_token,
        action="PROCESS",
        owner_user_id=owner.id,
        project_id=run.project_id,
        context_version=binding.context_body["context_version"],
    )
    assert not service.authorize(
        run_id=run.id,
        lease_token=binding.lease_token,
        action="PROCESS",
        owner_user_id=uuid4(),
        project_id=run.project_id,
        context_version=binding.context_body["context_version"],
    )


def test_fail_is_fenced_by_exact_running_lease_and_never_revives_cancelled_run() -> None:
    owner, run, binding = _fixture()
    service, repository = _service(owner, run, binding)
    binding.execution_status = "RUNNING"
    binding.lease_token = uuid4()
    binding.lease_expires_at = datetime.max.replace(tzinfo=UTC)
    run.status = "CANCELLED"
    repository.get_binding_by_lease.return_value = binding

    assert not service.fail(
        run_id=run.id,
        lease_token=binding.lease_token,
        code="W4_EXECUTION_FAILED",
    )
    assert binding.execution_status == "RUNNING"
    assert run.status == "CANCELLED"
