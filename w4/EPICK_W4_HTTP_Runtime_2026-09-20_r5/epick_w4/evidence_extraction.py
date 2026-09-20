"""Extract grounded source units before W4 candidate validation, using an injected LLM."""

from copy import deepcopy
import re

from .contracts import (
    ContractError, array_value, integer_value, object_value, string_value, strings,
)
from .llm_contract import JsonClient, LLMError
from .llm_prompts import EXTRACTION_PROMPT, EXTRACTION_PROMPT_VERSION
from .synthetic_policy import check_sample_payload, content_hash

INPUT_SCHEMA = "w4-evidence-input/0.1"
OUTPUT_SCHEMA = "w4-evidence-output/0.1"
FIELDS = ("ROLE", "ACTION", "RESULT", "GOAL", "PERIOD", "OBSTACLE", "REFLECTION", "CONTEXT")
SUBJECTS = ("SELF", "TEAM", "OTHER", "UNSPECIFIED", "MIXED")
ASSERTIONS = ("AFFIRMED", "NEGATED", "PLANNED", "HYPOTHETICAL", "UNCERTAIN", "MIXED")
ISSUES = (None, "INSUFFICIENT_CONTEXT", "INSTRUCTION_IN_SOURCE", "NO_EVIDENCE")
MAX_EPISODES = 20
MAX_RAW_CHARS = 12000
MAX_TOTAL_RAW_CHARS = 50000
MAX_UNITS = 64
MAX_UNIT_CHARS = 3000

FOLLOW_UPS = {
    "ROLE": "그 경험에서 본인이 맡은 역할은 무엇이었나요?",
    "ACTION": "팀 전체 활동과 구분하여 본인이 직접 한 일을 알려 주세요.",
    "RESULT": "그 행동 이후 어떤 결과가 있었고, 어떤 기록으로 확인할 수 있나요?",
    "GOAL": "당시 어떤 목표를 정했고, 누구의 목표였나요?",
    "PERIOD": "언제부터 언제까지, 또는 어떤 주기로 활동했나요?",
    "OBSTACLE": "진행 중 어떤 문제나 어려움이 있었나요?",
    "REFLECTION": "그 경험을 통해 무엇을 배우거나 느꼈나요?",
    "CONTEXT": "당시 상황과 함께한 사람들의 관계를 알려 주세요.",
}


def _input_keys(value: dict, allowed: set[str], field: str):
    if set(value) - allowed:
        raise ContractError("UNEXPECTED_FIELD", field)


def source_units(raw_text: str) -> list[dict]:
    """Keep whole nonblank lines, including their whitespace; offsets are code points.

    Full units prevent a model from cutting a negation off a selected quote. A
    paragraph without newlines stays together and can require a mixed-state review.
    """
    units = []
    for match in re.finditer(r"[^\r\n]+", raw_text):
        if match.group().strip():
            units.append({"unit_id": f"u{len(units) + 1}", "text": match.group(),
                          "start": match.start(), "end": match.end()})
    return units


def model_payload(episode: dict, units: list[dict]) -> dict:
    # Auth, project metadata and company/question text are unnecessary for extraction.
    return {"episode_id": episode["episode_id"], "episode_version": episode["version"],
            "source_units": [{"unit_id": unit["unit_id"], "text": unit["text"]}
                             for unit in units]}


