"""Recompute v4 scores and verify every captured request, response and frozen source."""

import argparse
import hashlib
import json
from pathlib import Path
import sys


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def save(path, data):
    data = json.loads(json.dumps(data, ensure_ascii=False))
    if path.exists():
        assert read(path) == data, f"Existing artifact differs: {path.name}"
    else:
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    parser.add_argument("--profile", choices=("a", "b"), required=True)
    args = parser.parse_args()
    root = args.directory.resolve()
    runtime = root / f"profile-{args.profile}-runtime"
    sys.path.insert(0, str(runtime))
    from epick_w4.stage_eval import build_stage_requests, evaluate_stage_runs
    from epick_w4.extraction_eval import build_extraction_requests, evaluate_extraction_runs
    from epick_w4.output_schemas import schema_for
    from epick_w4.synthetic_policy import check_sample_payload
    for relative, expected in read(runtime / "frozen-manifest.json").items():
        assert hashlib.sha256((runtime / relative).read_bytes()).hexdigest() == expected, relative
    benchmark = read(runtime / "samples/evaluation/benchmark.v4.draft.json")
    policy = read(runtime / "samples/evaluation/policy.v4.draft.json")
    source = read(runtime / "samples/extraction/raw-experiences.v4.synthetic.json")
    reference = read(runtime / "samples/extraction/benchmark.v4.draft.json")
    manifest = read(runtime / "samples/evaluation/local-models.v2.json")
    requests = build_stage_requests(benchmark) + build_extraction_requests(source)
    assert len(requests) == 42
    runs, extraction_runs, source_index = [], [], []
    for model in manifest["models"]:
        folder = root / f"{args.profile}-{model['candidate_id']}"
        actual = folder / model["candidate_id"]
        assert read(folder / "benchmark.json") == benchmark
        assert read(folder / "policy.json") == policy
        assert read(folder / "extraction-input.json") == source
        assert read(folder / "extraction-reference.json") == reference
        qrun, erun = read(actual / "question-matching-run.json"), read(actual / "extraction-run.json")
        traces = read(actual / "http-traces.json")
        records = qrun["records"] + erun["records"]
        assert len(traces) == len(records) == len(requests)
        for request, record, trace in zip(requests, records, traces):
            assert trace["error"] is record["error"] is None
            assert trace["request"]["model"] == trace["response"]["model"] == model["model"]
            assert trace["response"]["choices"][0]["finish_reason"] == "stop"
            assert record["response"] == json.loads(trace["response"]["choices"][0]["message"]["content"])
            messages = trace["request"]["messages"]
            assert messages[0] == {"role": "system", "content": request["system_prompt"]}
            assert messages[1]["role"] == "user" and json.loads(messages[1]["content"]) == request["payload"]
            assert trace["stage"] == request["stage"]
            check_sample_payload(request["stage"], request["system_prompt"], request["payload"])
            assert trace["request"]["response_format"]["schema"] == schema_for(request["stage"], request["payload"])
        for relative, expected in read(folder / "code-manifest.json")["files"].items():
            assert hashlib.sha256((runtime / relative).read_bytes()).hexdigest() == expected
        assert qrun["generation_config"]["model_sha256"] == erun["generation_config"]["model_sha256"] == model["sha256"]
        assert json.loads(json.dumps(evaluate_stage_runs(benchmark, policy, [qrun], exploratory=True))) == read(folder / "question-matching-report.json")
        assert evaluate_extraction_runs(source, reference, [erun]) == read(folder / "extraction-report.json")
        runs.append(qrun); extraction_runs.append(erun)
        source_index.append({"candidate_id": model["candidate_id"], "directory": str(folder), "captured_calls": len(traces)})
    save(root / f"{args.profile}-question-matching-report.json", evaluate_stage_runs(benchmark, policy, runs, exploratory=True))
    save(root / f"{args.profile}-extraction-report.json", evaluate_extraction_runs(source, reference, extraction_runs))
    audit = {"status": "PASSED", "profile": args.profile, "captured_calls": len(requests) * len(runs),
             "frozen_files_verified": len(read(runtime / "frozen-manifest.json")), "source_index": source_index}
    save(root / f"{args.profile}-audit.json", audit)
    print(json.dumps({k: v for k, v in audit.items() if k != "source_index"}))


if __name__ == "__main__":
    main()
