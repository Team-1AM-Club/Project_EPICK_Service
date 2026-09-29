"""Separate exploratory source-unit classification measurements on fixed fiction."""

from copy import deepcopy
from datetime import datetime, timezone

from .evidence_extraction import _decisions, _usable, model_payload, source_units
from .llm_contract import LLMError
from .llm_prompts import EXTRACTION_PROMPT
from .model_eval import digest, require
from .synthetic_policy import check_sample_payload

SCORER_VERSION = "w4-extraction-unit-audit/0.2"


def _prediction_units(value, units):
    """Score identifiable units separately; any error still fails the request gate."""
    known = {u["unit_id"] for u in units}
    malformed = not isinstance(value, dict) or set(value) != {"units"}
    items = value.get("units") if isinstance(value, dict) else None
    if malformed or not isinstance(items, list):
        return {}, {uid: "INVALID_MODEL_RESPONSE" for uid in known}
    records = {}
    for item in items:
        uid = item.get("unit_id") if isinstance(item, dict) else None
        if not isinstance(uid, str) or uid not in known or uid in records:
            return {}, {key: "LLM_INVALID_UNIT_REFERENCE" for key in known}
        records[uid] = item
    predictions, errors = {}, {}
    for unit in units:
        uid = unit["unit_id"]
        if uid not in records:
            errors[uid] = "LLM_INCOMPLETE_UNIT_SET"
            continue
        try:
            predictions[uid] = _decisions({"units": [records[uid]]}, [unit])[uid]
        except (LLMError, TypeError, ValueError, KeyError) as error:
            errors[uid] = error.code if isinstance(error, LLMError) else "INVALID_MODEL_RESPONSE"
    return predictions, errors


def build_extraction_requests(payload):
    require(payload.get("data_kind") == "SYNTHETIC", "SYNTHETIC_BENCHMARK_REQUIRED")
    requests = []
    for episode in payload["episodes"]:
        outbound = model_payload(episode, source_units(episode["raw_text"]))
        check_sample_payload("extraction", EXTRACTION_PROMPT, outbound)
        requests.append({"case_id": episode["episode_id"], "stage": "extraction",
                         "system_prompt": EXTRACTION_PROMPT, "payload": outbound})
    require(bool(requests) and len({r["case_id"] for r in requests}) == len(requests), "INVALID_CASES")
    return requests


def validate_references(payload, reference):
    requests = build_extraction_requests(payload)
    require(reference.get("schema_version") == "w4-extraction-benchmark/0.1", "BENCHMARK_VERSION")
    require(reference.get("input_sha256") == digest(payload), "EXTRACTION_INPUT_CHANGED")
    require(reference.get("protocol_sha256") == digest(requests), "PROTOCOL_CHANGED")
    require(set(reference["answers"]) == {r["case_id"] for r in requests}, "REFERENCE_CASES_MISMATCH")
    for request in requests:
        _decisions(reference["answers"][request["case_id"]], request["payload"]["source_units"])
    return requests


def collect_extraction_run(payload, reference, client, *, candidate_id, repetitions=3):
    requests = validate_references(payload, reference)
    require(type(repetitions) is int and 1 <= repetitions <= 10, "INVALID_REPETITIONS")
    records = []
    for repeat in range(repetitions):
        for request in requests:
            record = {"case_id": request["case_id"], "repeat": repeat,
                      "request_sha256": digest(request), "response": None, "error": None}
            try:
                record["response"] = client.complete_json(
                    stage="extraction", system_prompt=EXTRACTION_PROMPT,
                    payload=deepcopy(request["payload"]))
            except Exception as error:
                record["error"] = error.code if isinstance(error, LLMError) else "CLIENT_CALL_FAILED"
            records.append(record)
    return {"schema_version": "w4-extraction-run/0.1", "candidate_id": candidate_id,
            "provider": client.provider, "model": client.model,
            "generation_config": deepcopy(client.generation_config),
            "provenance": "SIMULATED" if client.simulated else "CAPTURED",
            "created_at": datetime.now(timezone.utc).isoformat(),
            "reference_sha256": digest(reference), "protocol_sha256": digest(requests),
            "repetitions": repetitions, "records": records}


