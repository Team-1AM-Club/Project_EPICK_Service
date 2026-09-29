"""Source extraction -> draft content assessment -> deterministic recommendation."""

from copy import deepcopy

from .contracts import ContractError, integer_value, object_value
from .detail_contract import PROMPT, PROMPT_VERSION, STATUSES, catalog, check_sample_detail, question_spec
from .evidence_extraction import _assemble, _decisions, _parse, _usable, extract_evidence, source_units
from .llm_contract import LLMError
from .synthetic_policy import content_hash

INPUT_SCHEMA = "w4-detailed-input/0.1"

# Necessary source kinds, never sufficient semantic proof. The LLM must also establish their connection.
REQUIRED_KINDS = {
    "expertise.specific_area": {"ACTION"}, "expertise.sustained_learning": {"ACTION", "PERIOD"},
    "expertise.practical_use": {"ACTION"}, "teamwork.shared_goal": {"GOAL"},
    "teamwork.cooperation_action": {"ACTION"}, "teamwork.contribution": {"GOAL", "ACTION", "RESULT"},
    "challenge.self_set_goal": {"GOAL"}, "challenge.persistence": {"OBSTACLE", "ACTION"},
    "self_description.theme": {"ACTION"}, "job_experience.job_connection": {"ACTION"},
}
FOLLOW_UPS = {
    "expertise.specific_area": "어떤 지식이나 기술을 어떤 방법으로 배웠나요?",
    "expertise.sustained_learning": "그 학습을 언제부터 어떤 주기로 이어 갔나요?",
    "expertise.practical_use": "배운 내용을 실제로 어디에 어떻게 사용했나요?",
    "teamwork.shared_goal": "함께 정한 목표와 완료 기준은 무엇이었나요?",
    "teamwork.cooperation_action": "다른 사람의 협력을 얻기 위해 본인이 한 일을 알려 주세요.",
    "teamwork.contribution": "본인의 협력 행동이 공동 목표에 어떤 도움이 됐는지 근거와 함께 알려 주세요.",
    "challenge.self_set_goal": "본인이 정한 목표는 무엇이며 당시 왜 높은 목표였나요?",
    "challenge.persistence": "어떤 난관에서 행동을 어떻게 지속하거나 바꿨나요?",
    "self_description.theme": "선택한 주제를 보여 주는 본인의 행동과 맥락을 알려 주세요.",
    "job_experience.job_connection": "대상 IT 업무와 연결되는 본인의 실제 작업을 구체적으로 알려 주세요.",
}


def prepare_request(payload, user_id):
    root = object_value(payload, "request")
    if root.get("schema_version") != INPUT_SCHEMA:
        raise ContractError("UNSUPPORTED_SCHEMA", "schema_version")
    raw = deepcopy(root)
    selector = raw.pop("question", None)
    top_k = raw.pop("top_k", 3)
    integer_value(top_k, "top_k")
    if top_k > 20:
        raise ContractError("INVALID_TOP_K", "top_k")
    raw["schema_version"] = "w4-evidence-input/0.1"
    _parse(raw, user_id)  # Ownership, snapshot, exclusions and real-data gate before model calls.
    return raw, question_spec(selector), top_k


def _source_checked(raw, extraction, user_id):
    request, episodes, _ = _parse(raw, user_id)
    if (extraction.get("project_id") != request["project"]["project_id"]
            or extraction.get("request_id") != request["request_id"]
            or extraction.get("snapshot", {}).get("snapshot_id") != request["snapshot"]["snapshot_id"]):
        raise ContractError("EXTRACTION_SCOPE_MISMATCH", "extraction")
    items = extraction.get("episodes")
    if not isinstance(items, list) or len(items) != len(episodes):
        raise ContractError("EXTRACTION_EPISODE_MISMATCH", "extraction.episodes")
    rebuilt = []
    for episode, item in zip(episodes, items):
        try:
            units = source_units(episode["raw_text"])
            decisions = _decisions({"units": [{k: u[k] for k in ("unit_id", "kinds", "subject", "assertion", "issue")}
                                             for u in item["evidence_units"]]}, units)
            expected = _assemble(episode, units, decisions)
            if item["episode"] != expected["episode"] or item["evidence_units"] != expected["evidence_units"]:
                raise ValueError("source mismatch")
        except (KeyError, TypeError, ValueError, LLMError):
            raise ContractError("EXTRACTION_SOURCE_MISMATCH", "extraction.episodes") from None
        rebuilt.append(expected)
    return rebuilt


