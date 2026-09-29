"""Measure four actual local models on fixed C01 fiction, then run one live API demo."""

import argparse
import asyncio
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

from epick_w4 import extract_evidence
from epick_w4.c01_adapter import consume_c01
from epick_w4.c01_consumer import C01Consumer
from epick_w4.c01_contract import C01ServiceOutput
from epick_w4.c01_detail import PROMPT, PROMPT_VERSION, company_support, check_sample_c01_detail
from epick_w4.company_detail import outbound_payload
from epick_w4.detail_contract import question_spec
from epick_w4.detailed_recommendation import _assess, model_payload
from epick_w4.extraction_eval import (
    build_extraction_requests, collect_extraction_run, evaluate_extraction_runs,
)
from epick_w4.llm_contract import LLMError
from epick_w4.local_client import LocalClient
from epick_w4.model_eval import digest
from examples.w4_c01_demo import build_context, C01DemoBackend, FixtureTransport
from examples.w4_service_demo import read as service_read

SPEC = importlib.util.spec_from_file_location("local_runner", ROOT / "scripts/run-local-evaluation.py")
RUNNER = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(RUNNER)
read, write = RUNNER.read, RUNNER.write


def protocol():
    context = build_context(1789603200.0)
    company = consume_c01(context["company_knowledge"], context["question_scope_id"])
    raw = {"schema_version": "w4-evidence-input/0.1", "request_id": "c01-evaluation-pilot-v1",
           **{k: deepcopy(context[k]) for k in (
               "data_kind", "project", "snapshot", "episodes", "excluded_episode_ids")}}
    previous = read(ROOT / "samples/extraction/benchmark.v4.draft.json")
    answers = {e["episode_id"]: previous["answers"][e["episode_id"]] for e in raw["episodes"]}
    reference = {"schema_version": "w4-extraction-benchmark/0.1", "data_kind": "SYNTHETIC",
                 "review": {"status": "DRAFT"}, "answers": answers,
                 "input_sha256": digest(raw), "protocol_sha256": digest(build_extraction_requests(raw)),
                 "case_splits": {key: "existing_c01_regression" for key in answers}}

    class ReferenceClient:
        # Explicit reference construction only; this is never counted as a model call.
        simulated = True
        provider = "draft-reference"
        model = "reference-labels-not-inference"

        def complete_json(self, *, payload, **_):
            return deepcopy(answers[payload["episode_id"]])

    extraction = extract_evidence(raw, user_id="user-demo", llm=ReferenceClient())
    gold = read(ROOT / "samples/c01/evaluation-reference.draft.json")
    cases = []
    for qid in gold["questions"]:
        question = question_spec({"scope_id": context["question_scope_id"], "question_id": qid})
        for item in extraction["episodes"]:
            outbound = outbound_payload(model_payload(question, item), company)
            check_sample_c01_detail(PROMPT, outbound)
            cases.append({"case_id": qid + "/" + item["episode"]["episode_id"],
                          "payload": outbound, "item": item, "question": question})
    return context, raw, reference, company, gold, cases


