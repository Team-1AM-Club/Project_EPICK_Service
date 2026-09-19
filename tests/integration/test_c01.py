import copy
import json
from pathlib import Path
from uuid import uuid4

import pytest

FIXTURES = Path(__file__).parents[2] / "contracts/c01/v0.2-candidate/fixtures"
SOURCE = "10000000-0000-4000-8000-000000000005"
NAMES = ["version-available", "version-partial", "observation-changed", "restriction-changed"]


def fixture(name):
    return json.loads((FIXTURES / f"source-event-{name}.json").read_text(encoding="utf-8"))


def parse(value):
    from w3_knowledge.c01.contracts import Event

    return Event.model_validate(value)


def store_at(path, scope="version", clock=lambda: "2026-09-16T00:00:00Z"):
    from w3_knowledge.c01.store import Store

    return Store(path, restriction_scope=scope, max_ttl_seconds=3600, clock=clock)


def released(revision=2, restriction_revision=1):
    value = fixture("restriction-changed")
    value["revision"] = revision
    value["event_id"] = str(uuid4())
    value["payload"].update(
        restriction_revision=restriction_revision,
        restriction_status="cleared",
        accuracy_status="verified_in_scope",
    )
    return value


def allowed_version():
    value = fixture("version-available")
    value["payload"]["policy"]["redistribution_permission"] = "allowed"
    return value


def index_request(store):
    from w3_knowledge.c01.contracts import IndexRequest, VERSION

    status = store.status(SOURCE)
    evidence = fixture("version-available")["payload"]["evidence_spans"][0]
    return IndexRequest.model_validate(
        dict(
            schema_version=VERSION,
            source_id=SOURCE,
            event_cursor=status["event_cursor"],
            restriction_revision=status["restriction_revision"],
            index_key=status["index_key"],
            retention_scope="excerpts_only",
            expires_at="2026-09-16T00:30:00Z",
            documents=[
                dict(
                    document_id=str(uuid4()),
                    evidence_id=evidence["evidence_id"],
                    text=evidence["text_excerpt"],
                )
            ],
        )
    )


@pytest.mark.parametrize("name", NAMES)
def test_original_w2_event_validates_without_id_or_enum_rewriting(name):
    value = fixture(name)
    assert parse(value).model_dump(mode="json") == value


@pytest.mark.parametrize("name", ["private-field", "transport-fields"])
def test_original_invalid_payload_is_rejected(name):
    from w3_knowledge.c01.contracts import validate_payload

    value = json.loads((FIXTURES / f"invalid-source-event-{name}.json").read_text())
    with pytest.raises(ValueError):
        validate_payload(value)


@pytest.mark.parametrize(
    "mutation", ["wrong_type", "uuid", "source", "private", "date", "overflow"]
)
def test_adapter_rejects_inconsistent_or_private_input(mutation):
    value = fixture("version-available")
    if mutation == "wrong_type":
        value["event_type"] = "source.restriction.changed"
    if mutation == "uuid":
        value["event_id"] = "not-uuid"
    if mutation == "source":
        value["aggregate_id"] = str(uuid4())
    if mutation == "private":
        value["payload"]["job_id"] = str(uuid4())
    if mutation == "date":
        value["occurred_at"] = "yesterday"
    if mutation == "overflow":
        value["revision"] = 2**63
    with pytest.raises(ValueError):
        parse(value)


def test_four_original_events_use_two_cursors_and_survive_restart(tmp_path):
    path = tmp_path / "w3.db"
    with store_at(path) as store:
        for name in NAMES:
            store.consume(parse(fixture(name)))
        status = store.status(SOURCE)
        assert (status["event_cursor"], status["restriction_revision"]) == (4, 1)
        assert status["reason"] == "OBSERVATION_BLOCKED"
        assert status["index_key"]["representation"] == "static_html"
        assert status["index_key"]["source_version_id"].endswith("0009")
        assert store.consume(parse(fixture(NAMES[3])))["outcome"] == "DUPLICATE"
    with store_at(path) as store:
        assert store.status(SOURCE)["event_cursor"] == 4


def test_out_of_order_replay_closes_transport_gap_without_fabricated_restrictions(tmp_path):
    from w3_knowledge.c01.contracts import Replay, VERSION

    with store_at(tmp_path / "gap.db") as store:
        assert store.consume(parse(fixture(NAMES[3])))["reason"] == "EVENT_GAP"
        reply = store.replay(
            Replay.model_validate(
                dict(
                    schema_version=VERSION,
                    source_id=SOURCE,
                    after_cursor=0,
                    high_watermark=4,
                    retention_floor_cursor=0,
                    events=[fixture(n) for n in NAMES[:3]],
                )
            )
        )
        assert reply["event_cursor"] == 4
        assert reply["restriction_revision"] == 1
        assert reply["reason"] == "OBSERVATION_BLOCKED"