def model_payload(question, item):
    return {"question": deepcopy(question), "episode_id": item["episode"]["episode_id"],
            "episode_version": item["episode"]["version"],
            "source_units": [{k: u[k] for k in ("unit_id", "text", "kinds", "subject", "assertion", "issue")}
                             for u in item["evidence_units"]]}


def _assess(response, item, question):
    def require(condition, code="LLM_SCHEMA_INVALID"):
        if not condition:
            raise LLMError(code, "details")
    require(isinstance(response, dict) and set(response) == {"checks"})
    wanted = {c["id"]: c for c in question["content_checks"]}
    rows = response["checks"]
    require(isinstance(rows, list) and len(rows) == len(wanted), "LLM_INCOMPLETE_CHECK_SET")
    known = {u["unit_id"]: u for u in item["evidence_units"]}
    seen, result = set(), []
    for row in rows:
        require(isinstance(row, dict) and set(row) == {"check_id", "status", "evidence_ids"})
        cid = row["check_id"]
        require(isinstance(cid, str) and cid in wanted and cid not in seen, "LLM_INVALID_CHECK_REFERENCE")
        seen.add(cid)
        require(row["status"] in STATUSES)
        refs = row["evidence_ids"]
        require(isinstance(refs, list) and all(isinstance(uid, str) and uid in known for uid in refs)
                and len(refs) == len(set(refs)), "LLM_INVALID_UNIT_REFERENCE")
        status, issue = row["status"], None
        require((status != "NOT_SHOWN" or not refs) and (status not in ("SUPPORTED", "CONTRADICTED") or bool(refs)))
        cited = [known[uid] for uid in refs]
        if status == "SUPPORTED":
            required_kinds = REQUIRED_KINDS[cid]
            if cid == "self_description.theme" and question["user_theme_requirements"]:
                required_kinds = required_kinds | {"PERIOD"}
            kinds = {k for u in cited for k in u["kinds"] if _usable(u, k)}
            unsafe = any(u["issue"] is not None or u["assertion"] != "AFFIRMED"
                         or any(k in u["kinds"] and not _usable(u, k) for k in ("ROLE", "ACTION")) for u in cited)
            if not required_kinds <= kinds:
                issue = "MISSING_REQUIRED_SOURCE_KINDS"
            if "ACTION" in required_kinds and not any("ACTION" in u["kinds"] and _usable(u, "ACTION") for u in cited):
                issue = "PERSONAL_ACTION_NOT_SUPPORTED"
            if cid == "challenge.self_set_goal" and not any(
                    "GOAL" in u["kinds"] and u["subject"] == "SELF" and _usable(u, "GOAL") for u in cited):
                issue = "PERSONAL_GOAL_NOT_SUPPORTED"
            if cid.startswith("teamwork.") and "GOAL" in REQUIRED_KINDS[cid] and not any(
                    "GOAL" in u["kinds"] and u["subject"] in ("SELF", "TEAM") and _usable(u, "GOAL") for u in cited):
                issue = "SHARED_GOAL_NOT_SUPPORTED"
            if unsafe:
                issue = "SOURCE_STATE_NOT_SUPPORTED"
        if any(u["issue"] in ("INSTRUCTION_IN_SOURCE", "NO_EVIDENCE") for u in cited):
            issue = "NON_EVIDENCE_REFERENCED"
        if issue:
            status = "AMBIGUOUS"
        result.append({"check_id": cid, "description": wanted[cid]["description"], "status": status,
                       "model_status": row["status"], "validation_issue": issue,
                       "evidence": [{"evidence_id": u["evidence_id"], **u["evidence"],
                                     "exact_quote": u["text"], "kinds": u["kinds"],
                                     "subject": u["subject"], "assertion": u["assertion"], "issue": u["issue"]} for u in cited],
                       "follow_up_question": FOLLOW_UPS[cid] if status != "SUPPORTED" else None})
    return sorted(result, key=lambda r: list(wanted).index(r["check_id"]))