def score_case(case, response, company, gold):
    """Separate status, citation and structural errors; do not repair model responses."""
    positive = case["item"]["episode"]["episode_id"] == gold["positive_episode"]
    counts = {"question_status": [0, len(case["question"]["content_checks"])],
              "claim_link": [0, 1], "leaf_status": [0, 3], "requirement_root": [0, 1],
              "evidence_sufficiency": [0, len(case["question"]["content_checks"]) + 4]}
    result = {"case_id": case["case_id"], "structurally_valid": False, "error": None,
              "counts": counts, "false_positive_company_support": 0, "details": []}
    try:
        support, _ = company_support(response, case["item"], company)
        _assess({"checks": response["checks"]}, case["item"], case["question"])
    except (LLMError, KeyError, TypeError, ValueError) as error:
        result["error"] = error.code if isinstance(error, LLMError) else "INVALID_RESPONSE"
        return result
    result["structurally_valid"] = True

    def citations(refs, expected):
        if expected is None:
            return refs == []
        return set(expected["required_units"]) <= set(refs) <= set(expected["allowed_units"])

    def record(kind, key, actual, wanted, refs_ok):
        correct = all(value == wanted for value in actual)
        counts[kind][0] += int(correct)
        if kind != "requirement_root":
            counts["evidence_sufficiency"][0] += int(correct and refs_ok)
        result["details"].append({"kind": kind, "id": key, "expected": wanted,
                                  "actual_by_source": actual, "correct": correct,
                                  "evidence_sufficient": refs_ok})
        if kind in {"claim_link", "leaf_status"} and wanted != "SUPPORTED" and "SUPPORTED" in actual:
            result["false_positive_company_support"] += 1

    checks = {c["check_id"]: c for c in response["checks"]}
    for check in case["question"]["content_checks"]:
        key = check["id"]
        row = checks[key]
        record("question_status", key, [row["status"]], "SUPPORTED" if positive else "NOT_SHOWN",
               citations(row["evidence_ids"], gold["positive_checks"][key] if positive else None))
    links = {r["id"]: r for r in response["company_links"]}
    claim_ids = [c["id"] for c in company.criteria if c["kind"] == "CLAIM"]
    record("claim_link", "sustained_technical_learning",
           ["SUPPORTED" if key in links else "NOT_SHOWN" for key in claim_ids],
           "SUPPORTED" if positive else "NOT_SHOWN",
           all(citations(links.get(key, {}).get("evidence_ids", []),
                         gold["positive_claim"] if positive else None) for key in claim_ids))
    requirements = [{n["node_id"]: n for n in r["nodes"]} for r in response["requirement_checks"]]
    for key, expected in gold["positive_leaves"].items():
        wanted = "SUPPORTED" if positive and expected else "NOT_SHOWN"
        record("leaf_status", key, [r[key]["status"] for r in requirements], wanted,
               all(citations(r[key]["evidence_ids"], expected if positive else None) for r in requirements))
    roots = [s["status"] for s in support if s["kind"] == "REQUIREMENT"]
    record("requirement_root", "apply_AND_validation_OR_documentation", roots,
           "SUPPORTED" if positive else "NOT_SHOWN", True)
    return result


def score_judgment(records, cases, company, gold):
    expected = {case["case_id"]: case for case in cases}
    mapped = {}
    for row in records:
        key = row["case_id"]
        if key not in expected or key in mapped:
            raise ValueError("Unknown or duplicate evaluation case")
        mapped[key] = row
    scored = []
    for key, case in expected.items():
        row = mapped.get(key, {})
        result = score_case(case, row.get("response"), company, gold)
        if row.get("error") or not row:
            result["error"] = row.get("error", "MISSING_RECORD")
            result["structurally_valid"] = False
            result["counts"] = {k: [0, v[1]] for k, v in result["counts"].items()}
        scored.append(result)
    totals = {key: [sum(r["counts"][key][0] for r in scored), sum(r["counts"][key][1] for r in scored)]
              for key in scored[0]["counts"]}
    false_support = sum(r["false_positive_company_support"] for r in scored)
    valid = sum(r["structurally_valid"] for r in scored)
    return {"scheduled_requests": len(cases), "valid_requests": valid, "counts": totals,
            "scores": {key: round(100 * n / d, 6) for key, (n, d) in totals.items()},
            "false_positive_company_support": false_support,
            "demo_gate_passed": valid == len(cases) and false_support == 0
                                and totals["evidence_sufficiency"][0] == totals["evidence_sufficiency"][1],
            "details": scored}


def validate_capture(previous_protocol, previous_configuration, *, raw, reference, cases, manifest, fit_target):
    if (previous_protocol["raw"] != raw or previous_protocol["extraction_reference"] != reference
            or previous_protocol["cases"] != cases or previous_configuration["models"] != manifest
            or previous_configuration["prompt_version"] != PROMPT_VERSION
            or previous_configuration["fit_target_mib"] != fit_target):
        raise ValueError("Captured inference input, model or generation settings differ")


