"""Deterministic W4 model evaluation against explicitly reviewed reference answers.

No provider is preferred, no LLM judges another LLM, and no reference is generated
by this module. Draft references and simulated runs can only produce previews.
"""

import copy
from datetime import datetime, timezone
from fractions import Fraction
import hashlib
import json
import math

from .llm_contract import LLMError, parse_json_object
from .llm_prompts import CANDIDATE_PROMPT, QUESTION_PROMPT
from .semantic_matching import FACT_ISSUES, SemanticMatcher

SCORER_VERSION = "w4-reviewed-exact/0.1"
AXES = ("question", "matching")


class EvaluationError(ValueError):
    pass


def require(condition, code):
    if not condition:
        raise EvaluationError(code)


def digest(value):
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"),
                     allow_nan=False).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def document_digest(document):
    return digest({k: v for k, v in document.items() if k != "review"})


def approval_valid(document):
    review = document.get("review", {})
    if not isinstance(review, dict):
        return False
    try:
        when = datetime.fromisoformat(review.get("reviewed_at", ""))
        return (review.get("status") == "APPROVED"
                and isinstance(review.get("reviewer"), str) and bool(review["reviewer"].strip())
                and when.utcoffset() is not None
                and review.get("content_sha256") == document_digest(document))
    except (ValueError, TypeError):
        return False


def approve_document(document, reviewer):
    """Call only after the named human actually reviewed this exact document.

    This is an audit record, not authentication or a cryptographic signature.
    """
    require(isinstance(reviewer, str) and bool(reviewer.strip()), "REVIEWER_REQUIRED")
    result = copy.deepcopy(document)
    result["review"] = {"status": "APPROVED", "reviewer": reviewer,
                        "reviewed_at": datetime.now(timezone.utc).isoformat(),
                        "content_sha256": document_digest(document)}
    return result


def _text(value):
    return isinstance(value, str) and bool(value.strip())


def _keys(value, keys):
    require(isinstance(value, dict) and set(value) == set(keys), "INVALID_FIELDS")


class _Replay:
    def __init__(self, answer):
        self.answer = answer

    def complete_json(self, **_):
        return copy.deepcopy(self.answer)


def question_tokens(question_text, answer):
    # Reuse the W4 output/reference checks, but score the RAW answer. The
    # production pipeline's rule-based repairs must not improve a model's score.
    SemanticMatcher(_Replay(answer)).analyze(question_text)
    require(bool(answer["criteria"]) != answer["needs_confirmation"],
            "INCONSISTENT_CONFIRMATION")
    tokens = {("confirmation", str(answer["needs_confirmation"]))}
    for item in answer["criteria"]:
        # Known IDs have a fixed meaning. Custom IDs have no meaning without
        # their label, so human-approved custom labels remain part of the match.
        label = item["label"] if item["criterion_id"].startswith("custom_") else ""
        tokens.add(("criterion", item["criterion_id"], label, item["question_quote"]))
    tokens.update(("required_fact", kind) for kind in answer["required_facts"])
    return tokens