def recommend_extracted(raw, extraction, *, question, top_k, user_id, llm, company_context=None, c01_split=False):
    """Internal stage boundary for sequential GPU loading; revalidate source and scope."""
    items = _source_checked(raw, extraction, user_id)
    # Reconstruct the approved catalog content even for direct Python callers.
    if question != question_spec({k: question.get(k) for k in ("scope_id", "question_id", "user_theme")}):
        raise ContractError("QUESTION_DRAFT_MISMATCH", "question")
    integer_value(top_k, "top_k")
    if top_k > 20:
        raise ContractError("INVALID_TOP_K", "top_k")
    data = catalog()
    needs_theme = question["question_id"] == "self_description" and question["user_theme"] is None
    payloads = [model_payload(question, item) for item in items] if not needs_theme else []
    prompt, prompt_version, stage = PROMPT, PROMPT_VERSION, "details"
    check_payload = check_sample_detail
    if company_context is not None:
        from .company_detail import (COMPANY_PROMPT, PROMPT_VERSION as COMPANY_VERSION,
                                     check_sample_company_detail, outbound_payload)
        prompt, prompt_version, stage = COMPANY_PROMPT, COMPANY_VERSION, "company_details"
        check_payload = check_sample_company_detail
        if company_context.c01:
            from .c01_detail import PROMPT as C01_PROMPT, PROMPT_VERSION as C01_VERSION, check_sample_c01_detail
            prompt, prompt_version, stage = C01_PROMPT, C01_VERSION, "c01_company_details"
            check_payload = check_sample_c01_detail
        payloads = [outbound_payload(p, company_context) for p in payloads]
    if not llm.simulated:
        for outbound in payloads:
            check_payload(prompt, outbound)
    split = c01_split and company_context is not None and company_context.c01
    if split:
        from .c01_staged import PROMPTS, VERSION, assess
        prompt, prompt_version = PROMPTS, VERSION
    candidates, calls = [], []
    for item, outbound in zip(items, payloads):
        if split:
            response, stage_calls = assess(llm, outbound)
            calls.extend(stage_calls)
        else:
            response = llm.complete_json(stage=stage, system_prompt=prompt, payload=deepcopy(outbound))
            calls.append({"episode_id": item["episode"]["episode_id"], "input_sha256": content_hash(outbound)})
        support, used_refs = None, None
        if company_context is not None:
            from .company_detail import company_support
            if company_context.c01:
                from .c01_detail import company_support
            support, used_refs = company_support(response, item, company_context)
            response = {"checks": response["checks"]}
        checks = _assess(response, item, question)
        if company_context is not None and question["question_id"] == "job_experience":
            for check in checks:
                if not company_context.criteria:
                    check.update(status="NOT_SHOWN", evidence=[], validation_issue="COMPANY_CONTEXT_UNAVAILABLE",
                                 follow_up_question="검증된 대상 직무의 기업 근거가 필요합니다.")
                elif check["status"] == "SUPPORTED" and not any(s["status"] == "SUPPORTED" for s in support):
                    check.update(status="AMBIGUOUS", validation_issue="COMPANY_CONNECTION_NOT_SUPPORTED",
                                 follow_up_question=FOLLOW_UPS[check["check_id"]])
        supported = [c for c in checks if c["status"] == "SUPPORTED"]
        has_action = any("ACTION" in u["kinds"] and _usable(u, "ACTION") for u in item["evidence_units"])
        uncertain = any(c["status"] in ("AMBIGUOUS", "CONTRADICTED") for c in checks)
        status = ("NEEDS_CONFIRMATION" if not supported or uncertain or not has_action else
                  "DIRECT_MATCH" if len(supported) == len(checks) else "PARTIAL_MATCH")
        episode = item["episode"]
        kinds = {k for c in supported for e in c["evidence"] for k in e["kinds"]}
        candidates.append({"episode_id": episode["episode_id"], "episode_version": episode["version"],
                           "title": episode["title"], "status": status, "content_checks": checks,
                           "reasons": [c["description"] for c in supported],
                           "confirmation_items": [{"check_id": c["check_id"], "status": c["status"],
                                                    "question": c["follow_up_question"]} for c in checks if c["follow_up_question"]],
                           "ranking_evidence_kind_count": len(kinds)})
        if company_context is not None:
            candidates[-1].update(company_support=support, used_company_refs=used_refs)
    order = {"DIRECT_MATCH": 0, "PARTIAL_MATCH": 1, "NEEDS_CONFIRMATION": 2}
    candidates.sort(key=lambda c: (order[c["status"]], -c["ranking_evidence_kind_count"], c["episode_id"]))
    ranking = candidates[:top_k]
    for i, candidate in enumerate(ranking):
        candidate["rank"] = i + 1
        candidate["tied_on_quality_keys"] = any(
            other["episode_id"] != candidate["episode_id"] and other["status"] == candidate["status"]
            and other["ranking_evidence_kind_count"] == candidate["ranking_evidence_kind_count"] for other in candidates)
    result = {"schema_version": "w4-detailed-output/0.1", "request_id": raw["request_id"],
            "project_id": raw["project"]["project_id"], "data_kind": "SYNTHETIC",
            "status": "NEEDS_INPUT" if needs_theme or (not ranking and extraction["diagnostics"]) else "NO_CANDIDATES" if not ranking else
                      "COMPLETED_WITH_LIMITATIONS" if any(c["status"] != "DIRECT_MATCH" for c in ranking) or extraction["diagnostics"] else "COMPLETED",
            "question_scope": {"scope_id": data["scope_id"], "question_id": question["question_id"],
                               "user_theme": question["user_theme"], "catalog_sha256": content_hash(data),
                               "status": data["status"], "official_text": None, "is_official_rubric": False,
                               "form_constraints": {"max_characters": None, "required": None, "max_hashtags": None},
                               "enforced_form_constraints": []},
            "candidates": ranking, "snapshot": deepcopy(extraction["snapshot"]),
            "diagnostics": deepcopy(extraction["diagnostics"]),
            "follow_up_questions": ["드러내고 싶은 주제를 직접 선택해 주세요."] if needs_theme else [],
            "ranking_policy": ["문항 전체의 근거 상태", "지원 근거의 종류 수", "경험 ID"],
            "subcheck_counts_used_as_ranking_points": False,
            "inference": {"extraction": deepcopy(extraction["inference"]),
                          "judgment": {"mode": "SIMULATED_LLM" if llm.simulated else "LLM", "provider": llm.provider,
                                       "model": llm.model, "prompt_version": prompt_version,
                                       "prompt_sha256": content_hash(prompt), "calls": calls},
                          "selection_status": "EXPLORATORY_NOT_PRODUCTION_SELECTED"},
            "eligibility_assessment": {"status": "NOT_ASSESSED"},
            "limitations": ["문항 5개·내용 점검 10개는 공식 지원서가 확인되지 않은 해석 초안입니다.",
                            "SUPPORTED는 원문 진술의 뒷받침이며 실제 수행·인과·지원 자격의 독립 검증이 아닙니다.",
                            "미확인 글자 수·필수 여부를 적용하지 않았으며 NOT_SHOWN은 경험의 부재를 뜻하지 않습니다.",
                            "일부 점검의 충족을 별도 가산점으로 더하지 않습니다. 같은 정렬 조건이면 경험 ID로 순서를 정합니다."]}
    if company_context is not None:
        from .company_detail import finish_company_result
        return finish_company_result(result, company_context)
    return result


def recommend_from_raw(payload, *, user_id, extraction_llm, judgment_llm, company_context=None, c01_split=False):
    raw, question, top_k = prepare_request(payload, user_id)
    if not judgment_llm.simulated and question["user_theme"] not in (None, "꾸준히 배우고 적용하는 태도"):
        raise LLMError("LLM_SYNTHETIC_SAMPLE_REQUIRED", "input")
    if question["question_id"] == "self_description" and question["user_theme"] is None:
        # Missing user intent does not require paying for extraction.
        empty = deepcopy(raw)
        empty["episodes"] = []
        extraction = extract_evidence(empty, user_id=user_id, llm=extraction_llm)
        return recommend_extracted(empty, extraction, question=question, top_k=top_k, user_id=user_id,
                                   llm=judgment_llm, company_context=company_context, c01_split=c01_split)
    extraction = extract_evidence(raw, user_id=user_id, llm=extraction_llm)
    return recommend_extracted(raw, extraction, question=question, top_k=top_k, user_id=user_id,
                               llm=judgment_llm, company_context=company_context, c01_split=c01_split)
