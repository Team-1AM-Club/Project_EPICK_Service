import json
from uuid import UUID

import pytest

from w3_knowledge.core_decision import DecisionContext
from w3_knowledge.core_runtime import AnalysisPlan, Authorization, CoreRuntime
from w3_knowledge.retention import RetentionPolicy


TEST_POLICY = RetentionPolicy(
    handoff_body_seconds=60,
    private_body_max_seconds=10_000,
    terminal_metadata_seconds=10_000,
    owner_tombstone_seconds=10_000,
    retired_counter_seconds=10_000,
    backup_seconds=10_000,
)


def auth():
    return Authorization(
        context=DecisionContext(
            job_id=UUID(int=1),
            company_id=UUID(int=2),
            source_id=UUID(int=3),
            analysis_input_version="analysis-1",
        ),
        owner_id=UUID(int=4),
        owner_epoch=1,
        active=True,
    )


def plan(required=True):
    return AnalysisPlan(
        context=auth().context,
        analysis_request_id=UUID(int=10),
        analysis_request_issued_at=100,
        required_sources=[UUID(int=3)] if required else [],
        optional_sources=[] if required else [UUID(int=3)],
    )


class Authority:
    def __init__(self):
        self.value = auth()

    def current(self, job_id, source_id):
        return self.value


class Transport:
    def __init__(self):
        self.bodies = []
        self.fail = False

    def send(self, body):
        self.bodies.append(body)
        if self.fail:
            raise TimeoutError("secret should never be persisted")
        return "transport-id"


def runtime(tmp_path):
    return CoreRuntime(tmp_path / "runtime.db", policy=TEST_POLICY, max_attempts=3)


def test_supplier_core_and_noncore(tmp_path):
    r = runtime(tmp_path)
    core = r.supply(plan(), Authority(), "first", now=100)
    noncore = r.supply(plan(False), Authority(), "second", now=101)
    assert (core.decision_code, core.is_core) == ("CORE_REQUIRED", True)
    assert (noncore.decision_code, noncore.is_core) == ("NON_CORE_OPTIONAL", False)
    assert noncore.decision_version == 2


def test_unknown_and_ambiguous_dependencies_fail_without_outbox(tmp_path):
    r = runtime(tmp_path)
    for p in [
        AnalysisPlan(
            context=auth().context,
            analysis_request_id=UUID(int=10),
            analysis_request_issued_at=100,
            required_sources=[],
            optional_sources=[],
        ),
        AnalysisPlan(
            context=auth().context,
            analysis_request_id=UUID(int=10),
            analysis_request_issued_at=100,
            required_sources=[UUID(int=3)],
            optional_sources=[UUID(int=3)],
        ),
    ]:
        with pytest.raises(ValueError, match="SOURCE_DEPENDENCY_UNRESOLVED"):
            r.supply(p, Authority(), "bad", now=100)
    assert r.inspect() == []


def test_retry_restart_same_body_transport_not_acceptance(tmp_path):
    r, authority, transport = runtime(tmp_path), Authority(), Transport()
    event = r.supply(plan(), authority, "first", now=100)
    transport.fail = True
    assert r.relay_once(authority, transport, now=100) == "RETRY"
    assert r.relay_once(authority, transport, now=101) == "IDLE"
    transport.fail = False
    assert runtime(tmp_path).relay_once(authority, transport, now=200) == "TRANSPORT_HANDOFF"
    assert transport.bodies[0] == transport.bodies[1] == event.model_dump_json()
    assert r.relay_once(authority, transport, now=201) == "IDLE"
    assert r.inspect()[0]["state"] == "TRANSPORT_HANDOFF"
    assert "secret" not in json.dumps(r.inspect())


def test_deleted_owner_cannot_resend_or_recreate_and_counter_survives(tmp_path):
    r, authority, transport = runtime(tmp_path), Authority(), Transport()
    first = r.supply(plan(), authority, "first", now=100)
    assert r.delete_owner(UUID(int=4), deletion_epoch=2, now=100) == 1
    assert r.relay_once(authority, transport, now=100) == "IDLE"
    with pytest.raises(ValueError, match="OWNER_DELETED"):
        r.supply(plan(), authority, "again", now=101)
    assert transport.bodies == []
    authority.value = auth().model_copy(
        update={
            "owner_id": UUID(int=5),
            "context": auth().context.model_copy(update={"job_id": UUID(int=6)}),
        }
    )
    other = plan().model_copy(update={"context": authority.value.context})
    second = r.supply(other, authority, "other", now=102)
    assert second.decision_version == first.decision_version + 1


def test_retention_purges_body_not_idempotency_or_counter(tmp_path):
    r, authority = runtime(tmp_path), Authority()
    r.supply(plan(), authority, "first", now=100)
    r.relay_once(authority, Transport(), now=100)
    assert r.expire(now=159)["bodies"] == 0
    assert r.expire(now=160)["bodies"] == 1
    with pytest.raises(ValueError, match="EVENT_RETIRED"):
        r.supply(plan(), authority, "first", now=161)
    assert r.supply(plan(), authority, "second", now=161).decision_version == 2