def audit_demo_result(result, gold):
    """HTTP success is insufficient: inspect the fixed-fiction final company links too."""
    expected_ids = {gold["positive_episode"], *gold["negative_episodes"]}
    candidates = result.get("candidates", [])
    actual_ids = [c["episode_id"] for c in candidates]
    failures = []
    if len(actual_ids) != len(set(actual_ids)) or set(actual_ids) != expected_ids:
        failures.append({"code": "CANDIDATE_COVERAGE_MISMATCH"})
    for candidate in candidates:
        positive = candidate["episode_id"] == gold["positive_episode"]
        expected_status = "DIRECT_MATCH" if positive else "NEEDS_CONFIRMATION"
        if candidate["status"] != expected_status:
            failures.append({"code": "CANDIDATE_STATUS_MISMATCH", "episode_id": candidate["episode_id"]})
        for check in candidate["content_checks"]:
            if check["status"] != ("SUPPORTED" if positive else "NOT_SHOWN"):
                failures.append({"code": "QUESTION_CHECK_MISMATCH", "episode_id": candidate["episode_id"],
                                 "check_id": check["check_id"]})
        support = candidate["company_support"]
        if positive and (len(support) != 4 or any(s["status"] != "SUPPORTED" for s in support)):
            failures.append({"code": "POSITIVE_COMPANY_SUPPORT_MISSING", "episode_id": candidate["episode_id"]})
        for row in support:
            if not positive and row["status"] == "SUPPORTED":
                failures.append({"code": "FALSE_POSITIVE_COMPANY_SUPPORT", "episode_id": candidate["episode_id"],
                                 "kind": row["kind"], "criterion_id": row["id"],
                                 "criterion_text": row["statement"],
                                 "cited_quotes": [e["exact_quote"] for e in row["episode_evidence"]]})
    return {"scope": "FIXED_THREE_EXPERIENCE_EXPERTISE_DEMO", "quality_passed": not failures,
            "reference_review": gold["review_status"], "failures": failures,
            "production_selection": "WITHHELD"}


class SwitchingClient:
    """A nonlogging live service client; a single GPU switches once between stages."""
    simulated = False
    content_logging_enabled = False

    def __init__(self, manager, model):
        self.manager, self.spec = manager, model
        self.provider, self.model = model["provider"], model["model"]

    def complete_json(self, **kwargs):
        self.manager.ensure(self.spec)
        return self.manager.client.complete_json(**kwargs)


