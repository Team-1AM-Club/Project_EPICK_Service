"""Verify W2 PostgreSQL outbox delivery against a pinned local W3 checkout.

This is a synthetic, isolated integration probe. It neither crawls a real
source nor approves a production W3 retention TTL or deployment topology.
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker

from epick_engine.source_collection.collector import (
    StaticFetchRequest,
    StaticFetchResult,
    StaticResponseCandidate,
)
from epick_engine.source_collection.contracts import SourceEvent, SourceType
from epick_engine.source_collection.parsing import extract_static_candidate
from epick_engine.source_collection.persistence import (
    CollectionRuntimeAttempt,
    Company,
    Evidence,
    OutboxEvent,
    Source,
    SourcePolicyDecision,
    SourceVersion,
)
from epick_engine.source_collection.policy import Representation, UntrustedDocument, ValidatedTarget
from epick_engine.source_collection.private_deletion_v2 import PrivateDeletionScope
from epick_engine.source_collection.source_runtime import handle_collection_dispatch
from epick_engine.source_collection.source_runtime_input import (
    RuntimeSourceConfigFile,
    SqlAlchemyCollectionInputProvider,
)
from epick_engine.source_collection.w1_private_authority_contracts import (
    CurrentWriteScopeLookupResponse,
    PrivateWriteAuthorityResponse,
    W1PrivateBinding,
)
from epick_engine.source_collection.w1_transport import (
    LookupResponse,
    W1Dispatch,
    parse_w1_dispatch,
)
from epick_engine.source_collection.w3_public_transport import source_event_to_w3_wire

ROOT = Path(__file__).resolve().parents[1]
FIXTURE_NAMES = (
    "version-available",
    "version-partial",
    "observation-changed",
    "restriction-changed",
)


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def _get_json(url: str, *, token: str | None = None) -> dict[str, object]:
    headers = {"Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    with urllib.request.urlopen(
        urllib.request.Request(url, headers=headers), timeout=3
    ) as response:
        return json.load(response)


def _post_json(url: str, *, token: str, value: dict[str, object]) -> dict[str, object]:
    request = urllib.request.Request(
        url,
        data=json.dumps(value).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        },
        method="POST",
    )
    with urllib.request.urlopen(request, timeout=3) as response:
        return json.load(response)


def _wait_ready(process: subprocess.Popen[bytes], url: str, *, token: str | None = None) -> None:
    deadline = time.monotonic() + 15
    last_error: Exception | None = None
    while time.monotonic() < deadline:
        if process.poll() is not None:
            error = process.stderr.read(1000) if process.stderr is not None else b""
            raise RuntimeError(f"server exited before readiness: {process.returncode}: {error!r}")
        try:
            _get_json(url, token=token)
            return
        except (urllib.error.URLError, TimeoutError) as error:
            last_error = error
            time.sleep(0.1)
    raise RuntimeError(f"local server did not become ready: {last_error!r}")


def _start(
    command: list[str], *, cwd: Path, environment: dict[str, str]
) -> subprocess.Popen[bytes]:
    return subprocess.Popen(
        command,
        cwd=cwd,
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )


def _stop(process: subprocess.Popen[bytes] | None) -> None:
    if process is None or process.poll() is not None:
        return
    process.terminate()
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _worker(environment: dict[str, str], *args: str) -> tuple[int, dict[str, object]]:
    completed = subprocess.run(
        [sys.executable, "-m", "epick_engine.source_collection.w3_outbox_operator", *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


def _recovery_worker(environment: dict[str, str], *args: str) -> tuple[int, dict[str, object]]:
    completed = subprocess.run(
        [sys.executable, "-m", "epick_engine.source_collection.w3_recovery_operator", *args],
        cwd=ROOT,
        env=environment,
        capture_output=True,
        text=True,
        timeout=20,
        check=False,
    )
    return completed.returncode, json.loads(completed.stdout)


def _w3_command(python: Path, db_path: Path, port: int) -> list[str]:
    return [
        str(python),
        "-m",
        "w3_knowledge.c01.http",
        "--db",
        str(db_path),
        "--port",
        str(port),
        "--restriction-scope",
        "version",
        "--max-ttl-seconds",
        "300",
        "--source-authority",
        "w3_knowledge.c01.source_authority_http:create_source_authority",
    ]


def _fixtures(w3_checkout: Path) -> list[SourceEvent]:
    directory = w3_checkout / "contracts" / "c01" / "v0.2-candidate" / "fixtures"
    events = []
    for name in FIXTURE_NAMES:
        envelope = json.loads((directory / f"source-event-{name}.json").read_text())
        payload = envelope["payload"]
        if name in {"observation-changed", "restriction-changed"}:
            payload.pop("schema_version")
        events.append(
            SourceEvent.model_validate(
                {
                    "event_id": envelope["event_id"],
                    "event_type": envelope["event_type"],
                    "schema_version": "w2.source.v1",
                    "aggregate_id": envelope["aggregate_id"],
                    "aggregate_revision": envelope["revision"],
                    "occurred_at": envelope["occurred_at"],
                    "payload": payload,
                }
            )
        )
    assert [event.aggregate_revision for event in events] == [1, 2, 3, 4]
    return events


def _seed(session_factory: sessionmaker, events: list[SourceEvent]) -> None:
    first = events[0]
    payload = first.payload
    with session_factory.begin() as session:
        session.add(
            Company(
                company_id=payload.company_id,
                legal_name="Synthetic W2-W3 Integration Company",
                aliases=[],
                official_domains=["example.test"],
                legal_identifiers={"synthetic": "w2-w3-e2e"},
                identity_status="verified",
                identity_evidence=["synthetic://w2-w3-e2e"],
            )
        )
        session.add(
            Source(
                source_id=first.aggregate_id,
                company_id=payload.company_id,
                source_type=payload.source_type.value,
                canonical_url=str(payload.url_or_path),
                title=payload.title,
            )
        )
    with session_factory.begin() as session:
        for event in events:
            session.add(_outbox_row(event))


def _outbox_row(event: SourceEvent) -> OutboxEvent:
    return OutboxEvent(
        event_id=event.event_id,
        aggregate_id=event.aggregate_id,
        aggregate_revision=event.aggregate_revision,
        event_type=event.event_type.value,
        schema_version=event.schema_version,
        payload=event.payload.model_dump(mode="json"),
        occurred_at=event.occurred_at,
        delivery_state="pending",
    )


def _later_observation(base: SourceEvent, revision: int) -> SourceEvent:
    occurred_at = base.occurred_at + timedelta(minutes=revision * 5)
    payload = base.payload.model_dump(mode="json")
    payload.update(observation_id=str(uuid4()), observed_at=occurred_at.isoformat())
    return SourceEvent.model_validate(
        {
            "event_id": uuid4(),
            "event_type": "source.observation.changed",
            "schema_version": "w2.source.v1",
            "aggregate_id": base.aggregate_id,
            "aggregate_revision": revision,
            "occurred_at": occurred_at,
            "payload": payload,
        }
    )


def _index_request(
    source_id: UUID,
    status: dict[str, object],
    *,
    evidence_id: UUID,
    text_excerpt: str,
) -> dict[str, object]:
    return {
        "schema_version": "w3-c01/0.2-candidate",
        "source_id": str(source_id),
        "event_cursor": status["event_cursor"],
        "restriction_revision": status["restriction_revision"],
        "index_key": status["index_key"],
        "retention_scope": "excerpts_only",
        "expires_at": (datetime.now(UTC) + timedelta(seconds=120)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "documents": [
            {
                "document_id": str(uuid4()),
                "evidence_id": str(evidence_id),
                "text": text_excerpt,
            }
        ],
    }


def _states(session_factory: sessionmaker) -> list[str]:
    with session_factory() as session:
        return list(
            session.scalars(
                select(OutboxEvent.delivery_state).order_by(OutboxEvent.aggregate_revision)
            )
        )


def _collect_product_source(session_factory: sessionmaker[Session]) -> dict[str, object]:
    """Run the deployed W2 collection handler with only W1 and fetch I/O substituted."""

    source_id, company_id, command_id = uuid4(), uuid4(), uuid4()
    source_url = "https://synthetic-meridian-careers.test/jobs/static-posting"
    document = (
        ROOT / "tests" / "fixtures" / "synthetic_sources" / "static_posting.html"
    ).read_text(encoding="utf-8")
    now = datetime.now(UTC)
    with session_factory.begin() as session:
        session.add(
            Company(
                company_id=company_id,
                legal_name="Synthetic Meridian Careers Ltd.",
                aliases=[],
                official_domains=["synthetic-meridian-careers.test"],
                legal_identifiers={},
                identity_status="verified",
                identity_evidence=["fixture:static_posting"],
            )
        )
        session.add(
            Source(
                source_id=source_id,
                company_id=company_id,
                source_type=SourceType.JOB_POSTING.value,
                canonical_url=source_url,
                title="Synthetic static posting",
            )
        )
        session.add(
            SourcePolicyDecision(
                policy_decision_id=uuid4(),
                source_id=source_id,
                revision=1,
                official_status="verified",
                access_class="public",
                collection_permission="allowed",
                excerpt_storage_permission="allowed",
                body_storage_permission="denied",
                redistribution_permission="unknown",
                evidence_refs=["fixture:static_posting"],
                checked_at=now,
                policy_version="synthetic.product-e2e.v1",
            )
        )

    raw_dispatch = json.loads(
        (
            ROOT / "tests" / "fixtures" / "w1_private_contract" / "private-w2-command-dispatch.json"
        ).read_text(encoding="utf-8")
    )
    raw_dispatch["message_id"] = str(command_id)
    raw_dispatch["payload"].update(
        command_id=str(command_id),
        job_id=str(uuid4()),
        authenticated_owner_ref=str(uuid4()),
        company_id=str(company_id),
        source_id=str(source_id),
    )
    raw_dispatch["lookup_request"]["command_id"] = str(command_id)
    raw_dispatch["core_decision_pin"].update(
        company_id=str(company_id), source_id=str(source_id), decision_id=str(uuid4())
    )
    dispatch = parse_w1_dispatch(raw_dispatch)
    config = RuntimeSourceConfigFile.model_validate(
        {
            "schema_version": "w2.source-runtime-config.v1",
            "claim_lease_seconds": 120,
            "sources": {
                str(source_id): {
                    "policy_revision": 1,
                    "robots_permission": "allowed",
                    "result_version": 1,
                    "language": "en",
                    "redirect_robots_permissions": [],
                    "limits": {
                        "site_concurrency": 1,
                        "global_concurrency": 2,
                        "source_ttl_seconds": 300,
                        "max_response_bytes": 1_048_576,
                        "max_decompressed_bytes": 2_097_152,
                        "connect_timeout_seconds": 3.0,
                        "read_timeout_seconds": 5.0,
                        "max_redirects": 2,
                        "general_retry_limit": 0,
                        "retention_days": 7,
                    },
                }
            },
        }
    )

    class _AvailableLookup:
        def lookup_dispatch(self, received: W1Dispatch) -> LookupResponse:
            assert received == dispatch
            return LookupResponse(
                schema_version="w1.private.command-lookup.v1",
                command_id=received.payload.command_id,
                status="AVAILABLE",
                reason_code=None,
                command=received.payload,
            )

    class _SyntheticAccountAuthority:
        def _payload(self, binding: W1PrivateBinding) -> dict[str, object]:
            assert binding == W1PrivateBinding.from_collection(dispatch.payload)
            return {
                "owner_user_id": binding.owner_user_id,
                "owner_deletion_epoch": binding.owner_deletion_epoch,
                "command_id": binding.command_id,
                "job_id": binding.job_id,
                "execution_fence": binding.execution_fence,
            }

        def lookup_current_scope(
            self,
            binding: W1PrivateBinding,
        ) -> CurrentWriteScopeLookupResponse:
            return CurrentWriteScopeLookupResponse.model_validate(
                {
                    "schema_version": "w1.private.w2-current-write-scope-lookup.v1",
                    **self._payload(binding),
                    "scope": {"type": "ACCOUNT"},
                },
                strict=True,
            )

        def authorize_write(
            self,
            binding: W1PrivateBinding,
            scope: PrivateDeletionScope,
        ) -> PrivateWriteAuthorityResponse:
            assert scope == PrivateDeletionScope(kind="ACCOUNT", project_id=None)
            return PrivateWriteAuthorityResponse.model_validate(
                {
                    "schema_version": "w1.private.w2-write-authority.v1",
                    **self._payload(binding),
                    "scope": {"type": "ACCOUNT"},
                    "authority_ref": "synthetic:w2-w3-postgres-http",
                },
                strict=True,
            )

    class _FixtureCollector:
        def __init__(self) -> None:
            self.fetches = 0
            self.closed = False

        def fetch(
            self, request: StaticFetchRequest, *, is_cancelled: Callable[[], bool]
        ) -> StaticFetchResult:
            assert not is_cancelled()
            self.fetches += 1
            return StaticFetchResult(
                command_id=request.command_id,
                candidate=StaticResponseCandidate(
                    final_target=ValidatedTarget(
                        url=source_url,
                        hostname="synthetic-meridian-careers.test",
                        port=443,
                        resolved_addresses=frozenset({"198.51.100.20"}),
                    ),
                    representation=Representation.HTML,
                    document=UntrustedDocument(text=document),
                    http_status=200,
                    raw_size=len(document.encode("utf-8")),
                    decompressed_size=len(document.encode("utf-8")),
                ),
                failure_code=None,
            )

        def close(self) -> None:
            self.closed = True

    collector = _FixtureCollector()
    proposal = handle_collection_dispatch(
        dispatch,
        session_factory=session_factory,
        lookup_client=_AvailableLookup(),
        private_authority_client=_SyntheticAccountAuthority(),
        input_provider=SqlAlchemyCollectionInputProvider(session_factory, config),
        collector_factory=lambda: collector,
        parser=extract_static_candidate,
        runtime_config=config,
        clock=lambda: now,
        uuid_factory=uuid4,
    )
    assert collector.fetches == 1 and collector.closed
    assert not proposal.result.failures
    with session_factory() as session:
        source = session.get(Source, source_id)
        attempt = session.get(CollectionRuntimeAttempt, command_id)
        versions = session.scalars(
            select(SourceVersion).where(SourceVersion.source_id == source_id)
        ).all()
        evidence = session.scalars(
            select(Evidence).where(Evidence.source_version_id == versions[0].source_version_id)
        ).all()
        events = session.scalars(
            select(OutboxEvent).where(OutboxEvent.aggregate_id == source_id)
        ).all()
        assert source is not None and source.current_source_version_id is None
        assert attempt is not None and attempt.state == "PERSISTED"
        assert len(versions) == 1 and len(evidence) > 0 and len(events) == 1
        assert any("AWS experience is required." in row.text_excerpt for row in evidence)
        assert events[0].event_type == "source.version.available"
        assert events[0].aggregate_revision == 1 and events[0].delivery_state == "pending"
        assert str(dispatch.payload.authenticated_owner_ref) not in json.dumps(events[0].payload)
    return {
        "source_id": str(source_id),
        "source_version_id": str(versions[0].source_version_id),
        "evidence_count": len(evidence),
        "outbox_event_id": str(events[0].event_id),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--w3-checkout", type=Path, required=True)
    parser.add_argument("--w3-sha", required=True)
    parser.add_argument("--w2-code-sha", required=True)
    args = parser.parse_args()
    w3_checkout = args.w3_checkout.resolve(strict=True)
    w3_python = w3_checkout / ".venv" / "Scripts" / "python.exe"
    if not w3_python.is_file():
        raise ValueError("W3 locked virtual environment is required")
    pinned = subprocess.check_output(
        ["git", "-c", f"safe.directory={w3_checkout}", "-C", str(w3_checkout), "rev-parse", "HEAD"],
        text=True,
    ).strip()
    if pinned != args.w3_sha:
        raise ValueError("W3 checkout SHA does not match the requested pin")
    w3_dirty = subprocess.check_output(
        [
            "git",
            "-c",
            f"safe.directory={w3_checkout}",
            "-C",
            str(w3_checkout),
            "status",
            "--porcelain",
            "--",
            "src",
        ],
        text=True,
    ).strip()
    if w3_dirty:
        raise ValueError("W3 source checkout has uncommitted changes")
    w2_git = ["git", "-c", f"safe.directory={ROOT}", "-C", str(ROOT)]
    for revision_args in (
        ["diff", "--quiet", args.w2_code_sha, "HEAD", "--", "src", "migrations"],
        ["diff", "--quiet", "--", "src", "migrations"],
    ):
        if subprocess.run(w2_git + revision_args, check=False).returncode:
            raise ValueError("W2 source or migration does not match the requested code pin")

    raw_url = os.environ.get("EPICK_TEST_DATABASE_URL", "")
    url = make_url(raw_url)
    if (
        os.environ.get("EPICK_TEST_DATABASE_APPROVED") != "1"
        or not url.drivername.startswith("postgresql")
        or url.host not in {"127.0.0.1", "localhost", "::1"}
        or not url.database
        or raw_url == os.environ.get("EPICK_DATABASE_URL")
    ):
        raise ValueError("approved isolated loopback PostgreSQL test URL is required")

    schema = f"epick_w2_w3_e2e_{uuid4().hex}"
    admin = create_engine(url, pool_pre_ping=True)
    authority: subprocess.Popen[bytes] | None = None
    w3: subprocess.Popen[bytes] | None = None
    recovery_w3s: list[subprocess.Popen[bytes]] = []
    scoped_engine = None
    runtime_dir: tempfile.TemporaryDirectory[str] | None = None
    created = False
    try:
        with admin.begin() as connection:
            connection.execute(text(f'CREATE SCHEMA "{schema}"'))
        created = True
        scoped_url = url.set(query={**url.query, "options": f"-csearch_path={schema}"})
        db_url = scoped_url.render_as_string(hide_password=False)
        base_env = {**os.environ, "EPICK_DATABASE_URL": db_url}
        migration = subprocess.run(
            [sys.executable, "-m", "alembic", "upgrade", "head"],
            cwd=ROOT,
            env=base_env,
            capture_output=True,
            text=True,
            timeout=45,
            check=False,
        )
        if migration.returncode:
            raise RuntimeError("isolated W2 Alembic migration failed")

        scoped_engine = create_engine(scoped_url, pool_pre_ping=True)
        session_factory = sessionmaker(scoped_engine, expire_on_commit=False)
        events = _fixtures(w3_checkout)
        _seed(session_factory, events)
        assert _states(session_factory) == ["pending"] * 4

        authority_port, w3_port = _free_port(), _free_port()
        authority_token = secrets.token_urlsafe(32)
        w2_token = secrets.token_urlsafe(32)
        operator_token = secrets.token_urlsafe(32)
        w4_token = secrets.token_urlsafe(32)
        w2_env = {
            **base_env,
            "EPICK_W2_SOURCE_AUTHORITY_TOKEN": authority_token,
            "EPICK_W3_EVENT_ENDPOINT": f"http://127.0.0.1:{w3_port}/c01/v1/events",
            "EPICK_W3_W2_TOKEN": w2_token,
        }
        w3_env = {
            **os.environ,
            "W3_SOURCE_AUTHORITY_ENDPOINT": f"http://127.0.0.1:{authority_port}",
            "W3_SOURCE_AUTHORITY_TOKEN": authority_token,
            "W3_W2_TOKEN": w2_token,
            "W3_OPERATOR_TOKEN": operator_token,
            "W3_W4_TOKEN": w4_token,
        }
        runtime_dir = tempfile.TemporaryDirectory(prefix="epick-w2-w3-")
        if runtime_dir is not None:
            db_path = Path(runtime_dir.name) / "w3-c01.sqlite"
            w3_command = _w3_command(w3_python, db_path, w3_port)
            w3 = _start(w3_command, cwd=w3_checkout, environment=w3_env)
            _wait_ready(w3, f"http://127.0.0.1:{w3_port}/health")

            failed_code, failed_response = _worker(w2_env, "--limit", "25")
            assert failed_code == 1 and failed_response == {"status": "W3_OUTBOX_FAILED"}
            assert _states(session_factory) == ["pending"] * 4

            authority = _start(
                [
                    sys.executable,
                    "-m",
                    "uvicorn",
                    "epick_engine.source_collection.source_authority_operator:create_app",
                    "--factory",
                    "--host",
                    "127.0.0.1",
                    "--port",
                    str(authority_port),
                ],
                cwd=ROOT,
                environment=w2_env,
            )
            _wait_ready(
                authority,
                f"http://127.0.0.1:{authority_port}/internal/v1/sources/"
                f"{events[0].aggregate_id}/authority",
                token=authority_token,
            )
            authority_result = _get_json(
                f"http://127.0.0.1:{authority_port}/internal/v1/sources/"
                f"{events[0].aggregate_id}/authority",
                token=authority_token,
            )
            assert authority_result == {
                "source_id": str(events[0].aggregate_id),
                "registered": True,
            }
            absent = _get_json(
                f"http://127.0.0.1:{authority_port}/internal/v1/sources/{uuid4()}/authority",
                token=authority_token,
            )
            assert absent["registered"] is False

            code, response = _worker(w2_env, "--limit", "25")
            assert code == 0 and response == {"status": "DELIVERED", "count": 4}
            assert _states(session_factory) == ["delivered"] * 4
            status_url = f"http://127.0.0.1:{w3_port}/c01/v1/status/{events[0].aggregate_id}"
            status = _get_json(status_url, token=w2_token)
            assert status["event_cursor"] == 4
            assert status["restriction_revision"] == 1
            assert status["index_ack"] is False

            replay_code, replay = _worker(w2_env, "--replay-event-id", str(events[0].event_id))
            assert replay_code == 0 and replay == {"status": "REPLAYED", "count": 1}
            assert _get_json(status_url, token=w2_token)["event_cursor"] == 4
            assert _states(session_factory) == ["delivered"] * 4

            _stop(w3)
            w3 = _start(w3_command, cwd=w3_checkout, environment=w3_env)
            _wait_ready(w3, f"http://127.0.0.1:{w3_port}/health")
            restarted = _get_json(status_url, token=w2_token)
            assert restarted["event_cursor"] == 4
            assert restarted["restriction_revision"] == 1
            assert restarted["index_ack"] is False

            replacement_id = uuid4()
            cleared_at = events[3].occurred_at + timedelta(minutes=5)
            cleared_payload = events[3].payload.model_dump(mode="json")
            cleared_payload.update(
                restriction_revision=2,
                restriction_status="cleared",
                accuracy_status="verified_in_scope",
                changed_at=cleared_at.isoformat(),
                replacement_ref=str(replacement_id),
            )
            cleared = SourceEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "event_type": "source.restriction.changed",
                    "schema_version": "w2.source.v1",
                    "aggregate_id": events[0].aggregate_id,
                    "aggregate_revision": 5,
                    "occurred_at": cleared_at,
                    "payload": cleared_payload,
                }
            )
            with session_factory.begin() as session:
                session.add(_outbox_row(cleared))
            assert _states(session_factory) == ["delivered"] * 4 + ["pending"]
            unregistered_code, unregistered_response = _worker(w2_env, "--limit", "25")
            assert unregistered_code == 1
            assert unregistered_response == {"status": "W3_OUTBOX_FAILED"}
            assert _states(session_factory) == ["delivered"] * 4 + ["pending"]
            assert _get_json(status_url, token=w2_token)["event_cursor"] == 4

            with session_factory.begin() as session:
                session.add(
                    Source(
                        source_id=replacement_id,
                        company_id=events[0].payload.company_id,
                        source_type=events[0].payload.source_type.value,
                        canonical_url=f"https://synthetic.example/replacement/{replacement_id}",
                        title="Synthetic replacement Source",
                    )
                )
            cleared_code, cleared_response = _worker(w2_env, "--limit", "25")
            assert cleared_code == 0
            assert cleared_response == {"status": "DELIVERED", "count": 1}
            assert _states(session_factory) == ["delivered"] * 5
            cleared_status = _get_json(status_url, token=w2_token)
            assert cleared_status["event_cursor"] == 5
            assert cleared_status["restriction_revision"] == 2
            assert cleared_status["index_ack"] is False

            seventh = _later_observation(events[2], 7)
            with session_factory.begin() as session:
                session.add(_outbox_row(seventh))
            gap_code, gap_response = _worker(w2_env, "--limit", "25")
            assert gap_code == 0 and gap_response == {"status": "DELIVERED", "count": 1}
            gap_status = _get_json(status_url, token=w2_token)
            assert gap_status["event_cursor"] == 5
            assert gap_status["required_event_cursor"] == 7
            assert gap_status["reason"] == "EVENT_GAP"
            assert gap_status["index_ack"] is False

            sixth = _later_observation(events[2], 6)
            with session_factory.begin() as session:
                session.add(_outbox_row(sixth))
            recovery_code, recovery_response = _worker(w2_env, "--limit", "25")
            assert recovery_code == 0
            assert recovery_response == {"status": "DELIVERED", "count": 1}
            recovered = _get_json(status_url, token=w2_token)
            assert recovered["event_cursor"] == 7
            assert recovered["required_event_cursor"] == 7
            assert recovered["restriction_revision"] == 2
            assert _states(session_factory) == ["delivered"] * 7

            index_source_id, index_version_id, index_evidence_id = uuid4(), uuid4(), uuid4()
            index_url = f"https://synthetic.example/jobs/index-{index_source_id}"
            allowed_payload = events[0].payload.model_dump(mode="json")
            allowed_payload.update(
                source_id=str(index_source_id),
                source_version_id=str(index_version_id),
                extraction_revision_id=str(uuid4()),
                url_or_path=index_url,
            )
            allowed_payload["policy"].update(
                policy_decision_id=str(uuid4()), redistribution_permission="allowed"
            )
            allowed_payload["evidence_spans"][0].update(
                evidence_id=str(index_evidence_id), source_version_id=str(index_version_id)
            )
            allowed_payload["posting_sections"][0]["evidence_ids"] = [str(index_evidence_id)]
            allowed = SourceEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "event_type": "source.version.available",
                    "schema_version": "w2.source.v1",
                    "aggregate_id": index_source_id,
                    "aggregate_revision": 1,
                    "occurred_at": events[0].occurred_at,
                    "payload": allowed_payload,
                }
            )
            with session_factory.begin() as session:
                session.add(
                    Source(
                        source_id=index_source_id,
                        company_id=events[0].payload.company_id,
                        source_type=events[0].payload.source_type.value,
                        canonical_url=index_url,
                        title="Synthetic indexable Source",
                    )
                )
            with session_factory.begin() as session:
                session.add(_outbox_row(allowed))
            allowed_code, allowed_response = _worker(w2_env, "--limit", "25")
            assert allowed_code == 0
            assert allowed_response == {"status": "DELIVERED", "count": 1}
            indexed_status_url = f"http://127.0.0.1:{w3_port}/c01/v1/status/{index_source_id}"
            index_url_http = f"http://127.0.0.1:{w3_port}/c01/v1/index"
            search_url = f"http://127.0.0.1:{w3_port}/c01/v1/search?q=cloud"
            index_status = _get_json(indexed_status_url, token=operator_token)
            assert index_status["reason"] == "INDEX_PENDING"
            evidence = allowed.payload.evidence_spans[0]
            index_request = _index_request(
                index_source_id,
                index_status,
                evidence_id=evidence.evidence_id,
                text_excerpt=evidence.text_excerpt,
            )
            first_index = _post_json(index_url_http, token=operator_token, value=index_request)
            assert first_index["index_ack"] is True
            assert len(_get_json(search_url, token=operator_token)["results"]) == 1

            restriction_id = uuid4()
            active_at = datetime.now(UTC)
            active_payload = events[3].payload.model_dump(mode="json")
            active_payload.update(
                restriction_id=str(restriction_id),
                source_id=str(index_source_id),
                source_version_id=str(index_version_id),
                restriction_revision=1,
                changed_at=active_at.isoformat(),
                replacement_ref=None,
            )
            active = SourceEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "event_type": "source.restriction.changed",
                    "schema_version": "w2.source.v1",
                    "aggregate_id": index_source_id,
                    "aggregate_revision": 2,
                    "occurred_at": active_at,
                    "payload": active_payload,
                }
            )
            with session_factory.begin() as session:
                session.add(_outbox_row(active))
            active_code, active_response = _worker(w2_env, "--limit", "25")
            assert active_code == 0
            assert active_response == {"status": "DELIVERED", "count": 1}
            blocked = _get_json(indexed_status_url, token=operator_token)
            assert blocked["index_ack"] is False
            assert blocked["reason"] == "RESTRICTED"
            assert _get_json(search_url, token=operator_token)["results"] == []

            release_at = active_at + timedelta(seconds=1)
            release_payload = dict(active_payload)
            release_payload.update(
                restriction_revision=2,
                restriction_status="cleared",
                accuracy_status="verified_in_scope",
                changed_at=release_at.isoformat(),
            )
            release = SourceEvent.model_validate(
                {
                    "event_id": uuid4(),
                    "event_type": "source.restriction.changed",
                    "schema_version": "w2.source.v1",
                    "aggregate_id": index_source_id,
                    "aggregate_revision": 3,
                    "occurred_at": release_at,
                    "payload": release_payload,
                }
            )
            with session_factory.begin() as session:
                session.add(_outbox_row(release))
            release_code, release_response = _worker(w2_env, "--limit", "25")
            assert release_code == 0
            assert release_response == {"status": "DELIVERED", "count": 1}
            pending_index = _get_json(indexed_status_url, token=operator_token)
            assert pending_index["reason"] == "INDEX_PENDING"
            assert pending_index["index_ack"] is False
            assert _get_json(search_url, token=operator_token)["results"] == []
            second_index = _post_json(
                index_url_http,
                token=operator_token,
                value=_index_request(
                    index_source_id,
                    pending_index,
                    evidence_id=evidence.evidence_id,
                    text_excerpt=evidence.text_excerpt,
                ),
            )
            assert second_index["index_ack"] is True
            assert len(_get_json(search_url, token=operator_token)["results"]) == 1
            assert _states(session_factory) == ["delivered"] * 10

            replay_port = _free_port()
            replay_w3 = _start(
                _w3_command(
                    w3_python,
                    Path(runtime_dir.name) / "w3-recovery-replay.sqlite",
                    replay_port,
                ),
                cwd=w3_checkout,
                environment=w3_env,
            )
            recovery_w3s.append(replay_w3)
            _wait_ready(replay_w3, f"http://127.0.0.1:{replay_port}/health")
            replay_status_url = (
                f"http://127.0.0.1:{replay_port}/c01/v1/status/{events[0].aggregate_id}"
            )
            _post_json(
                f"http://127.0.0.1:{replay_port}/c01/v1/events",
                token=w2_token,
                value=source_event_to_w3_wire(seventh),
            )
            replay_gap = _get_json(replay_status_url, token=w2_token)
            assert replay_gap["event_cursor"] == 0
            assert replay_gap["required_event_cursor"] == 7
            assert replay_gap["reason"] == "EVENT_GAP"
            states_before_recovery = _states(session_factory)
            replay_env = {
                **w2_env,
                "EPICK_W3_EVENT_ENDPOINT": f"http://127.0.0.1:{replay_port}/c01/v1/events",
            }
            replay_code, replay_result = _recovery_worker(
                replay_env,
                "--source-id",
                str(events[0].aggregate_id),
                "--mode",
                "replay",
            )
            assert replay_code == 0 and replay_result == {
                "status": "REPLAYED",
                "source_id": str(events[0].aggregate_id),
                "event_cursor": 7,
            }
            replay_recovered = _get_json(replay_status_url, token=w2_token)
            assert replay_recovered["event_cursor"] == 7
            assert replay_recovered["required_event_cursor"] == 7
            assert replay_recovered["restriction_revision"] == 2
            assert _states(session_factory) == states_before_recovery

            snapshot_port = _free_port()
            snapshot_w3 = _start(
                _w3_command(
                    w3_python,
                    Path(runtime_dir.name) / "w3-recovery-snapshot.sqlite",
                    snapshot_port,
                ),
                cwd=w3_checkout,
                environment=w3_env,
            )
            recovery_w3s.append(snapshot_w3)
            _wait_ready(snapshot_w3, f"http://127.0.0.1:{snapshot_port}/health")
            snapshot_status_url = (
                f"http://127.0.0.1:{snapshot_port}/c01/v1/status/{index_source_id}"
            )
            snapshot_env = {
                **w2_env,
                "EPICK_W3_EVENT_ENDPOINT": f"http://127.0.0.1:{snapshot_port}/c01/v1/events",
            }
            initial_snapshot_status = _get_json(snapshot_status_url, token=w2_token)
            assert initial_snapshot_status["event_cursor"] == 0
            snapshot_code, snapshot_result = _recovery_worker(
                snapshot_env,
                "--source-id",
                str(index_source_id),
                "--mode",
                "snapshot",
            )
            assert snapshot_code == 0 and snapshot_result == {
                "status": "SNAPSHOT_APPLIED",
                "source_id": str(index_source_id),
                "event_cursor": 3,
            }
            snapshot_status = _get_json(snapshot_status_url, token=operator_token)
            assert snapshot_status["event_cursor"] == 3
            assert snapshot_status["required_event_cursor"] == 3
            assert snapshot_status["restriction_revision"] == 2
            assert snapshot_status["required_restriction_revision"] == 2
            assert snapshot_status["history_complete"] is False
            assert snapshot_status["index_ack"] is False
            assert snapshot_status["reason"] == "INDEX_PENDING"
            repeat_snapshot_code, repeat_snapshot_result = _recovery_worker(
                snapshot_env,
                "--source-id",
                str(index_source_id),
                "--mode",
                "snapshot",
            )
            assert repeat_snapshot_code == 0 and repeat_snapshot_result == {
                "status": "SNAPSHOT_APPLIED",
                "source_id": str(index_source_id),
                "event_cursor": 3,
            }
            repeat_snapshot_status = _get_json(snapshot_status_url, token=operator_token)
            assert {
                key: value for key, value in repeat_snapshot_status.items() if key != "generation"
            } == {key: value for key, value in snapshot_status.items() if key != "generation"}
            assert repeat_snapshot_status["generation"] == snapshot_status["generation"] + 1
            assert _states(session_factory) == states_before_recovery
            snapshot_index = _post_json(
                f"http://127.0.0.1:{snapshot_port}/c01/v1/index",
                token=operator_token,
                value=_index_request(
                    index_source_id,
                    snapshot_status,
                    evidence_id=evidence.evidence_id,
                    text_excerpt=evidence.text_excerpt,
                ),
            )
            assert snapshot_index["index_ack"] is True
            assert _get_json(snapshot_status_url, token=operator_token)["index_ack"] is True
            assert _states(session_factory) == states_before_recovery

            conflicting_wire = source_event_to_w3_wire(events[0])
            conflicting_payload = conflicting_wire["payload"]
            assert isinstance(conflicting_payload, dict)
            conflicting_payload["title"] = "Conflicting synthetic title"
            try:
                _post_json(
                    f"http://127.0.0.1:{replay_port}/c01/v1/events",
                    token=w2_token,
                    value=conflicting_wire,
                )
            except urllib.error.HTTPError:
                pass
            assert _get_json(replay_status_url, token=w2_token)["reason"] == "CONFLICT"
            conflict_code, conflict_result = _recovery_worker(
                replay_env,
                "--source-id",
                str(events[0].aggregate_id),
                "--mode",
                "replay",
            )
            assert conflict_code == 1
            assert conflict_result == {"status": "W3_RECOVERY_FAILED"}
            assert _states(session_factory) == states_before_recovery

            absent_source_id = uuid4()
            absent_authority = _get_json(
                f"http://127.0.0.1:{authority_port}/internal/v1/sources/"
                f"{absent_source_id}/authority",
                token=authority_token,
            )
            assert absent_authority == {
                "source_id": str(absent_source_id),
                "registered": False,
            }
            absent_status_url = f"http://127.0.0.1:{snapshot_port}/c01/v1/status/{absent_source_id}"
            absent_before = _get_json(absent_status_url, token=w2_token)
            assert absent_before["reason"] == "UNKNOWN_SOURCE"
            assert absent_before["event_cursor"] == 0
            assert absent_before["index_ack"] is False

            absent_event = source_event_to_w3_wire(allowed)
            absent_event["aggregate_id"] = str(absent_source_id)
            absent_event_payload = absent_event["payload"]
            assert isinstance(absent_event_payload, dict)
            absent_event_payload["source_id"] = str(absent_source_id)
            absent_replay = {
                "schema_version": "w3-c01/0.2-candidate",
                "source_id": str(absent_source_id),
                "after_cursor": 0,
                "high_watermark": 0,
                "retention_floor_cursor": 0,
                "events": [],
            }
            absent_snapshot = {
                "schema_version": "w3-c01/0.2-candidate",
                "source_id": str(absent_source_id),
                "as_of": datetime.now(UTC).isoformat(),
                "event_cursor": 0,
                "restriction_revision": 0,
                "complete": True,
                "versions": [],
                "restrictions": [],
                "observation": None,
            }
            absent_index = _index_request(
                absent_source_id,
                index_status,
                evidence_id=evidence.evidence_id,
                text_excerpt=evidence.text_excerpt,
            )
            for route, token, request_body in (
                ("events", w2_token, absent_event),
                ("replay", w2_token, absent_replay),
                ("snapshot", w2_token, absent_snapshot),
                ("index", operator_token, absent_index),
            ):
                try:
                    _post_json(
                        f"http://127.0.0.1:{snapshot_port}/c01/v1/{route}",
                        token=token,
                        value=request_body,
                    )
                except urllib.error.HTTPError as rejected:
                    assert rejected.code == 422
                    assert json.load(rejected) == {
                        "error": "SOURCE_NOT_REGISTERED",
                        "index_ack": False,
                    }
                else:
                    raise AssertionError(f"unregistered Source accepted by W3 {route}")
                assert _get_json(absent_status_url, token=w2_token) == absent_before

            absent_code, absent_result = _recovery_worker(
                snapshot_env,
                "--source-id",
                str(absent_source_id),
                "--mode",
                "replay",
            )
            assert absent_code == 1
            assert absent_result == {"status": "W3_RECOVERY_FAILED"}
            assert _get_json(absent_status_url, token=w2_token) == absent_before
            assert _states(session_factory) == states_before_recovery

            unavailable_authority_port = _free_port()
            outage_w3_env = {
                **w3_env,
                "W3_SOURCE_AUTHORITY_ENDPOINT": (f"http://127.0.0.1:{unavailable_authority_port}"),
            }
            outage_port = _free_port()
            outage_w3 = _start(
                _w3_command(
                    w3_python,
                    Path(runtime_dir.name) / "w3-recovery-authority-outage.sqlite",
                    outage_port,
                ),
                cwd=w3_checkout,
                environment=outage_w3_env,
            )
            recovery_w3s.append(outage_w3)
            _wait_ready(outage_w3, f"http://127.0.0.1:{outage_port}/health")
            outage_env = {
                **w2_env,
                "EPICK_W3_EVENT_ENDPOINT": f"http://127.0.0.1:{outage_port}/c01/v1/events",
            }
            outage_code, outage_result = _recovery_worker(
                outage_env,
                "--source-id",
                str(index_source_id),
                "--mode",
                "replay",
            )
            assert outage_code == 1
            assert outage_result == {"status": "W3_RECOVERY_FAILED"}
            outage_status = _get_json(
                f"http://127.0.0.1:{outage_port}/c01/v1/status/{index_source_id}",
                token=w2_token,
            )
            assert outage_status["event_cursor"] == 0
            assert outage_status["index_ack"] is False
            assert _states(session_factory) == states_before_recovery

            product = _collect_product_source(session_factory)
            product_source_id = product["source_id"]
            assert isinstance(product_source_id, str)
            product_authority = _get_json(
                f"http://127.0.0.1:{authority_port}/internal/v1/sources/"
                f"{product_source_id}/authority",
                token=authority_token,
            )
            assert product_authority == {
                "source_id": product_source_id,
                "registered": True,
            }
            product_status_url = f"http://127.0.0.1:{w3_port}/c01/v1/status/{product_source_id}"
            assert _get_json(product_status_url, token=w2_token)["event_cursor"] == 0
            product_code, product_delivery = _worker(w2_env, "--limit", "25")
            assert product_code == 0 and product_delivery == {"status": "DELIVERED", "count": 1}
            product_status = _get_json(product_status_url, token=w2_token)
            assert product_status["event_cursor"] == 1
            assert product_status["required_event_cursor"] == 1
            assert product_status["index_ack"] is False
            with session_factory() as session:
                product_event = session.get(OutboxEvent, UUID(str(product["outbox_event_id"])))
                assert product_event is not None and product_event.delivery_state == "delivered"
            assert _states(session_factory) == ["delivered"] * 11

            print(
                json.dumps(
                    {
                        "status": "PASS",
                        "w2_code_sha": args.w2_code_sha,
                        "w3_sha": pinned,
                        "w2_migration": "PASS",
                        "authority_outage_pending": 4,
                        "authority_registered_true": True,
                        "authority_unregistered_false": True,
                        "outbox_delivered": 11,
                        "product_collection_source_version_id": product["source_version_id"],
                        "product_collection_evidence_count": product["evidence_count"],
                        "product_collection_outbox_delivered": True,
                        "product_collection_w3_cursor": product_status["event_cursor"],
                        "product_collection_fixture_type": "synthetic_static_posting",
                        "unregistered_replacement_pending": True,
                        "cleared_restriction_revision": 2,
                        "gap_detected_at_cursor": gap_status["event_cursor"],
                        "gap_required_cursor": gap_status["required_event_cursor"],
                        "gap_recovered": True,
                        "index_ack_before_restriction": True,
                        "restriction_blocks_search": True,
                        "release_requires_reindex": True,
                        "index_ack_after_reindex": True,
                        "w3_event_cursor": recovered["event_cursor"],
                        "w3_restriction_revision": recovered["restriction_revision"],
                        "w3_index_ack": recovered["index_ack"],
                        "w3_reason": recovered.get("reason"),
                        "replay_without_cursor_advance": True,
                        "restart_state_preserved": True,
                        "replay_recovery_gap_to_cursor_7": True,
                        "replay_recovery_outbox_states_unchanged": True,
                        "snapshot_recovery_cursor_3_restriction_2": True,
                        "snapshot_history_complete_after_apply": False,
                        "snapshot_index_ack_after_apply": False,
                        "repeated_explicit_snapshot_applied": True,
                        "repeated_explicit_snapshot_status_unchanged_except_generation": True,
                        "repeated_explicit_snapshot_generation_changed": (
                            repeat_snapshot_status["generation"] != snapshot_status["generation"]
                        ),
                        "repeated_explicit_snapshot_generation_delta": (
                            repeat_snapshot_status["generation"] - snapshot_status["generation"]
                        ),
                        "repeated_explicit_snapshot_outbox_states_unchanged": True,
                        "snapshot_index_ack_after_reindex": True,
                        "immutable_conflict_fail_closed": True,
                        "unregistered_source_w2_authority_false": True,
                        "unregistered_source_w2_preflight_fail_closed": True,
                        "unregistered_source_w3_events_422": True,
                        "unregistered_source_w3_replay_422": True,
                        "unregistered_source_w3_snapshot_422": True,
                        "unregistered_source_w3_index_422": True,
                        "unregistered_source_w3_state_unchanged": True,
                        "source_authority_outage_fail_closed": True,
                        "fixture_type": "synthetic",
                        "retention_ttl": "300 seconds for local test only",
                    },
                    sort_keys=True,
                )
            )
        return 0
    finally:
        for recovery_w3 in recovery_w3s:
            _stop(recovery_w3)
        _stop(w3)
        _stop(authority)
        if runtime_dir is not None:
            runtime_dir.cleanup()
        if scoped_engine is not None:
            scoped_engine.dispose()
        if created:
            with admin.begin() as connection:
                connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


if __name__ == "__main__":
    raise SystemExit(main())
