"""Consume W3 attestations and references; do not repeat W2/W3 source verification."""

from dataclasses import dataclass
from datetime import date

from .contracts import Request


@dataclass(frozen=True)
class KnowledgeContext:
    claims: tuple[dict, ...]
    diagnostics: tuple[str, ...]

    def summary(self) -> dict:
        return {
            "status": "AVAILABLE" if self.claims else "UNAVAILABLE",
            "accepted_claim_ids": [claim["claim_id"] for claim in self.claims],
            "diagnostics": list(self.diagnostics),
        }


def real_id(value) -> bool:
    return isinstance(value, str) and bool(value.strip()) and "{{" not in value and "}}" not in value


def indexed(records, key: str) -> dict | None:
    if not isinstance(records, list):
        return None
    result = {}
    for record in records:
        if not isinstance(record, dict) or not real_id(record.get(key)) or record[key] in result:
            return None
        result[record[key]] = record
    return result


def consume_knowledge(request: Request) -> KnowledgeContext:
    bundle = request.knowledge
    if not bundle:
        return KnowledgeContext((), ("COMPANY_CONTEXT_NOT_PROVIDED",))
    if (
        bundle.get("evaluation_mode") == "CONTRACT_SAMPLE_DIAGNOSTIC"
        or bundle.get("purpose") == "SAMPLE_DIAGNOSTIC"
        or bundle.get("production_usable") is False
    ):
        return KnowledgeContext((), ("DIAGNOSTIC_COMPANY_SAMPLE_REJECTED",))
    if bundle.get("schema_version") != "w4-company-context/0.1":
        return KnowledgeContext((), ("UNSUPPORTED_COMPANY_SCHEMA",))
    if bundle.get("data_kind") not in ("REAL", "SYNTHETIC"):
        return KnowledgeContext((), ("INVALID_COMPANY_DATA_KIND",))
    if bundle["data_kind"] == "SYNTHETIC" and request.data_kind != "SYNTHETIC":
        return KnowledgeContext((), ("SYNTHETIC_CONTEXT_IN_REAL_REQUEST",))
    if bundle.get("company_id") != request.company_id:
        return KnowledgeContext((), ("COMPANY_SCOPE_MISMATCH",))
    sources = indexed(bundle.get("source_versions"), "source_version_id")
    evidence = indexed(bundle.get("evidence"), "evidence_id")
    claims = indexed(bundle.get("claims"), "claim_id")
    if any(value is None for value in (sources, evidence, claims)):
        return KnowledgeContext((), ("MALFORMED_COMPANY_REFERENCES",))
    accepted = []
    diagnostics = []
    for claim in claims.values():
        code = None
        source_version_id = claim.get("source_version_id")
        source = sources.get(source_version_id) if real_id(source_version_id) else None
        refs = claim.get("evidence_ids")
        scope = claim.get("scope")
        if (
            claim.get("verification_status") != "VERIFIED"
            or claim.get("usage_status") != "ALLOWED"
            or claim.get("usable_for_matching") is not True
        ):
            code = "CLAIM_NOT_VERIFIED_OR_ALLOWED"
        elif not isinstance(claim.get("statement"), str) or not claim["statement"].strip():
            code = "CLAIM_STATEMENT_MISSING"
        elif (
            source is None or source_version_id not in request.source_version_ids
            or source.get("company_id") != request.company_id
            or not real_id(source.get("source_id"))
        ):
            code = "SOURCE_VERSION_NOT_RESOLVED_IN_SNAPSHOT"
        elif not isinstance(scope, dict) or scope.get("company_id") != request.company_id:
            code = "CLAIM_SCOPE_MISMATCH"
        elif scope.get("job_role") not in (None, request.job_role):
            code = "CLAIM_JOB_ROLE_MISMATCH"
        elif scope.get("job_posting_version_id") not in (None, request.posting_version_id):
            code = "CLAIM_POSTING_VERSION_MISMATCH"
        elif not isinstance(refs, list) or not refs or any(not real_id(ref) for ref in refs):
            code = "EVIDENCE_REFERENCE_MISSING"
        elif len(set(refs)) != len(refs):
            code = "DUPLICATE_EVIDENCE_REFERENCE"
        else:
            for ref in refs:
                item = evidence.get(ref)
                if (
                    item is None or item.get("source_version_id") != source_version_id
                    or not isinstance(item.get("exact_quote"), str)
                    or not item["exact_quote"].strip()
                    or not isinstance(item.get("locator"), str) or not item["locator"].strip()
                ):
                    code = "EVIDENCE_NOT_RESOLVED_IN_SOURCE_VERSION"
                    break
        if not code:
            try:
                dates = {
                    name: date.fromisoformat(claim[name]) if claim.get(name) is not None else None
                    for name in ("valid_from", "valid_to", "published_at")
                }
                start, end = dates["valid_from"], dates["valid_to"]
                if start and end and start > end:
                    code = "INVALID_CLAIM_VALIDITY"
                elif (start and request.as_of < start) or (end and request.as_of > end):
                    code = "CLAIM_OUTSIDE_VALIDITY"
                elif dates["published_at"] is None and request.unknown_date_policy == "EXCLUDE":
                    code = "UNDATED_CLAIM_EXCLUDED"
            except (ValueError, TypeError):
                code = "INVALID_CLAIM_DATE"
        if code:
            diagnostics.append(code)
            continue
        # Keep company citations separate from Episode citations and preserve scope.
        accepted.append({
            "claim_id": claim["claim_id"], "statement": claim["statement"],
            "source_version_id": source_version_id, "scope": dict(scope),
            "published_at": claim.get("published_at"),
            "valid_from": claim.get("valid_from"), "valid_to": claim.get("valid_to"),
            "evidence": [{
                "evidence_id": ref, "source_version_id": source_version_id,
                "exact_quote": evidence[ref]["exact_quote"], "locator": evidence[ref]["locator"],
            } for ref in refs],
        })
        if claim.get("published_at") is None:
            diagnostics.append("CLAIM_PUBLICATION_DATE_UNKNOWN")
        if claim.get("valid_from") is None or claim.get("valid_to") is None:
            diagnostics.append("CLAIM_VALIDITY_PARTIALLY_UNKNOWN")
    if not accepted:
        diagnostics.append("NO_USABLE_COMPANY_CLAIMS")
    return KnowledgeContext(tuple(accepted), tuple(dict.fromkeys(diagnostics)))
