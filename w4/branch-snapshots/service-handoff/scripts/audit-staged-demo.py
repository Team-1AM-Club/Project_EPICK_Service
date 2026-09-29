"""Replay captured responses for audit only; this command makes no model calls."""

import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import sys


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--directory", type=Path, required=True)
    args = parser.parse_args()
    folder = args.directory.resolve()
    runtime = folder / "source"
    sys.path.insert(0, str(runtime))
    from epick_w4 import extract_evidence, recommend
    from epick_w4.detail_contract import catalog, question_spec
    from epick_w4.detailed_recommendation import recommend_extracted
    from epick_w4.output_schemas import schema_for
    from epick_w4.synthetic_policy import check_sample_payload
    for relative, expected in read(folder / "code-manifest.json").items():
        assert hashlib.sha256((runtime / relative).read_bytes()).hexdigest() == expected, relative
    config = read(folder / "configuration.json")
    source = read(folder / "raw-input.json")
    extraction = read(folder / "live-extraction.json")
    all_traces = []

    class CapturedReplay:
        # Preserve measured-output metadata solely to compare recomputation byte-for-byte.
        # No transport exists in this class. The audit is not a CAPTURED measurement run.
        simulated = False
        def __init__(self, phase, model):
            self.provider, self.model = model["provider"], model["model"]
            self.traces = read(folder / phase / "http-traces.json")
            self.index = 0
            all_traces.extend(self.traces)
        def complete_json(self, *, stage, system_prompt, payload):
            trace = self.traces[self.index]; self.index += 1
            assert trace["error"] is None and trace["stage"] == stage
            assert trace["request"]["model"] == trace["response"]["model"] == self.model
            messages = trace["request"]["messages"]
            assert messages[0] == {"role": "system", "content": system_prompt}
            assert messages[1]["role"] == "user" and json.loads(messages[1]["content"]) == payload
            check_sample_payload(stage, system_prompt, payload)
            assert trace["request"]["response_format"]["schema"] == schema_for(stage, payload)
            choice = trace["response"]["choices"][0]
            assert choice["finish_reason"] == "stop"
            return json.loads(choice["message"]["content"])
        def done(self):
            assert self.index == len(self.traces)

    reused = config.get("extraction_provenance", {}).get("mode") == "REUSED_CAPTURED"
    if reused:
        provenance = config["extraction_provenance"]
        original = Path(provenance["directory"]) / "live-extraction.json"
        assert hashlib.sha256(original.read_bytes()).hexdigest() == provenance["extraction_sha256"]
        assert extraction == read(original)
    else:
        client = CapturedReplay("extraction", config["extractor"])
        assert extract_evidence(source, user_id="user-demo", llm=client) == extraction
        client.done()
    client = CapturedReplay("judgment", config["judge"])
    scenarios = []
    for scenario in ("collaboration", "learning"):
        path = folder / f"live-{scenario}.json"
        if not path.exists():
            continue
        result = recommend(read(folder / f"live-{scenario}-input.json"), user_id="user-demo", llm=client)
        result["inference"]["upstream_extraction"] = extraction["inference"]
        result["inference"]["selection_status"] = "EXPLORATORY_NOT_PRODUCTION_SELECTED"
        assert json.loads(json.dumps(result)) == read(path)
        scenarios.append(scenario)
    detailed = []
    statuses = Counter()
    for question in catalog()["questions"]:
        qid = question["question_id"]
        payload = read(folder / f"detailed-{qid}-input.json")
        result = recommend_extracted(source, extraction, question=question_spec(payload["question"]), top_k=payload["top_k"],
                                     user_id="user-demo", llm=client)
        assert json.loads(json.dumps(result)) == read(folder / f"detailed-{qid}.json")
        for candidate in result["candidates"]:
            raw = next(e["raw_text"] for e in source["episodes"] if e["episode_id"] == candidate["episode_id"])
            for check in candidate["content_checks"]:
                statuses[check["status"]] += 1
                for evidence in check["evidence"]:
                    assert raw[evidence["start"]:evidence["end"]] == evidence["exact_quote"]
        detailed.append({"question_id": qid, "status": result["status"], "candidates": len(result["candidates"])})
    client.done()
    audit = {"status": "PASSED", "method": "CAPTURED_RESPONSE_RECOMPUTATION_NO_MODEL_CALLS",
             "new_captured_calls_in_this_directory": len(all_traces), "reused_extraction": reused,
             "source_files_verified": len(read(folder / "code-manifest.json")),
             "legacy_scenarios": scenarios, "detailed_scenarios": detailed, "check_status_counts": dict(statuses),
             "meaning_accuracy_independently_verified": False}
    output = folder / "audit.json"
    if output.exists():
        assert read(output) == audit
    else:
        output.write_text(json.dumps(audit, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(audit, ensure_ascii=False))


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
