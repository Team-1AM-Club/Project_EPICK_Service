"""W4 v2: one-experience requests and separately auditable quality dimensions.

Reference anchors permit longer exact source quotes. No model judges or repairs
another model's output, and draft references never authorize production selection.
"""

from copy import deepcopy
from datetime import datetime, timezone

from .llm_contract import LLMError, parse_json_object
from .model_eval import (
    AXES, EvaluationError, _f1, _matching_payload, approval_valid, digest,
    document_digest, matching_tokens, question_tokens, require, validate_benchmark,
)
from .llm_prompts import CANDIDATE_PROMPT, QUESTION_PROMPT
from .output_schemas import SCHEMAS
from .semantic_matching import single_candidate_payloads

SCORER_VERSION = "w4-separated-grounding/0.2"
COMPONENT_SCORER_VERSION = "w4-source-context-meaning/0.3"


def build_stage_requests(benchmark):
    validate_benchmark(benchmark)
    requests = []
    for case in benchmark["cases"]:
        requests.append({"case_id": case["case_id"], "axis": "question", "episode_id": None,
                         "stage": "question", "system_prompt": QUESTION_PROMPT,
                         "payload": {"question_text": case["question_text"]}})
        if case.get("matching") is not None:
            for payload in single_candidate_payloads(_matching_payload(case)):
                requests.append({"case_id": case["case_id"], "axis": "matching",
                                 "episode_id": payload["candidates"][0]["episode_id"],
                                 "stage": "candidates", "system_prompt": CANDIDATE_PROMPT,
                                 "payload": payload})
    return deepcopy(requests)


def validate_stage_policy(benchmark, policy):
    component = policy.get("schema_version") == "w4-stage-policy/0.3"
    require(component or policy.get("schema_version") == "w4-stage-policy/0.2", "POLICY_VERSION")
    require(policy.get("scorer_version") == (COMPONENT_SCORER_VERSION if component else SCORER_VERSION),
            "SCORER_VERSION_CHANGED")
    require(policy.get("benchmark_sha256") == document_digest(benchmark), "BENCHMARK_CHANGED")
    require(policy.get("protocol_sha256") == digest(build_stage_requests(benchmark)), "PROTOCOL_CHANGED")
    require(type(policy.get("repetitions")) is int and 1 <= policy["repetitions"] <= 10,
            "INVALID_REPETITIONS")
    # These are a declared proposal, not hidden fitted weights or thresholds.
    require(policy.get("weights") == {"question": 50, "matching": 50}, "INVALID_WEIGHTS")
    gates = {
        "contract_valid_percent": 100, "unsafe_fact_admissions": 0,
        "question_quote_valid_percent": 100,
    }
    if component:
        from .evaluation_diagnostics import validate_label_aliases
        gates = {"contract_valid_percent": 100, "literal_quote_valid_percent": 100,
                 "context_preserved_percent": 100, "unsafe_fact_admissions": 0,
                 "unsafe_fact_references": 0}
        for case in benchmark["cases"]:
            validate_label_aliases(case)
    require(policy.get("gates") == gates, "INVALID_GATES")


def collect_stage_run(benchmark, policy, client, *, candidate_id, exploratory=False):
    validate_stage_policy(benchmark, policy)
    require(isinstance(candidate_id, str) and bool(candidate_id), "CANDIDATE_ID_REQUIRED")
    if not client.simulated and not exploratory:
        require(approval_valid(benchmark) and approval_valid(policy), "HUMAN_REVIEW_REQUIRED")
    requests = build_stage_requests(benchmark)
    records = []
    for repeat in range(policy["repetitions"]):
        for request in requests:
            record = {k: request[k] for k in ("case_id", "axis", "episode_id")}
            record.update(repeat=repeat, request_sha256=digest(request), response=None, error=None)
            try:
                record["response"] = client.complete_json(
                    stage=request["stage"], system_prompt=request["system_prompt"],
                    payload=deepcopy(request["payload"]))
            except Exception as error:
                record["error"] = error.code if isinstance(error, LLMError) else "CLIENT_CALL_FAILED"
            records.append(record)
    return {"schema_version": "w4-stage-run/0.2", "candidate_id": candidate_id,
            "provider": client.provider, "model": client.model,
            "generation_config": deepcopy(client.generation_config),
            "provenance": "SIMULATED" if client.simulated else "CAPTURED",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "benchmark_sha256": document_digest(benchmark), "protocol_sha256": digest(requests),
            "repetitions": policy["repetitions"], "records": records}


