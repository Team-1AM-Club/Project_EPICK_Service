"""Content allowlist: a SYNTHETIC label alone does not authorize transmission."""

import hashlib
import json
from pathlib import Path

from .candidate_retriever import Candidate
from .llm_contract import LLMError
from .llm_prompts import CANDIDATE_PROMPT, EXTRACTION_PROMPT, QUESTION_PROMPT


def content_hash(value) -> str:
    encoded = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def check_sample_inputs(question_text: str, pool: list[Candidate]):
    # Reject an unapproved episode before paying for even the question call.
    check_sample_payload("candidates", CANDIDATE_PROMPT, {
        "question_text": question_text, "criteria": [], "required_facts": [],
        "candidates": [{
            "episode_id": item.episode.episode_id, "episode_version": item.episode.version,
            "title": item.episode.title,
            "facts": [{"fact_id": fact.fact_id, "kind": fact.kind, "text": fact.text}
                      for fact in item.episode.facts],
        } for item in pool],
    })


def check_sample_payload(stage: str, system_prompt: str, payload: dict):
    if stage in ("c01_question", "c01_claims", "c01_requirements"):
        from .c01_staged import check_sample
        return check_sample(stage, system_prompt, payload)
    if stage == "c01_company_details":
        from .c01_detail import check_sample_c01_detail
        return check_sample_c01_detail(system_prompt, payload)
    if stage == "company_details":
        from .company_detail import check_sample_company_detail
        return check_sample_company_detail(system_prompt, payload)
    if stage == "details":
        from .detail_contract import check_sample_detail
        return check_sample_detail(system_prompt, payload)

    def require(condition):
        if not condition:
            raise LLMError("LLM_SYNTHETIC_SAMPLE_REQUIRED", stage)

    if stage == "extraction":
        require(system_prompt == EXTRACTION_PROMPT and isinstance(payload, dict))
        allowed = json.loads(Path(__file__).with_name("extraction_allowlist.json").read_text(encoding="utf-8"))
        require(content_hash(payload) in allowed["payload_sha256"])
        return
    require(stage in ("question", "candidates") and isinstance(payload, dict))
    require(system_prompt == (QUESTION_PROMPT if stage == "question" else CANDIDATE_PROMPT))
    evaluation = json.loads(Path(__file__).with_name("evaluation_allowlist.json").read_text(encoding="utf-8"))
    if content_hash({"stage": stage, "system_prompt": system_prompt, "payload": payload}) in evaluation["request_sha256"]:
        return
    allowed = json.loads(Path(__file__).with_name("synthetic_allowlist.json").read_text(encoding="utf-8"))
    require(content_hash(payload.get("question_text")) in allowed["questions"])
    if stage == "question":
        require(set(payload) == {"question_text"})
        return
    require(set(payload) == {"question_text", "criteria", "required_facts", "candidates"})
    require(isinstance(payload["candidates"], list))
    for episode in payload["candidates"]:
        require(isinstance(episode, dict)
                and set(episode) == {"episode_id", "episode_version", "title", "facts"})
        header = {key: episode[key] for key in ("episode_id", "episode_version", "title")}
        known = allowed["episodes"].get(content_hash(header))
        require(known is not None and isinstance(episode["facts"], list))
        require(all(content_hash(fact) in known for fact in episode["facts"]))
