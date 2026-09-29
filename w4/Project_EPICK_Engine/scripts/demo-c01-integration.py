"""Run received W3 over localhost HTTP and the actual W4 API with fictional data."""

import argparse
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import queue
import secrets
import subprocess
import sys
import threading
from uuid import uuid4

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from fastapi import FastAPI
from epick_w4.api import create_service_router
from epick_w4.c01_consumer import C01Consumer, C01HTTPTransport
from epick_w4.c01_contract import COMMIT, C01ServiceOutput
from examples.w4_c01_demo import build_context, C01DemoBackend, C01DemoClient
from examples.w4_service_demo import read


def write(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


async def main(w3_root, out):
    out.mkdir(parents=True, exist_ok=False)
    receipt = json.loads((w3_root / "CANONICAL_RECEIPT.json").read_text(encoding="utf-8"))
    if receipt["full_commit"] != COMMIT:
        raise ValueError("W3_COMMIT_MISMATCH")
    manifest = json.loads((w3_root / "handoff-manifest.json").read_text(encoding="utf-8"))
    if any(
        hashlib.sha256((w3_root / name).read_bytes()).hexdigest() != digest
        for name, digest in manifest["sha256"].items()
    ):
        raise ValueError("W3_FILES_CHANGED")
    interpreter = w3_root / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not interpreter.is_file():
        raise ValueError("W3_LOCKED_ENVIRONMENT_REQUIRED")
    tokens = {role: secrets.token_urlsafe(32) for role in ("W2", "OPERATOR", "W4")}
    environment = {
        **os.environ,
        "PYTHONPATH": str(w3_root / "src"),
        "PYTHONDONTWRITEBYTECODE": "1",
        **{f"W3_{role}_TOKEN": token for role, token in tokens.items()},
    }
    process = subprocess.Popen(
        [
            str(interpreter),
            "-B",
            "-X",
            "utf8",
            "-m",
            "w3_knowledge.c01.http",
            "--db",
            str(out / "w3.sqlite"),
            "--port",
            "0",
            "--restriction-scope",
            "version",
            "--max-ttl-seconds",
            "3600",
        ],
        cwd=w3_root,
        env=environment,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    consumer = None
    report = {
        "status": "RUNNING",
        "w3_commit": COMMIT,
        "w3_transport": "LOCALHOST_HTTP_SUBPROCESS",
        "w4_transport": "ACTUAL_FASTAPI_ASGI",
        "knowledge": "EXPLICIT_SYNTHETIC_FIXTURE",
        "live_w1_w2_integration": False,
        "real_model_calls": 0,
        "scenarios": [],
    }
    write(out / "summary.json", report)
    try:
        startup = queue.Queue()
        threading.Thread(target=lambda: startup.put(process.stdout.readline()), daemon=True).start()
        base = json.loads(startup.get(timeout=15))["url"]
        transports = {
            role: C01HTTPTransport(base, token, timeout=3) for role, token in tokens.items()
        }
        context = build_context()
        version_template = json.loads(
            (
                w3_root / "contracts/c01/v0.2-candidate/examples/version-policy-allowed.json"
            ).read_text(encoding="utf-8")
        )
        documents = {}
        for binding in context["company_knowledge"]["sources"]:
            metadata = binding["metadata"]
            source, key = metadata["source_id"], metadata["index_key"]
            event = deepcopy(version_template)
            event.update(event_id=str(uuid4()), aggregate_id=source)
            payload = event["payload"]
            payload.update(
                source_id=source,
                source_version_id=key["source_version_id"],
                extraction_revision_id=key["extraction_revision_id"],
                content_hash=metadata["content_sha256"],
            )
            for field in ("published_at", "valid_from", "valid_to"):
                payload[field] = {
                    "status": "known",
                    "raw_text": metadata[field],
                    "value": metadata[field],
                    "precision": "day",
                    "timezone": "Asia/Seoul",
                }
            payload["evidence_spans"], payload["posting_sections"], documents[source] = [], [], []
            for index, evidence in enumerate(binding["knowledge"]["bundle"]["evidences"]):
                eid = str(uuid4())
                payload["evidence_spans"].append(
                    {
                        "evidence_id": eid,
                        "source_version_id": key["source_version_id"],
                        "section_title": "Synthetic criterion",
                        "text_excerpt": evidence["excerpt"],
                        "chunk_order": index,
                        "locator": {
                            "kind": "css",
                            "value": f"#synthetic-{index}",
                            "normalization_version": None,
                            "start": None,
                            "end": None,
                        },
                    }
                )
                payload["posting_sections"].append(
                    {
                        "section_key": f"synthetic-{index}",
                        "kind": "general",
                        "heading_raw": "Synthetic",
                        "text_raw": evidence["excerpt"],
                        "evidence_ids": [eid],
                        "order": index,
                        "relation_text": None,
                    }
                )
                documents[source].append(
                    {"document_id": str(uuid4()), "evidence_id": eid, "text": evidence["excerpt"]}
                )
            transports["W2"]._request("/events", event)

        def index_source(binding):
            source = binding["metadata"]["source_id"]
            status = transports["W4"].status(source)
            expiry = (
                datetime.fromisoformat(binding["metadata"]["expires_at"])
                .astimezone(timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%SZ")
            )
            binding["metadata"]["expires_at"] = expiry
            value = {
                "schema_version": "w3-c01/0.2-candidate",
                "source_id": source,
                "event_cursor": status["event_cursor"],
                "restriction_revision": status["restriction_revision"],
                "index_key": binding["metadata"]["index_key"],
                "retention_scope": "excerpts_only",
                "expires_at": expiry,
                "documents": documents[source],
            }
            result = transports["OPERATOR"]._request("/index", value)
            if not result["index_ack"]:
                raise ValueError("SYNTHETIC_INDEX_NOT_READY")
            ready = next(
                s
                for s in reversed(transports["W4"].signals()["signals"])
                if s["source_id"] == source and s["usable"]
            )
            binding.update(signal=ready, knowledge_generation=ready["generation"])

        for binding in context["company_knowledge"]["sources"]:
            index_source(binding)
        consumer = C01Consumer(
            out / "w4.sqlite", transport=transports["W4"], max_cache_ttl_seconds=120
        )
        consumer.drain()
        backend = C01DemoBackend(context)
        extractor, judge = C01DemoClient("extraction"), C01DemoClient("judgment")
        write(out / "server-context.initial.json", context)

        async def post(name, expected):
            app = FastAPI()
            app.include_router(
                create_service_router(
                    authenticate=lambda: "user-demo",
                    backend=backend,
                    extraction_client_factory=lambda: extractor,
                    client_factory=lambda: judge,
                    c01_consumer=consumer,
                )
            )
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://in-process-w4"
            ) as http:
                response = await http.post("/w4/recommend-from-raw", json=read("request.json"))
            result = response.json()
            report["scenarios"].append(
                {
                    "name": name,
                    "http_status": response.status_code,
                    "expected_status": expected,
                    "error": result.get("error"),
                    "passed": response.status_code == expected,
                }
            )
            if response.status_code != expected:
                raise ValueError("UNEXPECTED_W4_HTTP_RESULT_" + name)
            if expected == 200:
                C01ServiceOutput.model_validate(result)
            return result

        normal = await post("normal_two_sources", 200)
        write(out / "recommendation.json", normal)
        cached = await post("cached_with_revalidation", 200)
        if normal != cached or len(extractor.calls) + len(judge.calls) != 6:
            raise ValueError("CACHE_MISS_OR_CHANGED_RESULT")
        binding = context["company_knowledge"]["sources"][1]
        source = binding["metadata"]["source_id"]
        active = json.loads(
            (w3_root / "contracts/c01/v0.2-candidate/examples/restrict.json").read_text(
                encoding="utf-8"
            )
        )
        active.update(event_id=str(uuid4()), aggregate_id=source, revision=2)
        active["payload"].update(
            source_id=source,
            source_version_id=binding["metadata"]["index_key"]["source_version_id"],
            restriction_status="active",
            restriction_revision=1,
            accuracy_status="verified_in_scope",
            replacement_ref=None,
        )
        transports["W2"]._request("/events", active)
        await post("blocked_before_signal_delivery", 409)
        if consumer.db.execute("SELECT count(*) FROM results").fetchone()[0] != 0:
            raise ValueError("DEPENDENT_RESULT_NOT_INVALIDATED")
        consumer.drain()
        released = deepcopy(active)
        released.update(event_id=str(uuid4()), revision=3)
        released["payload"].update(restriction_status="cleared", restriction_revision=2)
        transports["W2"]._request("/events", released)
        consumer.drain()
        await post("cleared_requires_reindex", 409)
        index_source(binding)
        consumer.drain()
        context["context_version"] = "synthetic-c01-recovery-v2"
        recovered = await post("reindexed_new_generation", 200)
        write(out / "recovered-recommendation.json", recovered)
        consumer.close()
        consumer = C01Consumer(
            out / "w4.sqlite", transport=transports["W4"], max_cache_ttl_seconds=120
        )
        await post("consumer_restart_preserves_valid_result", 200)
        if len(extractor.calls) + len(judge.calls) != 12:
            raise ValueError("UNEXPECTED_SIMULATED_CALL_COUNT")
        process.terminate()
        process.wait(timeout=10)
        await post("status_outage_blocks_cached_result", 503)
        if consumer.db.execute("SELECT count(*) FROM results").fetchone()[0] != 0:
            raise ValueError("OUTAGE_CACHE_NOT_REMOVED")
        report.update(status="PASSED", simulated_calls=12)
    except Exception as exc:
        report.update(status="FAILED", error_type=type(exc).__name__, error=str(exc))
        raise
    finally:
        if consumer is not None:
            consumer.close()
        if process.poll() is None:
            process.terminate()
            process.wait(timeout=10)
        write(out / "summary.json", report)
    print(json.dumps(report, ensure_ascii=False))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--w3-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.w3_root.resolve(), args.output_dir.resolve()))
