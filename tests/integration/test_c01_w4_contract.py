"""W3-owned candidate consumer tests; this is not W4 team's acceptance sign-off."""

import copy
import json

import pytest

from test_c01 import (
    SOURCE,
    allowed_version,
    index_request,
    parse,
    released,
    snapshot_value,
    store_at,
)
from test_c01_guard import source
from test_restriction_guard import ports
from tests.support.factories import request


def knowledge(store):
    from w3_knowledge.c01.contracts import IndexKey
    from w3_knowledge.c01.guard import structure_guarded

    keys = {SOURCE: IndexKey.model_validate(store.status(SOURCE)["index_key"])}
    return structure_guarded(request(source()), ports(), store, keys).model_dump(mode="json")


def test_typed_knowledge_consumer_rejects_wrong_source_version_and_generation(tmp_path):
    from w3_knowledge.c01.w4 import W4Cache

    with store_at(tmp_path / "w3.db") as store, W4Cache(tmp_path / "w4.db") as cache:
        store.consume(parse(allowed_version()))
        store.index(index_request(store))
        signal = store.signals()[-1]
        cache.apply(signal)
        response = knowledge(store)
        cache.put_knowledge(SOURCE, signal["generation"], response)
        assert cache.get(SOURCE, store.status)["bundle"]["evidences"]
        wrong = copy.deepcopy(response)
        wrong["bundle"]["evidences"][0]["source_ref"]["source_version_id"] = "foreign-version"
        with pytest.raises(ValueError):
            cache.put_knowledge(SOURCE, signal["generation"], wrong)
        with pytest.raises(ValueError):
            cache.put_knowledge(SOURCE, signal["generation"] + 1, response)


def test_ack_loss_redelivery_restart_and_duplicate_do_not_evict_valid_cache(tmp_path):
    from w3_knowledge.c01.w4 import W4Cache

    with store_at(tmp_path / "w3.db") as store:
        store.consume(parse(allowed_version()))
        store.index(index_request(store))
        ready = store.signals()[-1]
        with W4Cache(tmp_path / "w4.db") as cache:
            assert cache.apply(ready) == "APPLIED"
            cache.put_knowledge(SOURCE, ready["generation"], knowledge(store))
            # W4 committed locally; the process ends before sending the ACK.
        assert any(s["signal_id"] == ready["signal_id"] for s in store.signals())
        with W4Cache(tmp_path / "w4.db") as cache:
            assert cache.apply(ready) == "DUPLICATE"
            assert cache.get(SOURCE, store.status) is not None
            assert store.acknowledge(ready["signal_id"]) is True
            # ACK committed but its HTTP response was lost; repeat same identity.
            assert store.acknowledge(ready["signal_id"]) is True
            assert all(s["signal_id"] != ready["signal_id"] for s in store.signals())


def test_gap_replay_snapshot_and_late_release_keep_w4_blocked_until_reindex(tmp_path):
    from w3_knowledge.c01.contracts import Replay, VERSION
    from w3_knowledge.c01.w4 import W4Cache

    with store_at(tmp_path / "w3.db") as store, W4Cache(tmp_path / "w4.db") as cache:
        store.consume(parse(allowed_version()))
        store.index(index_request(store))
        ready = store.signals()[-1]
        cache.apply(ready)
        cache.put_knowledge(SOURCE, ready["generation"], knowledge(store))
        active, release = released(2, 1), released(3, 2)
        active["payload"]["restriction_status"] = "active"
        store.consume(parse(release))
        assert store.status(SOURCE)["reason"] == "EVENT_GAP"
        assert cache.get(SOURCE, store.status) is None
        batch = Replay.model_validate(
            dict(
                schema_version=VERSION,
                source_id=SOURCE,
                after_cursor=1,
                high_watermark=3,
                retention_floor_cursor=0,
                events=[active],
            )
        )
        assert store.replay(batch)["reason"] == "INDEX_PENDING"
        cache.apply(store.signals()[-1])
        assert cache.apply(ready) == "STALE"
        store.index(index_request(store))
        cache.apply(store.signals()[-1])
        cache.put_knowledge(SOURCE, store.status(SOURCE)["generation"], knowledge(store))
        snap = snapshot_value(3, restrictions=[release], restriction_revision=2)
        assert store.snapshot(snap)["reason"] == "INDEX_PENDING"
        assert cache.get(SOURCE, store.status) is None
        assert store.consume(parse(active))["outcome"] == "DUPLICATE"
        assert not store.status(SOURCE)["index_ack"]