def test_authority_failure_never_sends_and_inactive_purges(tmp_path):
    r, authority, transport = runtime(tmp_path), Authority(), Transport()
    r.supply(plan(), authority, "first", now=100)
    authority.value = auth().model_copy(update={"active": False})
    assert r.relay_once(authority, transport, now=100) == "BLOCKED"
    assert transport.bodies == []
    assert r.inspect()[0]["state"] == "BLOCKED"


def test_supported_backup_never_relays(tmp_path):
    r, authority, transport = runtime(tmp_path), Authority(), Transport()
    r.supply(plan(), authority, "first", now=100)
    backup = tmp_path / "backup.db"
    r.backup(backup, now=100)
    restored = CoreRuntime(backup, policy=TEST_POLICY, max_attempts=3)
    with pytest.raises(ValueError, match="RESTORE_QUARANTINED"):
        restored.relay_once(authority, transport, now=100)
    with pytest.raises(ValueError, match="RESTORE_QUARANTINED"):
        restored.supply(plan(), authority, "new", now=100)
    assert transport.bodies == []


def test_crash_after_send_replays_original_and_does_not_allocate_again(tmp_path):
    r, authority = runtime(tmp_path), Authority()
    event = r.supply(plan(), authority, "first", now=100)

    class Crash(Transport):
        def send(self, body):
            super().send(body)
            raise SystemExit("crash after remote acceptance")

    crash = Crash()
    with pytest.raises(SystemExit):
        r.relay_once(authority, crash, now=100)
    normal = Transport()
    assert runtime(tmp_path).relay_once(authority, normal, now=101) == "TRANSPORT_HANDOFF"
    assert crash.bodies == normal.bodies == [event.model_dump_json()]


def test_max_attempts_holds_then_explicit_replay_uses_same_event(tmp_path):
    r, authority, transport = runtime(tmp_path), Authority(), Transport()
    event = r.supply(plan(), authority, "first", now=100)
    transport.fail = True
    assert [r.relay_once(authority, transport, now=t) for t in [100, 200, 300]] == [
        "RETRY",
        "RETRY",
        "HELD",
    ]
    assert r.relay_once(authority, transport, now=1000) == "IDLE"
    r.replay(event.message_id, authority, now=1000)
    transport.fail = False
    assert r.relay_once(authority, transport, now=1000) == "TRANSPORT_HANDOFF"
    assert len(set(transport.bodies)) == 1


def test_authority_outage_retries_without_send(tmp_path):
    r, transport = runtime(tmp_path), Transport()
    r.supply(plan(), Authority(), "first", now=100)

    class Down:
        def current(self, *args):
            raise TimeoutError("private upstream URL")

    assert r.relay_once(Down(), transport, now=100) == "RETRY"
    assert transport.bodies == []


def test_delete_before_any_event_blocks_later_creation(tmp_path):
    r = runtime(tmp_path)
    assert r.delete_owner(UUID(int=4), deletion_epoch=2, now=100) == 0
    with pytest.raises(ValueError, match="OWNER_DELETED"):
        r.supply(plan(), Authority(), "first", now=100)


def test_real_sdk_send_contract_with_stubber(tmp_path):
    import boto3
    from botocore.stub import Stubber
    from w3_knowledge.core_runtime import SqsTransport

    client = boto3.client(
        "sqs",
        region_name="ap-northeast-2",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
    )
    queue = "https://sqs.ap-northeast-2.amazonaws.com/000000000000/synthetic"
    r, authority = runtime(tmp_path), Authority()
    event = r.supply(plan(), authority, "first", now=100)
    with Stubber(client) as stub:
        stub.add_response(
            "send_message",
            {"MessageId": "sqs-id", "ResponseMetadata": {"HTTPStatusCode": 200}},
            {"QueueUrl": queue, "MessageBody": event.model_dump_json()},
        )
        assert r.relay_once(authority, SqsTransport(client, queue), now=100) == "TRANSPORT_HANDOFF"
        stub.assert_no_pending_responses()


