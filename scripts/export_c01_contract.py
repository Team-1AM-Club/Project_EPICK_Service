"""Export the C01 candidate schemas and labeled synthetic recovery fixtures."""

import argparse
import copy
import hashlib
import json
from pathlib import Path

from w3_knowledge.c01.contracts import SCHEMAS, VERSION

ROOT = Path(__file__).resolve().parents[1]
DEST = ROOT / "contracts/c01/v0.2-candidate"


def artifacts():
    result = {
        f"{name}.schema.json": {
            "$schema": "https://json-schema.org/draft/2020-12/schema",
            "$id": f"urn:epick:w3-c01:0.2-candidate:{name}",
            **model.model_json_schema(),
        }
        for name, model in SCHEMAS.items()
    }

    def load(name):
        return json.loads(
            (DEST / "fixtures" / f"source-event-{name}.json").read_text(encoding="utf-8")
        )

    version = load("version-available")
    # Explicitly synthetic policy grant; never relabel the producer's original example.
    version["payload"]["policy"]["redistribution_permission"] = "allowed"
    source = version["aggregate_id"]
    active = load("restriction-changed")
    active["revision"] = 2
    release = copy.deepcopy(active)
    release["revision"] = 3
    release["event_id"] = "30000000-0000-4000-8000-000000000003"
    release["payload"].update(
        restriction_revision=2,
        restriction_status="cleared",
        accuracy_status="verified_in_scope",
        reason_code="SYNTHETIC_RESOLVED",
    )
    result["examples/version-policy-allowed.json"] = version
    result["examples/restrict.json"] = active
    result["examples/release.json"] = release
    result["examples/replay.json"] = dict(
        schema_version=VERSION,
        source_id=source,
        after_cursor=0,
        high_watermark=3,
        retention_floor_cursor=0,
        events=[version, active, release],
    )
    result["examples/snapshot.json"] = dict(
        schema_version=VERSION,
        source_id=source,
        as_of="2026-09-16T00:00:00Z",
        event_cursor=3,
        restriction_revision=2,
        complete=True,
        versions=[version],
        restrictions=[release],
        observation=None,
    )
    p = version["payload"]
    result["examples/index-request.json"] = dict(
        schema_version=VERSION,
        source_id=source,
        event_cursor=3,
        restriction_revision=2,
        index_key=dict(
            source_version_id=p["source_version_id"],
            extraction_revision_id=p["extraction_revision_id"],
            representation=p["metadata"]["representation"],
            normalization_version=p["metadata"]["normalization_version"],
        ),
        retention_scope="excerpts_only",
        expires_at="2026-09-16T00:30:00Z",
        documents=[
            dict(
                document_id="30000000-0000-4000-8000-000000000004",
                evidence_id=p["evidence_spans"][0]["evidence_id"],
                text=p["evidence_spans"][0]["text_excerpt"],
            )
        ],
    )
    ready_status = dict(
        schema_version=VERSION,
        source_id=source,
        event_cursor=3,
        required_event_cursor=3,
        restriction_revision=2,
        required_restriction_revision=2,
        generation=4,
        restriction_scope="source",
        reason="READY",
        index_ack=True,
        index_key=result["examples/index-request.json"]["index_key"],
        history_complete=True,
    )
    ready = dict(
        ready_status,
        event_type="w3.source.usability.changed",
        signal_id="30000000-0000-4000-8000-000000000005",
        usable=True,
    )
    blocked_status = {
        **ready_status,
        "event_cursor": 4,
        "required_event_cursor": 4,
        "restriction_revision": 3,
        "required_restriction_revision": 3,
        "generation": 5,
        "reason": "RESTRICTED",
        "index_ack": False,
    }
    blocked = dict(
        blocked_status,
        event_type="w3.source.usability.changed",
        signal_id="30000000-0000-4000-8000-000000000006",
        usable=False,
    )
    ref = dict(
        source_id=source, source_version_id=p["source_version_id"], source_kind="job_posting"
    )
    knowledge = dict(
        status="LIMITED",
        errors=[],
        bundle=dict(
            mode="SYNTHETIC",
            purpose="SYNTHETIC_ACCEPTANCE",
            requirement_presence="NOT_ASSESSED",
            claims=[],
            requirements=[],
            limitations=[],
            source_reviews=[
                dict(source_ref=ref, checks=[], limitations=[], process_status="LIMITED")
            ],
            evidences=[
                dict(
                    evidence_id="evidence-c01-demo",
                    artifact_id="c01-demo",
                    source_ref=ref,
                    excerpt=p["evidence_spans"][0]["text_excerpt"],
                )
            ],
        ),
    )
    result["examples/w4-consumer.json"] = dict(
        ready=ready,
        ready_status=ready_status,
        blocked=blocked,
        blocked_status=blocked_status,
        knowledge=knowledge,
        ack={"signal_id": ready["signal_id"]},
        ack_response={"delivered": True},
    )
    inputs = sorted((DEST / "fixtures").glob("*.json")) + [
        ROOT / "src/w3_knowledge/c01/source-event-payload.schema.json"
    ]
    result["received-manifest.json"] = {
        "provenance": "User supplied W2 schema and six public event examples, copied without edits",
        "sha256": {
            path.relative_to(ROOT).as_posix(): hashlib.sha256(path.read_bytes()).hexdigest()
            for path in inputs
        },
    }
    return result


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--check", action="store_true")
    args = parser.parse_args(argv)
    changed = []
    for name, value in artifacts().items():
        path = DEST / name
        text = json.dumps(value, ensure_ascii=False, indent=2) + "\n"
        if args.check:
            if not path.exists() or path.read_text(encoding="utf-8") != text:
                changed.append(name)
        else:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(text, encoding="utf-8")
    print(
        json.dumps(
            {
                "mode": "check" if args.check else "write",
                "files": len(artifacts()),
                "drift": changed,
            }
        )
    )
    return bool(changed)


if __name__ == "__main__":
    raise SystemExit(main())
