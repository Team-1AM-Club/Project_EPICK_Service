"""Write fixed fiction and schemas for review; no model calls or production grants."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from epick_w4.company_context import consume_company, project_diagnostic_handoff
from epick_w4.handoff_contract import KnowledgeBundle, ServerContext, ServiceOutput, ServiceRequest
from epick_w4.synthetic_policy import content_hash


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8"))


def write(path, value):
    target = ROOT / path
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def main():
    scope = "skhynix-2026-march-R260521-IT"
    sv = "synthetic-service-source-v1"
    statements = ["가상 기업은 꾸준한 기술 학습 경험을 참고합니다.",
                  "가상 기업은 배운 검증 규칙을 프로그램에 적용한 경험을 우대합니다."]
    evidence = [{"evidence_id": f"synthetic-company-evidence-{i+1}", "source_version_id": sv,
                 "exact_quote": s, "locator": f"synthetic-document:line-{i+1}"} for i, s in enumerate(statements)]
    common = {"scope_id": scope, "source_version_id": sv, "verification_status": "VERIFIED",
              "usage_status": "USABLE", "published_at": "2026-09-01", "valid_from": "2026-09-01", "valid_to": "2026-12-31"}
    bundle = {"schema_version": "w4-knowledge-bundle/0.1", "knowledge_bundle_id": "synthetic-knowledge-v1",
              "contract_version": "w3-w4-projection/0.1-draft", "scope_id": scope, "data_kind": "SYNTHETIC",
              "as_of": "2026-09-09", "input_version_refs": {"source_version_ids": [sv]},
              "source_versions": [{"source_version_id": sv, "source_id": "synthetic-company-document",
                  "scope_id": scope, "content_sha256": hashlib.sha256("\n".join(statements).encode()).hexdigest(),
                  "published_at": "2026-09-01", "parse_status": "PARSED"}], "evidence": evidence,
              "claims": [{**common, "claim_id": "synthetic-learning-claim", "statement": statements[0],
                          "evidence_ids": [evidence[0]["evidence_id"]]}],
              "explicit_requirements": [{**common, "requirement_id": "synthetic-application-preference",
                          "requirement_type": "PREFERRED", "statement": statements[1], "evidence_ids": [evidence[1]["evidence_id"]]}],
              "source_reviews": [{"review_id": "synthetic-review-v1", "source_version_id": sv,
                                  "required": True, "status": "USABLE", "codes": [], "limitations": []}]}
    raw = read("samples/extraction/raw-experiences.v4.synthetic.json")
    episodes = raw["episodes"][:3]
    context = {"schema_version": "w4-server-context/0.1", "context_version": "synthetic-context-v1",
               "data_kind": "SYNTHETIC", "project": raw["project"], "snapshot": {
                   "snapshot_id": "synthetic-service-snapshot-v1", "episode_versions": {e["episode_id"]: e["version"] for e in episodes}},
               "episodes": episodes, "excluded_episode_ids": [], "question_scope_id": scope, "company_knowledge": bundle}
    request = {"schema_version": "w4-service-input/0.1", "request_id": "synthetic-service-review-v1",
               "project_id": raw["project"]["project_id"], "question": {"scope_id": scope, "question_id": "expertise"}, "top_k": 3}
    folder = "samples/service-handoff"
    write(f"{folder}/request.json", ServiceRequest.model_validate(request).model_dump())
    write(f"{folder}/server-context.synthetic.json", ServerContext.model_validate(context).model_dump())
    unavailable = deepcopy(context)
    unavailable["company_knowledge"] = project_diagnostic_handoff(read("samples/upstream/skhynix_claim_handoff_sample.json"), scope_id=scope)
    write(f"{folder}/server-context.diagnostic.json", ServerContext.model_validate(unavailable).model_dump())
    answers = read("samples/extraction/benchmark.v4.draft.json")["answers"]
    write(f"{folder}/extraction-responses.simulated.json", {e["episode_id"]: answers[e["episode_id"]] for e in episodes})
    criteria = list(consume_company(bundle, scope).criteria)
    write("epick_w4/company_allowlist.json", {"schema_version": "w4-company-fiction-allowlist/0.1",
          "note": "Fixed fictional statements only. These are not verified SK hynix facts.",
          "criteria_sha256": [content_hash([]), content_hash(criteria)]})
    for name, model in (("service-input", ServiceRequest), ("server-context", ServerContext),
                        ("knowledge-bundle", KnowledgeBundle), ("detailed-output", ServiceOutput)):
        schema = model.model_json_schema()
        schema["$schema"] = "https://json-schema.org/draft/2020-12/schema"
        write(f"schemas/w4-{name}.schema.json", schema)
    print("Prepared synthetic service fixtures and 4 JSON schemas.")


if __name__ == "__main__":
    main()
