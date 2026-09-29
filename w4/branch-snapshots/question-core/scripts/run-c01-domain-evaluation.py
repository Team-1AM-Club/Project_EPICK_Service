"""Freeze new fiction, share actual extraction across four judges, audit the old API regression."""

import argparse
import asyncio
from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from epick_w4.api import create_service_router
from epick_w4.c01_consumer import C01Consumer
from epick_w4.c01_contract import C01ServiceOutput
from epick_w4.c01_detail import PROMPT, PROMPT_VERSION, company_support
from epick_w4.detail_contract import question_spec
from epick_w4.detailed_recommendation import _assess, recommend_extracted
from epick_w4.evidence_extraction import extract_evidence
from epick_w4.extraction_eval import build_extraction_requests, evaluate_extraction_runs
from epick_w4.llm_contract import LLMError
from epick_w4.model_eval import digest
from examples.w4_c01_demo import C01DemoBackend, FixtureTransport, build_context
from examples.w4_c01_domain_demo import protocol
from examples.w4_service_demo import read as service_read

SPEC = importlib.util.spec_from_file_location("c01_pilot", ROOT / "scripts/run-c01-evaluation.py")
PILOT = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PILOT)
RUNNER, read, write = PILOT.RUNNER, PILOT.read, PILOT.write


def citation_ok(status, refs, expected):
    if status == "NOT_SHOWN":
        return refs == []
    if status not in expected["statuses"]:
        return False
    return set(expected["required_units"]) <= set(refs) <= set(expected["allowed_units"])


def score_case(record, item, company, question, gold):
    counts = {key: [0, n] for key, n in (
        ("question_status", 3), ("delivered_question_status", 3), ("claim_link", 1),
        ("leaf_status", 3), ("delivered_company_status", 4), ("requirement_root", 1),
        ("evidence_sufficiency", 7))}
    result = {"case_id": gold["case_id"], "structurally_valid": False, "counts": counts,
              "error": record.get("error"), "false_positive_company_support": 0,
              "delivered_false_positive_company_support": 0, "details": []}
    try:
        if result["error"]:
            return result
        response = record["response"]
        support, _ = company_support(response, item, company)
        assessed = _assess({"checks": response["checks"]}, item, question)
    except (LLMError, KeyError, TypeError, ValueError) as error:
        result["error"] = error.code if isinstance(error, LLMError) else "INVALID_RESPONSE"
        return result
    result["structurally_valid"] = True

    def measure(kind, key, rows, expected, *, delivered=None, company_check=False):
        statuses = [r["status"] for r in rows]
        correct = all(s in expected["statuses"] for s in statuses)
        refs_ok = all(citation_ok(r["status"], r.get("evidence_ids", []), expected) for r in rows)
        counts[kind][0] += int(correct)
        if kind != "requirement_root":
            counts["evidence_sufficiency"][0] += int(correct and refs_ok)
        if delivered is not None:
            field = "delivered_company_status" if company_check else "delivered_question_status"
            counts[field][0] += int(all(s in expected["statuses"] for s in delivered))
        if company_check and "SUPPORTED" not in expected["statuses"]:
            result["false_positive_company_support"] += int("SUPPORTED" in statuses)
            result["delivered_false_positive_company_support"] += int("SUPPORTED" in delivered)
        result["details"].append({"kind": kind, "id": key, "expected": expected["statuses"],
                                  "actual_by_source": statuses, "delivered": delivered,
                                  "correct": correct, "evidence_sufficient": refs_ok})

    checks = {r["check_id"]: r for r in response["checks"]}
    final_checks = {r["check_id"]: r for r in assessed}
    for key, expected in gold["checks"].items():
        measure("question_status", key, [checks[key]], expected, delivered=[final_checks[key]["status"]])
    links = {r["id"]: {**r, "status": "SUPPORTED"} for r in response["company_links"]}
    final = {r["id"]: r for r in support}
    claim_ids = [c["id"] for c in company.criteria if c["kind"] == "CLAIM"]
    missing = {"status": "NOT_SHOWN", "evidence_ids": []}
    measure("claim_link", "software_sustained_learning", [links.get(key, missing) for key in claim_ids],
            gold["claim"], delivered=[final.get(key, missing)["status"] for key in claim_ids], company_check=True)
    requirements = {r["id"]: {n["node_id"]: n for n in r["nodes"]} for r in response["requirement_checks"]}
    for key, expected in gold["leaves"].items():
        delivered = [next(n["status"] for n in final[rid]["condition_assessment"] if n["node_id"] == key)
                     for rid in requirements]
        measure("leaf_status", key, [r[key] for r in requirements.values()], expected,
                delivered=delivered, company_check=True)
    measure("requirement_root", "apply_AND_validation_OR_documentation",
            [final[rid] for rid in requirements], gold["root"])
    return result


