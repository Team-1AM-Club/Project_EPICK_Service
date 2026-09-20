import sqlite3
from uuid import UUID

import pytest

from w3_knowledge.core_decision import CoreDecisionProducer, DecisionContext
from w3_knowledge.core_runtime import AnalysisPlan, Authorization, CoreRuntime
from w3_knowledge.retention import RetentionPolicy


POLICY = RetentionPolicy(
    handoff_body_seconds=14,
    private_body_max_seconds=30,
    terminal_metadata_seconds=90,
    owner_tombstone_seconds=365,
    retired_counter_seconds=90,
    backup_seconds=30,
)


def authorization(*, active=True):
    return Authorization(
        context=DecisionContext(
            job_id=UUID(int=1),
            company_id=UUID(int=2),
            source_id=UUID(int=3),
            analysis_input_version="analysis-1",
        ),
        owner_id=UUID(int=4),
        owner_epoch=1,
        active=active,
    )


def analysis_plan(*, issued_at=100, request_id=10):
    current = authorization().context
    return AnalysisPlan(
        context=current,
        analysis_request_id=UUID(int=request_id),
        analysis_request_issued_at=issued_at,
        required_sources=[current.source_id],
        optional_sources=[],
    )


class Authority:
    def __init__(self, value=None):
        self.value = value or authorization()

    def current(self, job_id, source_id):
        return self.value


class Transport:
    def __init__(self, *, fail=False):
        self.fail = fail
        self.bodies = []

    def send(self, body):
        self.bodies.append(body)
        if self.fail:
            raise TimeoutError("synthetic transport failure")
        return "transport-id"


def runtime(tmp_path, *, attempts=3):
    return CoreRuntime(tmp_path / "runtime.db", policy=POLICY, max_attempts=attempts)


def test_day_29_handoff_and_replay_cannot_extend_day_30_body_deadline(tmp_path):
    r, authority, transport = runtime(tmp_path), Authority(), Transport()
    event = r.supply(analysis_plan(), authority, "first", now=100)

    assert r.relay_once(authority, transport, now=129) == "TRANSPORT_HANDOFF"
    r.replay(event.message_id, authority, now=129.5)
    assert r.relay_once(authority, transport, now=129.5) == "TRANSPORT_HANDOFF"
    assert r.expire(now=129.999)["bodies"] == 0

    assert r.expire(now=130)["bodies"] == 1
    assert r.inspect()[0]["state"] == "EXPIRED"
    assert r.inspect()[0]["created_at"] == 100
    assert r.inspect()[0]["terminal_at"] == 130
    with pytest.raises(ValueError, match="EVENT_RETIRED"):
        r.replay(event.message_id, authority, now=130)


def test_pending_retry_and_held_share_the_immutable_creation_deadline(tmp_path):
    r, authority, transport = runtime(tmp_path), Authority(), Transport(fail=True)
    r.supply(analysis_plan(), authority, "first", now=100)
    assert [r.relay_once(authority, transport, now=value) for value in (100, 110, 120)] == [
        "RETRY",
        "RETRY",
        "HELD",
    ]
    assert r.inspect()[0]["held_at"] == 120
    assert r.expire(now=129.999)["bodies"] == 0
    assert r.expire(now=130)["bodies"] == 1
    assert r.inspect()[0]["state"] == "DELIVERY_EXPIRED"
    assert r.inspect()[0]["terminal_at"] == 130


def test_terminal_metadata_is_removed_from_first_terminal_time(tmp_path):
    r = runtime(tmp_path)
    r.supply(analysis_plan(), Authority(), "first", now=100)
    assert r.expire(now=130)["bodies"] == 1
    assert r.expire(now=219.999)["metadata"] == 0
    assert r.expire(now=220)["metadata"] == 1
    assert r.inspect() == []
    with pytest.raises(ValueError, match="ANALYSIS_REQUEST_EXPIRED"):
        r.supply(analysis_plan(), Authority(), "first", now=220)


def test_first_valid_deletion_time_is_immutable_and_tombstone_expires(tmp_path):
    path = tmp_path / "runtime.db"
    r = runtime(tmp_path)
    r.supply(analysis_plan(), Authority(), "first", now=100)
    assert r.delete_owner(UUID(int=4), deletion_epoch=2, now=110) == 1
    assert r.delete_owner(UUID(int=4), deletion_epoch=3, now=200) == 0

    with sqlite3.connect(path) as db:
        assert db.execute("SELECT epoch,deleted_at FROM core_deleted_owners").fetchone() == (
            3,
            110,
        )
    assert r.expire(now=474.999)["tombstones"] == 0
    assert r.expire(now=475)["tombstones"] == 1

    inactive = Authority(authorization(active=False))
    with pytest.raises(ValueError, match="CURRENT_AUTHORIZATION_REJECTED"):
        r.supply(analysis_plan(issued_at=475, request_id=11), inactive, "new", now=475)


