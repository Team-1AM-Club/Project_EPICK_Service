from w3_knowledge.models import (
    CheckStatus,
    ConditionNode,
    ConditionOperator,
    Necessity,
    RequirementCandidate,
    SourceInput,
)
from w3_knowledge.service import W3Ports, structure
from w3_knowledge.skills import normalize_skill_mentions
from tests.support.fakes import (
    FakeAdditionalVerification,
    FakeExtraction,
    FakePolicy,
    FakeRetainedText,
    FakeSemanticValidation,
)
from tests.support.factories import request, requirement_candidate, source_with_text


def _ports(
    *,
    requirements: tuple[RequirementCandidate, ...],
    semantic_status: CheckStatus = CheckStatus.PASS,
) -> W3Ports:
    return W3Ports(
        FakePolicy(),
        FakeRetainedText(),
        FakeExtraction(requirements=requirements),
        FakeSemanticValidation(semantic_status),
        FakeAdditionalVerification(),
    )


def test_as301_requirement_preserves_or_same_experience_and_original_terms() -> None:
    candidate = requirement_candidate()
    root = candidate.condition_nodes[0].model_copy(update={"same_experience_required": True})
    candidate = candidate.model_copy(
        update={"condition_nodes": (root, *candidate.condition_nodes[1:])}
    )

    response = structure(request(), _ports(requirements=(candidate,)))

    requirement = response.bundle.requirements[0]
    assert requirement.necessity == Necessity.REQUIRED
    assert requirement.condition_nodes[0].operator == ConditionOperator.OR
    assert requirement.condition_nodes[0].same_experience_required is True
    assert requirement.technical_terms == ("Python", "JavaScript")


def test_as302_preserves_preferred_and_general_without_promoting_them_to_required() -> None:
    base = requirement_candidate()
    preferred = base.model_copy(
        update={
            "candidate_id": "requirement-preferred",
            "original_text": "Docker experience preferred",
            "necessity": Necessity.PREFERRED,
        }
    )
    general = base.model_copy(
        update={
            "candidate_id": "requirement-general",
            "original_text": "Team collaboration",
            "necessity": Necessity.GENERAL,
        }
    )

    response = structure(request(), _ports(requirements=(preferred, general)))

    assert [item.necessity for item in response.bundle.requirements] == [
        Necessity.PREFERRED,
        Necessity.GENERAL,
    ]


def test_as303_preserves_distinct_technical_terms_and_only_links_approved_aliases() -> None:
    candidate = requirement_candidate().model_copy(
        update={
            "technical_terms": ("AWS", "Java", "JavaScript"),
            "original_text": "AWS, Java, and JavaScript",
        }
    )

    response = structure(request(), _ports(requirements=(candidate,)))
    mentions = normalize_skill_mentions(
        response.bundle.requirements[0].technical_terms,
        {"aws": ("Amazon Web Services", "approved abbreviation")},
    )

    assert response.bundle.requirements[0].technical_terms == ("AWS", "Java", "JavaScript")
    assert mentions[0].canonical == "Amazon Web Services"
    assert mentions[1].canonical is None
    assert mentions[2].canonical is None
    assert mentions[1].original != mentions[2].original


def test_as304_preserves_posting_role_and_time_scopes() -> None:
    candidate = requirement_candidate().model_copy(
        update={
            "company_scope": "Synthetic company",
            "posting_scope": "posting-2026-01",
            "role_scope": "platform engineer",
            "time_scope": "2026-Q1",
        }
    )

    response = structure(request(), _ports(requirements=(candidate,)))
    requirement = response.bundle.requirements[0]

    assert (
        requirement.company_scope,
        requirement.posting_scope,
        requirement.role_scope,
        requirement.time_scope,
    ) == ("Synthetic company", "posting-2026-01", "platform engineer", "2026-Q1")


def test_as305_keeps_body_requirement_when_required_attachment_is_unparsed() -> None:
    source = source_with_text("Python experience required")
    missing_attachment = source.artifacts[0].model_copy(
        update={
            "artifact_id": "detail-attachment",
            "text": None,
            "parsing_status": "UNPARSED",
            "required_for_scope": True,
            "expected_integrity": None,
        }
    )
    partial_source = SourceInput(
        source_ref=source.source_ref,
        artifacts=(source.artifacts[0], missing_attachment),
    )

    response = structure(request(partial_source), _ports(requirements=(requirement_candidate(),)))

    assert len(response.bundle.requirements) == 1
    assert response.bundle.requirements[0].technical_terms == ("Python", "JavaScript")
    assert any(item.code == "REQUIRED_ARTIFACT_UNPARSED" for item in response.bundle.limitations)
    assert response.status.value == "LIMITED"


def test_as306_preserves_comparison_exception_and_required_status() -> None:
    candidate = RequirementCandidate(
        candidate_id="requirement-duration",
        original_text="Operations experience for at least 3 months required; education practice excluded.",
        evidence_ids=("evidence-synthetic-artifact",),
        necessity=Necessity.REQUIRED,
        root_node_id="condition-root",
        condition_nodes=(
            ConditionNode(
                node_id="condition-root",
                operator=ConditionOperator.AND,
                text="duration and exclusion",
                children=("condition-duration", "condition-exception"),
            ),
            ConditionNode(
                node_id="condition-duration",
                operator=ConditionOperator.LEAF,
                text="operations experience",
                comparison_basis="GTE 3 months",
            ),
            ConditionNode(
                node_id="condition-exception",
                operator=ConditionOperator.LEAF,
                text="education practice",
                exception_text="excluded",
            ),
        ),
        technical_terms=(),
    )

    response = structure(request(), _ports(requirements=(candidate,)))
    requirement = response.bundle.requirements[0]

    assert requirement.necessity == Necessity.REQUIRED
    assert requirement.condition_nodes[1].comparison_basis == "GTE 3 months"
    assert requirement.condition_nodes[2].exception_text == "excluded"


def test_as307_context_limited_requirement_stays_restricted_not_absent() -> None:
    candidate = requirement_candidate().model_copy(
        update={
            "original_text": "Selected requirement excerpt",
            "company_scope": None,
            "posting_scope": None,
            "role_scope": None,
            "time_scope": None,
        }
    )

    response = structure(
        request(),
        _ports(requirements=(candidate,), semantic_status=CheckStatus.PENDING),
    )

    requirement = response.bundle.requirements[0]
    assert response.bundle.requirement_presence.value == "FOUND"
    assert requirement.verification_status.value == "PENDING"
    assert requirement.usage_status.value == "RESTRICTED"
    assert requirement.company_scope is None
    assert requirement.role_scope is None


def test_invalid_requirement_tree_is_not_usable() -> None:
    candidate = requirement_candidate().model_copy(update={"root_node_id": "missing-root"})

    response = structure(request(), _ports(requirements=(candidate,)))

    assert response.bundle.requirements[0].usage_status.value == "BLOCKED"
