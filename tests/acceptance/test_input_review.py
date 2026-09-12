from w3_knowledge.models import SourceInput
from w3_knowledge.review import review_source
from w3_knowledge.service import W3Ports, structure
from tests.support.fakes import (
    FakeAdditionalVerification,
    FakeExtraction,
    FakePolicy,
    FakeRetainedText,
    FakeSemanticValidation,
)
from tests.support.factories import request, source_with_text


def _ports() -> W3Ports:
    return W3Ports(
        FakePolicy(),
        FakeRetainedText(),
        FakeExtraction(),
        FakeSemanticValidation(),
        FakeAdditionalVerification(),
    )


def test_as101_normal_source_review_is_completed() -> None:
    result = structure(request(), _ports())
    assert result.status.value == "COMPLETED"
    assert result.bundle.source_reviews[0].process_status.value == "COMPLETED"


def test_as103_summary_or_missing_raw_text_is_limited() -> None:
    source = source_with_text()
    artifact = source.artifacts[0].model_copy(update={"text": None, "expected_integrity": None})
    result = structure(
        request(SourceInput(source_ref=source.source_ref, artifacts=(artifact,))), _ports()
    )
    assert result.status.value == "LIMITED"
    assert result.bundle.claims == ()
    assert any(item.code == "RAW_TEXT_MISSING" for item in result.bundle.limitations)


def test_as105_required_unparsed_attachment_is_not_absence() -> None:
    source = source_with_text()
    artifact = source.artifacts[0].model_copy(
        update={"required_for_scope": True, "parsing_status": "UNPARSED"}
    )
    review = review_source(SourceInput(source_ref=source.source_ref, artifacts=(artifact,)))
    assert any(item.code == "REQUIRED_ARTIFACT_UNPARSED" for item in review.limitations)


def test_as108_one_limited_source_does_not_hide_other_source() -> None:
    good = source_with_text()
    raw_missing = good.artifacts[0].model_copy(
        update={"artifact_id": "other-artifact", "text": None}
    )
    other = SourceInput(
        source_ref=good.source_ref.model_copy(
            update={"source_id": "other-source", "source_version_id": "other-source-v1"}
        ),
        artifacts=(
            raw_missing.model_copy(
                update={
                    "source_ref": good.source_ref.model_copy(
                        update={"source_id": "other-source", "source_version_id": "other-source-v1"}
                    )
                }
            ),
        ),
    )
    result = structure(request(good).model_copy(update={"sources": (good, other)}), _ports())
    assert len(result.bundle.source_reviews) == 2
    assert any(review.process_status.value == "LIMITED" for review in result.bundle.source_reviews)