def shape_valid(value, schema):
    """The small static output-schema vocabulary; references checked separately."""
    if "anyOf" in schema:
        return any(shape_valid(value, option) for option in schema["anyOf"])
    if "const" in schema:
        return type(value) is type(schema["const"]) and value == schema["const"]
    if "enum" in schema:
        return any(type(value) is type(option) and value == option for option in schema["enum"])
    kind = schema.get("type")
    if kind == "object":
        return (isinstance(value, dict) and set(value) == set(schema["properties"])
                and all(shape_valid(value[k], s) for k, s in schema["properties"].items()))
    if kind == "array":
        return (isinstance(value, list) and len(value) >= schema.get("minItems", 0)
                and len(value) <= schema.get("maxItems", len(value))
                and all(shape_valid(v, schema["items"]) for v in value))
    if kind == "string":
        return isinstance(value, str)
    if kind == "integer":
        return type(value) is int and value >= schema.get("minimum", value)
    if kind == "boolean":
        return type(value) is bool
    return False


def _criterion(item):
    return (item["criterion_id"], item["label"] if item["criterion_id"].startswith("custom_") else "")


def score_question(case, answer):
    predicted = {_criterion(c) for c in answer["criteria"]}
    options = []
    for reference in case["question_answers"]:
        wanted = {_criterion(c) for c in reference["criteria"]}
        criteria_correct = (predicted == wanted
                            and answer["needs_confirmation"] == reference["needs_confirmation"])
        required_correct = set(answer["required_facts"]) == set(reference["required_facts"])
        quotes_correct = True
        for actual in answer["criteria"]:
            # A reference is a minimum complete semantic anchor, including any
            # negation needed to preserve meaning. Surrounding text is allowed.
            quote = actual["question_quote"]
            anchors = [c["question_quote"] for c in reference["criteria"]
                       if _criterion(c) == _criterion(actual)]
            quotes_correct &= (bool(quote.strip()) and quote in case["question_text"]
                               and any(anchor in quote for anchor in anchors))
        options.append({"criteria_correct": criteria_correct, "required_facts_correct": required_correct,
                        "question_quote_valid": quotes_correct,
                        "criterion_f1": float(_f1(predicted, wanted)),
                        "correct": criteria_correct and required_correct and quotes_correct})
    return max(options, key=lambda x: (x["correct"], x["criteria_correct"],
                                      x["criterion_f1"], x["question_quote_valid"]))


def score_matching(case, payload, answer):
    predicted, edges = matching_tokens(payload, answer)
    eid = payload["candidates"][0]["episode_id"]
    options = []
    for reference in case["matching"]["answers"]:
        gold = {"candidates": [c for c in reference["candidates"] if c["episode_id"] == eid]}
        wanted, gold_edges = matching_tokens(payload, gold)
        actual_checks = {c["fact_id"]: c for c in answer["candidates"][0]["fact_checks"]}
        gold_checks = {c["fact_id"]: c for c in gold["candidates"][0]["fact_checks"]}
        unsafe = [fid for fid, expected in gold_checks.items()
                  if not expected["usable"] and actual_checks[fid]["usable"]]
        options.append({"correct": predicted == wanted,
                        "matching_edge_f1": float(_f1(edges, gold_edges)),
                        "fact_usability_correct": sum(actual_checks[f]["usable"] == g["usable"]
                                                      for f, g in gold_checks.items()),
                        "fact_count": len(gold_checks), "unsafe_fact_ids": unsafe,
                        "missing": sorted(wanted - predicted), "unexpected": sorted(predicted - wanted)})
    return max(options, key=lambda x: (x["correct"], -len(x["unsafe_fact_ids"]), x["matching_edge_f1"]))


