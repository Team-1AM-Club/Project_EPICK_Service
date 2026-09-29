"""Run fingerprinted real local models; retain traces, failures, and draft scores."""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import statistics
import shutil
import subprocess
import sys
import tempfile
import time
import threading
from urllib.request import ProxyHandler, build_opener

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from epick_w4 import extract_evidence, recommend
from epick_w4.extraction_eval import collect_extraction_run, evaluate_extraction_runs
from epick_w4.llm_contract import LLMError
from epick_w4.local_client import LocalClient
from epick_w4.model_eval import collect_run, evaluate_runs
from epick_w4.stage_eval import collect_stage_run, evaluate_stage_runs


def read(path):
    return json.loads(path.read_text(encoding="utf-8"))


def write(path, value):
    with path.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(value, ensure_ascii=False, indent=2) + "\n")


class ProgressClient(LocalClient):
    def complete_json(self, **kwargs):
        try:
            return super().complete_json(**kwargs)
        finally:
            if self.calls:
                item = self.calls[-1]
                print(json.dumps({"model": self.model, "call": len(self.calls),
                                  "stage": kwargs["stage"], "error": item["error"],
                                  "seconds": item["elapsed_seconds"]}), flush=True)


def start_server(executable, model, cache, output, port, *, v2=False, fit_target_mib=1536):
    path = cache / model["filename"]
    # A separately started preparation job may still be finishing this model.
    deadline = time.monotonic() + 600
    partial = path.with_suffix(path.suffix + ".partial")
    while not path.exists() and partial.exists() and time.monotonic() < deadline:
        print(f"Waiting for verified download: {model['filename']} ({partial.stat().st_size} bytes)", flush=True)
        time.sleep(10)
    if not path.is_file() or path.stat().st_size != model["size"]:
        raise ValueError("Run prepare-local-models.py first")
    with path.open("rb") as stream:
        hasher = hashlib.sha256()
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            hasher.update(chunk)
        checksum = hasher.hexdigest()
    if checksum != model["sha256"]:
        raise ValueError("Model checksum changed")
    args = [str(executable), "--model", str(path), "--alias", model["model"],
            "--host", "127.0.0.1", "--port", str(port), "--ctx-size", "8192" if v2 else "16384",
            "--parallel", "1", "--threads", "8",
            "--flash-attn", "on", "--reasoning", "off", "--no-context-shift"]
    args += ["--fit", "on", "--fit-target", str(fit_target_mib)] if v2 else ["--n-gpu-layers", "99"]
    write(output / "server.json", {"args": args, "model": model,
                                   "started_at": datetime.now(timezone.utc).isoformat()})
    log = (output / "server.log").open("x", encoding="utf-8")
    process = subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT,
                               creationflags=subprocess.CREATE_NO_WINDOW)
    opener = build_opener(ProxyHandler({}))
    deadline = time.monotonic() + 180
    try:
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError("Local server exited; inspect server.log")
            try:
                with opener.open(f"http://127.0.0.1:{port}/health", timeout=1) as response:
                    if response.status == 200:
                        return process, log
            except OSError:
                time.sleep(0.5)
        raise TimeoutError("Local server did not become ready")
    except BaseException:
        stop_server(process)
        log.close()
        raise


def stop_server(process):
    if process.poll() is None:
        process.terminate()
        try:
            process.wait(timeout=15)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=5)


class GpuMonitor:
    """Sample whole-device VRAM (includes other apps), not an allocation profiler."""
    def __init__(self):
        self.samples = []
        self.stop_event = threading.Event()
        self.executable = shutil.which("nvidia-smi")
        self.thread = threading.Thread(target=self._run, daemon=True)

    def _run(self):
        while not self.stop_event.is_set():
            try:
                raw = subprocess.check_output([self.executable, "--query-gpu=memory.used,memory.total",
                                               "--format=csv,noheader,nounits"], timeout=3,
                                              creationflags=subprocess.CREATE_NO_WINDOW, text=True)
                used, total = map(int, raw.splitlines()[0].split(","))
                self.samples.append({"at": datetime.now(timezone.utc).isoformat(),
                                     "used_mib": used, "total_mib": total})
            except (OSError, ValueError, subprocess.SubprocessError):
                pass
            self.stop_event.wait(2)

    def start(self):
        if self.executable:
            self.thread.start()

    def stop(self):
        self.stop_event.set()
        if self.thread.is_alive():
            self.thread.join(timeout=5)


