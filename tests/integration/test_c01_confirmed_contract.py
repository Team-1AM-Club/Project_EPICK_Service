from uuid import UUID, uuid4

import pytest

from test_c01 import (
    SOURCE,
    SourceAuthority,
    allowed_version,
    index_request,
    parse,
    released,
    store_at,
)


def test_retired_source_wide_mode_is_rejected(tmp_path):
    with pytest.raises(ValueError):
        store_at(tmp_path / "old.db", scope="source")


@pytest.mark.parametrize("replacement", ["https://example.com/source", "opaque-source"])
def test_replacement_must_be_source_uuid_not_uri(replacement):
    value = released()
    value["payload"]["replacement_ref"] = replacement
    with pytest.raises(ValueError):
        parse(value)


def test_replacement_requires_authoritative_w2_source(tmp_path):
    authority = SourceAuthority(allowed={UUID(SOURCE)})
    with store_at(tmp_path / "replacement.db", scope="version", authority=authority) as store:
        store.consume(parse(allowed_version()))
        event = released()
        target = str(uuid4())
        event["payload"]["replacement_ref"] = target
        with pytest.raises(ValueError, match="SOURCE_NOT_REGISTERED"):
            store.consume(parse(event))
        assert store.status(SOURCE)["event_cursor"] == 1
        authority.allowed.add(UUID(target))
        result = store.consume(parse(event))
        assert result["event_cursor"] == 2


def test_snapshot_and_replay_cannot_bypass_replacement_registration(tmp_path):
    from test_c01 import snapshot_value
    from w3_knowledge.c01.contracts import Replay, VERSION

    event = released()
    event["payload"]["replacement_ref"] = str(uuid4())
    authority = SourceAuthority(allowed={UUID(SOURCE)})
    with store_at(tmp_path / "recovery.db", scope="version", authority=authority) as store:
        store.consume(parse(allowed_version()))
        with pytest.raises(ValueError, match="SOURCE_NOT_REGISTERED"):
            store.snapshot(snapshot_value(2, restrictions=[event], restriction_revision=1))
        replay = Replay.model_validate(
            dict(
                schema_version=VERSION,
                source_id=SOURCE,
                after_cursor=1,
                high_watermark=2,
                retention_floor_cursor=0,
                events=[event],
            )
        )
        with pytest.raises(ValueError, match="SOURCE_NOT_REGISTERED"):
            store.replay(replay)
        assert store.status(SOURCE)["required_event_cursor"] == 1


@pytest.mark.parametrize(
    "accuracy", ["unverified", "verified_in_scope", "error_confirmed", "superseded"]
)
@pytest.mark.parametrize("redistribution", ["allowed", "unknown", "denied"])
def test_cleared_accuracy_redistribution_policy_matrix(tmp_path, accuracy, redistribution):
    with store_at(tmp_path / "matrix.db", scope="version") as store:
        version = allowed_version()
        version["payload"]["policy"]["redistribution_permission"] = redistribution
        store.consume(parse(version))
        event = released()
        event["payload"]["accuracy_status"] = accuracy
        store.consume(parse(event))
        expected = (
            "RESTRICTED"
            if accuracy in {"error_confirmed", "superseded"}
            else "POLICY_BLOCKED"
            if redistribution != "allowed"
            else "INDEX_PENDING"
        )
        assert store.status(SOURCE)["reason"] == expected
        assert store.index(index_request(store))["index_ack"] is (expected == "INDEX_PENDING")


@pytest.mark.parametrize("cursor,floor,expected", [(1, 1, "REPLAYED"), (1, 2, "SNAPSHOT_REQUIRED")])
def test_retention_floor_equality_is_safe_but_lower_cursor_needs_snapshot(
    tmp_path, cursor, floor, expected
):
    from w3_knowledge.c01.contracts import Replay, VERSION

    with store_at(tmp_path / "floor.db", scope="version") as store:
        store.consume(parse(allowed_version()))
        reply = store.replay(
            Replay.model_validate(
                dict(
                    schema_version=VERSION,
                    source_id=SOURCE,
                    after_cursor=cursor,
                    high_watermark=floor,
                    retention_floor_cursor=floor,
                    events=[],
                )
            )
        )
        assert reply["outcome"] == expected


def test_interleaved_source_and_version_restriction_sequence(tmp_path):
    import json
    from pathlib import Path

    value = json.loads(
        (
            Path(__file__).parents[2]
            / "contracts/c01/v0.2-candidate/examples/revision-unit-comparison.json"
        ).read_text(encoding="utf-8")
    )
    with store_at(tmp_path / "sequence.db") as store:
        for event, expected in zip(value["variants"]["source"], value["expected"], strict=True):
            result = store.consume(parse(event))
            assert result["event_cursor"] == expected["revision"]
            assert result["reason"] == expected["reason"]
            assert result["restriction_revision"] == expected["source_restriction_revision"]
        assert store.consume(parse(value["variants"]["source"][3]))["outcome"] == "DUPLICATE"
        assert store.status(SOURCE)["restriction_revision"] == 8


def test_per_id_variant_is_not_silently_accepted_by_source_revision_consumer(tmp_path):
    import json
    from pathlib import Path

    value = json.loads(
        (
            Path(__file__).parents[2]
            / "contracts/c01/v0.2-candidate/examples/revision-unit-comparison.json"
        ).read_text(encoding="utf-8")
    )
    # All events validate as C01; the sequencing contract, not JSON shape, differs.
    for event in value["variants"]["restriction_id"]:
        parse(event)
    with store_at(tmp_path / "incompatible.db") as store:
        results = [store.consume(parse(e)) for e in value["variants"]["restriction_id"][:3]]
        assert results[-1]["reason"] == "CONFLICT"


@pytest.mark.parametrize("delivery", ["event", "replay", "snapshot"])
def test_same_restriction_id_cannot_change_version_scope(tmp_path, delivery):
    from test_c01 import snapshot_value
    from w3_knowledge.c01.contracts import Replay, VERSION

    with store_at(tmp_path / "scope-fixed.db") as store:
        store.consume(parse(allowed_version()))
        first = released(2, 1)
        first["payload"]["restriction_status"] = "active"
        store.consume(parse(first))
        changed = released(3, 2)
        changed["payload"]["source_version_id"] = None
        if delivery == "event":
            result = store.consume(parse(changed))
        elif delivery == "replay":
            result = store.replay(
                Replay.model_validate(
                    dict(
                        schema_version=VERSION,
                        source_id=SOURCE,
                        after_cursor=2,
                        high_watermark=3,
                        retention_floor_cursor=0,
                        events=[changed],
                    )
                )
            )
        else:
            result = store.snapshot(
                snapshot_value(3, restrictions=[changed], restriction_revision=2)
            )
        assert result["reason"] == "CONFLICT"
        assert result["index_ack"] is False


def test_restriction_id_cannot_move_to_another_source(tmp_path):
    from uuid import uuid4

    with store_at(tmp_path / "source-fixed.db") as store:
        first = released(1, 1)
        store.consume(parse(first))
        other = released(1, 1)
        other_source = str(uuid4())
        other["aggregate_id"] = other_source
        other["payload"]["source_id"] = other_source
        other["payload"]["source_version_id"] = None
        result = store.consume(parse(other))
        assert result["reason"] == "CONFLICT"
