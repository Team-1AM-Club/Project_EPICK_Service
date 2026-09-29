"""Real staged regression, mixed-model HTTP demo and run-bound W1 fixture demo."""

import argparse
import asyncio
import hashlib
import importlib.util
from pathlib import Path
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from epick_w4.api import create_service_router
from epick_w4.c01_consumer import C01Consumer
from epick_w4.c01_contract import C01ServiceOutput
from epick_w4.c01_staged import PROMPTS, assess
from epick_w4.company_detail import outbound_payload
from epick_w4.detail_contract import question_spec
from epick_w4.detailed_recommendation import model_payload
from epick_w4.llm_contract import LLMError
from epick_w4.w1_bridge import W1ExecutionAdapter
from examples.w4_c01_demo import C01DemoBackend, FixtureTransport, build_context
from examples.w4_c01_domain_demo import protocol
from examples.w4_service_demo import read as service_read
from examples.w4_w1_demo import SyntheticRunStore, candidate_responses, fixture

SPEC = importlib.util.spec_from_file_location("domain_final", ROOT / "scripts/run-c01-domain-evaluation.py")
DOMAIN = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DOMAIN)
read, write, RUNNER = DOMAIN.read, DOMAIN.write, DOMAIN.RUNNER


class SwitchingRuntime:
    """One owned local process at a time; preserve a client per stage/model."""
    def __init__(self, args, manifest, folder):
        self.args, self.manifest, self.folder = args, manifest, folder
        self.process = self.log = self.active = None
        self.loads, self.clients = 0, []

    def client(self, spec):
        runtime = self
        class Client:
            provider, model = spec["provider"], spec["model"]
            simulated = False
            content_logging_enabled = False
            def __init__(self):
                self.actual = RUNNER.ProgressClient(provider=self.provider, model=self.model,
                    model_sha256=spec["sha256"], structured=True, capture_traces=False, port=runtime.args.port)
            @property
            def generation_config(self):
                return self.actual.generation_config
            def complete_json(self, **kwargs):
                runtime.ensure(spec)
                return self.actual.complete_json(**kwargs)
        client = Client()
        self.clients.append(client)
        return client

    def ensure(self, spec):
        if self.active == spec["candidate_id"]:
            return
        self.stop()
        self.loads += 1
        folder = self.folder / f"load-{self.loads:02d}-{spec['candidate_id']}"
        folder.mkdir(parents=True)
        self.process, self.log = RUNNER.start_server(self.args.runtime, spec,
            Path(self.manifest["model_directory"]), folder, self.args.port, v2=True,
            fit_target_mib=self.args.fit_target_mib)
        self.active = spec["candidate_id"]

    def stop(self):
        if self.process is not None:
            RUNNER.stop_server(self.process)
            self.log.close()
        self.process = self.log = self.active = None

    def audit(self):
        return [{"provider": c.provider, "model": c.model, "generation_config": c.actual.generation_config,
                 "content_logging_enabled": c.content_logging_enabled, "calls": c.actual.calls} for c in self.clients]


def http_demo(args, manifest, models, output):
    import httpx
    from fastapi import FastAPI
    folder = output / "gemma12-to-gemma26-http"
    folder.mkdir()
    runtime = SwitchingRuntime(args, manifest, folder)
    context = build_context()
    consumer = C01Consumer(folder / "c01.sqlite", transport=FixtureTransport(context), max_cache_ttl_seconds=300)
    try:
        consumer.drain()
        extractor = runtime.client(models["gemma4-12b-qat"])
        judge = runtime.client(models["gemma4-26b-qat"])
        app = FastAPI()
        app.include_router(create_service_router(authenticate=lambda: "user-demo", backend=C01DemoBackend(context),
            extraction_client_factory=lambda: extractor, client_factory=lambda: judge,
            c01_consumer=consumer, c01_split=True))
        async def request():
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://w4.local") as http:
                return await http.post("/w4/recommend-from-raw", json=service_read("request.json"))
        response = asyncio.run(request())
        write(folder / "response.json", response.json())
        quality = {"http_status": response.status_code, "quality_passed": False}
        if response.status_code == 200:
            C01ServiceOutput.model_validate(response.json())
            quality.update(DOMAIN.PILOT.audit_demo_result(response.json(), read(ROOT / "samples/c01/evaluation-reference.draft.json")))
        write(folder / "quality.json", quality)
        return quality
    finally:
        runtime.stop()
        write(folder / "model-call-audit.json", runtime.audit())
        consumer.close()


