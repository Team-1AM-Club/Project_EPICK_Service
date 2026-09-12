import json

import pytest

from w3_knowledge.adapters.observed_package import load_observed_package


def _package(tmp_path, *, with_excerpt: bool = True):
    observed = tmp_path / "observed"
    observed.mkdir()
    (observed / "source-envelopes.json").write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source": {"id": "pm-source"},
                        "source_version": {"id": "pm-source-v1"},
                        "acquisition_status": "success",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    span = (
        [{"evidence_id": "synthetic-evidence", "text": "Synthetic attachment excerpt."}]
        if with_excerpt
        else []
    )
    (observed / "attachment-extraction.json").write_text(
        json.dumps(
            {
                "attachments": [
                    {
                        "attachment_id": "pm-attachment",
                        "source_id": "pm-attachment",
                        "source_version_id": "pm-attachment-v1",
                        "parent_source_id": "pm-source",
                        "evidence_spans": span,
                        "download": {"status": "success"},
                        "extraction": {"status": "parsed"},
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    return tmp_path


def test_observed_package_maps_parent_and_json_pointer_without_fetch(tmp_path) -> None:
    records = load_observed_package(_package(tmp_path))
    attachment = records[1]
    assert attachment.source_ref.parent_source_id == "pm-source"
    assert "#/attachments/0" in attachment.artifacts[0].native_locator.value
    assert attachment.artifacts[0].text == "Synthetic attachment excerpt."
    assert attachment.artifacts[0].upstream_evidence_id == "synthetic-evidence"


def test_observed_package_missing_required_file_is_error(tmp_path) -> None:
    with pytest.raises(FileNotFoundError):
        load_observed_package(tmp_path)