def test_retired_source_counter_expires_but_source_identifier_stays_blocked(tmp_path):
    path = tmp_path / "runtime.db"
    r = runtime(tmp_path)
    r.supply(analysis_plan(), Authority(), "first", now=100)
    r.retire_source(UUID(int=2), UUID(int=3), now=200)

    assert r.expire(now=289.999)["counters"] == 0
    assert r.expire(now=290)["counters"] == 1
    with sqlite3.connect(path) as db:
        assert db.execute("SELECT COUNT(*) FROM w3_core_counters").fetchone()[0] == 0
        assert db.execute("SELECT COUNT(*) FROM core_retired_sources").fetchone()[0] == 1
    with pytest.raises(ValueError, match="SOURCE_RETIRED"):
        r.supply(analysis_plan(issued_at=290, request_id=11), Authority(), "new", now=290)


def test_backup_deadline_cannot_outlive_included_record_deadline(tmp_path):
    path = tmp_path / "backup.db"
    r = runtime(tmp_path)
    r.supply(analysis_plan(), Authority(), "first", now=100)
    r.delete_owner(UUID(int=4), deletion_epoch=2, now=110)
    result = r.backup(path, now=460)

    assert result == {"created_at": 460, "expires_at": 475}
    with sqlite3.connect(path) as db:
        settings = dict(db.execute("SELECT key,value FROM core_runtime_settings"))
    assert settings["policy_revision"] == "w3.retention/1.1"
    assert float(settings["backup_created_at"]) == 460
    assert float(settings["backup_expires_at"]) == 475


@pytest.mark.parametrize(
    ("issued_at", "error"),
    [(70, "ANALYSIS_REQUEST_EXPIRED"), (101, "ANALYSIS_REQUEST_FROM_FUTURE")],
)
def test_supply_rejects_untrusted_analysis_request_time_bounds(tmp_path, issued_at, error):
    with pytest.raises(ValueError, match=error):
        runtime(tmp_path).supply(analysis_plan(issued_at=issued_at), Authority(), "first", now=100)


def test_legacy_migration_uses_event_time_and_drops_row_without_timestamp_basis(tmp_path):
    path = tmp_path / "legacy.db"
    producer = CoreDecisionProducer(path)
    current = authorization().context
    event = producer.publish(
        requested=current,
        current=current,
        authenticated_principal="w3",
        idempotency_key="legacy",
        decision_code="CORE_REQUIRED",
        reason_code="REQUIRED_ANALYSIS_DEPENDENCY",
    )
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE core_runtime_settings (key TEXT PRIMARY KEY,value TEXT NOT NULL)")
        db.execute("INSERT INTO core_runtime_settings VALUES ('quarantined','0')")
        db.execute("CREATE TABLE core_deleted_owners (owner_hash TEXT PRIMARY KEY,epoch INTEGER)")
        db.execute(
            """CREATE TABLE core_delivery (
            key_hash TEXT PRIMARY KEY,owner_hash TEXT,epoch INTEGER,event_id TEXT UNIQUE,
            digest TEXT,request_hash TEXT,state TEXT,attempts INTEGER,next_attempt REAL,
            published_at REAL)"""
        )
        db.execute(
            "INSERT INTO core_delivery VALUES (?,?,?,?,?,?,'PENDING',0,0,NULL)",
            ("key", "owner", 1, str(event.message_id), "digest", "request"),
        )
        db.execute(
            "INSERT INTO core_delivery VALUES (?,?,?,?,?,?,'PENDING',0,0,NULL)",
            ("orphan", "owner", 1, str(UUID(int=99)), "digest", "request"),
        )

    migrated = CoreRuntime(path, policy=POLICY)
    rows = migrated.inspect()
    assert len(rows) == 1
    assert rows[0]["event_id"] == str(event.message_id)
    assert rows[0]["created_at"] == event.occurred_at.timestamp()


def test_cli_exposes_policy_and_rejects_unapproved_retention_value(tmp_path):
    import json
    import subprocess
    import sys

    approved = tmp_path / "approved.db"
    initialized = subprocess.run(
        [
            sys.executable,
            "-m",
            "w3_knowledge.core_runtime_cli",
            "init",
            "--db",
            str(approved),
            "--retention-seconds",
            "1209600",
        ],
        capture_output=True,
        text=True,
    )
    assert initialized.returncode == 0, initialized.stdout
    inspected = subprocess.run(
        [
            sys.executable,
            "-m",
            "w3_knowledge.core_runtime_cli",
            "inspect",
            "--db",
            str(approved),
            "--retention-seconds",
            "1209600",
        ],
        capture_output=True,
        text=True,
    )
    assert inspected.returncode == 0, inspected.stdout
    assert json.loads(inspected.stdout) == {
        "policy_revision": "w3.retention/1.1",
        "migration_blockers": {"owner_tombstone_without_deleted_at": 0},
        "deliveries": [],
    }

    rejected = subprocess.run(
        [
            sys.executable,
            "-m",
            "w3_knowledge.core_runtime_cli",
            "init",
            "--db",
            str(tmp_path / "rejected.db"),
            "--retention-seconds",
            "60",
        ],
        capture_output=True,
        text=True,
    )
    assert rejected.returncode == 1
    assert json.loads(rejected.stdout)["code"] == "CORE_RUNTIME_COMMAND_FAILED"
