"""Validate model interpretations against locally admitted original references."""

from dataclasses import replace
import hashlib
import json
import re

from .candidate_retriever import Candidate
from .candidate_validator import ValidatedCandidate
from .llm_contract import JsonClient, LLMError
from .llm_prompts import CANDIDATE_PROMPT, PROMPT_VERSION, QUESTION_PROMPT
from .question_analyzer import QuestionAnalysis, QuestionAnalyzer, RULES

MAX_EPISODES = 20
MAX_QUESTION_CHARS = 4000
MAX_INPUT_CHARS = 50000
MAX_FACTS_PER_EPISODE = 64
SEMANTIC_METHOD = "llm_evidence_matching/0.2"
FACT_ISSUES = (
    "PERSONAL_CONTRIBUTION_UNCLEAR", "NEGATED_OR_AMBIGUOUS_FACT",
    "INSUFFICIENT_CONTEXT", "INSTRUCTION_IN_SOURCE",
)


def require(condition: bool, stage: str, code: str = "LLM_SCHEMA_INVALID"):
    if not condition:
        raise LLMError(code, stage)


def exact_keys(value, keys: set[str], stage: str):
    require(isinstance(value, dict) and set(value) == keys, stage)


def nonempty_text(value, stage: str, maximum: int = 4000) -> str:
    require(isinstance(value, str) and bool(value.strip()) and len(value) <= maximum, stage)
    return value


def unique_strings(value, stage: str) -> list[str]:
    require(isinstance(value, list), stage)
    for item in value:
        nonempty_text(item, stage)
    require(len(set(value)) == len(value), stage)
    return value


def single_candidate_payloads(payload: dict) -> list[dict]:
    """Share the same one-experience call boundary between serving and evaluation."""
    return [{**payload, "candidates": [candidate]} for candidate in payload["candidates"]]