def evaluate_stage_runs(benchmark, policy, runs, *, exploratory=False):
    validate_stage_policy(benchmark, policy)
    component = policy["schema_version"] == "w4-stage-policy/0.3"
    if component:
        from .evaluation_diagnostics import diagnose_matching_response, score_question_components
    require(isinstance(runs, list) and bool(runs), "RUNS_REQUIRED")
    reviewed = approval_valid(benchmark) and approval_valid(policy)
    if not reviewed and not exploratory:
        require(all(r.get("provenance") == "SIMULATED" for r in runs), "HUMAN_REVIEW_REQUIRED")
    requests = build_stage_requests(benchmark)
    expected = {(r["case_id"], r["axis"], r["episode_id"], n): r
                for n in range(policy["repetitions"]) for r in requests}
    cases = {c["case_id"]: c for c in benchmark["cases"]}
    rows, seen, configurations = [], set(), set()
    blockers = [] if reviewed and not exploratory else ["DRAFT_OR_EXPLORATORY_EVALUATION"]
    for run in runs:
        require(run.get("schema_version") == "w4-stage-run/0.2", "RUN_VERSION")
        cid = run.get("candidate_id")
        require(isinstance(cid, str) and bool(cid) and cid not in seen, "INVALID_CANDIDATE_ID")
        seen.add(cid)
        require(all(isinstance(run.get(k), str) and run[k] for k in ("provider", "model")), "MODEL_METADATA_REQUIRED")
        require(isinstance(run.get("generation_config"), dict) and bool(run["generation_config"]), "SETTINGS_REQUIRED")
        config = (run["provider"], run["model"], digest(run["generation_config"]))
        require(config not in configurations, "DUPLICATE_MODEL_CONFIGURATION")
        configurations.add(config)
        require(run["benchmark_sha256"] == document_digest(benchmark), "RUN_BENCHMARK_MISMATCH")
        require(run["protocol_sha256"] == digest(requests), "RUN_PROTOCOL_MISMATCH")
        require(type(run["repetitions"]) is int and run["repetitions"] == policy["repetitions"], "RUN_REPETITIONS_MISMATCH")
        require(run["provenance"] in ("SIMULATED", "CAPTURED", "IMPORTED"), "INVALID_PROVENANCE")
        if run["provenance"] == "SIMULATED" or (run["provenance"] == "IMPORTED" and not approval_valid(run)):
            blockers.append("UNVERIFIED_RUN:" + cid)
        records = {}
        for record in run["records"]:
            key = tuple(record[k] for k in ("case_id", "axis", "episode_id", "repeat"))
            require(key in expected and key not in records, "UNKNOWN_OR_DUPLICATE_RECORD")
            require(record["request_sha256"] == digest(expected[key]), "REQUEST_MISMATCH")
            records[key] = record
        details = []
        for key, request in expected.items():
            record = records.get(key)
            detail = {k: request[k] for k in ("case_id", "axis", "episode_id")}
            detail.update(repeat=key[-1], split=cases[key[0]].get("split", "development"),
                          json_valid=False, schema_valid=False, contract_valid=False, correct=False)
            if component and key[1] == "matching":
                raw_answer = record.get("response") if isinstance(record, dict) else None
                if isinstance(raw_answer, str):
                    try:
                        raw_answer = parse_json_object(raw_answer, request["stage"])
                    except LLMError:
                        raw_answer = None
                detail["safety_diagnostics"] = diagnose_matching_response(cases[key[0]], request["payload"], raw_answer)
            try:
                require(record is not None, "MISSING_RECORD")
                require(record.get("error") is None, "CALL_FAILED")
                answer = record["response"]
                if isinstance(answer, str):
                    answer = parse_json_object(answer, request["stage"])
                require(isinstance(answer, dict), "INVALID_JSON_OBJECT")
                detail["json_valid"] = True
                require(shape_valid(answer, SCHEMAS[request["stage"]]), "INVALID_SCHEMA")
                detail["schema_valid"] = True
                if key[1] == "question":
                    scorer = score_question_components if component else score_question
                    detail.update(scorer(cases[key[0]], answer))
                    question_tokens(cases[key[0]]["question_text"], answer)
                else:
                    detail.update(score_matching(cases[key[0]], request["payload"], answer))
                detail["contract_valid"] = True
            except (EvaluationError, LLMError, ValueError, KeyError, TypeError, AttributeError) as error:
                detail["correct"] = False
                detail["error"] = error.code if isinstance(error, LLMError) else str(error) if isinstance(error, EvaluationError) else "INVALID_MODEL_RESPONSE"
            details.append(detail)
        def percent(items, key):
            return round(100 * sum(bool(d.get(key)) for d in items) / len(items), 6) if items else 0.0
        dimensions = {}
        for axis in AXES:
            subset = [d for d in details if d["axis"] == axis]
            dimensions[axis] = {k: percent(subset, k) for k in ("json_valid", "schema_valid", "contract_valid", "correct")}
            dimensions[axis]["scheduled"] = len(subset)
        questions = [d for d in details if d["axis"] == "question"]
        matches = [d for d in details if d["axis"] == "matching"]
        for k in ("criteria_correct", "required_facts_correct", "question_quote_valid"):
            dimensions["question"][k] = percent(questions, k)
        dimensions["question"]["criterion_f1"] = round(100 * sum(d.get("criterion_f1", 0) for d in questions) / len(questions), 6)
        dimensions["matching"]["edge_f1"] = round(100 * sum(d.get("matching_edge_f1", 0) for d in matches) / len(matches), 6)
        unsafe = sum(len(d.get("unsafe_fact_ids", [])) for d in matches)
        dimensions["matching"]["unsafe_fact_admissions"] = unsafe
        if component:
            for name in ("literal_quote_valid", "context_preserved"):
                dimensions["question"][name] = percent(questions, name)
            audits = [d["safety_diagnostics"] for d in matches]
            dimensions["matching"].update(
                contract_valid_safety_records=sum(d["contract_valid"] for d in matches),
                scheduled_fact_checks=sum(d["scheduled_fact_checks"] for d in audits),
                diagnosed_fact_checks=sum(d["diagnosed_fact_checks"] for d in audits),
                reported_unsafe_fact_admissions=sum(len(d["reported_unsafe_fact_ids"]) for d in audits),
                reported_unsafe_fact_references=sum(len(d["reported_unsafe_reference_ids"]) for d in audits))
        gates = []
        if any(dimensions[a]["contract_valid"] != 100 for a in AXES):
            gates.append("INVALID_OR_MISSING_OUTPUT")
        if component:
            if dimensions["question"]["literal_quote_valid"] != 100:
                gates.append("QUESTION_SOURCE_QUOTE_FAILED")
            if dimensions["question"]["context_preserved"] != 100:
                gates.append("QUESTION_CONTEXT_FAILED")
            if dimensions["matching"]["reported_unsafe_fact_references"]:
                gates.append("UNSAFE_FACT_REFERENCED")
            unsafe = dimensions["matching"]["reported_unsafe_fact_admissions"]
        elif dimensions["question"]["question_quote_valid"] != 100:
            gates.append("QUESTION_GROUNDING_FAILED")
        if unsafe:
            gates.append("UNSAFE_FACT_ADMITTED")
        scores = {a: dimensions[a]["correct"] for a in AXES}
        total = sum(scores.values()) / 2
        if total == 0:
            gates.append("NO_CORRECT_ANSWERS")
        rows.append({"candidate_id": cid, "provider": run["provider"], "model": run["model"],
                     "provenance": run["provenance"], "generation_config": run["generation_config"],
                     "scores": scores, "total_score": round(total, 6), "dimensions": dimensions,
                     "eligible": not gates, "gate_failures": gates,
                     "split_scores": {split: {axis: percent([d for d in details if d["split"] == split and d["axis"] == axis], "correct")
                                               for axis in AXES} for split in sorted({d["split"] for d in details})},
                     "details": details})
    rows.sort(key=lambda r: (-r["total_score"], r["candidate_id"]))
    for row in rows:
        row["rank"] = 1 + sum(r["total_score"] > row["total_score"] for r in rows)
    eligible = [r for r in rows if r["eligible"]]
    if len(rows) < 2:
        blockers.append("AT_LEAST_TWO_CANDIDATES_REQUIRED")
    if not eligible:
        blockers.append("NO_MODEL_PASSED_REQUIRED_GATES")
    winners = [r["candidate_id"] for r in eligible if r["total_score"] == eligible[0]["total_score"]]
    return {"schema_version": "w4-stage-report/0.3" if component else "w4-stage-report/0.2",
            "scorer_version": policy["scorer_version"],
            "benchmark_sha256": document_digest(benchmark), "policy_sha256": document_digest(policy),
            "status": "PREVIEW_ONLY" if blockers else "EVALUATED", "results": rows,
            "recommendation": {"status": "WITHHELD" if blockers else "TIED" if len(winners) != 1 else "HIGHEST_OBSERVED_SCORE",
                               "candidate_ids": [] if blockers else winners, "blockers": blockers},
            "limitations": ["Draft references are not human-approved ground truth.",
                            "Extraction is scored separately and is not included in this 50:50 score.",
                            "Longer quotes are accepted only when they contain a complete reference anchor.",
                            "Per-experience accuracy uses equally weighted candidate requests, not batch accuracy."]}
