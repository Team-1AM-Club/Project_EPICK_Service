"""Consume W3 attestations; never promote manual examples or verify a company."""

from copy import deepcopy
from dataclasses import dataclass
from datetime import date

from pydantic import ValidationError

from .contracts import ContractError
from .handoff_contract import KnowledgeBundle, UnavailableKnowledge


def empty_refs():
    return {"claim_ids": [], "requirement_ids": [], "source_version_ids": [], "evidence_ids": []}


def refs_for(criteria):
    result = empty_refs()
    for item in criteria:
        result["claim_ids" if item["kind"] == "CLAIM" else "requirement_ids"].append(item["id"])
        result["source_version_ids"].append(item["source_version_id"])
        result["evidence_ids"].extend(e["evidence_id"] for e in item["company_evidence"])
    return {k: sorted(set(v)) for k, v in result.items()}


@dataclass(frozen=True)
class CompanyContext:
    criteria: tuple[dict, ...]
    summary: dict
    c01: bool = False


def _index(items, key):
    result = {item[key]: item for item in items}
    if len(result) != len(items):
        raise ContractError("DUPLICATE_COMPANY_REFERENCE", "company_knowledge")
    return result


def consume_company(value, scope_id):
    try:
        if value.get("schema_version") == "w4-knowledge-unavailable/0.1":
            data = UnavailableKnowledge.model_validate(value).model_dump()
            if data["scope_id"] != scope_id:
                raise ContractError("COMPANY_SCOPE_MISMATCH", "company_knowledge")
            return CompanyContext((), {
                "status": "UNAVAILABLE", "knowledge_bundle_id": None, "contract_version": None,
                "input_version_refs": {"source_version_ids": []}, "accepted_refs": empty_refs(),
                "diagnostics": [data["reason"], "NO_USABLE_COMPANY_EVIDENCE"],
                "source_reviews": data["source_reviews"],
            })
        data = KnowledgeBundle.model_validate(value).model_dump()
        as_of = date.fromisoformat(data["as_of"])
    except (ValidationError, ValueError, TypeError, AttributeError):
        raise ContractError("INVALID_COMPANY_CONTRACT", "company_knowledge") from None
    if data["scope_id"] != scope_id:
        raise ContractError("COMPANY_SCOPE_MISMATCH", "company_knowledge")
    sources = _index(data["source_versions"], "source_version_id")
    evidence = _index(data["evidence"], "evidence_id")
    _index(data["source_reviews"], "review_id")
    _index(data["claims"], "claim_id")
    _index(data["explicit_requirements"], "requirement_id")
    versions = data["input_version_refs"]["source_version_ids"]
    if len(set(versions)) != len(versions) or set(versions) != set(sources):
        raise ContractError("COMPANY_VERSION_SNAPSHOT_MISMATCH", "company_knowledge")
    for review in data["source_reviews"]:
        if review["source_version_id"] is not None and review["source_version_id"] not in sources:
            raise ContractError("SOURCE_REVIEW_VERSION_MISMATCH", "company_knowledge")
    for ev in evidence.values():
        if ev["source_version_id"] not in sources:
            raise ContractError("COMPANY_EVIDENCE_VERSION_MISMATCH", "company_knowledge")
    required_limited = any(r["required"] and r["status"] != "USABLE" for r in data["source_reviews"])
    blocked_versions = {r["source_version_id"] for r in data["source_reviews"] if r["status"] != "USABLE"}
    reviewed_versions = {r["source_version_id"] for r in data["source_reviews"] if r["status"] == "USABLE"}
    accepted, diagnostics = [], []
    if required_limited:
        diagnostics.append("REQUIRED_COMPANY_SOURCE_UNAVAILABLE")
    for kind, records, key in (("CLAIM", data["claims"], "claim_id"),
                                ("REQUIREMENT", data["explicit_requirements"], "requirement_id")):
        for record in records:
            source = sources.get(record["source_version_id"])
            refs = record["evidence_ids"]
            code = None
            if record["verification_status"] != "VERIFIED" or record["usage_status"] != "USABLE":
                code = "COMPANY_ATTESTATION_NOT_USABLE"
            elif record["scope_id"] != scope_id or source is None or source["scope_id"] != scope_id:
                code = "COMPANY_REFERENCE_SCOPE_MISMATCH"
            elif required_limited or source["parse_status"] != "PARSED" or source["source_version_id"] in blocked_versions:
                code = "COMPANY_SOURCE_NOT_READY"
            elif source["source_version_id"] not in reviewed_versions:
                code = "COMPANY_SOURCE_REVIEW_MISSING"
            elif len(set(refs)) != len(refs) or any(
                    ref not in evidence or evidence[ref]["source_version_id"] != source["source_version_id"] for ref in refs):
                code = "COMPANY_EVIDENCE_NOT_RESOLVED"
            else:
                try:
                    dates = {name: date.fromisoformat(record[name]) if record[name] else None
                             for name in ("published_at", "valid_from", "valid_to")}
                    source_date = date.fromisoformat(source["published_at"]) if source["published_at"] else None
                    start, end, published = dates["valid_from"], dates["valid_to"], dates["published_at"]
                    if published is None or source_date is None:
                        code = "COMPANY_PUBLICATION_DATE_UNKNOWN"
                    elif published != source_date:
                        code = "COMPANY_PUBLICATION_DATE_MISMATCH"
                    elif published > as_of or (start and as_of < start) or (end and as_of > end):
                        code = "COMPANY_ATTESTATION_OUTSIDE_VALIDITY"
                    elif start and end and start > end:
                        code = "INVALID_COMPANY_DATE"
                    elif start is None or end is None:
                        diagnostics.append("COMPANY_VALIDITY_PARTIALLY_UNKNOWN")
                except ValueError:
                    code = "INVALID_COMPANY_DATE"
            if code:
                diagnostics.append(code)
                continue
            accepted.append({"kind": kind, "id": record[key], "statement": record["statement"],
                             "requirement_type": record.get("requirement_type"),
                             "source_version_id": record["source_version_id"],
                             "company_evidence": [deepcopy(evidence[ref]) for ref in refs]})
    if len(accepted) > 20:
        raise ContractError("COMPANY_CRITERIA_LIMIT_EXCEEDED", "company_knowledge")
    if not accepted:
        diagnostics.append("NO_USABLE_COMPANY_EVIDENCE")
    limited = diagnostics or any(r["codes"] or r["limitations"] or r["status"] != "USABLE" for r in data["source_reviews"])
    return CompanyContext(tuple(accepted), {
        "status": "UNAVAILABLE" if not accepted else "LIMITED" if limited else "AVAILABLE",
        "knowledge_bundle_id": data["knowledge_bundle_id"], "contract_version": data["contract_version"],
        "input_version_refs": deepcopy(data["input_version_refs"]), "accepted_refs": refs_for(accepted),
        "diagnostics": list(dict.fromkeys(diagnostics)), "source_reviews": deepcopy(data["source_reviews"]),
    })


def project_diagnostic_handoff(value, *, scope_id):
    """Explicit rejection projection for the supplied sample, never an ID binding."""
    if not isinstance(value, dict) or value.get("schema_version") != "claim-handoff-sample/0.1-draft":
        raise ContractError("UNSUPPORTED_DIAGNOSTIC_HANDOFF", "company_knowledge")
    reviews = []
    for i, review in enumerate(value.get("source_reviews", [])):
        codes = list(review.get("diagnostic_codes", []))
        if review.get("published_at") is None:
            codes.append("COMPANY_PUBLICATION_DATE_UNKNOWN")
        reviews.append({"review_id": f"local-diagnostic-review-{i + 1}", "source_version_id": None,
                        "required": "ATTACHMENT_UNPARSED" in codes, "status": "UNAVAILABLE",
                        "codes": list(dict.fromkeys(codes)), "limitations": [review["matching_impact"]]})
    return UnavailableKnowledge.model_validate({
        "schema_version": "w4-knowledge-unavailable/0.1", "scope_id": scope_id,
        "reason": "DIAGNOSTIC_SAMPLE_REJECTED", "source_reviews": reviews,
    }).model_dump()