def matching_tokens(payload, answer):
    _keys(answer, ("candidates",))
    require(isinstance(answer["candidates"], list), "INVALID_CANDIDATES")
    originals = {item["episode_id"]: item for item in payload["candidates"]}
    criteria = {item["criterion_id"] for item in payload["criteria"]}
    tokens, edges, seen = set(), set(), set()
    for item in answer["candidates"]:
        _keys(item, ("episode_id", "episode_version", "relevance", "fact_checks", "matches"))
        eid = item["episode_id"]
        require(_text(eid) and eid in originals and eid not in seen, "INVALID_EPISODE")
        seen.add(eid)
        require(type(item["episode_version"]) is int
                and item["episode_version"] == originals[eid]["episode_version"], "INVALID_VERSION")
        require(item["relevance"] in ("RELATED", "UNRELATED", "UNCERTAIN"), "INVALID_RELEVANCE")
        tokens.add(("relevance", eid, item["relevance"]))
        require(isinstance(item["fact_checks"], list), "INVALID_FACT_CHECKS")
        facts = {fact["fact_id"]: fact for fact in originals[eid]["facts"]}
        checks = {}
        for check in item["fact_checks"]:
            _keys(check, ("fact_id", "usable", "issue"))
            fid = check["fact_id"]
            require(_text(fid) and fid in facts and fid not in checks, "INVALID_FACT_REFERENCE")
            require(type(check["usable"]) is bool, "INVALID_USABILITY")
            require(check["issue"] is None if check["usable"] else check["issue"] in FACT_ISSUES,
                    "INVALID_ISSUE")
            checks[fid] = check["usable"]
            tokens.add(("fact", eid, fid, str(check["usable"]), check["issue"] or ""))
        require(set(checks) == set(facts), "INCOMPLETE_FACT_CHECKS")
        require(isinstance(item["matches"], list), "INVALID_MATCHES")
        matched_criteria = set()
        for match in item["matches"]:
            _keys(match, ("criterion_id", "fact_ids"))
            cid, refs = match["criterion_id"], match["fact_ids"]
            require(_text(cid) and cid in criteria and cid not in matched_criteria,
                    "INVALID_CRITERION_REFERENCE")
            matched_criteria.add(cid)
            require(isinstance(refs, list) and bool(refs) and all(_text(ref) for ref in refs),
                    "INVALID_FACT_REFERENCE")
            require(len(refs) == len(set(refs)), "DUPLICATE_FACT_REFERENCE")
            for fid in refs:
                require(fid in facts and checks[fid] and facts[fid]["kind"] == "ACTION",
                        "INVALID_FACT_REFERENCE")
                edges.add((eid, cid, fid))
        require(item["relevance"] != "UNRELATED" or not item["matches"], "UNRELATED_HAS_MATCH")
    require(seen == set(originals), "INCOMPLETE_CANDIDATES")
    tokens.update(("edge", *edge) for edge in edges)
    return tokens, edges


def _matching_payload(case):
    matching = case["matching"]
    basis = matching["basis"]
    criteria = []
    for item in basis["criteria"]:
        start = case["question_text"].find(item["question_quote"])
        criteria.append({"criterion_id": item["criterion_id"], "label": item["label"],
                         "question_evidence": {"quote": item["question_quote"], "start": start,
                                               "end": start + len(item["question_quote"])}})
    return {"question_text": case["question_text"], "criteria": criteria,
            "required_facts": basis["required_facts"], "candidates": matching["candidates"]}


def validate_benchmark(benchmark):
    require(isinstance(benchmark, dict), "INVALID_BENCHMARK")
    require(benchmark.get("schema_version") == "w4-model-benchmark/0.1", "BENCHMARK_VERSION")
    require(benchmark.get("data_kind") == "SYNTHETIC", "SYNTHETIC_BENCHMARK_REQUIRED")
    require(_text(benchmark.get("benchmark_id")), "BENCHMARK_ID_REQUIRED")
    cases = benchmark.get("cases")
    require(isinstance(cases, list) and 1 <= len(cases) <= 1000, "INVALID_CASES")
    seen = set()
    for case in cases:
        require(isinstance(case, dict), "INVALID_CASE")
        cid = case.get("case_id")
        require(_text(cid) and cid not in seen, "DUPLICATE_OR_INVALID_CASE_ID")
        seen.add(cid)
        require(_text(case.get("question_text")) and len(case["question_text"]) <= 4000,
                "INVALID_QUESTION")
        answers = case.get("question_answers")
        require(isinstance(answers, list) and bool(answers), "QUESTION_REFERENCES_REQUIRED")
        for answer in answers:
            question_tokens(case["question_text"], answer)
        matching = case.get("matching")
        if matching is None:
            continue
        require(isinstance(matching, dict), "INVALID_MATCHING_CASE")
        basis = question_tokens(case["question_text"], matching["basis"])
        require(not matching["basis"]["needs_confirmation"], "MATCHING_NEEDS_CLEAR_BASIS")
        require(any(basis == question_tokens(case["question_text"], answer) for answer in answers),
                "BASIS_MUST_BE_ACCEPTED_QUESTION_ANSWER")
        candidates = matching["candidates"]
        require(isinstance(candidates, list) and 1 <= len(candidates) <= 20, "INVALID_CANDIDATE_INPUT")
        episode_ids = set()
        for candidate in candidates:
            _keys(candidate, ("episode_id", "episode_version", "title", "facts"))
            eid = candidate["episode_id"]
            require(_text(eid) and eid not in episode_ids, "INVALID_CANDIDATE_INPUT")
            episode_ids.add(eid)
            require(type(candidate["episode_version"]) is int and candidate["episode_version"] > 0,
                    "INVALID_CANDIDATE_INPUT")
            require(_text(candidate["title"]), "INVALID_CANDIDATE_INPUT")
            require(isinstance(candidate["facts"], list) and len(candidate["facts"]) <= 64,
                    "INVALID_FACT_INPUT")
            fact_ids = set()
            for fact in candidate["facts"]:
                _keys(fact, ("fact_id", "kind", "text"))
                require(_text(fact["fact_id"]) and fact["fact_id"] not in fact_ids, "INVALID_FACT_INPUT")
                fact_ids.add(fact["fact_id"])
                require(fact["kind"] in ("ROLE", "ACTION", "RESULT") and _text(fact["text"]),
                        "INVALID_FACT_INPUT")
        answers = matching["answers"]
        require(isinstance(answers, list) and bool(answers), "MATCHING_REFERENCES_REQUIRED")
        for answer in answers:
            matching_tokens(_matching_payload(case), answer)
    require(any(case.get("matching") is not None for case in cases), "MATCHING_CASES_REQUIRED")


