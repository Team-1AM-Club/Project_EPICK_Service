"""Run synthetic HTTP integration fixtures. This is not a new LLM benchmark."""

import argparse
import asyncio
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import httpx
from epick_w4.handoff_contract import ServiceOutput
from examples.w4_service_demo import DemoBackend, DemoClient, create_demo_app, read


async def main(out):
    out.mkdir(parents=True, exist_ok=True)
    (out / "demo-summary.json").write_text('{"status": "RUNNING"}\n', encoding="utf-8")
    scenarios = []
    for name, diagnostic in (("synthetic-company", False), ("diagnostic-company", True)):
        backend, extractor, judge = DemoBackend(diagnostic), DemoClient("extraction"), DemoClient("judgment")
        app = create_demo_app(backend, extractor, judge)
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://in-process-test") as client:
            response = await client.post("/w4/recommend-from-raw", json=read("request.json"))
        if response.status_code != 200:
            raise RuntimeError(f"{name}: HTTP {response.status_code}: {response.text}")
        result = response.json()
        # Retain the actual fixture response even if schema/golden comparison fails.
        (out / f"{name}.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        result = ServiceOutput.model_validate(result).model_dump()
        expected = "UNAVAILABLE" if diagnostic else "AVAILABLE"
        if result["processing_status"] != "COMPLETED_WITH_LIMITATIONS" or result["company_context"]["status"] != expected:
            raise RuntimeError(f"{name}: unexpected processing/company status")
        if len(extractor.calls) != 3 or len(judge.calls) != 3:
            raise RuntimeError(f"{name}: expected 3 extraction and 3 judgment fixture calls")
        if not all(c.simulated and c.provider == "simulated-fixture" for c in (extractor, judge)):
            raise RuntimeError(f"{name}: demo must use simulated fixtures")
        golden = read(f"results/{name}.json")
        if result != golden:
            raise RuntimeError(f"{name}: output differs from the reviewed fixture; inspect the change")
        scenarios.append({"name": name, "http_status": response.status_code,
                          "processing_status": result["processing_status"], "company_status": expected,
                          "extraction_calls": len(extractor.calls), "judgment_calls": len(judge.calls),
                          "simulated_calls": len(extractor.calls) + len(judge.calls), "real_model_calls": 0,
                          "schema_valid": True, "matches_reviewed_fixture": True})
        print(name, result["processing_status"], result["company_context"]["status"],
              f"{len(extractor.calls)+len(judge.calls)} simulated calls; 0 real calls")
    (out / "demo-summary.json").write_text(json.dumps({
        "status": "PASSED", "scenarios": scenarios, "real_model_calls": 0,
        "simulated_calls": sum(s["simulated_calls"] for s in scenarios),
    }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=ROOT / "output/service-handoff-20260909")
    asyncio.run(main(parser.parse_args().output_dir))