def score_records(records, extraction, company, question, gold):
    expected = {c["case_id"]: c for c in gold["cases"]}
    mapped = {}
    for record in records:
        key = record["case_id"]
        if key not in expected or key in mapped:
            raise ValueError("Unknown or duplicate evaluation case")
        mapped[key] = record
    items = {i["episode"]["episode_id"]: i for i in extraction["episodes"]}
    if set(items) != set(expected):
        raise ValueError("Actual extraction must cover every frozen case")
    details = [score_case(mapped.get(key, {"error": "MISSING_RECORD"}), items[key], company, question, row)
               for key, row in expected.items()]
    counts = {k: [sum(d["counts"][k][0] for d in details), sum(d["counts"][k][1] for d in details)]
              for k in details[0]["counts"]}
    valid = sum(d["structurally_valid"] for d in details)
    return {"scheduled_requests": len(expected), "valid_requests": valid, "counts": counts,
            "scores": {k: round(100 * n / d, 6) for k, (n, d) in counts.items()},
            "false_positive_company_support": sum(d["false_positive_company_support"] for d in details),
            "delivered_false_positive_company_support": sum(d["delivered_false_positive_company_support"] for d in details),
            "frozen_case_gate_passed": valid == len(expected) and all(n == d for n, d in counts.values()),
            "details": details}


class RecordingClient(RUNNER.ProgressClient):
    """Capture only the pre-registered fiction benchmark; not the service API."""
    def __init__(self, *, folder, **kwargs):
        super().__init__(**kwargs)
        self.folder, self.records = folder, []

    def complete_json(self, **kwargs):
        record = {"case_id": kwargs["payload"]["episode_id"], "response": None, "error": None}
        try:
            record["response"] = super().complete_json(**kwargs)
            return record["response"]
        except LLMError as error:
            record["error"] = error.code
            raise
        finally:
            self.records.append(record)
            write(self.folder / f"response-{len(self.records):03d}.json", record)


class CapturedClient:
    """Recompose from verified captured responses; issue zero new inference calls."""
    simulated = False  # The response origin is actual inference, never draft reference labels.
    replayed = True

    def __init__(self, spec, traces):
        self.provider, self.model = spec["provider"], spec["model"]
        self.calls, self.records = deepcopy(traces), []

    def complete_json(self, *, stage, system_prompt, payload):
        trace = self.calls[len(self.records)]
        request = trace["request"]
        if (trace["error"] is not None or trace["stage"] != stage or request["model"] != self.model
                or request["messages"][0] != {"role": "system", "content": system_prompt}
                or json.loads(request["messages"][1]["content"]) != payload
                or trace["response"]["model"] != self.model):
            raise ValueError("Captured response does not belong to this exact input/model")
        choice = trace["response"]["choices"][0]
        if choice["finish_reason"] != "stop":
            raise ValueError("Incomplete captured response")
        response = json.loads(choice["message"]["content"])
        self.records.append({"case_id": payload["episode_id"], "response": response, "error": None})
        return deepcopy(response)


def validate_resume(previous, protocol_document, configuration):
    if read(previous / "protocol.json") != protocol_document:
        raise ValueError("Resume cannot change the input, reference or judgment prompt")
    before = read(previous / "configuration.json")
    for key in ("models", "prompt_version", "runtime_sha256", "fit_target_mib", "generation", "extraction_candidate_id"):
        if before[key] != configuration[key]:
            raise ValueError("Resume cannot change models or generation settings")
    for name, checksum in read(previous / "source-sha256.json").items():
        if Path(name).parts[0] in {"epick_w4", "examples"}:
            if hashlib.sha256((ROOT / name).read_bytes()).hexdigest() != checksum:
                raise ValueError("Resume cannot change inference engine or fixtures")