def _parse(payload: dict, user_id: str) -> tuple[dict, list[dict], bool]:
    string_value(user_id, "authenticated_user_id")
    root = object_value(payload, "request")
    project = object_value(root.get("project"), "project")
    if project.get("owner_id") != user_id:
        raise ContractError("PROJECT_ACCESS_DENIED", "project.owner_id")
    _input_keys(root, {"schema_version", "request_id", "data_kind", "project", "snapshot",
                       "episodes", "excluded_episode_ids"}, "request")
    if root.get("schema_version") != INPUT_SCHEMA:
        raise ContractError("UNSUPPORTED_SCHEMA", "schema_version")
    if root.get("data_kind") not in ("REAL", "SYNTHETIC"):
        raise ContractError("INVALID_DATA_KIND", "data_kind")
    if root["data_kind"] != "SYNTHETIC":
        raise LLMError("LLM_REAL_DATA_NOT_ENABLED", "input")
    _input_keys(project, {"project_id", "owner_id"}, "project")
    string_value(project.get("project_id"), "project.project_id")
    string_value(root.get("request_id"), "request_id")
    snapshot = object_value(root.get("snapshot"), "snapshot")
    _input_keys(snapshot, {"snapshot_id", "episode_versions"}, "snapshot")
    string_value(snapshot.get("snapshot_id"), "snapshot.snapshot_id")
    versions = object_value(snapshot.get("episode_versions"), "snapshot.episode_versions")
    for key, value in versions.items():
        string_value(key, "snapshot.episode_versions")
        integer_value(value, "snapshot.episode_versions")
    excluded = strings(root.get("excluded_episode_ids", []), "excluded_episode_ids")
    episodes = []
    seen = set()
    mismatch = False
    for index, value in enumerate(array_value(root.get("episodes"), "episodes")):
        field = f"episodes[{index}]"
        episode = object_value(value, field)
        _input_keys(episode, {"episode_id", "version", "owner_id", "activity_id", "title",
                              "raw_text"}, field)
        for key in ("episode_id", "owner_id", "activity_id", "title", "raw_text"):
            string_value(episode.get(key), f"{field}.{key}")
        integer_value(episode.get("version"), f"{field}.version")
        if episode["episode_id"] in seen:
            raise ContractError("DUPLICATE_EPISODE", f"{field}.episode_id")
        seen.add(episode["episode_id"])
        if episode["owner_id"] != user_id or episode["episode_id"] in excluded:
            continue
        if versions.get(episode["episode_id"]) != episode["version"]:
            mismatch = True
            continue
        episodes.append(deepcopy(episode))
    # Validate metadata too: direct Python callers do not pass through the HTTP parser.
    try:
        content_hash(root)
    except UnicodeError:
        raise ContractError("INVALID_TEXT_ENCODING", "request") from None
    return deepcopy(root), episodes, mismatch


def _require(condition: bool, code: str = "LLM_SCHEMA_INVALID"):
    if not condition:
        raise LLMError(code, "extraction")


def _keys(value, keys: set[str]):
    _require(isinstance(value, dict) and set(value) == keys)


def _decisions(value: dict, units: list[dict]) -> dict:
    _keys(value, {"units"})
    _require(isinstance(value["units"], list) and len(value["units"]) == len(units),
             "LLM_INCOMPLETE_UNIT_SET")
    known = {unit["unit_id"] for unit in units}
    decisions = {}
    for item in value["units"]:
        _keys(item, {"unit_id", "kinds", "subject", "assertion", "issue"})
        key = item["unit_id"]
        _require(isinstance(key, str) and key in known and key not in decisions,
                 "LLM_INVALID_UNIT_REFERENCE")
        kinds = item["kinds"]
        _require(isinstance(kinds, list) and all(isinstance(k, str) and k in FIELDS for k in kinds))
        _require(len(kinds) == len(set(kinds)))
        _require(item["subject"] in SUBJECTS and item["assertion"] in ASSERTIONS)
        _require(item["issue"] in ISSUES)
        # A discarded instruction/non-evidence unit cannot yield facts. Its
        # terminal issue takes precedence over uncertainty about the sentence.
        if item["issue"] in ("NO_EVIDENCE", "INSTRUCTION_IN_SOURCE"):
            _require(not kinds)
        elif item["subject"] == "MIXED" or item["assertion"] in ("MIXED", "UNCERTAIN"):
            _require(item["issue"] == "INSUFFICIENT_CONTEXT")
        if not kinds:
            _require(item["issue"] is not None)
        decisions[key] = item
    _require(set(decisions) == known, "LLM_INCOMPLETE_UNIT_SET")
    return decisions


def _usable(unit: dict, kind: str) -> bool:
    if unit["issue"] is not None or unit["assertion"] != "AFFIRMED":
        return False
    if kind in ("ROLE", "ACTION"):
        return unit["subject"] == "SELF"
    if kind == "RESULT":
        return unit["subject"] in ("SELF", "TEAM", "UNSPECIFIED")
    return unit["subject"] != "MIXED"


def _assemble(episode: dict, units: list[dict], decisions: dict) -> dict:
    evidence = []
    facts = []
    for unit in units:
        decision = decisions[unit["unit_id"]]
        key = f"extract:{episode['episode_id']}:v{episode['version']}:{unit['unit_id']}"
        ref = {"episode_id": episode["episode_id"], "episode_version": episode["version"],
               "start": unit["start"], "end": unit["end"]}
        item = {"evidence_id": key, "unit_id": unit["unit_id"], "text": unit["text"],
                "evidence": ref, "kinds": [kind for kind in FIELDS if kind in decision["kinds"]],
                "subject": decision["subject"], "assertion": decision["assertion"],
                "issue": decision["issue"]}
        evidence.append(item)
        for kind in ("ROLE", "ACTION", "RESULT"):
            if kind in item["kinds"] and _usable(item, kind):
                facts.append({"fact_id": f"{key}:{kind.lower()}", "kind": kind,
                              "text": unit["text"], "evidence": dict(ref)})
    coverage = {}
    follow_ups = []
    for kind in FIELDS:
        relevant = [item for item in evidence if kind in item["kinds"]]
        good = [item["evidence_id"] for item in relevant if _usable(item, kind)]
        review = [item["evidence_id"] for item in relevant if not _usable(item, kind)]
        status = "EXTRACTED" if good else ("REVIEW_REQUIRED" if review else "NOT_EXTRACTED")
        coverage[kind] = {"status": status, "evidence_ids": good, "review_evidence_ids": review}
        if not good:
            follow_ups.append({"field": kind, "reason": status, "question": FOLLOW_UPS[kind]})
    return {"episode": {**episode, "facts": facts}, "evidence_units": evidence,
            "field_coverage": coverage, "follow_up_questions": follow_ups,
            "validation_status": "REQUIRES_CANDIDATE_VALIDATION"}


