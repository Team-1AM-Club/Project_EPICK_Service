"""Actually load an extraction model, then a judgment model, on fixed synthetic data."""

import argparse
from copy import deepcopy
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from epick_w4 import extract_evidence, recommend
from epick_w4.detail_contract import PROMPT_VERSION, catalog, question_spec
from epick_w4.detailed_recommendation import INPUT_SCHEMA, _source_checked, recommend_extracted
from epick_w4.llm_contract import LLMError

spec = importlib.util.spec_from_file_location("local_runner", ROOT / "scripts/run-local-evaluation.py")
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--extractor", default="gemma4-12b-qat")
    parser.add_argument("--judge", default="gemma4-26b-qat")
    parser.add_argument("--reuse-extraction-from", type=Path, help="Reuse a captured extraction with the same raw input and model manifest")
    parser.add_argument("--details-only", action="store_true", help="Measure only the detailed question stage")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists():
        parser.error("Use a new output directory; real traces are immutable")
    manifest = runner.read(ROOT / "samples/evaluation/local-models.v2.json")
    models = {m["candidate_id"]: m for m in manifest["models"]}
    if args.extractor not in models or args.judge not in models:
        parser.error("Unknown installed candidate")
    output.mkdir(parents=True)
    source = runner.read(ROOT / "samples/extraction/raw-experiences.synthetic.json")
    source["request_id"] = "staged-v4-synthetic-demo"
    original_ids = {e["episode_id"] for e in source["episodes"]}
    source["episodes"] += runner.read(ROOT / "samples/extraction/raw-experiences.v4.synthetic.json")["episodes"][:3]
    source["snapshot"]["episode_versions"] = {e["episode_id"]: e["version"] for e in source["episodes"]}
    runner.write(output / "raw-input.json", source)
    paths = sorted((ROOT / "epick_w4").glob("*.py")) + sorted((ROOT / "epick_w4").glob("*.json")) + [Path(__file__), ROOT / "scripts/run-local-evaluation.py"]
    frozen = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    for path in paths:
        target = output / "source" / path.relative_to(ROOT)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(path, target)
    runner.write(output / "code-manifest.json", frozen)
    extraction = None
    reuse = None
    if args.reuse_extraction_from:
        previous = args.reuse_extraction_from.resolve()
        if runner.read(previous / "raw-input.json") != source or runner.read(previous / "configuration.json")["extractor"] != models[args.extractor]:
            raise ValueError("Captured extraction input or model does not match")
        extraction = runner.read(previous / "live-extraction.json")
        _source_checked(source, extraction, "user-demo")
        reuse = {"mode": "REUSED_CAPTURED", "directory": str(previous),
                 "extraction_sha256": hashlib.sha256((previous / "live-extraction.json").read_bytes()).hexdigest()}
        runner.write(output / "live-extraction.json", extraction)
    runner.write(output / "configuration.json", {
        "extractor": models[args.extractor], "judge": models[args.judge], "source_profile": "B / prompts 0.3",
        "details_profile": PROMPT_VERSION, "selection_status": "EXPLORATORY_NOT_PRODUCTION_SELECTED",
        "extraction_provenance": reuse or {"mode": "FRESH_CAPTURED"},
        "note": "Judgment calls are fresh. A reused extraction is explicitly recorded as upstream captured input; no model responses are replayed in measurement."})
    executable = Path(tempfile.gettempdir()) / "epick-w4-local-models-b10859/runtime/llama-server.exe"
    cache = Path(manifest["model_directory"])
    phases = (("judgment", args.judge),) if reuse else (("extraction", args.extractor), ("judgment", args.judge))
    for phase, key in phases:
        model = models[key]
        folder = output / phase
        folder.mkdir()
        process, log = runner.start_server(executable, model, cache, folder, 18080, v2=True,
                                          fit_target_mib=4096 if "26b" in key else 1536)
        client = runner.ProgressClient(provider=model["provider"], model=model["model"], model_sha256=model["sha256"], structured=True)
        try:
            if phase == "extraction":
                extraction = extract_evidence(source, user_id="user-demo", llm=client)
                runner.write(output / "live-extraction.json", extraction)
            else:
                for scenario in (() if args.details_only else ("collaboration", "learning")):
                    payload = runner.read(ROOT / f"samples/w4_{scenario}.json")
                    payload["episodes"] = [deepcopy(e["episode"]) for e in extraction["episodes"] if e["episode"]["episode_id"] in original_ids]
                    payload["snapshot"]["episode_versions"] = {e["episode_id"]: e["version"] for e in payload["episodes"]}
                    payload["excluded_episode_ids"] = []
                    runner.write(output / f"live-{scenario}-input.json", payload)
                    try:
                        result = recommend(payload, user_id="user-demo", llm=client)
                        result["inference"]["upstream_extraction"] = extraction["inference"]
                        result["inference"]["selection_status"] = "EXPLORATORY_NOT_PRODUCTION_SELECTED"
                        runner.write(output / f"live-{scenario}.json", result)
                    except LLMError as error:
                        runner.write(output / f"live-{scenario}-error.json", {"error": error.code, "stage": error.stage})
                for q in catalog()["questions"]:
                    selector = {"scope_id": catalog()["scope_id"], "question_id": q["question_id"]}
                    if q["question_id"] == "self_description":
                        selector["user_theme"] = "꾸준히 배우고 적용하는 태도"  # Fictional user's explicitly selected demo theme.
                    payload = {**deepcopy(source), "schema_version": INPUT_SCHEMA, "question": selector, "top_k": 7}
                    runner.write(output / f"detailed-{q['question_id']}-input.json", payload)
                    try:
                        result = recommend_extracted(source, extraction, question=question_spec(selector), top_k=7,
                                                     user_id="user-demo", llm=client)
                        runner.write(output / f"detailed-{q['question_id']}.json", result)
                    except LLMError as error:
                        runner.write(output / f"detailed-{q['question_id']}-error.json", {"error": error.code, "stage": error.stage})
        finally:
            runner.write(folder / "http-traces.json", client.calls)
            runner.stop_server(process)
            log.close()
    assert all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in frozen.items()), "Runtime changed during live demo"
    print(json.dumps({"status": "STAGED_DEMO_FINISHED", "output": str(output)}, ensure_ascii=False), flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
