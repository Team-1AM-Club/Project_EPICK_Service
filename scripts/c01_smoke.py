"""Run the real C01 HTTP subprocess and durable W4 reference cache."""

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
from uuid import uuid4

from w3_knowledge.c01.contracts import VERSION
from w3_knowledge.c01.w4 import W4Cache, drain
from w3_knowledge.restriction.w4 import request

ROOT = Path(__file__).resolve().parents[1]
EXAMPLES = ROOT / "contracts/c01/v0.2-candidate/examples"


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path)
    args = parser.parse_args(argv)
    output = args.output_dir or ROOT / ".runtime" / f"c01-smoke-{uuid4()}"
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
            "w3_knowledge.c01.http",
            "--db",
            str(output / "w3.sqlite"),
            "--port",
            "0",
            "--restriction-scope",
            "source",
            "--max-ttl-seconds",
            "3600",
        ],
        cwd=ROOT,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    results, error = [], None

    def check(name, condition):
        results.append(dict(name=name, passed=bool(condition)))
        if not condition:
            raise ValueError("CHECK_FAILED")

    def load(name):
        return json.loads((EXAMPLES / (name + ".json")).read_text(encoding="utf-8"))

    try:
        startup = queue.Queue()
        threading.Thread(target=lambda: startup.put(process.stdout.readline()), daemon=True).start()
        base = json.loads(startup.get(timeout=10))["url"]

        def call(path, role="w4", payload=None):
            return request(base, tokens[role], "/c01/v1" + path, payload)

        version, active, release = load("version-policy-allowed"), load("restrict"), load("release")
        source = version["aggregate_id"]

        def status():
            return call("/status/" + source)

        def index():
            value = load("index-request")
            current = status()
            value.update(
                event_cursor=current["event_cursor"],
                restriction_revision=current["restriction_revision"],
                expires_at=(datetime.now(UTC) + timedelta(minutes=30)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                ),
            )
            return call("/index", "operator", value)

        result = call("/events", "w2", version)
        check("receipt_not_index_ack", result["receipt"] == "COMMITTED" and not result["index_ack"])
        check(
            "restriction_cursor_not_fabricated",
            result["event_cursor"] == 1 and result["restriction_revision"] == 0,
        )
        check("real_fts_ready", index()["index_ack"])
        check("search_has_exact_evidence", len(call("/search?q=cloud")["results"]) == 1)
        with W4Cache(output / "w4.sqlite") as cache:
            check("durable_signal_delivery", drain(base, tokens["w4"], cache) >= 2)
            cache.put(source, status()["generation"], {"summary": "synthetic derived value"})

            def current(sid):
                return call("/status/" + sid)

            check("cache_usable", cache.get(source, current) is not None)
            check("active_blocks", call("/events", "w2", active)["reason"] == "RESTRICTED")
            check("search_removed", call("/search?q=cloud")["results"] == [])
            check("cache_blocked_before_delivery", cache.get(source, current) is None)
            check("duplicate", call("/events", "w2", active)["outcome"] == "DUPLICATE")
            check(
                "release_requires_reindex",
                call("/events", "w2", release)["reason"] == "INDEX_PENDING",
            )
            check("release_reindexed", index()["index_ack"])
            gap = copy.deepcopy(release)
            gap.update(revision=5, event_id=str(uuid4()))
            gap["payload"]["restriction_revision"] = 3
            check("gap_blocks", call("/events", "w2", gap)["reason"] == "EVENT_GAP")
            replay = load("replay")
            replay.update(after_cursor=3, high_watermark=5, retention_floor_cursor=4, events=[])
            check(
                "retention_requires_snapshot",
                call("/replay", "w2", replay)["outcome"] == "SNAPSHOT_REQUIRED",
            )
            snapshot = load("snapshot")
            snapshot.update(event_cursor=5, restriction_revision=3, restrictions=[gap])
            recovered = call("/snapshot", "w2", snapshot)
            check(
                "snapshot_metadata_only",
                recovered["reason"] == "INDEX_PENDING" and not recovered["history_complete"],
            )
            check("snapshot_reindex", index()["index_ack"])
            drain(base, tokens["w4"], cache)
            check("delivery_ack_separate", call("/signals")["signals"] == [])
            check(
                "two_cursors_final",
                status()["event_cursor"] == 5 and status()["restriction_revision"] == 3,
            )
    except Exception as exc:
        error = type(exc).__name__
    finally:
        process.terminate()
        try:
            process.communicate(timeout=5)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate(timeout=5)
    report = dict(
        schema_version=VERSION,
        adoption="PENDING",
        scope="LOCAL_HTTP_SQLITE_REFERENCE_W4",
        checks=results,
        passed=sum(r["passed"] for r in results),
        total=len(results),
        error=error,
    )
    (output / "result.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(
        json.dumps(
            {
                "passed": report["passed"],
                "total": report["total"],
                "error": error,
                "report": str(output / "result.json"),
            }
        )
    )
    return 1 if error else 0


if __name__ == "__main__":
    raise SystemExit(main())
