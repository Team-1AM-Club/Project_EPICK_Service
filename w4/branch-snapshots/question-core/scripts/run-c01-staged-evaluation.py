"""Freeze draft grading, then compare actual local judgments on unseen fiction."""

import argparse
from datetime import datetime, timezone
import hashlib
import importlib.util
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from epick_w4.c01_adapter import consume_c01
from epick_w4.c01_staged import PROMPTS, VERSION, assess
from epick_w4.company_detail import outbound_payload
from epick_w4.detail_contract import question_spec
from epick_w4.detailed_recommendation import model_payload, recommend_extracted
from epick_w4.evidence_extraction import extract_evidence
from epick_w4.extraction_eval import build_extraction_requests, evaluate_extraction_runs
from epick_w4.llm_contract import LLMError
from epick_w4.model_eval import digest
from examples.w4_c01_fresh_demo import protocol

SPEC = importlib.util.spec_from_file_location("domain_runner", ROOT / "scripts/run-c01-domain-evaluation.py")
DOMAIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOMAIN)
read, write = DOMAIN.read, DOMAIN.write


def metrics(scored, records, gold):
    """Separate uncertain omission from an unsupported affirmative; fixed before calls."""
    false_positive, supported_correct, uncertain = 0, 0, 0
    positives = sum("SUPPORTED" in c["statuses"] for case in gold["cases"] for c in case["checks"].values())
    by_id = {r["case_id"]: r for r in records}
    for case in gold["cases"]:
        record = by_id.get(case["case_id"], {})
        if not record.get("response"):
            continue
        response = record["response"]
        for row in response["checks"]:
            expected = case["checks"][row["check_id"]]
            yes = "SUPPORTED" in expected["statuses"]
            supported_correct += int(yes and row["status"] == "SUPPORTED")
            false_positive += int(not yes and row["status"] == "SUPPORTED")
            uncertain += int(not yes and row["status"] == "AMBIGUOUS")
    return {"raw_question_false_support": false_positive,
            "positive_question_checks": positives, "recalled_positive_question_checks": supported_correct,
            "uncertain_negative_question_checks": uncertain,
            "demo_gate_passed": scored["frozen_case_gate_passed"] and false_positive == 0}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--runtime", type=Path, default=Path(tempfile.gettempdir()) /
                        "epick-w4-local-models-b10859/runtime/llama-server.exe")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--fit-target-mib", type=int, default=2048)
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists() or not args.runtime.is_file():
        parser.error("Use a new output directory and the installed runtime")
    output.mkdir(parents=True)
    context, raw, reference = protocol()
    company = consume_c01(context["company_knowledge"], context["question_scope_id"])
    question = question_spec({"scope_id": context["question_scope_id"], "question_id": "expertise"})
    gold = read(ROOT / "samples/c01/fresh-reference.v1.draft.json")
    manifest = read(ROOT / "samples/evaluation/local-models.v2.json")
    models = {m["candidate_id"]: m for m in manifest["models"]}
    write(output / "protocol.json", {"raw": raw, "context": context, "extraction_reference": reference,
        "judgment_reference": gold, "prompts": PROMPTS, "question": question,
        "gate": "All structural, status, sufficient-citation checks pass; no unsupported positives",
        "selection": "Assistant draft only. Highest delivered quality among gate passers; no production approval.",
        "frozen_before_inference": datetime.now(timezone.utc).isoformat()})
    write(output / "configuration.json", {"models": manifest, "protocol_version": VERSION, "repetitions": 1,
        "runtime_sha256": hashlib.sha256(args.runtime.read_bytes()).hexdigest(),
        "fit_target_mib": args.fit_target_mib, "generation": "LocalClient defaults; structured; seed42; temperature0",
        "extraction_candidate_id": "gemma4-12b-qat", "production_selection": "WITHHELD"})
    paths = list((ROOT / "epick_w4").glob("*.py")) + list((ROOT / "epick_w4").glob("*.json"))
    paths += list((ROOT / "examples").glob("w4_c01*.py")) + [Path(__file__), Path(DOMAIN.__file__)]
    frozen = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    for path in paths:
        target = output / "source" / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    write(output / "source-sha256.json", frozen)
    scores, calls = [], 0
    with DOMAIN.live_model(args, models["gemma4-12b-qat"], output / "shared-extraction", manifest) as client:
        extraction = extract_evidence(raw, user_id="user-demo", llm=client)
        requests = {r["case_id"]: r for r in build_extraction_requests(raw)}
        run = {"schema_version": "w4-extraction-run/0.1", "candidate_id": "gemma4-12b-qat",
            "provider": client.provider, "model": client.model, "generation_config": client.generation_config,
            "provenance": "CAPTURED", "created_at": datetime.now(timezone.utc).isoformat(),
            "reference_sha256": digest(reference), "protocol_sha256": digest(list(requests.values())),
            "repetitions": 1, "records": [{**r, "repeat": 0, "request_sha256": digest(requests[r["case_id"]])}
                                         for r in client.records]}
        calls += len(client.calls)
    write(output / "actual-extraction.json", extraction)
    write(output / "extraction-run.json", run)
    write(output / "extraction-scores.json", evaluate_extraction_runs(raw, reference, [run]))
    extraction_hash = digest(extraction)
    for spec in manifest["models"]:
        folder = output / spec["candidate_id"]
        records = []
        with DOMAIN.live_model(args, spec, folder, manifest) as client:
            for item in extraction["episodes"]:
                record = {"case_id": item["episode"]["episode_id"], "response": None, "error": None}
                try:
                    record["response"], _ = assess(client, outbound_payload(model_payload(question, item), company))
                except LLMError as error:
                    record["error"] = error.code
                records.append(record)
                write(folder / f"combined-{len(records):03d}.json", record)
            write(folder / "combined-records.json", records)
            calls += len(client.calls)
            scored = DOMAIN.score_records(records, extraction, company, question, gold)
            scored.update(candidate_id=spec["candidate_id"], shared_extraction_sha256=extraction_hash,
                          **metrics(scored, records, gold))
            write(folder / "scores.json", scored)
            scores.append(scored)
        if all(r["error"] is None for r in records):
            replay = DOMAIN.CapturedClient(spec, read(folder / "http-traces.json"))
            recommendation = recommend_extracted(raw, extraction, question=question, top_k=len(records),
                user_id="user-demo", llm=replay, company_context=company, c01_split=True)
            write(folder / "recommendation.json", recommendation)
        if digest(extraction) != extraction_hash:
            raise ValueError("Shared extraction was mutated")
    unchanged = all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in frozen.items())
    write(output / "summary.json", {"status": "MEASURED_ASSISTANT_DRAFT", "actual_model_calls": calls,
        "simulated_model_calls": 0, "fresh_cases": len(gold["cases"]), "models": len(scores),
        "passing_demo_candidates": [s["candidate_id"] for s in scores if s["demo_gate_passed"]],
        "shared_actual_extraction_sha256": extraction_hash, "frozen_files_unchanged": unchanged,
        "production_selection": "WITHHELD"})
    print("Staged evaluation complete", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
