"""Verify captured model provenance, W1 DTO alignment and final local checks."""
import argparse
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from epick_w4.c01_staged import requests
from epick_w4.company_detail import outbound_payload
from epick_w4.detailed_recommendation import model_payload
from epick_w4.synthetic_policy import content_hash
from examples.w4_c01_fresh_demo import protocol
from epick_w4.c01_adapter import consume_c01
from epick_w4.detail_contract import question_spec
from epick_w4.output_schemas import schema_for


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    from jsonschema import Draft202012Validator
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    base = ROOT / "output/c01-staged-evaluation-20260918"
    output = args.output_dir.resolve()
    output.mkdir(parents=True, exist_ok=False)
    context, _, _ = protocol()
    company = consume_c01(context["company_knowledge"], context["question_scope_id"])
    question = question_spec({"scope_id": context["question_scope_id"], "question_id": "expertise"})
    extraction = read(base / "run-2/actual-extraction.json")
    expected = [request for item in extraction["episodes"]
        for request in requests(outbound_payload(model_payload(question, item), company))]
    checked = 0
    for spec in read(base / "run-2/configuration.json")["models"]["models"]:
        traces = read(base / "run-2" / spec["candidate_id"] / "http-traces.json")
        assert len(traces) == len(expected) == 18
        for trace, (stage, prompt, payload) in zip(traces, expected):
            request, response = trace["request"], trace["response"]
            assert trace["error"] is None and trace["stage"] == stage
            assert request["model"] == response["model"] == spec["model"]
            assert request["messages"][0] == {"role": "system", "content": prompt}
            assert json.loads(request["messages"][1]["content"]) == payload
            assert request["response_format"]["schema"] == schema_for(stage, payload)
            assert response["choices"][0]["finish_reason"] == "stop"
            value = json.loads(response["choices"][0]["message"]["content"])
            Draft202012Validator(schema_for(stage, payload)).validate(value)
            checked += 1
    reference = ROOT / "output/service-reference-20260917"
    git = r"C:\Program Files\Git\cmd\git.exe"
    revisions = subprocess.check_output([git, "-C", str(reference), "rev-parse", "HEAD", "FETCH_HEAD"], text=True).splitlines()
    files = ["backend/app/services/recommendation_execution.py", "backend/app/services/recommendations.py",
             "backend/app/api/schemas/recommendations.py", "backend/app/models/recommendations.py",
             "backend/app/repo/recommendations.py"]
    source_hashes = {}
    for name in files:
        latest = subprocess.check_output([git, "-C", str(reference), "show", "FETCH_HEAD:" + name]).decode("utf-8")
        assert (reference / name).read_text(encoding="utf-8") == latest
        source_hashes[name] = hashlib.sha256(latest.encode()).hexdigest()
    spec = importlib.util.spec_from_file_location("actual_w1_dto", reference / files[2])
    dto = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = dto
    spec.loader.exec_module(dto)
    schema = dto.RecommendationCandidateResponse.model_json_schema()
    assert schema == read(ROOT / "schemas/w1-candidate.pinned.schema.json")
    actual_candidates = read(base / "w1-recheck/gemma12-to-gemma26-w1/candidate-responses.json")
    for candidate in actual_candidates:
        dto.RecommendationCandidateResponse.model_validate(candidate)
    final_test = read(base / "verification-final/summary.json")
    assert final_test["status"] == "PASSED"
    primary, demos, recheck = (read(base / name / "summary.json") for name in ("run-2", "final-demo", "w1-recheck"))
    assert recheck["quality"]["quality_passed"] and recheck["dto_valid"]
    assert recheck["duplicate_extra_calls"] == 0 and recheck["revoked_read_denied"]
    primary_count = sum(len(read(p)) for p in (base / "run-2").glob("*/http-traces.json"))
    assert primary_count == primary["actual_model_calls"] == 78
    logging_checks = []
    for folder in (base / "final-demo", base / "w1-recheck"):
        for path in folder.glob("*/model-call-audit.json"):
            for item in read(path):
                assert item["content_logging_enabled"] is False
                assert all(t["request"] is None and t["response"] is None and t["error"] is None for t in item["calls"])
                logging_checks.append({"model": item["model"], "calls": len(item["calls"])})
    aborted_calls = sum(len(read(p)) for p in (base / "run-1").glob("*/http-traces.json"))
    measured = primary["actual_model_calls"] + demos["actual_model_calls"] + recheck["actual_model_calls"]
    report = {"status": "VERIFIED_LOCAL_DRAFT", "actual_completed_calls_in_complete_runs": measured,
        "aborted_harness_run_completed_calls_excluded": aborted_calls,
        "total_actual_requests_this_work": measured + aborted_calls,
        "reused_earlier_extraction_calls": 7, "new_actual_judgment_payloads_verified": checked,
        "same_actual_extraction_sha256": content_hash(extraction), "actual_w1_dto_candidates_valid": len(actual_candidates),
        "w1_revisions": {"feat_BE": revisions[0], "develop": revisions[1]}, "matching_w1_source_sha256": source_hashes,
        "service_content_logging_checks": logging_checks,
        "tests": {k: final_test["tests"][k] for k in ("tests_run", "passed", "failures", "errors", "skipped")},
        "synthetic_http_regression": final_test["demos"],
        "quality_failures_preserved": {"fresh_gate_passers": primary["passing_demo_candidates"],
            "qwen9_w1_trial": demos["w1_fixture"]["quality"]},
        "team_server_and_postgresql": "NOT_RUN", "production_model_selection": "WITHHELD",
        "human_reference_review": "PENDING", "real_user_data_sent": False}
    (output / "verification.json").write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"verified_calls": measured, "total_actual_requests": measured + aborted_calls,
                      "tests_passed": final_test["tests"]["passed"]}))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