class SemanticMatcher:
    def __init__(self, client: JsonClient):
        self.client = client
        self.calls: list[dict] = []

    def preflight(self, question_text: str, pool: list[Candidate]):
        require(len(pool) <= MAX_EPISODES, "input", "LLM_CANDIDATE_LIMIT_EXCEEDED")
        size = len(question_text)
        require(size <= MAX_QUESTION_CHARS, "input", "LLM_INPUT_LIMIT_EXCEEDED")
        for item in pool:
            require(len(item.episode.facts) <= MAX_FACTS_PER_EPISODE,
                    "input", "LLM_INPUT_LIMIT_EXCEEDED")
            size += len(item.episode.title) + sum(len(fact.text) for fact in item.episode.facts)
        require(size <= MAX_INPUT_CHARS, "input", "LLM_INPUT_LIMIT_EXCEEDED")

    def _ask(self, stage: str, prompt: str, payload: dict) -> dict:
        encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True)
        require(len(prompt) + len(encoded) <= MAX_INPUT_CHARS,
                stage, "LLM_INPUT_LIMIT_EXCEEDED")
        result = self.client.complete_json(stage=stage, system_prompt=prompt, payload=payload)
        require(isinstance(result, dict), stage)
        self.calls.append({
            "stage": stage,
            "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
            "input_sha256": hashlib.sha256(encoded.encode("utf-8")).hexdigest(),
        })
        return result

    def metadata(self) -> dict:
        result = {
            "mode": "SIMULATED_LLM" if self.client.simulated else "LLM",
            "provider": self.client.provider, "model": self.client.model,
            "prompt_version": PROMPT_VERSION, "calls": list(self.calls),
            "ranking_method": "deterministic_evidence_order/0.1",
            "candidate_call_strategy": "one_episode_per_request/0.1",
        }
        selection = getattr(self.client, "selection", None)
        if selection is not None:
            result["model_selection"] = selection
        return result

    def analyze(self, question_text: str) -> QuestionAnalysis:
        stage = "question"
        value = self._ask(stage, QUESTION_PROMPT, {"question_text": question_text})
        exact_keys(value, {"criteria", "required_facts", "needs_confirmation"}, stage)
        require(type(value["needs_confirmation"]) is bool, stage)
        require(isinstance(value["criteria"], list) and len(value["criteria"]) <= 6, stage)
        required = unique_strings(value["required_facts"], stage)
        require(set(required) <= {"ROLE", "ACTION", "RESULT"}, stage)
        known_labels = {rule.key: rule.label for rule in RULES}
        criteria = []
        seen = set()
        for criterion in value["criteria"]:
            exact_keys(criterion, {"criterion_id", "label", "question_quote"}, stage)
            key = nonempty_text(criterion["criterion_id"], stage, 40)
            require(key in known_labels or re.fullmatch(r"custom_[1-6]", key) is not None, stage)
            require(key not in seen, stage)
            seen.add(key)
            label = nonempty_text(criterion["label"], stage, 60)
            quote = nonempty_text(criterion["question_quote"], stage)
            start = question_text.find(quote)
            require(start >= 0, stage, "LLM_UNGROUNDED_QUESTION")
            criteria.append({
                "criterion_id": key, "label": known_labels.get(key, label),
                "question_evidence": {"start": start, "end": start + len(quote), "quote": quote},
            })
        needs_confirmation = value["needs_confirmation"] or not criteria
        # Preserve explicit ROLE/ACTION/RESULT and length constraints even if omitted by the model.
        explicit = QuestionAnalyzer().analyze(question_text)
        required = tuple(kind for kind in ("ROLE", "ACTION", "RESULT")
                         if kind in set(required) | set(explicit.required_facts))
        return QuestionAnalysis(
            tuple(criteria), required, explicit.character_limit, needs_confirmation,
            ("문항의 경험 선택 기준을 확정하지 못해 해석 확인이 필요합니다.",) if needs_confirmation else (),
            method="llm_question_analysis/0.1",
        )

    def evaluate(
        self, question_text: str, analysis: QuestionAnalysis, validated: list[ValidatedCandidate],
    ) -> list[ValidatedCandidate]:
        stage = "candidates"
        scoped = [item for item in validated if not item.rejected]
        if not scoped:
            return []
        payload = {
            "question_text": question_text, "criteria": list(analysis.criteria),
            "required_facts": list(analysis.required_facts),
            "candidates": [{
                "episode_id": item.candidate.episode.episode_id,
                "episode_version": item.candidate.episode.version,
                "title": item.candidate.episode.title,
                "facts": [{"fact_id": fact.fact_id, "kind": fact.kind, "text": fact.text}
                          for fact in item.admitted_facts],
            } for item in scoped],
        }
        originals = {item.candidate.episode.episode_id: item for item in scoped}
        decisions = {}
        for outbound in single_candidate_payloads(payload):
            value = self._ask(stage, CANDIDATE_PROMPT, outbound)
            exact_keys(value, {"candidates"}, stage)
            require(isinstance(value["candidates"], list), stage)
            require(len(value["candidates"]) == 1, stage, "LLM_INCOMPLETE_CANDIDATE_SET"
                    if not value["candidates"] else "LLM_INVALID_EPISODE_REFERENCE")
            decision = value["candidates"][0]
            exact_keys(decision, {"episode_id", "episode_version", "relevance", "fact_checks", "matches"}, stage)
            episode_id = nonempty_text(decision["episode_id"], stage)
            require(episode_id == outbound["candidates"][0]["episode_id"] and episode_id not in decisions,
                    stage, "LLM_INVALID_EPISODE_REFERENCE")
            require(type(decision["episode_version"]) is int
                    and decision["episode_version"] == originals[episode_id].candidate.episode.version,
                    stage, "LLM_INVALID_EPISODE_REFERENCE")
            decisions[episode_id] = self._candidate_result(originals[episode_id], decision, analysis)
        require(set(decisions) == set(originals), stage, "LLM_INCOMPLETE_CANDIDATE_SET")
        # Input order, not model order, determines stable tie-breaking.
        return [decisions[key] for key in originals]

    def _candidate_result(
        self, original: ValidatedCandidate, decision: dict, analysis: QuestionAnalysis,
    ) -> ValidatedCandidate:
        stage = "candidates"
        require(decision["relevance"] in ("RELATED", "UNRELATED", "UNCERTAIN"), stage)
        require(isinstance(decision["fact_checks"], list), stage)
        facts = {fact.fact_id: fact for fact in original.admitted_facts}
        checks = {}
        issues = list(original.issues)
        for check in decision["fact_checks"]:
            exact_keys(check, {"fact_id", "usable", "issue"}, stage)
            fact_id = nonempty_text(check["fact_id"], stage)
            require(fact_id in facts and fact_id not in checks, stage, "LLM_INVALID_FACT_REFERENCE")
            require(type(check["usable"]) is bool, stage)
            if check["usable"]:
                require(check["issue"] is None, stage)
            else:
                require(check["issue"] in FACT_ISSUES, stage)
                issues.append({"code": check["issue"], "fact_id": fact_id})
            checks[fact_id] = check["usable"]
        require(set(checks) == set(facts), stage, "LLM_INCOMPLETE_FACT_CHECKS")
        admitted = tuple(fact for fact in original.admitted_facts if checks[fact.fact_id])
        allowed_actions = {fact.fact_id for fact in admitted if fact.kind == "ACTION"}
        criteria_ids = [item["criterion_id"] for item in analysis.criteria]
        require(isinstance(decision["matches"], list), stage)
        matches = {}
        for match in decision["matches"]:
            exact_keys(match, {"criterion_id", "fact_ids"}, stage)
            key = nonempty_text(match["criterion_id"], stage)
            require(key in criteria_ids and key not in matches, stage, "LLM_INVALID_CRITERION_REFERENCE")
            refs = unique_strings(match["fact_ids"], stage)
            require(bool(refs) and set(refs) <= allowed_actions, stage, "LLM_INVALID_FACT_REFERENCE")
            matches[key] = tuple(fact.fact_id for fact in admitted if fact.fact_id in refs)
        matches = {key: matches[key] for key in criteria_ids if key in matches}
        if decision["relevance"] == "UNRELATED":
            require(not matches, stage)
            # Missing usable personal actions are not evidence of irrelevance.
            if allowed_actions:
                return replace(original, status="REJECTED", admitted_facts=(), matches={},
                               issues=tuple(issues), rejected=True, method=SEMANTIC_METHOD)
        required = set(analysis.required_facts) | {"ACTION"}
        for kind in sorted(required - {fact.kind for fact in admitted}):
            issues.append({"code": "MISSING_PERSONAL_FACT", "kind": kind})
        if decision["relevance"] == "UNCERTAIN" or not allowed_actions:
            issues.append({"code": "SEMANTIC_MATCH_UNCERTAIN"})
        if issues or not matches:
            status = "NEEDS_CONFIRMATION"
        elif len(matches) == len(criteria_ids):
            status = "DIRECT_MATCH"
        else:
            status = "PARTIAL_MATCH"
        return replace(original, status=status, admitted_facts=admitted, matches=matches,
                       issues=tuple(issues), method=SEMANTIC_METHOD)