@pytest.mark.parametrize("optimization", [[], ["-O"]])
def test_cli_smoke_is_local_and_reports_limits(tmp_path, optimization):
    import subprocess
    import sys

    result = subprocess.run(
        [
            sys.executable,
            *optimization,
            "-m",
            "w3_knowledge.core_runtime_cli",
            "smoke",
            "--directory",
            str(tmp_path / "smoke"),
        ],
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stderr
    report = json.loads(result.stdout)
    assert report["status"] == "LOCAL_VERIFIED_NOT_DEPLOYED"
    assert report["joint_ct12"] == "NOT_RUN"
    assert report["same_body_retry"] is True


def test_interrupted_backup_never_writes_private_body_to_disk(tmp_path, monkeypatch):
    import sqlite3

    r = runtime(tmp_path)
    r.supply(plan(), Authority(), "first", now=100)
    observed = []
    original_connect = sqlite3.connect

    class InterruptedConnection(sqlite3.Connection):
        def backup(self, target, *args, **kwargs):
            super().backup(target, *args, **kwargs)
            if target.execute("PRAGMA database_list").fetchone()[2]:
                observed.append(
                    (
                        target.execute("SELECT COUNT(*) FROM w3_core_decision_outbox").fetchone()[
                            0
                        ],
                        target.execute(
                            "SELECT value FROM core_runtime_settings WHERE key='quarantined'"
                        ).fetchone()[0],
                    )
                )
                raise RuntimeError("interrupted after disk copy")

    def connect(*args, **kwargs):
        return original_connect(*args, **kwargs, factory=InterruptedConnection)

    monkeypatch.setattr(sqlite3, "connect", connect)
    with pytest.raises(RuntimeError):
        r.backup(tmp_path / "backup.db", now=100)
    assert observed == [(0, "1")]


def test_deletion_serializes_with_inflight_send(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Event

    r, authority = runtime(tmp_path), Authority()
    r.supply(plan(), authority, "first", now=100)
    entered, release, deleting = Event(), Event(), Event()

    class Slow(Transport):
        def send(self, body):
            entered.set()
            assert release.wait(5)
            return super().send(body)

    transport = Slow()

    def delete():
        deleting.set()
        return runtime(tmp_path).delete_owner(UUID(int=4), deletion_epoch=2, now=100)

    with ThreadPoolExecutor(max_workers=2) as pool:
        sending = pool.submit(r.relay_once, authority, transport, now=100)
        assert entered.wait(5)
        deletion = pool.submit(delete)
        assert deleting.wait(5)
        release.set()
        assert sending.result() == "TRANSPORT_HANDOFF"
        assert deletion.result() == 1
    assert r.relay_once(authority, transport, now=200) == "IDLE"
    assert r.inspect()[0]["state"] == "DELETED"


def test_owner_epoch_change_blocks_saved_event(tmp_path):
    r, authority, transport = runtime(tmp_path), Authority(), Transport()
    r.supply(plan(), authority, "first", now=100)
    authority.value = auth().model_copy(update={"owner_epoch": 2})
    assert r.relay_once(authority, transport, now=100) == "BLOCKED"
    assert transport.bodies == []


def test_stale_delete_cannot_purge_active_owner(tmp_path):
    r = runtime(tmp_path)
    r.supply(plan(), Authority(), "first", now=100)
    with pytest.raises(ValueError, match="STALE_DELETION_EPOCH"):
        r.delete_owner(UUID(int=4), deletion_epoch=1, now=100)
    assert r.relay_once(Authority(), Transport(), now=100) == "TRANSPORT_HANDOFF"


def test_supplier_failure_rolls_back_original_and_counter(tmp_path, monkeypatch):
    r = runtime(tmp_path)
    original = r.producer.publish

    def fail(**kwargs):
        original(**kwargs)
        raise RuntimeError("simulate metadata persistence failure")

    monkeypatch.setattr(r.producer, "publish", fail)
    with pytest.raises(RuntimeError):
        r.supply(plan(), Authority(), "first", now=100)
    assert r.inspect() == []
    monkeypatch.setattr(r.producer, "publish", original)
    assert r.supply(plan(), Authority(), "first", now=100).decision_version == 1


def test_inspection_digest_matches_w1_canonical_event(tmp_path):
    import hashlib

    r = runtime(tmp_path)
    event = r.supply(plan(), Authority(), "first", now=100)
    canonical = json.dumps(
        json.loads(event.model_dump_json()),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    )
    assert r.inspect()[0]["digest"] == hashlib.sha256(canonical.encode()).hexdigest()


@pytest.mark.parametrize(
    "expected_role,allowed", [("AROATESTROLE", True), ("AROAOTHERROLE", False)]
)
def test_cli_checks_actual_sts_role_before_sqs_client(monkeypatch, expected_role, allowed):
    import boto3
    from botocore.stub import Stubber
    from w3_knowledge.core_runtime_cli import aws_transport

    client = boto3.client(
        "sts",
        region_name="ap-northeast-2",
        aws_access_key_id="synthetic",
        aws_secret_access_key="synthetic",
    )
    monkeypatch.setenv("AWS_DEFAULT_REGION", "ap-northeast-2")
    monkeypatch.setenv(
        "W3_CORE_DECISION_QUEUE_URL",
        "https://sqs.ap-northeast-2.amazonaws.com/000000000000/synthetic",
    )
    monkeypatch.setenv("W3_CORE_DECISION_EXPECTED_ROLE_ID", expected_role)
    created = []

    class Session:
        def client(self, service, **kwargs):
            created.append(service)
            return client

    monkeypatch.setattr(boto3, "Session", lambda **kwargs: Session())
    with Stubber(client) as stub:
        stub.add_response(
            "get_caller_identity",
            {
                "UserId": "AROATESTROLE:session",
                "Account": "000000000000",
                "Arn": "arn:aws:sts::000000000000:assumed-role/synthetic/session",
            },
        )
        if allowed:
            aws_transport()
            assert created == ["sts", "sqs"]
        else:
            with pytest.raises(ValueError, match="WORKLOAD_ROLE_MISMATCH"):
                aws_transport()
            assert created == ["sts"]
