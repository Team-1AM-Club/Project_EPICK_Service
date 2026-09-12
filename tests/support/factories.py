from __future__ import annotations

from w3_knowledge.evidence import digest_text
from w3_knowledge.models import (
    Artifact,
    Audience,
    ConditionNode,
    ConditionOperator,
    ExecutionContext,
    IntegrityAssertion,
    NativeLocator,
    Necessity,
    Purpose,
    RequirementCandidate,
    SourceInput,
    SourceRef,
    StructureRequest,
    EvaluationMode,
)


def source_with_text(text: str = "Synthetic source text.") -> SourceInput:
    ref = SourceRef(
        source_id="synthetic-source",
        source_version_id="synthetic-source-v1",
        source_kind="synthetic",
    )
    artifact = Artifact(
        artifact_id="synthetic-artifact",
        source_ref=ref,
        text=text,
        native_locator=NativeLocator(
            locator_type="json_pointer", value="/synthetic/0", reproducible=True
        ),
        expected_integrity=IntegrityAssertion(digest=digest_text(text)),
        collection_status="SUCCESS",
        parsing_status="PARSED",
        access_status="AVAILABLE",
        retention_status="RETAINED",
    )
    return SourceInput(source_ref=ref, artifacts=(artifact,))


def request(
    source: SourceInput | None = None, *, mode: EvaluationMode = EvaluationMode.SYNTHETIC
) -> StructureRequest:
    return StructureRequest(
        sources=(source or source_with_text(),),
        context=ExecutionContext(
            actor_id="synthetic-user",
            audience=Audience.W2_REVIEW,
            purpose=Purpose.SYNTHETIC_ACCEPTANCE,
            mode=mode,
            request_id="synthetic-request",
        ),
    )


def requirement_candidate() -> RequirementCandidate:
    return RequirementCandidate(
        candidate_id="requirement-a",
        original_text="Python OR JavaScript",
        evidence_ids=("evidence-synthetic-artifact",),
        necessity=Necessity.REQUIRED,
        root_node_id="condition-root",
        condition_nodes=(
            ConditionNode(
                node_id="condition-root",
                operator=ConditionOperator.OR,
                text="Python OR JavaScript",
                children=("condition-python", "condition-javascript"),
            ),
            ConditionNode(
                node_id="condition-python", operator=ConditionOperator.LEAF, text="Python"
            ),
            ConditionNode(
                node_id="condition-javascript", operator=ConditionOperator.LEAF, text="JavaScript"
            ),
        ),
        technical_terms=("Python", "JavaScript"),
    )