def test_signal_same_generation_conflict_and_status_failure_remove_cache(tmp_path):
    from w3_knowledge.c01.w4 import W4Cache

    with store_at(tmp_path / "w3.db") as store, W4Cache(tmp_path / "w4.db") as cache:
        store.consume(parse(allowed_version()))
        store.index(index_request(store))
        ready = store.signals()[-1]
        cache.apply(ready)
        cache.put_knowledge(SOURCE, ready["generation"], knowledge(store))
        wrong = {**ready, "reason": "RESTRICTED", "index_ack": False, "usable": False}
        assert cache.apply(wrong) == "CONFLICT"
        assert cache.get(SOURCE, store.status) is None
        store.index(index_request(store))
        cache.apply(store.signals()[-1])
        cache.put_knowledge(SOURCE, store.status(SOURCE)["generation"], knowledge(store))

        def unavailable(_source):
            raise OSError("synthetic status outage")

        assert cache.get(SOURCE, unavailable) is None


def test_committed_w4_fixture_is_consumed_and_matches_exported_schemas(tmp_path):
    from pathlib import Path
    from jsonschema import Draft202012Validator, FormatChecker
    from w3_knowledge.c01.w4 import W4Cache

    root = Path(__file__).parents[2] / "contracts/c01/v0.2-candidate"
    fixture = json.loads((root / "examples/w4-consumer.json").read_text(encoding="utf-8"))
    for key, schema in [
        ("ready", "signal"),
        ("blocked", "signal"),
        ("knowledge", "knowledge"),
        ("ack", "delivery-receipt"),
    ]:
        validator = Draft202012Validator(
            json.loads((root / f"{schema}.schema.json").read_text(encoding="utf-8")),
            format_checker=FormatChecker(),
        )
        validator.validate(fixture[key])
    with W4Cache(tmp_path / "fixture.db") as cache:
        signal = fixture["ready"]
        cache.apply(signal)
        cache.put_knowledge(signal["source_id"], signal["generation"], fixture["knowledge"])
        assert cache.get(signal["source_id"], lambda _: fixture["ready_status"]) is not None
        cache.apply(fixture["blocked"])
        assert cache.get(signal["source_id"], lambda _: fixture["blocked_status"]) is None


@pytest.mark.parametrize("broken", ["root", "node_evidence"])
def test_typed_consumer_rejects_dangling_requirement_references(tmp_path, broken):
    from w3_knowledge.c01.w4 import W4Cache

    with store_at(tmp_path / "w3.db") as store, W4Cache(tmp_path / "w4.db") as cache:
        store.consume(parse(allowed_version()))
        store.index(index_request(store))
        signal = store.signals()[-1]
        cache.apply(signal)
        response = knowledge(store)
        evidence_id = response["bundle"]["evidences"][0]["evidence_id"]
        requirement = dict(
            candidate_id="requirement-demo",
            original_text="cloud services",
            evidence_ids=[evidence_id],
            necessity="REQUIRED",
            root_node_id="condition-leaf",
            condition_nodes=[
                dict(
                    node_id="condition-leaf", operator="LEAF", text="cloud", evidence_id=evidence_id
                )
            ],
            verification_status="VERIFIED",
            usage_status="USABLE",
            checks=[],
        )
        response["bundle"]["requirements"] = [requirement]
        cache.put_knowledge(SOURCE, signal["generation"], response)
        if broken == "root":
            requirement["root_node_id"] = "condition-missing"
        else:
            requirement["condition_nodes"][0]["evidence_id"] = "evidence-missing"
        with pytest.raises(ValueError):
            cache.put_knowledge(SOURCE, signal["generation"], response)
