"""Run the raw-experience extractor with scripted labels; no key or network."""

import argparse
from copy import deepcopy
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from epick_w4 import extract_evidence
from epick_w4.evidence_extraction import model_payload, source_units
from epick_w4.llm_contract import LLMError
from epick_w4.llm_prompts import EXTRACTION_PROMPT
from epick_w4.synthetic_policy import content_hash


class DemoFixture:
    provider = "SIMULATED-FIXTURE"
    model = "source-unit-label-fixture"
    simulated = True

    def __init__(self, request, responses):
        self.responses = {
            content_hash(model_payload(episode, source_units(episode["raw_text"]))):
            responses[episode["episode_id"]] for episode in request["episodes"]
        }

    def complete_json(self, *, stage, system_prompt, payload):
        key = content_hash(payload)
        if stage != "extraction" or system_prompt != EXTRACTION_PROMPT or key not in self.responses:
            raise LLMError("LLM_SYNTHETIC_SAMPLE_REQUIRED", "extraction")
        return deepcopy(self.responses[key])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Choose an output file that does not exist.")
    base = ROOT / "samples" / "extraction"
    request = json.loads((base / "raw-experiences.synthetic.json").read_text(encoding="utf-8"))
    fixture = json.loads((base / "model-output.mock.json").read_text(encoding="utf-8"))
    result = extract_evidence(request, user_id="user-demo",
                              llm=DemoFixture(request, fixture["responses"]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8", newline="\n") as stream:
        stream.write(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps({"status": result["status"], "mode": result["inference"]["mode"],
                      "episodes": len(result["episodes"]), "output": str(args.output)},
                     ensure_ascii=False))


if __name__ == "__main__":
    main()
