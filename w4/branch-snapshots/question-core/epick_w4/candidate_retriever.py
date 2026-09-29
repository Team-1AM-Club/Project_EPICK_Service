"""List-based candidate retrieval; Graph/Vector results can later supply episodes."""

from dataclasses import dataclass
import re

from .contracts import Episode, Request
from .question_analyzer import QuestionAnalysis, RULES


@dataclass(frozen=True)
class Candidate:
    episode: Episode
    retrieval_matches: tuple[str, ...]


class CandidateRetriever:
    def scoped_pool(self, request: Request, user_id: str) -> list[Candidate]:
        """Small supplied pool for semantic matching; no keyword pre-filter."""
        return [Candidate(episode, ()) for episode in request.episodes if (
            episode.owner_id == user_id and episode.episode_id not in request.excluded_ids
            and request.episode_versions.get(episode.episode_id) == episode.version
        )]

    def retrieve(self, request: Request, analysis: QuestionAnalysis, user_id: str) -> list[Candidate]:
        results = []
        wanted = {item["criterion_id"] for item in analysis.criteria}
        for scoped in self.scoped_pool(request, user_id):
            episode = scoped.episode
            matches = tuple(
                rule.key for rule in RULES
                if rule.key in wanted and re.search(
                    f"(?:{rule.question})|(?:{rule.action})", episode.raw_text, re.IGNORECASE,
                )
            )
            if matches:
                results.append(Candidate(episode, matches))
        # Apply top_k after validation, so invalid early hits cannot hide valid candidates.
        return results