class ModelManager:
    def __init__(self, args, folder, models):
        self.args, self.folder, self.models = args, folder, models
        self.key = self.process = self.log = self.client = None
        self.calls = []

    def close(self):
        if self.process is not None:
            self.calls.extend({**call, "provider": self.client.provider, "model": self.client.model,
                               "model_sha256": self.client.model_sha256} for call in self.client.calls)
            RUNNER.stop_server(self.process)
            self.log.close()
            self.key = self.process = self.log = self.client = None

    def ensure(self, spec):
        if self.key == spec["candidate_id"]:
            return
        self.close()
        folder = self.folder / spec["candidate_id"]
        folder.mkdir()
        process, log = RUNNER.start_server(self.args.runtime, spec, self.models, folder, self.args.port,
                                          v2=True, fit_target_mib=self.args.fit_target_mib)
        self.process, self.log, self.key = process, log, spec["candidate_id"]
        self.client = LocalClient(provider=spec["provider"], model=spec["model"], model_sha256=spec["sha256"],
                                  port=self.args.port, structured=True, capture_traces=False)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=Path(tempfile.gettempdir()) /
                        "epick-w4-local-models-b10859/runtime/llama-server.exe")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--fit-target-mib", type=int, default=6144)
    parser.add_argument("--rescore-from", type=Path,
                        help="Re-score captured identical inputs with current draft reference; only the final API demo calls models afresh")
    args = parser.parse_args()
    if args.output_dir.exists() or not args.runtime.is_file() or args.fit_target_mib < 1:
        parser.error("Use a new output directory, an installed runtime, and positive fit target")
    output = args.output_dir.resolve()
    output.mkdir(parents=True)
    context, raw, reference, company, gold, cases = protocol()
    manifest = read(ROOT / "samples/evaluation/local-models.v2.json")
    models = {m["candidate_id"]: m for m in manifest["models"]}
    previous = args.rescore_from.resolve() if args.rescore_from else None
    if previous:
        validate_capture(read(previous / "protocol.json"), read(previous / "configuration.json"),
                         raw=raw, reference=reference, cases=cases, manifest=manifest, fit_target=args.fit_target_mib)
        captured = [previous / "protocol.json", previous / "configuration.json", previous / "summary.json"]
        captured += [previous / cid / name for cid in models for name in
                     ("extraction-run.json", "judgment-responses.json", "http-traces.json")]
        write(output / "capture-provenance.json", {"mode": "RESCORED_CAPTURED_INFERENCE_NOT_FRESH_CALLS",
              "parent": str(previous), "reference_revision": gold["schema_version"],
              "files_sha256": {str(p.relative_to(previous)): hashlib.sha256(p.read_bytes()).hexdigest() for p in captured}})
    write(output / "protocol.json", {"context": context, "raw": raw, "extraction_reference": reference,
                                     "judgment_reference": gold, "cases": cases,
                                     "judgment_input": "DRAFT_REFERENCE_CLASSIFICATIONS_NOT_MODEL_CAPTURE"})
    write(output / "configuration.json", {"models": manifest, "prompt_version": PROMPT_VERSION,
                                          "runtime_path": str(args.runtime),
                                          "runtime_sha256": hashlib.sha256(args.runtime.read_bytes()).hexdigest(),
                                          "fit_target_mib": args.fit_target_mib, "repetitions": 1,
                                          "evaluation_mode": "RESCORED_CAPTURE" if previous else "FRESH_CAPTURE",
                                          "production_selection": "WITHHELD", "created_at": datetime.now(timezone.utc).isoformat()})
    frozen = {}
    paths = list((ROOT / "epick_w4").glob("*.py")) + list((ROOT / "epick_w4").glob("*.json"))
    paths += [Path(__file__), ROOT / "scripts/run-local-evaluation.py", ROOT / "examples/w4_c01_demo.py",
              ROOT / "samples/c01/evaluation-reference.draft.json"]
    for path in paths:
        relative = str(path.relative_to(ROOT))
        frozen[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
        target = output / "source" / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    write(output / "source-sha256.json", frozen)
    extraction_runs, judgment_scores, calls, reused_calls = [], [], 0, 0
    for model in manifest["models"]:
        folder = output / model["candidate_id"]
        folder.mkdir()
        if previous:
            run = read(previous / model["candidate_id"] / "extraction-run.json")
            records = read(previous / model["candidate_id"] / "judgment-responses.json")
            traces = read(previous / model["candidate_id"] / "http-traces.json")
            extraction_runs.append(run)
            reused_calls += len(traces)
            write(folder / "extraction-run.json", run)
            write(folder / "judgment-responses.json", records)
            write(folder / "reused-http-traces.json", traces)
            scored = {"candidate_id": model["candidate_id"], **score_judgment(records, cases, company, gold)}
            judgment_scores.append(scored)
            write(folder / "judgment-scores.json", scored)
            continue
        process, log = RUNNER.start_server(args.runtime, model, Path(manifest["model_directory"]), folder,
                                          args.port, v2=True, fit_target_mib=args.fit_target_mib)
        client = RUNNER.ProgressClient(provider=model["provider"], model=model["model"],
                                       model_sha256=model["sha256"], port=args.port, structured=True)
        records = []
        try:
            run = collect_extraction_run(raw, reference, client, candidate_id=model["candidate_id"], repetitions=1)
            extraction_runs.append(run)
            write(folder / "extraction-run.json", run)
            for case in cases:
                row = {"case_id": case["case_id"], "response": None, "error": None}
                try:
                    row["response"] = client.complete_json(stage="c01_company_details", system_prompt=PROMPT,
                                                           payload=deepcopy(case["payload"]))
                except LLMError as error:
                    row["error"] = error.code
                records.append(row)
            write(folder / "judgment-responses.json", records)
            scored = {"candidate_id": model["candidate_id"], **score_judgment(records, cases, company, gold)}
            judgment_scores.append(scored)
            write(folder / "judgment-scores.json", scored)
        finally:
            calls += len(client.calls)
            write(folder / "http-traces.json", client.calls)
            RUNNER.stop_server(process)
            log.close()
    extraction_scores = evaluate_extraction_runs(raw, reference, extraction_runs)
    write(output / "extraction-scores.json", extraction_scores)
    write(output / "judgment-scores.json", judgment_scores)
    extractors = [r for r in extraction_scores["results"] if r["required_checks_passed"]]
    judges = [r for r in judgment_scores if r["demo_gate_passed"]]
    selected = {"production_status": "WITHHELD", "demo_status": "BLOCKED_BY_PILOT_GATES"}
    if extractors and judges:
        extractor = min(extractors, key=lambda r: (-r["scores"]["unit_exact"], models[r["candidate_id"]]["size"], r["candidate_id"]))
        judge = min(judges, key=lambda r: (-r["scores"]["leaf_status"], -r["scores"]["question_status"],
                                          -r["scores"]["claim_link"], models[r["candidate_id"]]["size"], r["candidate_id"]))
        selected.update(demo_status="EXPLORATORY_PAIR_ONLY", extractor=extractor["candidate_id"], judge=judge["candidate_id"])
        demo = output / "live-api-demo"
        demo.mkdir()
        manager = ModelManager(args, demo, Path(manifest["model_directory"]))
        live_context = build_context()
        backend = C01DemoBackend(live_context)
        consumer = C01Consumer(demo / "consumer.sqlite", transport=FixtureTransport(live_context), max_cache_ttl_seconds=300)
        try:
            from fastapi import FastAPI
            import httpx
            from epick_w4.api import create_service_router
            consumer.drain()
            app = FastAPI()
            app.include_router(create_service_router(authenticate=lambda: "user-demo", backend=backend,
                extraction_client_factory=lambda: SwitchingClient(manager, models[extractor["candidate_id"]]),
                client_factory=lambda: SwitchingClient(manager, models[judge["candidate_id"]]), c01_consumer=consumer))

            async def request():
                async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://w4.local") as client:
                    return await client.post("/w4/recommend-from-raw", json=service_read("request.json"))

            response = asyncio.run(request())
            value = response.json()
            if response.status_code == 200:
                C01ServiceOutput.model_validate(value)
                quality = audit_demo_result(value, gold)
                write(demo / "quality-audit.json", quality)
                selected["demo_quality_passed"] = quality["quality_passed"]
            write(demo / "response.json", value)
            selected["http_status"] = response.status_code
        finally:
            manager.close()
            consumer.close()
            write(demo / "call-metadata.json", manager.calls)
            calls += len(manager.calls)
            selected["fresh_api_model_calls"] = len(manager.calls)
            selected["backend_and_w3_state"] = "SYNTHETIC_FIXTURES"
    assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in frozen.items())
    write(output / "summary.json", {"status": "MEASURED_NOT_PRODUCTION_SELECTED", "models": len(models),
                                    "real_model_calls": calls, "simulated_model_calls": 0,
                                    "reused_captured_model_calls": reused_calls,
                                    "extraction_requests_per_model": 3, "judgment_requests_per_model": len(cases),
                                    "existing_fictional_experiences": 3, "reference_review": gold["review_status"],
                                    "selected": selected, "source_hashes_unchanged": True})
    print(json.dumps({"status": "C01_PILOT_FINISHED", "real_model_calls": calls, "selected": selected}), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