def extract_evidence(payload: dict, *, user_id: str, llm: JsonClient) -> dict:
    """Classify raw source units; never rank, invent text or approve extracted facts.

    Authenticated scope and every payload are checked before the first model call.
    Each admitted episode uses one call. Failures abort the result without retries.
    """
    request, episodes, mismatch = _parse(payload, user_id)
    if len(episodes) > MAX_EPISODES:
        raise LLMError("LLM_CANDIDATE_LIMIT_EXCEEDED", "input")
    if sum(len(episode["raw_text"]) for episode in episodes) > MAX_TOTAL_RAW_CHARS:
        raise LLMError("LLM_INPUT_LIMIT_EXCEEDED", "input")
    prepared = []
    for episode in episodes:
        units = source_units(episode["raw_text"])
        if (len(episode["raw_text"]) > MAX_RAW_CHARS or len(units) > MAX_UNITS
                or any(len(unit["text"]) > MAX_UNIT_CHARS for unit in units)):
            raise LLMError("LLM_INPUT_LIMIT_EXCEEDED", "input")
        outbound = model_payload(episode, units)
        digest = content_hash(outbound)
        if not llm.simulated:
            check_sample_payload("extraction", EXTRACTION_PROMPT, outbound)
        prepared.append((episode, units, outbound, digest))
    results = []
    calls = []
    for episode, units, outbound, digest in prepared:
        response = llm.complete_json(stage="extraction", system_prompt=EXTRACTION_PROMPT,
                                     payload=deepcopy(outbound))
        results.append(_assemble(episode, units, _decisions(response, units)))
        calls.append({"stage": "extraction", "episode_id": episode["episode_id"],
                      "episode_version": episode["version"], "input_sha256": digest})
    incomplete = mismatch or any(
        item["follow_up_questions"] or any(
            unit["issue"] not in (None, "NO_EVIDENCE") or unit["assertion"] != "AFFIRMED"
            or (unit["subject"] != "SELF" and bool(set(unit["kinds"]) & {"ROLE", "ACTION"}))
            for unit in item["evidence_units"])
        for item in results
    )
    result = {
        "schema_version": OUTPUT_SCHEMA, "request_id": request["request_id"],
        "project_id": request["project"]["project_id"], "data_kind": request["data_kind"],
        "status": ("NO_ELIGIBLE_EPISODES" if not results else
                   "EXTRACTED_WITH_LIMITATIONS" if incomplete else "EXTRACTED"),
        "episodes": results,
        "snapshot": {"snapshot_id": request["snapshot"]["snapshot_id"],
                     "extracted_episode_versions": {e["episode_id"]: e["version"] for e in episodes}},
        "diagnostics": [{"code": "EPISODE_VERSION_MISMATCH"}] if mismatch else [],
        "inference": {"mode": "SIMULATED_LLM" if llm.simulated else "LLM",
                      "provider": llm.provider, "model": llm.model,
                      "prompt_version": EXTRACTION_PROMPT_VERSION,
                      "prompt_sha256": content_hash(EXTRACTION_PROMPT), "calls": calls},
        "limitations": [
            "원문 발췌는 코드가 복사하지만 종류·주체·완료 여부는 모델의 판단이며 추가 검증이 필요합니다.",
            "NOT_EXTRACTED는 추출되지 않았다는 뜻이며 실제 경험의 부재를 입증하지 않습니다.",
            "목표·기간 등의 맥락은 evidence_units에 보존하며 기존 Fact 종류에 억지로 넣지 않습니다.",
            "이 결과는 추천 순위·문항 충족·지원 자격·수행 사실의 확정 결과가 아닙니다.",
        ],
    }
    selection = getattr(llm, "selection", None)
    if selection is not None:
        result["inference"]["model_selection"] = selection
    return result