def test_index_is_fenced_by_policy_key_evidence_cursor_and_ttl(tmp_path):
    with store_at(tmp_path / "index.db") as store:
        store.consume(parse(allowed_version()))
        request = index_request(store)
        assert store.index(request)["index_ack"]
        assert len(store.search("cloud")) == 1
        wrong = request.model_copy(update={"event_cursor": 2})
        assert store.index(wrong)["reason"] == "INDEX_MISMATCH"
        assert store.search("cloud") == []
        assert store.index(request)["index_ack"]
        active = fixture("restriction-changed")
        active["revision"] = 2
        store.consume(parse(active))
        assert store.search("cloud") == []
        store.consume(parse(released(3, 2)))
        assert store.status(SOURCE)["reason"] == "INDEX_PENDING"
        assert store.index(index_request(store))["index_ack"]


def test_conflict_blocks_both_event_id_owners_and_does_not_auto_clear(tmp_path):
    with store_at(tmp_path / "conflict.db") as store:
        original = allowed_version()
        store.consume(parse(original))
        changed = copy.deepcopy(original)
        changed["payload"]["title"] = "different"
        assert store.consume(parse(changed))["reason"] == "CONFLICT"
        assert store.consume(parse(original))["reason"] == "CONFLICT"


def test_restriction_revision_gap_cannot_be_hidden_by_transport_cursor(tmp_path):
    with store_at(tmp_path / "restriction-gap.db") as store:
        store.consume(parse(allowed_version()))
        store.consume(parse(released(2, 2)))
        status = store.status(SOURCE)
        assert status["event_cursor"] == 2
        assert status["restriction_revision"] == 0
        assert status["required_restriction_revision"] == 2
        assert status["reason"] == "RESTRICTION_GAP"


def test_expired_content_is_physically_removed_from_live_tables(tmp_path):
    now = ["2026-09-16T00:00:00Z"]
    with store_at(tmp_path / "ttl.db", clock=lambda: now[0]) as store:
        store.consume(parse(allowed_version()))
        store.index(index_request(store))
        now[0] = "2026-09-16T00:30:00Z"
        assert store.search("cloud") == []
        assert store.status(SOURCE)["reason"] == "BODY_EXPIRED"
        assert store.db.execute("SELECT count(*) FROM documents").fetchone()[0] == 0
        # Receipt/projection history must never retain an extra copy of index body.
        rows = store.db.execute("SELECT projection FROM c01_events").fetchall()
        assert "Experience operating" not in str(rows)


def snapshot_value(cursor=1, versions=None, restrictions=None, restriction_revision=0):
    from w3_knowledge.c01.contracts import Snapshot, VERSION

    return Snapshot.model_validate(
        dict(
            schema_version=VERSION,
            source_id=SOURCE,
            as_of="2026-09-16T00:00:00Z",
            event_cursor=cursor,
            restriction_revision=restriction_revision,
            complete=True,
            versions=versions if versions is not None else [allowed_version()],
            restrictions=restrictions or [],
            observation=None,
        )
    )


def test_snapshot_rejects_known_event_mutation_and_omitted_active_restriction(tmp_path):
    with store_at(tmp_path / "snapshot-conflict.db") as store:
        store.consume(parse(allowed_version()))
        changed = allowed_version()
        changed["payload"]["title"] = "rewritten immutable event"
        assert store.snapshot(snapshot_value(versions=[changed]))["outcome"] == "CONFLICT"
    with store_at(tmp_path / "snapshot-omitted.db") as store:
        store.consume(parse(allowed_version()))
        restriction = released()
        restriction["payload"]["restriction_status"] = "active"
        store.consume(parse(restriction))
        other = released(3, 2)
        other["payload"]["restriction_id"] = str(uuid4())
        response = store.snapshot(snapshot_value(3, restrictions=[other], restriction_revision=2))
        assert response["outcome"] == "CONFLICT"
        assert not response["index_ack"]