def build_requests(benchmark):
    validate_benchmark(benchmark)
    requests = []
    for case in benchmark["cases"]:
        requests.append({"case_id": case["case_id"], "axis": "question", "stage": "question",
                         "system_prompt": QUESTION_PROMPT, "payload": {"question_text": case["question_text"]}})
        if case.get("matching") is not None:
            requests.append({"case_id": case["case_id"], "axis": "matching", "stage": "candidates",
                             "system_prompt": CANDIDATE_PROMPT, "payload": _matching_payload(case)})
    # No expected model answers, review notes, or weight policy enter requests.
    return copy.deepcopy(requests)


def validate_policy(benchmark, policy):
    require(isinstance(policy, dict) and policy.get("schema_version") == "w4-model-policy/0.1",
            "POLICY_VERSION")
    require(policy.get("scorer_version") == SCORER_VERSION, "SCORER_VERSION_CHANGED")
    require(policy.get("benchmark_sha256") == document_digest(benchmark), "BENCHMARK_CHANGED")
    require(policy.get("protocol_sha256") == digest(build_requests(benchmark)), "PROTOCOL_CHANGED")
    require(policy.get("metric") == "exact_match_accuracy", "UNSUPPORTED_METRIC")
    require(policy.get("case_aggregation") == "equal_case_mean", "UNSUPPORTED_AGGREGATION")
    require(type(policy.get("repetitions")) is int and 1 <= policy["repetitions"] <= 10,
            "INVALID_REPETITIONS")
    weights = policy.get("weights")
    require(isinstance(weights, dict) and set(weights) == set(AXES), "EXPLICIT_WEIGHTS_REQUIRED")
    require(all(type(w) in (int, float) and math.isfinite(w) and w > 0 for w in weights.values()),
            "INVALID_WEIGHTS")


def collect_run(benchmark, policy, client, *, candidate_id, generation_config, exploratory=False):
    """Collect independent tasks through an existing JsonClient implementation.

    Real adapters must describe their effective, non-secret generation settings.
    Their own data-transmission restrictions still apply. No new provider is
    installed or selected here, and failures never trigger automatic retries.
    """
    validate_policy(benchmark, policy)
    require(_text(candidate_id), "CANDIDATE_ID_REQUIRED")
    require(isinstance(generation_config, dict) and bool(generation_config), "SETTINGS_REQUIRED")
    require(type(client.simulated) is bool and _text(client.provider) and _text(client.model),
            "INVALID_CLIENT_METADATA")
    require(type(exploratory) is bool, "INVALID_EXPLORATORY_FLAG")
    if not client.simulated and not exploratory:
        require(approval_valid(benchmark) and approval_valid(policy), "HUMAN_REVIEW_REQUIRED")
    requests = build_requests(benchmark)
    records = []
    for repeat in range(policy["repetitions"]):
        for request in requests:
            record = {"case_id": request["case_id"], "axis": request["axis"], "repeat": repeat,
                      "request_sha256": digest(request), "response": None, "error": None}
            try:
                response = client.complete_json(stage=request["stage"],
                                                system_prompt=request["system_prompt"],
                                                payload=copy.deepcopy(request["payload"]))
                # Keep raw answers, including schema-invalid JSON, for scoring.
                json.dumps(response, ensure_ascii=False, allow_nan=False).encode("utf-8")
                record["response"] = response
            except Exception as error:
                # Never copy a provider exception body or credential into artifacts.
                record["error"] = error.code if isinstance(error, LLMError) else "CLIENT_CALL_FAILED"
            records.append(record)
    return {"schema_version": "w4-model-run/0.1", "candidate_id": candidate_id,
            "provider": client.provider, "model": client.model,
            "generation_config": copy.deepcopy(generation_config),
            "provenance": "SIMULATED" if client.simulated else "CAPTURED",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "benchmark_sha256": document_digest(benchmark),
            "protocol_sha256": digest(requests), "repetitions": policy["repetitions"],
            "reference_status": "APPROVED" if approval_valid(benchmark) and approval_valid(policy) else "DRAFT",
            "records": records}


