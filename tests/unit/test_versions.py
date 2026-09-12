import pytest

from w3_knowledge.evidence import evidence_checks, make_evidence
from w3_knowledge.models import SourceInput
from tests.support.factories import request, source_with_text


def test_duplicate_source_version_is_rejected() -> None:
    source = source_with_text()
    with pytest.raises(ValueError, match="중복"):
        request().model_validate(
            {
                "sources": [source.model_dump(), source.model_dump()],
                "context": request().context.model_dump(),
            }
        )


def test_source_input_keeps_version_as_part_of_identity() -> None:
    source = source_with_text()
    changed = SourceInput(
        source_ref=source.source_ref.model_copy(
            update={"source_version_id": "synthetic-source-v2"}
        ),
        artifacts=(
            source.artifacts[0].model_copy(
                update={
                    "source_ref": source.source_ref.model_copy(
                        update={"source_version_id": "synthetic-source-v2"}
                    )
                }
            ),
        ),
    )
    assert changed.source_ref.source_version_id != source.source_ref.source_version_id


def test_as204_evidence_from_a_different_source_version_fails_validation() -> None:
    artifact = source_with_text().artifacts[0]
    evidence = make_evidence(
        evidence_id="evidence-a", artifact=artifact, start_offset=0, end_offset=2
    ).model_copy(
        update={
            "source_ref": artifact.source_ref.model_copy(
                update={"source_version_id": "synthetic-source-v2"}
            )
        }
    )

    checks = evidence_checks(evidence, artifact)

    assert len(checks) == 1
    assert checks[0].code == "SOURCE_VERSION_MISMATCH"
    assert checks[0].status.value == "FAIL"
