"""Separate source, context and meaning checks without a model-as-judge."""

import re

from .model_eval import _f1, require


def normalized_label(value):
    return re.sub(r"\s+", " ", value.strip()).casefold()


def validate_label_aliases(case):
    aliases = case.get("criterion_label_aliases", {})
    require(isinstance(aliases, dict), "INVALID_LABEL_ALIASES")
    custom = {c["criterion_id"] for answer in case["question_answers"] for c in answer["criteria"]
              if c["criterion_id"].startswith("custom_")}
    require(set(aliases) <= custom, "UNKNOWN_ALIAS_CRITERION")
    for labels in aliases.values():
        require(isinstance(labels, list) and 1 <= len(labels) <= 30, "INVALID_LABEL_ALIASES")
        require(all(isinstance(s, str) and s.strip() and len(s) <= 60 for s in labels),
                "INVALID_LABEL_ALIASES")


def score_question_components(case, answer):
    """Aliases must be frozen in the reference; exact quotes alone prove no meaning."""
    options = []
    for reference in case["question_answers"]:
        expected = {c["criterion_id"]: c for c in reference["criteria"]}
        wanted = {(k, normalized_label(c["label"]) if k.startswith("custom_") else "")
                  for k, c in expected.items()}
        predicted = set()
        for item in answer["criteria"]:
            key = item["criterion_id"]
            label = ""
            if key.startswith("custom_"):
                label = normalized_label(item["label"])
                if key in expected:
                    accepted = {normalized_label(expected[key]["label"]), *(
                        normalized_label(s) for s in case.get("criterion_label_aliases", {}).get(key, []))}
                    if label in accepted:
                        label = normalized_label(expected[key]["label"])
            predicted.add((key, label))
        meaning = predicted == wanted and answer["needs_confirmation"] == reference["needs_confirmation"]
        required = set(answer["required_facts"]) == set(reference["required_facts"])
        literal = context = True
        if not answer["criteria"]:
            literal = context = not expected and answer["needs_confirmation"] is True
        for item in answer["criteria"]:
            quote = item["question_quote"]
            copied = bool(quote.strip()) and quote in case["question_text"]
            literal &= copied
            target = expected.get(item["criterion_id"])
            context &= copied and target is not None and target["question_quote"] in quote
        options.append({"literal_quote_valid": bool(literal), "context_preserved": bool(context),
                        "criteria_correct": meaning, "required_facts_correct": required,
                        "question_quote_valid": bool(literal and context),
                        "criterion_f1": float(_f1(predicted, wanted)),
                        "correct": bool(meaning and required and literal and context)})
    return max(options, key=lambda d: (d["correct"], d["criteria_correct"], d["criterion_f1"],
                                      d["context_preserved"], d["literal_quote_valid"]))


def diagnose_matching_response(case, payload, answer):
    """Inspect identifiable claims even in rejected JSON. Never repair the response."""
    source = payload["candidates"][0]
    facts = {f["fact_id"]: f for f in source["facts"]}
    # A fact permitted by an alternative accepted answer is not intrinsically unsafe.
    safe = {f["fact_id"] for a in case["matching"]["answers"] for c in a["candidates"]
            if c["episode_id"] == source["episode_id"] for f in c["fact_checks"] if f["usable"]}
    result = {"scheduled_fact_checks": len(facts), "diagnosed_fact_checks": 0,
              "reported_unsafe_fact_ids": [], "reported_unsafe_reference_ids": [],
              "unverifiable_fact_ids": sorted(facts)}
    candidates = answer.get("candidates") if isinstance(answer, dict) else None
    if (not isinstance(candidates, list) or len(candidates) != 1
            or not isinstance(candidates[0], dict)):
        return result
    candidate = candidates[0]
    if (candidate.get("episode_id") != source["episode_id"]
            or type(candidate.get("episode_version")) is not int
            or candidate["episode_version"] != source["episode_version"]):
        return result
    grouped = {}
    checks = candidate.get("fact_checks")
    for check in checks if isinstance(checks, list) else []:
        if isinstance(check, dict) and isinstance(check.get("fact_id"), str) and check["fact_id"] in facts:
            grouped.setdefault(check["fact_id"], []).append(check)
    identified = {fid: values[0]["usable"] for fid, values in grouped.items()
                  if len(values) == 1 and type(values[0].get("usable")) is bool}
    result.update(diagnosed_fact_checks=len(identified),
                  unverifiable_fact_ids=sorted(set(facts) - set(identified)),
                  reported_unsafe_fact_ids=sorted(fid for fid, usable in identified.items() if usable and fid not in safe))
    bad_refs = set()
    matches = candidate.get("matches")
    for match in matches if isinstance(matches, list) else []:
        if not isinstance(match, dict) or not isinstance(match.get("fact_ids"), list):
            continue
        for fid in match["fact_ids"]:
            if not isinstance(fid, str):
                continue
            if (fid not in safe or fid not in facts or facts[fid]["kind"] != "ACTION"
                    or identified.get(fid) is False):
                bad_refs.add(fid)
    result["reported_unsafe_reference_ids"] = sorted(bad_refs)
    return result
