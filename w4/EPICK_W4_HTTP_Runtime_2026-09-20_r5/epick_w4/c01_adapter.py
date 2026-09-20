"""Validate C01 wire data and preserve W3 semantics in recommendation inputs."""

from copy import deepcopy
from datetime import date
from functools import lru_cache
import hashlib
import json
from pathlib import Path
from uuid import UUID

from jsonschema import Draft202012Validator, FormatChecker

from .c01_contract import C01Knowledge, PROFILE
from .company_context import CompanyContext, refs_for
from .contracts import ContractError
from .llm_contract import LLMError

SCHEMAS = Path(__file__).with_name("c01_schemas")


def require(condition, code="C01_CONTRACT_INVALID"):
    if not condition:
        raise ContractError(code, "company_knowledge")


@lru_cache(maxsize=5)
def validator(name):
    schema = json.loads((SCHEMAS / f"{name}.schema.json").read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    return Draft202012Validator(schema, format_checker=FormatChecker())


def wire(name, value):
    try:
        require(len(json.dumps(value, allow_nan=False)) <= 1_000_000)
        require(validator(name).is_valid(value))
    except (ValueError, TypeError, RecursionError):
        raise ContractError("C01_CONTRACT_INVALID", "company_knowledge") from None
    result = deepcopy(value)
    if name in {"signal", "status"}:
        require(
            all(
                type(result[key]) is int
                for key in (
                    "generation",
                    "event_cursor",
                    "required_event_cursor",
                    "restriction_revision",
                    "required_restriction_revision",
                )
            )
        )
        result["source_id"] = str(UUID(result["source_id"]))
        if result["index_key"]:
            for key in ("source_version_id", "extraction_revision_id"):
                result["index_key"][key] = str(UUID(result["index_key"][key]))
        if name == "signal":
            result["signal_id"] = str(UUID(result["signal_id"]))
            require(result["usable"] == result["index_ack"])
        require(result["restriction_scope"] == "version", "C01_PROFILE_MISMATCH")
        ready = result["reason"] == "READY"
        require(result["index_ack"] == ready)
        if ready:
            require(result["index_key"] is not None)
            require(result["event_cursor"] == result["required_event_cursor"])
            require(result["restriction_revision"] == result["required_restriction_revision"])
        require(result["event_cursor"] <= result["required_event_cursor"])
        require(result["restriction_revision"] <= result["required_restriction_revision"])
    return result


def status_of(signal):
    return {
        key: deepcopy(value)
        for key, value in signal.items()
        if key not in {"event_type", "signal_id", "usable"}
    }


def source_references(node):
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "source_ref" and value is not None:
                yield value
            else:
                yield from source_references(value)
    elif isinstance(node, list):
        for child in node:
            yield from source_references(child)


def validate_tree(record):
    nodes = record["condition_nodes"]
    require(1 <= len(nodes) <= 50, "C01_CONDITION_TREE_INVALID")
    by_id = {node["node_id"]: node for node in nodes}
    require(len(nodes) == len(by_id), "C01_CONDITION_TREE_INVALID")
    parents, visited, active = {key: 0 for key in by_id}, set(), set()

    def visit(key):
        require(key in by_id and key not in active, "C01_CONDITION_TREE_INVALID")
        require(key not in visited, "C01_CONDITION_TREE_INVALID")
        active.add(key)
        node = by_id[key]
        children = node.get("children", [])
        require(
            not children if node["operator"] == "LEAF" else len(children) >= 2,
            "C01_CONDITION_TREE_INVALID",
        )
        require(
            node.get("evidence_id") is None or node["evidence_id"] in record["evidence_ids"],
            "C01_EVIDENCE_REFERENCE_INVALID",
        )
        for child in children:
            require(child in by_id, "C01_CONDITION_TREE_INVALID")
            parents[child] += 1
            visit(child)
        active.remove(key)
        visited.add(key)

    visit(record["root_node_id"])
    require(
        visited == set(by_id)
        and parents[record["root_node_id"]] == 0
        and all(count == 1 for key, count in parents.items() if key != record["root_node_id"]),
        "C01_CONDITION_TREE_INVALID",
    )
    return by_id


def alias(source, kind, identifier):
    return "c01-" + hashlib.sha256(json.dumps([source, kind, identifier]).encode()).hexdigest()


def validate_binding(binding):
    signal = wire("signal", binding["signal"])
    knowledge = wire("knowledge", binding["knowledge"])
    meta = binding["metadata"]
    require(
        signal["index_key"] is not None
        and meta["source_id"] == signal["source_id"]
        and meta["index_key"] == signal["index_key"],
        "C01_INDEX_KEY_MISMATCH",
    )
    require(
        binding["knowledge_generation"] == signal["generation"], "C01_KNOWLEDGE_GENERATION_MISMATCH"
    )
    require(knowledge["status"] in {"COMPLETED", "LIMITED"}, "C01_KNOWLEDGE_NOT_COMPLETED")
    bundle = knowledge["bundle"]
    if bundle["mode"] != "SYNTHETIC" or bundle["purpose"] != "SYNTHETIC_ACCEPTANCE":
        raise LLMError("LLM_REAL_DATA_NOT_ENABLED", "input")
    source, version = signal["source_id"], signal["index_key"]["source_version_id"]
    for ref in source_references(knowledge):
        require(
            ref["source_id"].lower() == source and ref["source_version_id"].lower() == version,
            "C01_KNOWLEDGE_SOURCE_VERSION_MISMATCH",
        )
    evidence = bundle.get("evidences", [])
    ids = [item["evidence_id"] for item in evidence]
    require(0 < len(ids) <= 100 and len(ids) == len(set(ids)), "C01_EVIDENCE_REFERENCE_INVALID")
    for kind in ("claims", "requirements"):
        records = bundle.get(kind, [])
        keys = [item["candidate_id"] for item in records]
        require(len(keys) <= 20 and len(keys) == len(set(keys)), "C01_CANDIDATE_REFERENCE_INVALID")
        for record in records:
            refs = record["evidence_ids"]
            require(
                bool(refs) and len(refs) == len(set(refs)) and set(refs) <= set(ids),
                "C01_EVIDENCE_REFERENCE_INVALID",
            )
            if kind == "requirements":
                validate_tree(record)
    require(bool(bundle["source_reviews"]), "C01_SOURCE_REVIEW_REQUIRED")
    for ev in evidence:
        quote = ev.get("excerpt")
        for field in ("expected_integrity", "observed_integrity"):
            assertion = ev.get(field)
            if quote is not None and assertion and assertion.get("scope", "artifact") == "excerpt":
                require(
                    hashlib.sha256(quote.encode("utf-8")).hexdigest() == assertion["digest"],
                    "C01_EXCERPT_HASH_MISMATCH",
                )
        start, end = ev.get("start_offset"), ev.get("end_offset")
        require(
            (start is None and end is None)
            or (start is not None and end is not None and end >= start),
            "C01_EVIDENCE_REFERENCE_INVALID",
        )
    return signal, knowledge


def consume_c01(value, scope_id):
    data = C01Knowledge.model_validate(value).model_dump()
    require(data["scope_id"] == scope_id, "COMPANY_SCOPE_MISMATCH")
    if data["data_kind"] != "SYNTHETIC":
        raise LLMError("LLM_REAL_DATA_NOT_ENABLED", "input")
    as_of = date.fromisoformat(data["as_of"])
    criteria, diagnostics, reviews, details, dependencies, sources = [], [], [], [], [], set()
    for binding in data["sources"]:
        signal, response = validate_binding(binding)
        source = signal["source_id"]
        require(source not in sources, "C01_DUPLICATE_SOURCE")
        sources.add(source)
        bundle, meta = response["bundle"], binding["metadata"]
        dependencies.append(
            {"profile": PROFILE, **status_of(signal), "expires_at": meta["expires_at"]}
        )
        details.append(
            {
                "source_id": source,
                "metadata": deepcopy(meta),
                "status": response["status"],
                "requirement_presence": bundle["requirement_presence"],
                "source_reviews": deepcopy(bundle["source_reviews"]),
                "errors": deepcopy(response.get("errors", [])),
                "limitations": deepcopy(bundle.get("limitations", [])),
                "attestations": [
                    {
                        "kind": kind,
                        "id": r["candidate_id"],
                        "verification_status": r["verification_status"],
                        "usage_status": r["usage_status"],
                    }
                    for kind, records in (
                        ("CLAIM", bundle.get("claims", [])),
                        ("REQUIREMENT", bundle.get("requirements", [])),
                    )
                    for r in records
                ],
            }
        )
        codes = []
        if not signal["usable"]:
            codes.append("C01_SOURCE_NOT_READY")
        if meta["scope_id"] != scope_id:
            codes.append("COMPANY_REFERENCE_SCOPE_MISMATCH")
        if meta["content_sha256"] is None:
            codes.append("COMPANY_SOURCE_HASH_UNKNOWN")
        if meta["published_at"] is None:
            codes.append("COMPANY_PUBLICATION_DATE_UNKNOWN")
        elif date.fromisoformat(meta["published_at"]) > as_of:
            codes.append("COMPANY_ATTESTATION_OUTSIDE_VALIDITY")
        start, end = (
            date.fromisoformat(meta[key]) if meta[key] else None
            for key in ("valid_from", "valid_to")
        )
        if (start and end and start > end) or (start and as_of < start) or (end and as_of > end):
            codes.append("COMPANY_ATTESTATION_OUTSIDE_VALIDITY")
        if meta["parse_status"] != "PARSED" or any(
            r["process_status"] != "COMPLETED" for r in bundle["source_reviews"]
        ):
            codes.append("COMPANY_SOURCE_NOT_READY")
        limitations = [item["impact"] for item in bundle.get("limitations", [])]
        limitations.extend(
            item["impact"]
            for review in bundle["source_reviews"]
            for item in review.get("limitations", [])
        )
        reviews.append(
            {
                "review_id": alias(source, "review", "source"),
                "source_version_id": meta["index_key"]["source_version_id"],
                "required": meta["required_for_scope"],
                "status": "LIMITED" if codes else "USABLE",
                "codes": codes,
                "limitations": limitations,
            }
        )
        diagnostics.extend(codes)
        if response["status"] == "LIMITED" or limitations or response.get("errors"):
            diagnostics.append("C01_KNOWLEDGE_LIMITED")
        if bundle["requirement_presence"] == "NOT_ASSESSED":
            diagnostics.append("C01_REQUIREMENTS_NOT_ASSESSED")
        if start is None or end is None:
            diagnostics.append("COMPANY_VALIDITY_PARTIALLY_UNKNOWN")
        evidence = {item["evidence_id"]: item for item in bundle.get("evidences", [])}
        for kind, records in (
            ("CLAIM", bundle.get("claims", [])),
            ("REQUIREMENT", bundle.get("requirements", [])),
        ):
            for record in records:
                if (
                    record["verification_status"] != "VERIFIED"
                    or record["usage_status"] != "USABLE"
                ):
                    diagnostics.append("COMPANY_ATTESTATION_NOT_USABLE")
                    continue
                if codes:
                    continue
                cited = [evidence[key] for key in record["evidence_ids"]]
                if any(
                    not ev.get("excerpt") or not ev["excerpt"].strip() or len(ev["excerpt"]) > 6000
                    for ev in cited
                ):
                    diagnostics.append("C01_EXCERPT_NOT_AVAILABLE")
                    continue
                criteria.append(
                    {
                        "kind": kind,
                        "id": alias(source, kind, record["candidate_id"]),
                        "statement": record["statement"]
                        if kind == "CLAIM"
                        else record["original_text"],
                        "requirement_type": record.get("necessity"),
                        "source_id": source,
                        "source_version_id": meta["index_key"]["source_version_id"],
                        "upstream_id": record["candidate_id"],
                        "upstream_record": deepcopy(record),
                        "company_evidence": [
                            {
                                "evidence_id": alias(source, "evidence", ev["evidence_id"]),
                                "source_version_id": meta["index_key"]["source_version_id"],
                                "exact_quote": ev["excerpt"],
                                "locator": ev["native_locator"]["value"]
                                if ev.get("native_locator")
                                else None,
                                "provenance": deepcopy(ev),
                            }
                            for ev in cited
                        ],
                    }
                )
    if any(review["required"] and review["status"] != "USABLE" for review in reviews):
        criteria = []
        diagnostics.append("REQUIRED_COMPANY_SOURCE_UNAVAILABLE")
    require(len(criteria) <= 20, "COMPANY_CRITERIA_LIMIT_EXCEEDED")
    if not criteria:
        diagnostics.append("NO_USABLE_COMPANY_EVIDENCE")
    summary = {
        "status": "UNAVAILABLE" if not criteria else "LIMITED" if diagnostics else "AVAILABLE",
        "knowledge_bundle_id": data["knowledge_bundle_id"],
        "contract_version": PROFILE,
        "input_version_refs": {
            "source_version_ids": sorted(
                {d["index_key"]["source_version_id"] for d in dependencies}
            )
        },
        "accepted_refs": refs_for(criteria),
        "diagnostics": list(dict.fromkeys(diagnostics)),
        "source_reviews": reviews,
        "dependencies": dependencies,
        "upstream_details": details,
    }
    return CompanyContext(tuple(criteria), summary, c01=True)
