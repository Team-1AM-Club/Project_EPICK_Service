"""Collection-aware orchestration for the adopted private commit gate."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from epick_engine.source_collection.commit_gate_contracts import (
    CommitGateAckProposal,
    CommitGateCommand,
    StagedResultProposal,
)
from epick_engine.source_collection.commit_gate_store import (
    CommitGateRejected,
    PrivateCommitStage,
    PrivateStagedOutbox,
    _hash,
    apply_commit_gate,
)
from epick_engine.source_collection.persistence import (
    CollectionRuntimeAttempt,
    SourceObservation,
    SourcePolicyDecision,
    SourceVersion,
    lock_source_policy_scope,
)
from epick_engine.source_collection.private_scope import (
    PrivateGateAuthority,
    PrivateScopeRejected,
)
from epick_engine.source_collection.w1_transport import W1WireContractError, _parse_wire


def _require_exact_integer_wire_types(raw: object, parsed: object) -> None:
    """Reject JSON numbers/bools that Pydantic can coerce into contract integers."""

    if type(parsed) is int:
        if type(raw) is not int:
            raise ValueError("wire integer requires an integer JSON value")
    elif isinstance(parsed, BaseModel) and isinstance(raw, dict):
        for name in type(parsed).model_fields:
            if name in raw:
                _require_exact_integer_wire_types(raw[name], getattr(parsed, name))
    elif isinstance(parsed, list | tuple) and isinstance(raw, list | tuple):
        for raw_item, parsed_item in zip(raw, parsed, strict=True):
            _require_exact_integer_wire_types(raw_item, parsed_item)


def _assert_gate_stage_binding(
    gate: CommitGateAckProposal,
    stage: PrivateCommitStage,
    private_gate_authority: PrivateGateAuthority,
) -> None:
    if (
        stage.owner_ref != gate.authenticated_owner_ref
        or stage.job_id != gate.job_id
        or stage.execution_fence != str(gate.execution_fence)
        or stage.owner_deletion_epoch != str(gate.owner_deletion_epoch)
        or stage.result_digest != gate.result_digest
        or stage.stage_kind != "COLLECTION"
    ):
        raise CommitGateRejected("collection commit-gate binding mismatch")
    if (
        stage.private_scope_kind != private_gate_authority.kind
        or stage.project_id != private_gate_authority.project_id
    ):
        raise PrivateScopeRejected("collection stage private scope does not match")


def _lock_candidate(
    session: Session,
    command_id: UUID,
) -> CollectionRuntimeAttempt | None:
    return session.scalar(
        select(CollectionRuntimeAttempt)
        .where(CollectionRuntimeAttempt.command_id == command_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    )


def _load_staged_proposal(
    session: Session,
    candidate: CollectionRuntimeAttempt,
    stage: PrivateCommitStage,
) -> StagedResultProposal:
    rows = session.scalars(
        select(PrivateStagedOutbox)
        .where(PrivateStagedOutbox.command_id == candidate.command_id)
        .with_for_update()
        .execution_options(populate_existing=True)
    ).all()
    if len(rows) != 1 or rows[0].payload is None:
        raise CommitGateRejected("collection staged proposal is unavailable")
    outbox = rows[0]
    try:
        proposal = _parse_wire(
            outbox.payload,
            StagedResultProposal,
            label="persisted W2 collection stage",
        )
        _require_exact_integer_wire_types(outbox.payload, proposal)
    except (TypeError, ValueError, W1WireContractError):
        raise CommitGateRejected("invalid collection staged proposal payload") from None
    raw = proposal.model_dump(mode="json")
    if (
        raw != outbox.payload
        or proposal.message_id != outbox.message_id
        or outbox.command_id != candidate.command_id
        or outbox.wire_hash != _hash(raw)
        or stage.result_payload != proposal.result.model_dump(mode="json")
        or stage.result_digest != proposal.result_digest
    ):
        raise CommitGateRejected("collection staged proposal binding mismatch")
    return proposal


def _assert_proposal_candidate_binding(
    proposal: StagedResultProposal,
    candidate: CollectionRuntimeAttempt,
) -> None:
    command = proposal.command
    result = proposal.result
    if (
        command.command_id != candidate.command_id
        or command.authenticated_owner_ref != candidate.owner_ref
        or command.job_id != candidate.job_id
        or command.source_id != candidate.source_id
        or command.company_id != candidate.company_id
        or result.command_id != candidate.command_id
        or result.job_id != candidate.job_id
        or result.source_id != candidate.source_id
        or result.policy_revision != candidate.effective_policy_revision
    ):
        raise CommitGateRejected("collection proposal candidate binding mismatch")


def _load_public_result(
    session: Session,
    proposal: StagedResultProposal,
    candidate: CollectionRuntimeAttempt,
) -> tuple[SourceObservation, SourceVersion | None, SourcePolicyDecision | None]:
    if candidate.observation_id is None:
        raise CommitGateRejected("collection candidate observation is unavailable")
    observation = session.get(
        SourceObservation,
        candidate.observation_id,
        populate_existing=True,
    )
    if observation is None or observation.source_id != candidate.source_id:
        raise CommitGateRejected("collection candidate observation binding mismatch")
    if observation.source_version_id != candidate.source_version_id:
        raise CommitGateRejected("collection candidate version binding mismatch")

    observation_policy: SourcePolicyDecision | None = None
    if observation.policy_decision_id is not None:
        observation_policy = session.get(
            SourcePolicyDecision,
            observation.policy_decision_id,
            populate_existing=True,
        )
        if (
            observation_policy is None
            or observation_policy.source_id != candidate.source_id
            or observation_policy.revision != candidate.effective_policy_revision
        ):
            raise CommitGateRejected("collection observation policy binding mismatch")

    references = proposal.result.successful_source_refs
    if not references:
        if candidate.source_version_id is not None:
            raise CommitGateRejected("collection failure candidate has a source version")
        return observation, None, observation_policy
    if len(references) != 1 or candidate.source_version_id is None:
        raise CommitGateRejected("collection successful version binding mismatch")
    reference = references[0]
    if (
        reference.source_id != candidate.source_id
        or reference.source_version_id != candidate.source_version_id
    ):
        raise CommitGateRejected("collection successful version binding mismatch")
    version = session.get(
        SourceVersion,
        candidate.source_version_id,
        populate_existing=True,
    )
    if (
        version is None
        or version.source_id != candidate.source_id
        or version.company_id != candidate.company_id
    ):
        raise CommitGateRejected("collection source version binding mismatch")
    version_policy = session.get(
        SourcePolicyDecision,
        version.policy_decision_id,
        populate_existing=True,
    )
    if (
        version_policy is None
        or version_policy.source_id != candidate.source_id
        or version_policy.revision != candidate.effective_policy_revision
    ):
        raise CommitGateRejected("collection source version policy binding mismatch")
    return observation, version, version_policy


def _policy_allows_current_version(policy: SourcePolicyDecision) -> bool:
    return (
        policy.official_status == "verified"
        and policy.access_class == "public"
        and policy.collection_permission == "allowed"
        and policy.excerpt_storage_permission == "allowed"
    )


def apply_collection_candidate_transition(
    session: Session,
    gate: CommitGateAckProposal,
    stage: PrivateCommitStage,
    *,
    private_gate_authority: PrivateGateAuthority,
) -> None:
    """Apply the public half of a collection gate while its command lock is held."""

    _assert_gate_stage_binding(gate, stage, private_gate_authority)
    candidate = _lock_candidate(session, gate.command_id)
    if candidate is not None and (
        candidate.private_scope_kind != stage.private_scope_kind
        or candidate.project_id != stage.project_id
    ):
        raise PrivateScopeRejected("collection candidate private scope does not match stage")
    if gate.action == "PREPARE":
        return
    if gate.action == "FINALIZE" and stage.state == "PURGED":
        return

    if gate.action in {"ABORT", "PURGE"}:
        if candidate is None:
            return
        candidate.state = "INVALIDATED"
        candidate.claim_token = None
        candidate.claim_expires_at = None
        candidate.observation_id = None
        candidate.source_version_id = None
        candidate.updated_at = gate.occurred_at
        return

    if gate.action != "FINALIZE":
        raise CommitGateRejected("unsupported collection commit-gate action")
    if candidate is None:
        raise CommitGateRejected("collection candidate is unavailable")
    if candidate.state == "FINALIZED":
        return
    if candidate.state != "PERSISTED":
        raise CommitGateRejected("collection candidate is not persisted")

    source = lock_source_policy_scope(session, candidate.source_id)
    if source.company_id != candidate.company_id:
        raise CommitGateRejected("collection candidate source company binding mismatch")
    if source.pointer_update_mode != "FINALIZE_GATE":
        raise CommitGateRejected("collection source is not in FINALIZE_GATE mode")

    proposal = _load_staged_proposal(session, candidate, stage)
    _assert_proposal_candidate_binding(proposal, candidate)

    observation, version, version_policy = _load_public_result(
        session,
        proposal,
        candidate,
    )
    current_policy_revision = session.scalar(
        select(func.max(SourcePolicyDecision.revision)).where(
            SourcePolicyDecision.source_id == candidate.source_id
        )
    )

    candidate.state = "FINALIZED"
    candidate.claim_token = None
    candidate.claim_expires_at = None
    candidate.updated_at = gate.occurred_at

    if (
        current_policy_revision != candidate.effective_policy_revision
        or candidate.observation_order <= source.last_promoted_observation_order
    ):
        return

    source.latest_observation_id = observation.observation_id
    source.last_promoted_observation_order = candidate.observation_order
    if (
        version is not None
        and version_policy is not None
        and _policy_allows_current_version(version_policy)
    ):
        source.current_source_version_id = version.source_version_id
        if source.first_collected_at is None:
            source.first_collected_at = observation.observed_at
        source.last_collected_at = observation.observed_at


def apply_collection_commit_gate(
    session: Session,
    gate_command: CommitGateCommand,
    *,
    ack_message_id: UUID,
    occurred_at: datetime,
    private_gate_authority: PrivateGateAuthority | None = None,
) -> CommitGateAckProposal:
    """Apply private gate state and collection promotion as one savepoint."""

    with session.begin_nested():
        ack = apply_commit_gate(
            session,
            gate_command,
            ack_message_id=ack_message_id,
            occurred_at=occurred_at,
            missing_stage_kind="COLLECTION",
            private_gate_authority=private_gate_authority,
        )
        assert private_gate_authority is not None
        stage = session.get(
            PrivateCommitStage,
            ack.command_id,
            populate_existing=True,
        )
        if stage is None or stage.stage_kind == "PRIVATE_ONLY":
            return ack
        apply_collection_candidate_transition(
            session,
            ack,
            stage,
            private_gate_authority=private_gate_authority,
        )
        session.flush()
        return ack
