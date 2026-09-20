"""Validate original spans, episode boundaries and personal action before composition."""

from dataclasses import dataclass
import re

from .candidate_retriever import Candidate
from .contracts import Fact, Request
from .question_analyzer import PERSONAL, UNCERTAIN, QuestionAnalysis, action_matches

TECHNOLOGIES = re.compile(r"\b(?:AWS|GCP|Azure|Python|JavaScript|Java|Neo4j|PostgreSQL)\b", re.I)
NUMBERS = re.compile(r"\d+(?:[,.]\d+)*(?:\s*%)?")


@dataclass(frozen=True)
class ValidatedCandidate:
    candidate: Candidate
    status: str
    admitted_facts: tuple[Fact, ...]
    matches: dict[str, tuple[str, ...]]
    issues: tuple[dict, ...]
    rejected: bool = False
    method: str = "rules"


class CandidateValidator:
    def validate(
        self, candidate: Candidate, request: Request, analysis: QuestionAnalysis, user_id: str,
        *, semantic: bool = False,
    ) -> ValidatedCandidate:
        episode = candidate.episode
        if (
            episode.owner_id != user_id or episode.episode_id in request.excluded_ids
            or request.episode_versions.get(episode.episode_id) != episode.version
        ):
            return ValidatedCandidate(candidate, "REJECTED", (), {}, (), True)
        admitted = []
        issues = []
        for fact in episode.facts:
            code = None
            if fact.episode_id != episode.episode_id or fact.episode_version != episode.version:
                code = "EPISODE_BOUNDARY_MISMATCH"
            elif not 0 <= fact.start < fact.end <= len(episode.raw_text):
                code = "INVALID_EVIDENCE_SPAN"
            else:
                original = episode.raw_text[fact.start:fact.end]
                if original != fact.text:
                    stated_tech = set(TECHNOLOGIES.findall(fact.text.lower()))
                    actual_tech = set(TECHNOLOGIES.findall(original.lower()))
                    if stated_tech - actual_tech:
                        code = "TECHNOLOGY_DISTORTION"
                    elif set(NUMBERS.findall(fact.text)) - set(NUMBERS.findall(original)):
                        code = "NUMBER_DISTORTION"
                    else:
                        code = "QUOTE_MISMATCH"
                elif not semantic and fact.kind in {"ROLE", "ACTION"} and not PERSONAL.search(original):
                    code = "PERSONAL_CONTRIBUTION_UNCLEAR"
                elif not semantic and UNCERTAIN.search(original):
                    code = "NEGATED_OR_AMBIGUOUS_FACT"
            if code:
                issues.append({"code": code, "fact_id": fact.fact_id})
            else:
                admitted.append(fact)
        if semantic:
            # Meaning/personal contribution require the semantic assessment below.
            # This result alone can never produce a DIRECT_MATCH.
            return ValidatedCandidate(candidate, "NEEDS_CONFIRMATION", tuple(admitted), {}, tuple(issues))
        actions = [fact for fact in admitted if fact.kind == "ACTION"]
        matches = {}
        for criterion in analysis.criteria:
            key = criterion["criterion_id"]
            grounded = tuple(fact.fact_id for fact in actions if action_matches(fact.text, key))
            if grounded:
                matches[key] = grounded
        # A relevant team story alone does not prove the user's contribution.
        present = {fact.kind for fact in admitted}
        required = set(analysis.required_facts) | {"ACTION"}
        for kind in sorted(required - present):
            issues.append({"code": "MISSING_PERSONAL_FACT", "kind": kind})
        if issues or not matches:
            status = "NEEDS_CONFIRMATION"
        elif len(matches) == len(analysis.criteria):
            status = "DIRECT_MATCH"
        else:
            status = "PARTIAL_MATCH"
        return ValidatedCandidate(candidate, status, tuple(admitted), matches, tuple(issues))