@contextmanager
def live_model(args, spec, folder, manifest, *, capture=True, previous=None):
    folder.mkdir(parents=True)
    prior_folder = previous / spec["candidate_id"] if previous else None
    captured = next((p for name in ("http-traces.json", "reused-http-traces.json")
                     if prior_folder is not None and (p := prior_folder / name).is_file()), None)
    if captured is not None and captured.is_file():
        traces = read(captured)
        if len(traces) == 7 and all(t["error"] is None and t.get("response") for t in traces):
            client = CapturedClient(spec, traces)
            write(folder / "reused-http-traces.json", traces)
            yield client
            return
        write(folder / "previous-incomplete-attempt.json", {
            "reason": "INCOMPLETE_OR_FAILED_CAPTURE_RERUN_WITH_UNCHANGED_PROTOCOL",
            "previous": str(captured), "sha256": hashlib.sha256(captured.read_bytes()).hexdigest(),
            "record_count": len(traces), "errors": [t["error"] for t in traces if t["error"]]})
    elif prior_folder is not None and prior_folder.is_dir():
        records = list(prior_folder.glob("response-*.json"))
        if records:
            write(folder / "previous-incomplete-attempt.json", {
                "reason": "INTERRUPTED_WITHOUT_COMPLETE_HTTP_TRACES_RERUN_WITH_UNCHANGED_PROTOCOL",
                "previous": str(prior_folder),
                "preserved_records_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in records}})
    process, log = RUNNER.start_server(args.runtime, spec, Path(manifest["model_directory"]), folder,
                                      args.port, v2=True, fit_target_mib=args.fit_target_mib)
    client = None
    try:
        cls = RecordingClient if capture else RUNNER.ProgressClient
        client = cls(provider=spec["provider"], model=spec["model"], model_sha256=spec["sha256"],
                     port=args.port, structured=True, capture_traces=capture, **({"folder": folder} if capture else {}))
        yield client
    finally:
        RUNNER.stop_server(process)
        log.close()
        if client is not None:
            write(folder / "http-traces.json", client.calls)


def old_api_regression(client, folder):
    import httpx
    from fastapi import FastAPI

    context = build_context()
    consumer = C01Consumer(folder / "consumer.sqlite", transport=FixtureTransport(context), max_cache_ttl_seconds=300)
    try:
        consumer.drain()
        app = FastAPI()
        app.include_router(create_service_router(authenticate=lambda: "user-demo", backend=C01DemoBackend(context),
            extraction_client_factory=lambda: client, client_factory=lambda: client, c01_consumer=consumer))

        async def request():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://w4.local") as http:
                return await http.post("/w4/recommend-from-raw", json=service_read("request.json"))

        response = asyncio.run(request())
        value = response.json()
        write(folder / "response.json", value)
        quality = {"quality_passed": False, "http_status": response.status_code}
        if response.status_code == 200:
            C01ServiceOutput.model_validate(value)
            quality.update(PILOT.audit_demo_result(value, read(ROOT / "samples/c01/evaluation-reference.draft.json")))
        write(folder / "quality-audit.json", quality)
        return quality
    finally:
        consumer.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=Path(tempfile.gettempdir()) /
                        "epick-w4-local-models-b10859/runtime/llama-server.exe")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--fit-target-mib", type=int, default=2048,
                        help="Target free-memory margin after fitting; other apps reduce the offload budget")
    parser.add_argument("--resume-from", type=Path,
                        help="Reuse identical successful captures after a harness-only correction; engine and references must match")
    args = parser.parse_args()
    if args.output_dir.exists() or not args.runtime.is_file() or args.fit_target_mib < 1:
        parser.error("Use a new output directory, installed runtime and positive fit target")
    output = args.output_dir.resolve()
    output.mkdir(parents=True)
    context, raw, reference, company = protocol()
    gold = read(ROOT / "samples/c01/domain-reference.v1.draft.json")
    manifest = read(ROOT / "samples/evaluation/local-models.v2.json")
    models = {m["candidate_id"]: m for m in manifest["models"]}
    question = question_spec({"scope_id": context["question_scope_id"], "question_id": "expertise"})
    # These files exist BEFORE the first inference. Do not tune the reference after looking at answers.
    protocol_document = {"raw": raw, "context": context, "question": question,
        "extraction_reference": reference, "judgment_reference": gold, "judgment_prompt": PROMPT,
        "judgment_input": "ONE_ACTUAL_GEMMA12_EXTRACTION_SHARED_UNMODIFIED_BY_ALL_JUDGES"}
    configuration = {"created_at": datetime.now(timezone.utc).isoformat(),
        "models": manifest, "prompt_version": PROMPT_VERSION, "runtime_path": str(args.runtime),
        "runtime_sha256": hashlib.sha256(args.runtime.read_bytes()).hexdigest(), "repetitions": 1,
        "fit_target_mib": args.fit_target_mib, "generation": "LocalClient defaults; structured JSON; seed 42",
        "extraction_candidate_id": "gemma4-12b-qat", "production_selection": "WITHHELD"}
    previous = args.resume_from.resolve() if args.resume_from else None
    if previous:
        validate_resume(previous, protocol_document, configuration)
        files = [p for p in previous.rglob("*.json") if "source" not in p.relative_to(previous).parts]
        write(output / "capture-provenance.json", {"parent": str(previous),
            "mode": "EXACT_CAPTURE_REUSE_NO_PROMPT_OR_REFERENCE_CHANGE",
            "files_sha256": {str(p.relative_to(previous)): hashlib.sha256(p.read_bytes()).hexdigest() for p in files}})
    write(output / "protocol.json", protocol_document)
    write(output / "configuration.json", configuration)
    paths = list((ROOT / "epick_w4").glob("*.py")) + list((ROOT / "epick_w4").glob("*.json"))
    paths += [Path(__file__), ROOT / "scripts/run-c01-evaluation.py", ROOT / "scripts/run-local-evaluation.py",
              ROOT / "examples/w4_c01_demo.py", ROOT / "examples/w4_c01_domain_demo.py",
              ROOT / "samples/c01/domain-reference.v1.draft.json"]
    frozen = {}
    for path in paths:
        relative = str(path.relative_to(ROOT))
        frozen[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        target = output / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    write(output / "source-sha256.json", frozen)
    real_calls, reused_calls, scores = 0, 0, []
    monitor = RUNNER.GpuMonitor()
    monitor.start()
    try:
        if previous:
            extraction = read(previous / "actual-extraction.json")
            run = read(previous / "extraction-run.json")
            reused_calls += len(run["records"])
        else:
            with live_model(args, models["gemma4-12b-qat"], output / "shared-extraction", manifest) as client:
                extraction = extract_evidence(raw, user_id="user-demo", llm=client)
                requests = {r["case_id"]: r for r in build_extraction_requests(raw)}
                run = {"schema_version": "w4-extraction-run/0.1", "candidate_id": "gemma4-12b-qat",
                       "provider": client.provider, "model": client.model, "generation_config": client.generation_config,
                       "provenance": "CAPTURED", "created_at": datetime.now(timezone.utc).isoformat(),
                       "reference_sha256": digest(reference), "protocol_sha256": digest(list(requests.values())),
                       "repetitions": 1, "records": [{**r, "repeat": 0, "request_sha256": digest(requests[r["case_id"]])}
                                                      for r in client.records]}
                real_calls += len(client.calls)
        write(output / "actual-extraction.json", extraction)
        write(output / "extraction-run.json", run)
        extraction_score = evaluate_extraction_runs(raw, reference, [run])
        write(output / "extraction-scores.json", extraction_score)
        extraction_hash = digest(extraction)
        for spec in manifest["models"]:
            folder = output / spec["candidate_id"]
            with live_model(args, spec, folder, manifest, previous=previous) as client:
                try:
                    value = recommend_extracted(raw, extraction, question=question, top_k=len(raw["episodes"]),
                        user_id="user-demo", llm=client, company_context=company)
                    # Internal core output has no host-owned server_context_version.
                    # The API regression below validates the complete service envelope.
                    write(folder / "recommendation.json", value)
                except LLMError as error:
                    write(folder / "pipeline-error.json", {"error": error.code, "stage": error.stage})
                scored = {"candidate_id": spec["candidate_id"], "actual_extraction_sha256": extraction_hash,
                          **score_records(client.records, extraction, company, question, gold)}
                write(folder / "scores.json", scored)
                scores.append(scored)
                if getattr(client, "replayed", False):
                    reused_calls += len(client.calls)
                else:
                    real_calls += len(client.calls)
            if digest(extraction) != extraction_hash:
                raise ValueError("Shared extraction was mutated")
        demo = output / "old-api-regression"
        with live_model(args, models["gemma4-12b-qat"], demo, manifest, capture=False) as client:
            quality = old_api_regression(client, demo)
            real_calls += len(client.calls)
        unchanged = all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in frozen.items())
        write(output / "summary.json", {"status": "MEASURED_DRAFT_NOT_PRODUCTION_SELECTED", "real_model_calls": real_calls,
            "reused_captured_model_calls": reused_calls, "total_distinct_model_calls": real_calls + reused_calls,
            "simulated_model_calls": 0, "new_cases": len(gold["cases"]), "judgment_models": len(scores),
            "shared_actual_extraction_sha256": extraction_hash, "source_hashes_unchanged": unchanged,
            "passing_frozen_case_candidates": [r["candidate_id"] for r in scores if r["frozen_case_gate_passed"]],
            "extraction_required_checks_passed": extraction_score["results"][0]["required_checks_passed"],
            "old_api_regression": quality, "production_selection": "WITHHELD"})
        print(f"Finished actual model calls: {real_calls}; original API quality: {quality['quality_passed']}", flush=True)
    finally:
        monitor.stop()
        write(output / "gpu-samples.json", monitor.samples)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
