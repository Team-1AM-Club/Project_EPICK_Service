"""Output shapes from the W4 prompt contract, without benchmark answers."""

from copy import deepcopy


def obj(properties):
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def array(items, minimum=0):
    return {"type": "array", "items": items, "minItems": minimum}


TEXT = {"type": "string"}
KINDS = {"type": "string", "enum": ["ROLE", "ACTION", "RESULT"]}
CRITERION = {"type": "string", "enum": [
    "collaboration", "problem_solving", "challenge", "leadership", "learning", "responsibility",
    "custom_1", "custom_2", "custom_3", "custom_4", "custom_5", "custom_6"]}

SCHEMAS = {
    "question": obj({
        "criteria": array(obj({"criterion_id": CRITERION, "label": TEXT, "question_quote": TEXT})),
        "required_facts": array(KINDS), "needs_confirmation": {"type": "boolean"},
    }),
    "candidates": obj({
        "candidates": array(obj({
            "episode_id": TEXT, "episode_version": {"type": "integer", "minimum": 1},
            "fact_checks": array(obj({
                "fact_id": TEXT, "usable": {"type": "boolean"},
                "issue": {"enum": [None, "PERSONAL_CONTRIBUTION_UNCLEAR", "NEGATED_OR_AMBIGUOUS_FACT",
                                   "INSUFFICIENT_CONTEXT", "INSTRUCTION_IN_SOURCE"]},
            })),
            "matches": array(obj({"criterion_id": TEXT, "fact_ids": array(TEXT, 1)})),
            "relevance": {"type": "string", "enum": ["RELATED", "UNRELATED", "UNCERTAIN"]},
        })),
    }),
    "extraction": obj({
        "units": array(obj({
            "unit_id": TEXT,
            "subject": {"type": "string", "enum": ["SELF", "TEAM", "OTHER", "UNSPECIFIED", "MIXED"]},
            "assertion": {"type": "string", "enum": [
                "AFFIRMED", "NEGATED", "PLANNED", "HYPOTHETICAL", "UNCERTAIN", "MIXED"]},
            "issue": {"enum": [None, "INSUFFICIENT_CONTEXT", "INSTRUCTION_IN_SOURCE", "NO_EVIDENCE"]},
            "kinds": array({"type": "string", "enum": [
                "ROLE", "ACTION", "RESULT", "GOAL", "PERIOD", "OBSTACLE", "REFLECTION", "CONTEXT"]}),
        })),
    }),
}


def schema_for(stage, payload):
    if stage in ("c01_question", "c01_claims", "c01_requirements"):
        from .c01_staged import schema_for as staged_schema
        return staged_schema(stage, payload)
    """Bind input IDs and enforce coherent states, without supplying judgments."""
    if stage == "c01_company_details":
        from .c01_detail import schema_for as c01_schema
        return c01_schema(payload)
    if stage == "company_details":
        from .company_detail import schema_for as company_schema
        return company_schema(payload)
    if stage == "details":
        from .detail_contract import schema_for as detail_schema
        return detail_schema(payload)
    schema = deepcopy(SCHEMAS[stage])
    if stage == "candidates" and len(payload["candidates"]) == 1:
        source = payload["candidates"][0]
        candidates = schema["properties"]["candidates"]
        candidates.update(minItems=1, maxItems=1)
        fields = candidates["items"]["properties"]
        fields["episode_id"] = {"const": source["episode_id"]}
        fields["episode_version"] = {"const": source["episode_version"]}
        checks = fields["fact_checks"]
        checks.update(minItems=len(source["facts"]), maxItems=len(source["facts"]))
        if source["facts"]:
            checks["items"]["properties"]["fact_id"] = {
                "enum": [fact["fact_id"] for fact in source["facts"]]}
        check_fields = checks["items"]["properties"]
        accepted, rejected = deepcopy(check_fields), deepcopy(check_fields)
        accepted.update(usable={"const": True}, issue={"const": None})
        rejected.update(usable={"const": False}, issue={"enum": [
            "PERSONAL_CONTRIBUTION_UNCLEAR", "NEGATED_OR_AMBIGUOUS_FACT",
            "INSUFFICIENT_CONTEXT", "INSTRUCTION_IN_SOURCE"]})
        checks["items"] = {"anyOf": [obj(accepted), obj(rejected)]}
        criteria = [c["criterion_id"] for c in payload["criteria"]]
        actions = [f["fact_id"] for f in source["facts"] if f["kind"] == "ACTION"]
        fields["matches"]["maxItems"] = len(criteria) if actions else 0
        if criteria and actions:
            matches = fields["matches"]["items"]["properties"]
            matches["criterion_id"] = {"enum": criteria}
            matches["fact_ids"]["items"] = {"enum": actions}
            matches["fact_ids"]["maxItems"] = len(actions)
        related, unrelated = deepcopy(fields), deepcopy(fields)
        related["relevance"] = {"enum": ["RELATED", "UNCERTAIN"]}
        unrelated["relevance"] = {"const": "UNRELATED"}
        unrelated["matches"]["maxItems"] = 0
        candidates["items"] = {"anyOf": [obj(related), obj(unrelated)]}
    elif stage == "extraction":
        units = schema["properties"]["units"]
        units.update(minItems=len(payload["source_units"]), maxItems=len(payload["source_units"]))
        if payload["source_units"]:
            units["items"]["properties"]["unit_id"] = {
                "enum": [unit["unit_id"] for unit in payload["source_units"]]}
        fields = units["items"]["properties"]
        normal, review, discarded = (deepcopy(fields) for _ in range(3))
        normal["subject"]["enum"].remove("MIXED")
        normal["assertion"]["enum"] = ["AFFIRMED", "NEGATED", "PLANNED", "HYPOTHETICAL"]
        normal["issue"] = {"const": None}
        normal["kinds"]["minItems"] = 1
        review["issue"] = {"const": "INSUFFICIENT_CONTEXT"}
        discarded["issue"] = {"enum": ["INSTRUCTION_IN_SOURCE", "NO_EVIDENCE"]}
        discarded["kinds"]["maxItems"] = 0
        units["items"] = {"anyOf": [obj(normal), obj(review), obj(discarded)]}
    return schema
