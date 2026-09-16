"""Export draft wire schemas and public synthetic examples; no network access."""

import argparse
import copy
import json
from pathlib import Path

from w3_knowledge.restriction.contracts import SCHEMAS, VERSION

ROOT = Path(__file__).resolve().parents[1] / "contracts/restriction/v0.1-draft"


def example(revision, status):
    return {
        "schema_version": VERSION,
        "event_type": "source.restriction.changed",
        "event_id": f"demo-event-{revision}",
        "aggregate_id": "demo-source",
        "aggregate_revision": revision,
        "occurred_at": "2026-09-13T00:00:00Z",
        "published_at": "2026-09-13T00:00:01Z",
        "payload": {
            "source_id": "demo-source",
            "restriction_id": "demo-restriction",
            "status": status,
            "accuracy": "UNKNOWN",
            "reason_code": "POLICY_REVIEW",
            "replacement_source_id": None,
            "index_key": {
                "source_version_id": "demo-version",
                "extraction_revision_id": "demo-extraction",
                "representation": "text",
                "normalization_version": "demo-normalization",
            },
        },
    }


def artifacts():
    result = {}
    for name, model in SCHEMAS.items():
        result[f"{name}.schema.json"] = {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:epick:w3-restriction:0.1-draft:{name}",
            **model.model_json_schema(),
        }
    result["examples/initial.json"] = example(1, "RELEASED")
    result["examples/restrict.json"] = example(2, "RESTRICTED")
    result["examples/release.json"] = example(3, "RELEASED")
    result["examples/replay.json"] = {
        "schema_version": VERSION,
        "source_id": "demo-source",
        "after_revision": 1,
        "high_watermark": 3,
        "retention_start": "2026-09-12T00:00:00Z",
        "events": [example(2, "RESTRICTED"), example(3, "RELEASED")],
    }
    result["examples/snapshot.json"] = {
        "schema_version": VERSION,
        "as_of": "2026-09-13T00:00:02Z",
        "event": example(3, "RELEASED"),
    }
    result["examples/index-request.json"] = {
        "schema_version": VERSION,
        "source_id": "demo-source",
        "restriction_revision": 3,
        "index_key": example(3, "RELEASED")["payload"]["index_key"],
        "retention_scope": "excerpts_only",
        "expires_at": "2026-09-14T00:00:00Z",
        "documents": [
            {
                "document_id": "demo-document",
                "evidence_id": "demo-evidence",
                "text": "Synthetic semiconductor evidence.",
            }
        ],
    }
    invalid = copy.deepcopy(example(1, "RELEASED"))
    invalid["payload"]["project_id"] = "private-not-allowed"
    result["invalid/private-field.json"] = invalid
    result["invalid/future-version.json"] = {
        **example(1, "RELEASED"),
        "schema_version": "w3-restriction/1.0",
    }
    result["invalid/revision-string.json"] = {**example(1, "RELEASED"), "aggregate_revision": "1"}
    result["invalid/source-mismatch.json"] = {
        **example(1, "RELEASED"),
        "aggregate_id": "other-source",
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    mismatches = []
    for name, value in artifacts().items():
        path = ROOT / name
        content = json.dumps(value, indent=2, ensure_ascii=False) + "\n"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != content:
                mismatches.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content, encoding="utf-8")
    print(
        json.dumps({"schema_version": VERSION, "files": len(artifacts()), "mismatches": mismatches})
    )
    return int(bool(mismatches))


if __name__ == "__main__":
    raise SystemExit(main())