def live_pipeline(client, folder, source):
    # Calls the model again on source text: no draft reference or replay input.
    extraction = extract_evidence(source, user_id="user-demo", llm=client)
    write(folder / "live-extraction.json", extraction)
    for scenario in ("collaboration", "learning"):
        request = read(ROOT / f"samples/w4_{scenario}.json")
        request["episodes"] = [deepcopy(item["episode"]) for item in extraction["episodes"]]
        request["snapshot"]["episode_versions"] = {e["episode_id"]: e["version"] for e in request["episodes"]}
        request["excluded_episode_ids"] = []
        request["request_id"] = f"live-local-{scenario}"
        write(folder / f"live-{scenario}-input.json", request)
        try:
            result = recommend(request, user_id="user-demo", llm=client)
            result["inference"]["selection_status"] = "EXPLORATORY_CANDIDATE_NOT_SELECTED_WINNER"
            write(folder / f"live-{scenario}.json", result)
        except LLMError as error:
            write(folder / f"live-{scenario}-error.json", {"error": error.code, "stage": error.stage})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--structured", action="store_true", help="Use the same W4 JSON schemas for both models")
    parser.add_argument("--v2", action="store_true", help="Use the four installed models and separated v2 evaluation")
    parser.add_argument("--v3", action="store_true", help="Use reviewed drafts and new v3 holdout cases")
    parser.add_argument("--v4", action="store_true", help="Use independent source/context/meaning scoring and fresh cases")
    parser.add_argument("--skip-demo", action="store_true", help="Collect evaluation only; integration is a separate run")
    parser.add_argument("--fit-target-mib", type=int, default=1536, help="GPU memory headroom target for v2/v3")
    parser.add_argument("--demo-only", action="store_true", help="Run source-to-recommendation demonstration only")
    parser.add_argument("--candidate", action="append", help="Choose a manifest candidate ID (repeatable)")
    args = parser.parse_args()
    if sum((args.v2, args.v3, args.v4)) > 1:
        parser.error("Choose one evaluation version")
    if args.fit_target_mib < 1:
        parser.error("--fit-target-mib must be positive")
    modern = args.v2 or args.v3 or args.v4
    suffix = ".v4" if args.v4 else ".v3" if args.v3 else ".v2" if args.v2 else ""
    if args.output_dir.exists():
        parser.error("Choose a new output directory; previous runs are immutable.")
    args.output_dir.mkdir(parents=True)
    cache = Path(tempfile.gettempdir()) / "epick-w4-local-models-b10859"
    executable = cache / "runtime/llama-server.exe"
    manifest = read(ROOT / ("samples/evaluation/local-models.v2.json" if modern else "samples/evaluation/local-models.json"))
    if modern:
        cache = Path(manifest["model_directory"])
    benchmark = read(ROOT / f"samples/evaluation/benchmark{suffix}.draft.json")
    policy = read(ROOT / f"samples/evaluation/policy{suffix}.draft.json")
    source = read(ROOT / f"samples/extraction/raw-experiences{suffix}.synthetic.json")
    reference = read(ROOT / f"samples/extraction/benchmark{suffix}.draft.json")
    demo_source = read(ROOT / "samples/extraction/raw-experiences.synthetic.json")
    if args.candidate:
        if set(args.candidate) - {m["candidate_id"] for m in manifest["models"]}:
            parser.error("Unknown candidate ID")
        manifest["models"] = [m for m in manifest["models"] if m["candidate_id"] in args.candidate]
    for name, value in (("models", manifest), ("benchmark", benchmark), ("policy", policy),
                        ("extraction-input", source), ("extraction-reference", reference)):
        write(args.output_dir / f"{name}.json", value)
    tracked = sorted((ROOT / "epick_w4").glob("*.py")) + [Path(__file__)]
    write(args.output_dir / "code-manifest.json", {
        "files": {str(f.relative_to(ROOT)): hashlib.sha256(f.read_bytes()).hexdigest() for f in tracked},
        "runtime_sha256": hashlib.sha256(executable.read_bytes()).hexdigest(),
        "note": "Hashes frozen before model calls. Held-out responses do not modify references or scoring."})
    runs, extraction_runs, performance = [], [], []
    for model in manifest["models"]:
        folder = args.output_dir / model["candidate_id"]
        folder.mkdir()
        monitor = GpuMonitor()
        monitor.start()
        try:
            process, log = start_server(executable, model, cache, folder, 18080, v2=modern,
                                        fit_target_mib=args.fit_target_mib)
        except (OSError, ValueError, RuntimeError, TimeoutError) as error:
            monitor.stop()
            write(folder / "startup-error.json", {"error": type(error).__name__})
            write(folder / "gpu-samples.json", monitor.samples)
            print(json.dumps({"model": model["model"], "error": "SERVER_START_FAILED"}), flush=True)
            continue
        client = None
        try:
            client = ProgressClient(provider=model["provider"], model=model["model"],
                                    model_sha256=model["sha256"], structured=args.structured or modern)
            if not args.demo_only:
                if modern:
                    run = collect_stage_run(benchmark, policy, client, candidate_id=model["candidate_id"], exploratory=True)
                else:
                    run = collect_run(benchmark, policy, client, candidate_id=model["candidate_id"],
                                      generation_config=client.generation_config, exploratory=True)
                write(folder / "question-matching-run.json", run)
                runs.append(run)
                extraction_run = collect_extraction_run(source, reference, client,
                                                        candidate_id=model["candidate_id"],
                                                        repetitions=policy["repetitions"])
                write(folder / "extraction-run.json", extraction_run)
                extraction_runs.append(extraction_run)
            comparison_calls = deepcopy(client.calls)
            if not args.skip_demo:
                try:
                    live_pipeline(client, folder, demo_source)
                except LLMError as error:
                    write(folder / "live-pipeline-error.json", {"error": error.code, "stage": error.stage})
            performance.append({"candidate_id": model["candidate_id"],
                                "comparison_calls": len(comparison_calls),
                                "comparison_failures": sum(c["error"] is not None for c in comparison_calls),
                                "median_request_seconds": statistics.median(c["elapsed_seconds"] for c in comparison_calls) if comparison_calls else None,
                                "total_request_seconds": sum(c["elapsed_seconds"] for c in comparison_calls)})
        finally:
            try:
                write(folder / "http-traces.json", client.calls if client is not None else [])
            finally:
                stop_server(process)
                log.close()
                monitor.stop()
                write(folder / "gpu-samples.json", monitor.samples)
    if args.demo_only or not runs:
        print(json.dumps({"status": "DEMO_ATTEMPTS_FINISHED", "output": str(args.output_dir)}), flush=True)
        return
    scorer = evaluate_stage_runs if modern else evaluate_runs
    report = scorer(benchmark, policy, runs, exploratory=True)
    extraction_report = evaluate_extraction_runs(source, reference, extraction_runs)
    write(args.output_dir / "question-matching-report.json", report)
    write(args.output_dir / "extraction-report.json", extraction_report)
    write(args.output_dir / "performance.json", performance)
    print(json.dumps({"status": "REAL_LOCAL_EVALUATION_COMPLETED", "output": str(args.output_dir),
                      "question_matching": [{k: row[k] for k in ("candidate_id", "scores", "total_score")} for row in report["results"]],
                      "extraction": [{k: row[k] for k in ("candidate_id", "scores")} for row in extraction_report["results"]]}), flush=True)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    main()
