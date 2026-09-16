import json
import subprocess
import sys

import pytest

from w3_knowledge.lab import Lab


def event(revision, *, blocked=False, version="v1", event_id=None):
    return {
        "event_id": event_id or f"event-{revision}",
        "source_id": "source-a",
        "revision": revision,
        "blocked": blocked,
        "version": version,
        "extraction": "ex1",
        "normalization": "norm1",
    }


def indexed(lab):
    lab.consume(event(1), now=0)
    return lab.index("source-a", ("v1", "ex1", "norm1"))


def test_restriction_blocks_existing_index_and_stale_release_cannot_restore_it():
    lab = Lab()
    assert indexed(lab)["ack"] is True
    lab.consume(event(2, blocked=True), now=1)
    assert lab.query("source-a")["reason"] == "RESTRICTED"
    assert lab.consume(event(1, event_id="late"), now=2) == "STALE"
    assert lab.query("source-a")["ack"] is False
    lab.consume(event(3), now=3)
    assert lab.query("source-a")["ack"] is True


def test_duplicate_and_restart_preserve_consumer_state(tmp_path):
    path = tmp_path / "lab.db"
    lab = Lab(path)
    indexed(lab)
    lab.close()
    lab = Lab(path)
    assert lab.consume(event(1), now=10) == "DUPLICATE"
    assert lab.query("source-a")["ack"] is True
    lab.close()
    other = Lab(path, consumer_id="other")
    assert other.query("source-a")["reason"] == "UNKNOWN_SOURCE"
    other.close()


def test_changed_payload_for_same_event_id_is_rejected():
    lab = Lab()
    indexed(lab)
    with pytest.raises(ValueError, match="EVENT_ID_COLLISION"):
        lab.consume(event(1, blocked=True), now=1)


@pytest.mark.parametrize(
    "key", [("v2", "ex1", "norm1"), ("v1", "ex2", "norm1"), ("v1", "ex1", "norm2")]
)
def test_index_key_mismatch_never_acknowledges(key):
    lab = Lab()
    indexed(lab)
    assert lab.index("source-a", key)["reason"] == "INDEX_MISMATCH"
    assert lab.query("source-a")["ack"] is False
    assert lab.query("source-a")["expected"] == ["v1", "ex1", "norm1"]


def test_index_failure_retains_sql_source_and_requires_successful_retry():
    lab = Lab()
    indexed(lab)
    assert lab.index("source-a", ("v1", "ex1", "norm1"), fail=True)["reason"] == "INDEX_FAILED"
    assert lab.query("source-a")["expected"] == ["v1", "ex1", "norm1"]
    assert lab.index("source-a", ("v1", "ex1", "norm1"))["ack"] is True


def test_gap_blocks_until_contiguous_replay_including_restriction():
    lab = Lab()
    indexed(lab)
    assert lab.consume(event(3), now=3) == "GAP"
    assert lab.query("source-a")["reason"] == "EVENT_GAP"
    lab.publish(event(2, blocked=True), at=2)
    assert lab.replay("source-a", now=4, retention=10) == "REPLAYED"
    assert lab.query("source-a")["revision"] == 3
    assert lab.query("source-a")["ack"] is True


def test_expired_replay_requires_snapshot_and_old_snapshot_cannot_clear_gap():
    lab = Lab()
    indexed(lab)
    lab.publish(event(2, blocked=True), at=2)
    lab.consume(event(3), now=3)
    assert lab.replay("source-a", now=12, retention=10) == "SNAPSHOT_REQUIRED"
    with pytest.raises(ValueError, match="STALE_SNAPSHOT"):
        lab.snapshot(event(2), now=12)
    assert lab.query("source-a")["ack"] is False
    lab.snapshot(event(3), now=12)
    assert lab.query("source-a")["reason"] == "INDEX_MISMATCH"
    assert "HISTORY_UNAVAILABLE" in lab.query("source-a")["limitations"]
    assert lab.index("source-a", ("v1", "ex1", "norm1"))["ack"] is True


@pytest.mark.parametrize(
    "scope,expected", [("FULL", "whole document"), ("EXCERPT", "excerpt"), ("NONE", None)]
)
def test_body_retention_scope_and_exact_expiry(scope, expected):
    lab = Lab()
    indexed(lab)
    lab.retain("source-a", full="whole document", excerpt="excerpt", scope=scope, now=0, ttl=10)
    assert lab.read_body("source-a", now=9) == expected
    assert lab.read_body("source-a", now=10) is None
    assert lab.query("source-a")["reason"] == "BODY_UNAVAILABLE"


def test_restriction_prevents_retained_body_access():
    lab = Lab()
    indexed(lab)
    lab.retain("source-a", full="private", excerpt="part", scope="FULL", now=0, ttl=10)
    lab.consume(event(2, blocked=True), now=1)
    assert lab.read_body("source-a", now=2) is None


def test_version_change_cannot_return_old_retained_body():
    lab = Lab()
    indexed(lab)
    lab.retain("source-a", full="v1 text", excerpt="v1", scope="FULL", now=0, ttl=100)
    lab.consume(event(2, version="v2"), now=1)
    assert lab.read_body("source-a", now=2) is None
    assert lab.index("source-a", ("v2", "ex1", "norm1"))["ack"] is False


def test_ack_query_enforces_body_expiry_without_reading_body_first():
    lab = Lab()
    indexed(lab)
    lab.retain("source-a", full="document", excerpt="part", scope="FULL", now=0, ttl=10)
    assert lab.query("source-a", now=9)["ack"] is True
    assert lab.query("source-a", now=10)["reason"] == "BODY_UNAVAILABLE"


def test_same_revision_conflicting_restriction_is_rejected():
    lab = Lab()
    indexed(lab)
    with pytest.raises(ValueError, match="REVISION_COLLISION"):
        lab.consume(event(1, event_id="conflict", blocked=True), now=1)


def test_public_event_rejects_unknown_personal_identifiers():
    lab = Lab()
    with pytest.raises(ValueError, match="LAB_EVENT_FIELDS_INVALID"):
        lab.consume({**event(1), "project_id": "private-project"}, now=0)
    assert lab.query("source-a")["reason"] == "UNKNOWN_SOURCE"


def test_cli_scenario_asserts_outcomes_and_has_no_external_calls(tmp_path):
    scenario = tmp_path / "scenario.json"
    scenario.write_text(
        json.dumps(
            {
                "steps": [
                    {"op": "consume", "event": event(1), "now": 0, "expect": "APPLIED"},
                    {
                        "op": "index",
                        "source_id": "source-a",
                        "key": ["v2", "ex1", "norm1"],
                        "expect": {"ack": False, "reason": "INDEX_MISMATCH"},
                    },
                ]
            }
        ),
        encoding="utf-8",
    )
    run = subprocess.run(
        [sys.executable, "-m", "w3_knowledge.lab", "--scenario", str(scenario)],
        capture_output=True,
        text=True,
    )
    assert run.returncode == 0, run.stderr
    assert json.loads(run.stdout)["passed"] == 2
    scenario.write_text(
        json.dumps({"steps": [{"op": "query", "source_id": "missing", "expect": {"ack": True}}]}),
        encoding="utf-8",
    )
    failed = subprocess.run(
        [sys.executable, "-m", "w3_knowledge.lab", "--scenario", str(scenario)],
        capture_output=True,
        text=True,
    )
    assert failed.returncode == 1
