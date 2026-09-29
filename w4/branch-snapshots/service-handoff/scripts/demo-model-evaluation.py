"""Exercise scoring with answer fixtures only. No real models, keys, or network.

Run from the repository: python scripts/demo-model-evaluation.py --output-dir output/evaluation-demo
"""

import argparse
import copy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from epick_w4.evaluate import read_document, report_markdown
from epick_w4.model_eval import build_requests, collect_run, digest, evaluate_runs


class DemoFixture:
    provider = "SIMULATED-FIXTURE"
    simulated = True

    def __init__(self, benchmark, *, misses=False):
        self.model = "fixture-with-omissions" if misses else "fixture-reference-copy"
        self.answers = {}
        cases = {case["case_id"]: case for case in benchmark["cases"]}
        for request in build_requests(benchmark):
            case = cases[request["case_id"]]
            answer = copy.deepcopy(case["question_answers"][0] if request["axis"] == "question"
                                   else case["matching"]["answers"][0])
            if misses and case["case_id"] == "paraphrase":
                if request["axis"] == "question":
                    answer["required_facts"] = []
                else:
                    answer["candidates"][0]["matches"] = []
            self.answers[(request["stage"], digest(request["payload"]))] = answer

    def complete_json(self, *, stage, system_prompt, payload):
        return copy.deepcopy(self.answers[(stage, digest(payload))])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    names = ("fixture-reference.json", "fixture-omissions.json", "report.json", "report.md")
    for name in names:
        if (args.output_dir / name).exists():
            parser.error("Choose an output directory without existing demo files.")
    base = ROOT / "samples" / "evaluation"
    benchmark = read_document(base / "benchmark.draft.json")
    policy = read_document(base / "policy.draft.json")
    runs = [collect_run(benchmark, policy, DemoFixture(benchmark, misses=misses),
                        candidate_id="fixture-omissions" if misses else "fixture-reference",
                        generation_config={"scripted_demo": True}) for misses in (False, True)]
    report = evaluate_runs(benchmark, policy, runs)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    for name, value in zip(names[:3], [*runs, report]):
        with (args.output_dir / name).open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")
    with (args.output_dir / "report.md").open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(report_markdown(report))
    print(json.dumps({"status": report["status"], "recommendation": report["recommendation"],
                      "output_directory": str(args.output_dir)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