def evaluate_extraction_runs(payload, reference, runs):
    requests = validate_references(payload, reference)
    require(isinstance(runs, list) and bool(runs), "RUNS_REQUIRED")
    require(len({run["repetitions"] for run in runs}) == 1, "RUN_REPETITIONS_MISMATCH")
    rows, candidate_ids = [], set()
    for run in runs:
        require(run.get("schema_version") == "w4-extraction-run/0.1", "RUN_VERSION")
        cid = run["candidate_id"]
        require(isinstance(cid, str) and bool(cid) and cid not in candidate_ids, "INVALID_CANDIDATE_ID")
        candidate_ids.add(cid)
        require(run.get("provenance") in ("CAPTURED", "SIMULATED"), "INVALID_PROVENANCE")
        require(run["reference_sha256"] == digest(reference), "REFERENCE_CHANGED")
        require(run["protocol_sha256"] == digest(requests), "PROTOCOL_CHANGED")
        repeats = run["repetitions"]
        require(type(repeats) is int and 1 <= repeats <= 10, "INVALID_REPETITIONS")
        expected = {(r["case_id"], n): r for n in range(repeats) for r in requests}
        records = {}
        for record in run["records"]:
            key = record["case_id"], record["repeat"]
            require(key in expected and key not in records, "UNKNOWN_OR_DUPLICATE_RECORD")
            require(record["request_sha256"] == digest(expected[key]), "REQUEST_MISMATCH")
            records[key] = record
        correct = dict.fromkeys(("unit_exact", "subject", "assertion", "issue"), 0)
        total, valid_requests, tp, fp, fn = 0, 0, 0, 0, 0
        json_valid_requests, unsafe_admissions = 0, 0
        details = []
        for key, request in expected.items():
            units = request["payload"]["source_units"]
            gold = _decisions(reference["answers"][request["case_id"]], units)
            record = records.get(key)
            error = "MISSING_RECORD" if record is None else record.get("error")
            prediction, unit_errors = {}, {}
            if error is None:
                json_valid_requests += int(isinstance(record.get("response"), dict))
                prediction, unit_errors = _prediction_units(record.get("response"), units)
                valid_requests += int(not unit_errors)
            for unit in units:
                uid = unit["unit_id"]
                wanted = gold[uid]
                actual = prediction.get(uid)
                total += 1
                desired_kinds = set(wanted["kinds"])
                observed_kinds = set(actual["kinds"]) if actual else set()
                tp += len(desired_kinds & observed_kinds)
                fp += len(observed_kinds - desired_kinds)
                fn += len(desired_kinds - observed_kinds)
                exact = (actual is not None and desired_kinds == observed_kinds
                         and all(actual[k] == wanted[k] for k in ("subject", "assertion", "issue")))
                correct["unit_exact"] += int(exact)
                for field in ("subject", "assertion", "issue"):
                    correct[field] += int(actual is not None and actual[field] == wanted[field])
                unsafe_kinds = [kind for kind in ("ROLE", "ACTION", "RESULT")
                                if actual is not None and kind in actual["kinds"]
                                and _usable(actual, kind)
                                and (kind not in wanted["kinds"] or not _usable(wanted, kind))]
                unsafe_admissions += len(unsafe_kinds)
                details.append({"case_id": key[0], "repeat": key[1], "unit_id": uid,
                                "split": reference.get("case_splits", {}).get(key[0], "development"),
                                "text": unit["text"], "correct": exact,
                                "error": error or unit_errors.get(uid),
                                "unsafe_admitted_kinds": unsafe_kinds,
                                "expected": wanted, "actual": actual})
        rows.append({"candidate_id": cid, "provider": run["provider"], "model": run["model"],
                     "provenance": run["provenance"], "scheduled_requests": len(expected),
                     "valid_requests": valid_requests, "scheduled_units": total,
                     "valid_units": sum(d["actual"] is not None for d in details),
                     "json_valid_requests": json_valid_requests,
                     "unsafe_fact_admissions": unsafe_admissions,
                     "required_checks_passed": valid_requests == len(expected) and unsafe_admissions == 0,
                     "correct": correct,
                     "scores": {**{k: round(100 * v / total, 6) for k, v in correct.items()},
                                "kinds_micro_f1": round(100 * 2 * tp / (2 * tp + fp + fn), 6)
                                if 2 * tp + fp + fn else (100.0 if valid_requests == len(expected) else 0.0)},
                     "split_scores": {split: {
                         "scheduled_units": len(part),
                         "unit_exact": round(100 * sum(d["correct"] for d in part) / len(part), 6),
                         "unsafe_fact_admissions": sum(len(d["unsafe_admitted_kinds"]) for d in part),
                     } for split in sorted({d["split"] for d in details})
                         for part in [[d for d in details if d["split"] == split]]},
                     "details": details})
    return {"schema_version": "w4-extraction-evaluation/0.2", "scorer_version": SCORER_VERSION,
            "status": "EXPLORATORY",
            "reference_sha256": digest(reference), "reference_review": reference.get("review"),
            "recommendation": {"status": "WITHHELD", "reason": "DRAFT_EXTRACTION_REFERENCES"},
            "results": rows,
            "limitations": [
                "Reference labels are assistant-authored drafts, not human-validated ground truth.",
                "Exact copied text/offsets are a code invariant, not an LLM accuracy measurement.",
                "Repeated fixed-seed greedy requests are not independent evidence of generalization.",
                "Extraction scores are separate from question/matching scores; no combined rank."]}