def test_replay_retention_and_snapshot_watermark_reindex_boundary(tmp_path):
    from w3_knowledge.c01.contracts import Replay, VERSION

    with store_at(tmp_path / "snapshot.db") as store:
        store.consume(parse(allowed_version()))
        result = store.replay(
            Replay.model_validate(
                dict(
                    schema_version=VERSION,
                    source_id=SOURCE,
                    after_cursor=1,
                    high_watermark=4,
                    retention_floor_cursor=3,
                    events=[],
                )
            )
        )
        assert result["outcome"] == "SNAPSHOT_REQUIRED"
        assert result["reason"] == "EVENT_GAP"
        assert store.snapshot(snapshot_value(1))["outcome"] == "STALE_SNAPSHOT"
        result = store.snapshot(snapshot_value(4))
        assert result["outcome"] == "SNAPSHOT_APPLIED"
        assert result["reason"] == "INDEX_PENDING"
        assert result["history_complete"] is False
        assert store.index(index_request(store))["index_ack"]
        assert store.snapshot(snapshot_value(4))["reason"] == "INDEX_PENDING"


def test_clearing_one_restriction_does_not_clear_another(tmp_path):
    with store_at(tmp_path / "many.db") as store:
        store.consume(parse(allowed_version()))
        first = released(2, 1)
        first["payload"]["restriction_status"] = "active"
        second = released(3, 2)
        second["payload"]["restriction_status"] = "active"
        second["payload"]["restriction_id"] = str(uuid4())
        store.consume(parse(first))
        store.consume(parse(second))
        store.consume(parse(released(4, 3)))
        assert store.status(SOURCE)["reason"] == "RESTRICTED"


def test_version_scope_does_not_apply_old_version_restriction_to_new_version(tmp_path):
    with store_at(tmp_path / "scope.db", scope="version") as store:
        store.consume(parse(allowed_version()))
        store.consume(parse(fixture("version-partial")))
        restriction = fixture("restriction-changed")
        restriction["revision"] = 3
        assert store.consume(parse(restriction))["reason"] == "POLICY_BLOCKED"
        restriction["event_id"] = str(uuid4())
        restriction["revision"] = 4
        # Source-wide restriction is a distinct ID: an existing ID's scope is immutable.
        restriction["payload"].update(
            restriction_id=str(uuid4()), source_version_id=None, restriction_revision=2
        )
        assert store.consume(parse(restriction))["reason"] == "RESTRICTED"


@pytest.mark.parametrize("wrong", ["evidence", "text", "ttl", "extraction"])
def test_index_cannot_substitute_unpermitted_content(tmp_path, wrong):
    from w3_knowledge.c01.contracts import IndexRequest

    with store_at(tmp_path / "wrong.db") as store:
        store.consume(parse(allowed_version()))
        value = index_request(store).model_dump()
        if wrong == "evidence":
            value["documents"][0]["evidence_id"] = str(uuid4())
        if wrong == "text":
            value["documents"][0]["text"] = "unrelated private text"
        if wrong == "ttl":
            value["expires_at"] = "2026-09-17T00:00:00Z"
        if wrong == "extraction":
            value["index_key"]["extraction_revision_id"] = str(uuid4())
        assert store.index(IndexRequest.model_validate(value))["reason"] == "INDEX_MISMATCH"
        assert store.search("cloud") == []


def test_w4_uses_c01_signal_and_checks_latest_status_before_cache_return(tmp_path):
    from w3_knowledge.c01.w4 import W4Cache

    with store_at(tmp_path / "w3.db") as store, W4Cache(tmp_path / "w4.db") as cache:
        store.consume(parse(allowed_version()))
        store.index(index_request(store))
        ready = store.signals()[-1]
        assert cache.apply(ready) == "APPLIED"
        cache.put(SOURCE, ready["generation"], {"result": "derived"})
        assert cache.get(SOURCE, store.status) == {"result": "derived"}
        restriction = released(2, 1)
        restriction["payload"]["restriction_status"] = "active"
        store.consume(parse(restriction))
        assert cache.get(SOURCE, store.status) is None
        assert cache.apply(store.signals()[-1]) == "APPLIED"
        assert cache.apply(ready) == "STALE"


def test_exported_schemas_validate_original_envelope_and_reject_wrong_type():
    from jsonschema import Draft202012Validator, FormatChecker
    from w3_knowledge.c01.contracts import SCHEMAS

    for model in SCHEMAS.values():
        Draft202012Validator.check_schema(model.model_json_schema())
    validator = Draft202012Validator(
        SCHEMAS["event"].model_json_schema(), format_checker=FormatChecker()
    )
    assert validator.is_valid(fixture("version-available"))
    wrong = fixture("version-available")
    wrong["event_type"] = "source.restriction.changed"
    assert not validator.is_valid(wrong)


