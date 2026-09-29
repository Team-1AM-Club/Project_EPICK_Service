"""Publish explicit synthetic fixtures and W4-owned C01 envelope schemas."""

import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from epick_w4.c01_adapter import consume_c01
from epick_w4.c01_contract import C01Knowledge, C01ServerContext, C01ServiceOutput
from epick_w4.evidence_extraction import model_payload, source_units
from epick_w4.synthetic_policy import content_hash
from epick_w4.c01_staged import PROMPTS, project_criteria
from examples.w4_c01_demo import build_context
from examples.w4_c01_domain_demo import build_domain_context, cases, protocol
from examples.w4_c01_fresh_demo import protocol as fresh_protocol, cases as fresh_cases


def write(path, value):
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    # Fixed fixture clock. Runnable demos replace only the test expiry before use.
    context = C01ServerContext.model_validate(build_context(1789603200)).model_dump()
    write("samples/c01/server-context.synthetic.json", context)
    criteria = list(
        consume_c01(context["company_knowledge"], context["question_scope_id"]).criteria
    )
    domain = build_domain_context(1789603200)
    domain_criteria = list(consume_c01(domain["company_knowledge"], domain["question_scope_id"]).criteria)
    write(
        "epick_w4/c01_allowlist.json",
        {
            "note": "Explicit W4 fiction only; domain benchmark references are assistant-authored drafts.",
            "criteria_sha256": [content_hash([]), content_hash(criteria), content_hash(domain_criteria)],
            "staged_criteria_sha256": {stage: [content_hash(project_criteria(value, stage))
                for value in ([], criteria, domain_criteria)] for stage in PROMPTS},
        },
    )
    registered = list(domain["episodes"])
    registered.extend({"episode_id": c["case_id"], "version": 1, "raw_text": "\n".join(c["lines"])}
                      for c in fresh_cases())
    write("samples/c01/fresh-reference.v1.draft.json", {
        "schema_version": "w4-c01-domain-reference/0.2", "review_status": "ASSISTANT_DRAFT",
        "human_reviewed": False, "cases": fresh_cases(),
        "notes": ["Frozen before inference. Correct affirmative status requires sufficient exact source units.",
                  "NOT_SHOWN and AMBIGUOUS are acceptable for absent completion; uncertainty is reported separately.",
                  "Relevant surrounding lines may be cited; necessary evidence units must remain present."]})
    for version in ("v3", "v4"):
        fixed = ROOT / f"samples/extraction/raw-experiences.{version}.synthetic.json"
        registered.extend(json.loads(fixed.read_text(encoding="utf-8"))["episodes"])
    checksums = {content_hash(model_payload(e, source_units(e["raw_text"]))) for e in registered}
    write("epick_w4/extraction_allowlist.json", {"payload_sha256": sorted(checksums)})
    fresh_context, fresh_raw, fresh_reference = fresh_protocol()
    write("samples/c01/fresh-input.synthetic.json", fresh_raw)
    write("samples/c01/fresh-server-context.synthetic.json", fresh_context)
    write("samples/c01/fresh-extraction-reference.draft.json", fresh_reference)
    _, raw, reference, _ = protocol()
    write("samples/c01/domain-server-context.synthetic.json", domain)
    write("samples/c01/domain-input.synthetic.json", raw)
    write("samples/c01/domain-extraction-reference.draft.json", reference)
    write("samples/c01/domain-reference.v1.draft.json", {
        "schema_version": "w4-c01-domain-reference/0.1", "review_status": "ASSISTANT_DRAFT",
        "human_reviewed": False, "cases": cases(),
        "notes": ["Frozen before inference; each semantic condition is counted once across two source copies.",
                  "General question expertise is separate from software-specific company learning.",
                  "General documentation is not restricted to software; code application remains a separate AND leaf.",
                  "Future plans may be NOT_SHOWN or AMBIGUOUS, never SUPPORTED."]})
    for path, model in (
        ("w4-c01-knowledge", C01Knowledge),
        ("w4-server-context-v2", C01ServerContext),
        ("w4-detailed-output-v3", C01ServiceOutput),
    ):
        write(
            f"schemas/{path}.schema.json",
            {
                "$schema": "https://json-schema.org/draft/2020-12/schema",
                **model.model_json_schema(),
            },
        )
    print("Prepared C01 and domain fiction, exact-payload allowlists, draft references and 3 W4 schemas.")


if __name__ == "__main__":
    main()
