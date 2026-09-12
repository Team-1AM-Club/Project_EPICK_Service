import json

from w3_knowledge.adapters.collection_sample import load_collection_sample


def test_legacy_summary_is_not_promoted_to_evidence(tmp_path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(
        json.dumps(
            {
                "sources": [
                    {
                        "source_id": "legacy-source",
                        "source_version_id": "legacy-source-v1",
                        "normalized_summary": "not evidence",
                    }
                ]
            }
        ),
        encoding="utf-8",
    )
    source = load_collection_sample(path)[0]
    assert source.artifacts[0].text is None
    assert any(item.code == "SUMMARY_NOT_EVIDENCE" for item in source.limitations)


def test_legacy_missing_version_is_explicitly_limited(tmp_path) -> None:
    path = tmp_path / "legacy.json"
    path.write_text(json.dumps({"sources": [{"source_id": "legacy-source"}]}), encoding="utf-8")
    source = load_collection_sample(path)[0]
    assert any(item.code == "SOURCE_VERSION_UNRESOLVED" for item in source.limitations)
