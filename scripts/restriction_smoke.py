"""Start the real HTTP entrypoint and exercise its durable index/cache boundaries."""

import argparse
import copy
from datetime import datetime, timedelta, UTC
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading
import time
from urllib.error import HTTPError
from urllib.request import Request, urlopen
from uuid import uuid4

from w3_knowledge.restriction.contracts import VERSION
from w3_knowledge.restriction.w4 import W4Cache, drain

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts/restriction/v0.1-draft/examples"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    output = args.output_dir or ROOT / ".runtime" / f"smoke-{uuid4()}"
    output.mkdir(parents=True, exist_ok=True)
    if any((output / name).exists() for name in ("w3.sqlite", "w4.sqlite", "result.json")):
        print('{"error":"USE_A_NEW_OUTPUT_DIRECTORY"}')
        return 2
    tokens = {role: secrets.token_urlsafe(32) for role in ("w2", "operator", "w4")}
    environment = {
        **os.environ,
        "PYTHONPATH": str(ROOT / "src"),
        **{f"W3_{role.upper()}_TOKEN": token for role, token in tokens.items()},
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "w3_knowledge.restriction.http",
            "--db",
            str(output / "w3.sqlite"),
            "--port",
            "0",
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    results = []
    error = None

    def check(name, condition):
        results.append({"name": name, "passed": bool(condition)})
        if not condition:
            raise ValueError("CHECK_FAILED")

    try:
        startup = queue.Queue()
        threading.Thread(target=lambda: startup.put(process.stdout.readline()), daemon=True).start()
        base = json.loads(startup.get(timeout=10))["url"]

        def call(path, role="w4", payload=None):
            req = Request(
                base + path,
                data=json.dumps(payload).encode() if payload is not None else None,
                headers={
                    "Authorization": f"Bearer {tokens[role]}",
                    "Content-Type": "application/json",
                },
            )
            try:
                with urlopen(req, timeout=5) as response:
                    return json.load(response)
            except HTTPError as response:
                return json.load(response)

        initial = json.loads((EXAMPLES / "initial.json").read_text(encoding="utf-8"))
        now = datetime.now(UTC)
        timestamp = now.strftime("%Y-%m-%dT%H:%M:%SZ")
        initial["occurred_at"] = initial["published_at"] = timestamp

        def event(revision, status="RELEASED"):
            value = copy.deepcopy(initial)
            value.update(event_id=f"demo-event-{revision}", aggregate_revision=revision)
            value["payload"]["status"] = status
            return value

        def index(revision, *, expires=None, mismatch=False):
            value = json.loads((EXAMPLES / "index-request.json").read_text(encoding="utf-8"))
            value["restriction_revision"] = revision
            value["expires_at"] = expires or (now + timedelta(hours=1)).strftime(
                "%Y-%m-%dT%H:%M:%SZ"
            )
            if mismatch:
                value["index_key"]["extraction_revision_id"] = "wrong-extraction"
            return call("/v1/index", "operator", value)

        def snapshot(revision):
            return call(
                "/v1/snapshot",
                "w2",
                {"schema_version": VERSION, "as_of": timestamp, "event": event(revision)},
            )

        check("health", call("/health")["status"] == "ok")
        check(
            "event_committed_without_index_ack",
            call("/v1/events", "w2", event(1))["index_ack"] is False,
        )
        check("real_fts_index_ack", index(1)["index_ack"] is True)
        check("actual_search_hit", len(call("/v1/search?q=semiconductor")["results"]) == 1)
        with W4Cache(output / "w4.sqlite") as cache:
            drain(base, tokens["w4"], cache)

            def current(source):
                return call(f"/v1/status/{source}")

            cache.put(
                "demo-source",
                current("demo-source")["generation"],
                {"summary": "Synthetic cached result"},
            )
            check("w4_cache_ready", cache.get("demo-source", current) is not None)
            check(
                "restriction_blocks_ack",
                call("/v1/events", "w2", event(2, "RESTRICTED"))["reason"] == "RESTRICTED",
            )
            check("restriction_removes_search", call("/v1/search?q=semiconductor")["results"] == [])
            check(
                "undelivered_signal_still_blocks_cache", cache.get("demo-source", current) is None
            )
            check(
                "duplicate_is_idempotent",
                call("/v1/events", "w2", event(2, "RESTRICTED"))["outcome"] == "DUPLICATE",
            )
            late = event(1)
            late["event_id"] = "late-event"
            check(
                "stale_release_cannot_restore",
                call("/v1/events", "w2", late)["reason"] == "RESTRICTED",
            )
            check(
                "release_requires_reindex",
                call("/v1/events", "w2", event(3))["reason"] == "INDEX_PENDING",
            )
            check("mismatch_blocks_ack", index(3, mismatch=True)["reason"] == "INDEX_MISMATCH")
            check("correct_index_retry", index(3)["index_ack"] is True)
            check("gap_blocks_use", call("/v1/events", "w2", event(5))["reason"] == "EVENT_GAP")
            replay = {
                "schema_version": VERSION,
                "source_id": "demo-source",
                "after_revision": 3,
                "high_watermark": 5,
                "retention_start": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
                "events": [event(4, "RESTRICTED"), event(5)],
            }
            check("contiguous_replay", call("/v1/replay", "w2", replay)["outcome"] == "REPLAYED")
            check("replay_requires_index", index(5)["index_ack"] is True)
            call("/v1/events", "w2", event(7))
            replay.update(
                after_revision=5,
                high_watermark=7,
                retention_start=timestamp,
                events=[event(6, "RESTRICTED"), event(7)],
            )
            check(
                "replay_exact_expiry",
                call("/v1/replay", "w2", replay)["outcome"] == "SNAPSHOT_REQUIRED",
            )
            check("stale_snapshot_rejected", snapshot(6)["outcome"] == "STALE_SNAPSHOT")
            recovered = snapshot(7)
            check(
                "snapshot_marks_history_gap",
                recovered["history_complete"] is False and recovered["index_ack"] is False,
            )
            expires = (datetime.now(UTC) + timedelta(seconds=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
            check("snapshot_reindex", index(7, expires=expires)["index_ack"] is True)
            drain(base, tokens["w4"], cache)
            cache.put(
                "demo-source",
                current("demo-source")["generation"],
                {"summary": "Synthetic refreshed result"},
            )
            time.sleep(2.1)
            check("real_clock_ttl_blocks_cache", cache.get("demo-source", current) is None)
            check(
                "real_clock_ttl_removes_search", call("/v1/search?q=semiconductor")["results"] == []
            )
            drain(base, tokens["w4"], cache)
            check("durable_signal_ack", call("/v1/signals")["signals"] == [])
    except (ValueError, OSError, KeyError, queue.Empty):
        error = "SMOKE_FAILED"
    finally:
        process.terminate()
        try:
            process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)
        process.stdout.close()
        process.stderr.close()
    report = {
        "schema_version": VERSION,
        "team_contract_approved": False,
        "transport": "real-http-subprocess",
        "storage": "sqlite-fts5",
        "passed": sum(r["passed"] for r in results),
        "total": len(results),
        "error": error,
        "checks": results,
    }
    (output / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "report": str(output / "result.json"),
                "passed": report["passed"],
                "total": report["total"],
                "error": error,
            }
        )
    )
    return int(error is not None or not results or report["passed"] != report["total"])


if __name__ == "__main__":
    raise SystemExit(main())