def w1_demo(args, manifest, models, output, *, judgment_id="qwen35-9b-q4"):
    from jsonschema import Draft202012Validator
    folder = output / ("gemma12-to-qwen9-w1" if judgment_id == "qwen35-9b-q4" else "gemma12-to-gemma26-w1")
    folder.mkdir()
    runtime = SwitchingRuntime(args, manifest, folder)
    binding, context = fixture()
    consumer = C01Consumer(folder / "c01.sqlite", transport=FixtureTransport(context), max_cache_ttl_seconds=300)
    store = SyntheticRunStore(folder / "synthetic-run.sqlite", binding, context, check_sources=consumer.check_current)
    try:
        consumer.drain()
        extractor, judge = runtime.client(models["gemma4-12b-qat"]), runtime.client(models[judgment_id])
        adapter = W1ExecutionAdapter(store=store, extraction_factory=lambda: extractor,
                                    judgment_factory=lambda: judge, c01_consumer=consumer)
        adapter.execute(owner_user_id=binding.owner_user_id, run_id=binding.run_id)
        value = store.read_result(binding.owner_user_id, binding.run_id)
        if value is None:
            raise ValueError("W1 fixture publication absent")
        write(folder / "publication.json", value)
        rows = candidate_responses(value)
        schema = read(ROOT / "schemas/w1-candidate.pinned.schema.json")
        for row in rows:
            Draft202012Validator(schema, format_checker=Draft202012Validator.FORMAT_CHECKER).validate(row)
        write(folder / "candidate-responses.json", rows)
        before = sum(len(c.actual.calls) for c in runtime.clients)
        adapter.execute(owner_user_id=binding.owner_user_id, run_id=binding.run_id)
        duplicate_extra = sum(len(c.actual.calls) for c in runtime.clients) - before
        store.revoke()
        denied = store.read_result(binding.owner_user_id, binding.run_id) is None
        quality = DOMAIN.PILOT.audit_demo_result(value["full_result"], read(ROOT / "samples/c01/evaluation-reference.draft.json"))
        audit = {"actual_model_calls": before, "duplicate_extra_calls": duplicate_extra,
            "revoked_read_denied": denied, "candidate_dto_count": len(rows), "dto_valid": True,
            "database": "ISOLATED_SYNTHETIC_SQLITE_NOT_W1_POSTGRESQL", "result_origin": value["result_origin"],
            "run_status": value["run_status"], "quality": quality}
        write(folder / "audit.json", audit)
        return audit
    finally:
        runtime.stop()
        write(folder / "model-call-audit.json", runtime.audit())
        consumer.close()
        store.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--extraction", type=Path, default=ROOT / "output/c01-domain-evaluation-20260918/run-3/actual-extraction.json")
    parser.add_argument("--runtime", type=Path, default=Path(tempfile.gettempdir()) / "epick-w4-local-models-b10859/runtime/llama-server.exe")
    parser.add_argument("--port", type=int, default=18080)
    parser.add_argument("--fit-target-mib", type=int, default=2048)
    parser.add_argument("--w1-only", action="store_true", help="Validate the HTTP-proven Gemma12/Gemma26 demo profile in the W1 fixture")
    args = parser.parse_args()
    output = args.output_dir.resolve()
    if output.exists():
        parser.error("Use a new output directory")
    output.mkdir(parents=True)
    if args.w1_only:
        manifest = read(ROOT / "samples/evaluation/local-models.v2.json")
        models = {m["candidate_id"]: m for m in manifest["models"]}
        write(output / "protocol.json", {"purpose": "RECHECK_AFTER_CACHE_AND_PUBLISHED_READ_REVIEW",
            "model_selection": "DEMO_ONLY_NOT_PRODUCTION_SELECTED", "judgment_id": "gemma4-26b-qat",
            "prior_qwen9_quality_failure_preserved": True,
            "source_sha256": {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest()
                for p in [*list((ROOT / "epick_w4").glob("*.py")), ROOT / "examples/w4_w1_demo.py", Path(__file__)]}})
        value = w1_demo(args, manifest, models, output, judgment_id="gemma4-26b-qat")
        write(output / "summary.json", value)
        return
    context, raw, _, company = protocol()
    question = question_spec({"scope_id": context["question_scope_id"], "question_id": "expertise"})
    extraction = read(args.extraction)
    # Source equality/ownership validation is performed by the same pipeline boundary.
    from epick_w4.detailed_recommendation import _source_checked
    _source_checked(raw, extraction, "user-demo")
    gold = read(ROOT / "samples/c01/domain-reference.v1.draft.json")
    for case in gold["cases"]:
        units = [f"u{i + 1}" for i in range(len(case["lines"]))]
        for expected in [*case["checks"].values(), case["claim"], *case["leaves"].values(), case["root"]]:
            if "SUPPORTED" not in expected["statuses"]:
                expected["statuses"] = ["NOT_SHOWN", "AMBIGUOUS"]
            expected["allowed_units"] = units
    gold["schema_version"] = "w4-c01-development-regression/0.2"
    write(output / "protocol.json", {"status": "DEVELOPMENT_CASES_NOT_HELD_OUT", "reference": gold,
        "prompts": PROMPTS, "extraction_reused_from": str(args.extraction),
        "extraction_file_sha256": hashlib.sha256(args.extraction.read_bytes()).hexdigest(),
        "new_extraction_calls_for_regression": 0, "production_selection": "WITHHELD"})
    manifest = read(ROOT / "samples/evaluation/local-models.v2.json")
    models = {m["candidate_id"]: m for m in manifest["models"]}
    paths = list((ROOT / "epick_w4").glob("*.py")) + list((ROOT / "epick_w4").glob("*.json"))
    paths += [Path(__file__), ROOT / "examples/w4_w1_demo.py"]
    frozen = {str(p.relative_to(ROOT)): hashlib.sha256(p.read_bytes()).hexdigest() for p in paths}
    write(output / "source-sha256.json", frozen)
    write(output / "configuration.json", {"models": manifest, "runtime_sha256": hashlib.sha256(args.runtime.read_bytes()).hexdigest(),
                                          "fit_target_mib": args.fit_target_mib, "repetitions": 1})
    scores, actual = [], 0
    for key in ("qwen35-9b-q4", "gemma4-26b-qat"):
        folder = output / key
        records = []
        with DOMAIN.live_model(args, models[key], folder, manifest) as client:
            for item in extraction["episodes"]:
                record = {"case_id": item["episode"]["episode_id"], "response": None, "error": None}
                try:
                    record["response"], _ = assess(client, outbound_payload(model_payload(question, item), company))
                except LLMError as error:
                    record["error"] = error.code
                records.append(record)
                write(folder / f"combined-{len(records):03d}.json", record)
            score = DOMAIN.score_records(records, extraction, company, question, gold)
            write(folder / "scores.json", score)
            scores.append({"candidate_id": key, "scores": score["scores"], "gate": score["frozen_case_gate_passed"]})
            actual += len(client.calls)
    http = http_demo(args, manifest, models, output)
    w1 = w1_demo(args, manifest, models, output)
    for path in output.glob("*/model-call-audit.json"):
        actual += sum(len(item["calls"]) for item in read(path))
    write(output / "summary.json", {"actual_model_calls": actual, "reused_extraction_calls": 7,
        "regression": scores, "http": http, "w1_fixture": w1, "production_selection": "WITHHELD",
        "frozen_files_unchanged": all(hashlib.sha256((ROOT / p).read_bytes()).hexdigest() == h for p, h in frozen.items())})
    print("Regression and both actual mixed-model demos complete", flush=True)


if __name__ == "__main__":
    sys.stdout.reconfigure(encoding="utf-8")
    main()