def _f1(predicted, expected):
    overlap = len(predicted & expected)
    return Fraction(2 * overlap, len(predicted) + len(expected)) if predicted or expected else Fraction(1)


def _score(case, axis, response):
    if isinstance(response, str):
        response = parse_json_object(response, axis)
    if axis == "question":
        predicted = question_tokens(case["question_text"], response)
        expected = [question_tokens(case["question_text"], a) for a in case["question_answers"]]
        details = predicted, expected
    else:
        payload = _matching_payload(case)
        predicted, predicted_edges = matching_tokens(payload, response)
        pairs = [matching_tokens(payload, a) for a in case["matching"]["answers"]]
        expected = [p[0] for p in pairs]
        details = predicted_edges, [p[1] for p in pairs]
    exact = predicted in expected
    best = max(range(len(expected)), key=lambda i: (predicted == expected[i], _f1(predicted, expected[i])))
    return {"correct": exact, "reference_variant": best,
            "missing": sorted(expected[best] - predicted),
            "unexpected": sorted(predicted - expected[best]),
            "diagnostic_f1": round(float(_f1(details[0], details[1][best])), 6)}


def evaluate_runs(benchmark, policy, runs, *, exploratory=False):
    validate_policy(benchmark, policy)
    require(isinstance(runs, list) and bool(runs), "RUNS_REQUIRED")
    require(type(exploratory) is bool, "INVALID_EXPLORATORY_FLAG")
    # The explicit exploratory path permits real pilot measurements against a
    # draft. It never approves references or enables production model selection.
    if not exploratory and not (approval_valid(benchmark) and approval_valid(policy)):
        require(all(isinstance(run, dict) and run.get("provenance") == "SIMULATED" for run in runs),
                "HUMAN_REVIEW_REQUIRED")
    requests = build_requests(benchmark)
    expected = {(r["case_id"], r["axis"], repeat): r
                for repeat in range(policy["repetitions"]) for r in requests}
    cases = {case["case_id"]: case for case in benchmark["cases"]}
    weights = {axis: Fraction(str(policy["weights"][axis])) for axis in AXES}
    blockers = ["EXPLORATORY_EVALUATION"] if exploratory else []
    if not approval_valid(benchmark):
        blockers.append("BENCHMARK_NOT_APPROVED_OR_CHANGED")
    if not approval_valid(policy):
        blockers.append("POLICY_NOT_APPROVED_OR_CHANGED")
    if len(runs) < 2:
        blockers.append("AT_LEAST_TWO_CANDIDATES_REQUIRED")
    rows, totals, candidate_ids, configurations = [], {}, set(), set()
    for run in runs:
        require(isinstance(run, dict) and run.get("schema_version") == "w4-model-run/0.1", "RUN_VERSION")
        cid = run.get("candidate_id")
        require(_text(cid) and cid not in candidate_ids, "DUPLICATE_OR_INVALID_CANDIDATE_ID")
        candidate_ids.add(cid)
        require(_text(run.get("provider")) and _text(run.get("model")), "MODEL_METADATA_REQUIRED")
        require(isinstance(run.get("generation_config"), dict) and bool(run["generation_config"]),
                "SETTINGS_REQUIRED")
        configuration = (run["provider"], run["model"], digest(run["generation_config"]))
        require(configuration not in configurations, "DUPLICATE_MODEL_CONFIGURATION")
        configurations.add(configuration)
        require(run.get("benchmark_sha256") == document_digest(benchmark), "RUN_BENCHMARK_MISMATCH")
        require(run.get("protocol_sha256") == digest(requests), "RUN_PROTOCOL_MISMATCH")
        require(type(run.get("repetitions")) is int and run["repetitions"] == policy["repetitions"],
                "RUN_REPETITIONS_MISMATCH")
        require(run.get("provenance") in ("SIMULATED", "CAPTURED", "IMPORTED"), "INVALID_PROVENANCE")
        if run["provenance"] == "SIMULATED":
            blockers.append("SIMULATED_RUN:" + cid)
        if run["provenance"] == "IMPORTED" and not approval_valid(run):
            blockers.append("IMPORTED_RUN_NOT_REVIEWED:" + cid)
        require(isinstance(run.get("records"), list), "INVALID_RECORDS")
        records = {}
        for record in run["records"]:
            require(isinstance(record, dict) and _text(record.get("case_id"))
                    and _text(record.get("axis")) and type(record.get("repeat")) is int, "INVALID_RECORD")
            key = record["case_id"], record["axis"], record["repeat"]
            require(key in expected and key not in records, "UNKNOWN_OR_DUPLICATE_RECORD")
            require(record.get("request_sha256") == digest(expected[key]), "REQUEST_MISMATCH")
            records[key] = record
        details, correct, counts = [], dict.fromkeys(AXES, 0), dict.fromkeys(AXES, 0)
        for key, request in expected.items():
            case_id, axis, repeat = key
            counts[axis] += 1
            record = records.get(key)
            detail = {"case_id": case_id, "axis": axis, "repeat": repeat, "correct": False}
            if record is None:
                detail["error"] = "MISSING_RECORD"
                blockers.append("INCOMPLETE_RUN:" + cid)
            elif record.get("error") is not None:
                require(record.get("response") is None and _text(record["error"]), "INVALID_ERROR_RECORD")
                detail["error"] = "CALL_FAILED"
            else:
                try:
                    detail.update(_score(cases[case_id], axis, record.get("response")))
                except (EvaluationError, LLMError, KeyError, TypeError, ValueError, AttributeError):
                    detail["error"] = "INVALID_MODEL_RESPONSE"
            correct[axis] += int(detail["correct"])
            details.append(detail)
        scores = {axis: Fraction(100 * correct[axis], counts[axis]) for axis in AXES}
        total = sum(scores[a] * weights[a] for a in AXES) / sum(weights.values())
        totals[cid] = total
        rows.append({"candidate_id": cid, "provider": run["provider"], "model": run["model"],
                     "provenance": run["provenance"], "generation_config": run["generation_config"],
                     "scores": {a: round(float(scores[a]), 6) for a in AXES},
                     "correct": correct, "scheduled": counts, "total_score": round(float(total), 6),
                     "details": details})
    if max(totals.values()) == 0:
        blockers.append("NO_CORRECT_ANSWERS")
    # Names are only a stable display order; they never break score ties.
    rows.sort(key=lambda row: (-totals[row["candidate_id"]], row["candidate_id"]))
    for row in rows:
        row["rank"] = 1 + sum(value > totals[row["candidate_id"]] for value in totals.values())
    winners = [row["candidate_id"] for row in rows if row["rank"] == 1]
    return {"schema_version": "w4-model-evaluation-report/0.1", "scorer_version": SCORER_VERSION,
            "benchmark_sha256": document_digest(benchmark), "policy_sha256": document_digest(policy),
            "status": "PREVIEW_ONLY" if blockers else "EVALUATED",
            "recommendation": {"status": "WITHHELD" if blockers else "TIED" if len(winners) > 1 else "HIGHEST_OBSERVED_SCORE",
                               "candidate_ids": [] if blockers else winners,
                               "blockers": sorted(set(blockers))},
            "weights": policy["weights"], "metric": policy["metric"], "results": rows,
            "limitations": ["Scores apply only to this reviewed benchmark, prompt and recorded configuration.",
                            "No claim of statistical significance or general model superiority.",
                            "Matching is evaluated with shared reviewed criteria, not each model's question output.",
                            "Provenance and reviewer names are audit declarations, not authenticated signatures."]}