def test_replay_sql_failure_rolls_back_cursor_receipts_and_signals(tmp_path):
    import sqlite3
    from w3_knowledge.c01.contracts import Replay, VERSION

    with store_at(tmp_path / "rollback.db") as store:
        store.db.execute(
            "CREATE TRIGGER fail_second BEFORE INSERT ON c01_events WHEN NEW.revision=2 BEGIN SELECT RAISE(ABORT, 'synthetic fault'); END"
        )
        batch = Replay.model_validate(
            dict(
                schema_version=VERSION,
                source_id=SOURCE,
                after_cursor=0,
                high_watermark=2,
                retention_floor_cursor=0,
                events=[fixture(n) for n in NAMES[:2]],
            )
        )
        with pytest.raises(sqlite3.Error):
            store.replay(batch)
        assert store.status(SOURCE)["event_cursor"] == 0
        assert store.signals() == []
        assert store.db.execute("SELECT count(*) FROM c01_events").fetchone()[0] == 0


def test_index_sql_failure_removes_search_exposure(tmp_path):
    with store_at(tmp_path / "index-fail.db") as store:
        store.consume(parse(allowed_version()))
        store.db.execute(
            "CREATE TRIGGER fail_index BEFORE INSERT ON indexed BEGIN SELECT RAISE(ABORT, 'synthetic fault'); END"
        )
        assert store.index(index_request(store))["reason"] == "INDEX_FAILED"
        assert store.search("cloud") == []


def test_snapshot_cannot_omit_pending_active_restriction(tmp_path):
    with store_at(tmp_path / "pending-snapshot.db") as store:
        store.consume(parse(allowed_version()))
        pending = released(3, 1)
        pending["payload"]["restriction_status"] = "active"
        store.consume(parse(pending))
        other = released(2, 1)
        other["payload"]["restriction_id"] = str(uuid4())
        reply = store.snapshot(snapshot_value(3, restrictions=[other], restriction_revision=1))
        assert reply["outcome"] == "CONFLICT"
        assert not reply["index_ack"]


def test_snapshot_event_identity_survives_later_event_delivery(tmp_path):
    with store_at(tmp_path / "identity.db") as store:
        store.snapshot(snapshot_value())
        changed = allowed_version()
        changed["revision"] = 2
        changed["payload"]["title"] = "reused event id"
        assert store.consume(parse(changed))["outcome"] == "CONFLICT"


def test_uuid_case_cannot_bypass_version_restriction_or_fork_source_state(tmp_path):
    with store_at(tmp_path / "uuid-case.db", scope="version") as store:
        version = allowed_version()
        source = "abcdefab-1234-4000-8000-abcdefabcdef"
        version_id = "abcdefab-1234-4000-8001-abcdefabcdef"
        version["aggregate_id"] = version["payload"]["source_id"] = source
        version["payload"]["source_version_id"] = version_id
        version["payload"]["evidence_spans"][0]["source_version_id"] = version_id
        store.consume(parse(version))
        restriction = released(2, 1)
        restriction["aggregate_id"] = source.upper()
        restriction["payload"].update(
            source_id=source.upper(),
            source_version_id=version_id.upper(),
            restriction_status="active",
        )
        reply = store.consume(parse(restriction))
        assert reply["reason"] == "RESTRICTED"
        assert reply["event_cursor"] == 2
        assert store.status(source.upper()) == store.status(source)


def test_snapshot_cannot_reuse_restriction_revision_to_clear_active(tmp_path):
    with store_at(tmp_path / "snapshot-rseq.db") as store:
        store.consume(parse(allowed_version()))
        first = released(2, 1)
        first["payload"]["restriction_status"] = "active"
        store.consume(parse(first))
        changed = released(3, 1)
        other = released(4, 2)
        other["payload"]["restriction_id"] = str(uuid4())
        result = store.snapshot(
            snapshot_value(4, restrictions=[changed, other], restriction_revision=2)
        )
        assert result["outcome"] == "CONFLICT"
        assert not result["index_ack"]


@pytest.mark.parametrize("revisions", [(1, 1), (2, 1)])
def test_snapshot_rejects_duplicate_or_reversed_restriction_sequence(revisions):
    first, second = released(2, revisions[0]), released(3, revisions[1])
    second["payload"]["restriction_id"] = str(uuid4())
    with pytest.raises(ValueError):
        snapshot_value(3, restrictions=[first, second], restriction_revision=max(revisions))
