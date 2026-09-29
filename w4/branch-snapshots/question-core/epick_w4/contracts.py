"""Small, dependency-free contracts for the first internal W4 integration."""

from dataclasses import dataclass
from datetime import date
from typing import Any

INPUT_SCHEMA = "w4-recommendation-input/0.1"
OUTPUT_SCHEMA = "w4-recommendation-output/0.1"


class ContractError(ValueError):
    def __init__(self, code: str, field: str):
        self.code = code
        self.field = field
        # Only field paths/codes are reported, never private input text.
        super().__init__(f"{code}: {field}")


def object_value(value: Any, field: str) -> dict:
    if not isinstance(value, dict):
        raise ContractError("EXPECTED_OBJECT", field)
    return value


def array_value(value: Any, field: str) -> list:
    if not isinstance(value, list):
        raise ContractError("EXPECTED_ARRAY", field)
    return value


def string_value(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ContractError("EXPECTED_NONEMPTY_STRING", field)
    return value


def integer_value(value: Any, field: str, minimum: int = 1) -> int:
    if type(value) is not int or value < minimum:
        raise ContractError("EXPECTED_INTEGER", field)
    return value


def strings(value: Any, field: str) -> tuple[str, ...]:
    items = tuple(string_value(item, field) for item in array_value(value, field))
    if len(set(items)) != len(items):
        raise ContractError("DUPLICATE_REFERENCE", field)
    return items


@dataclass(frozen=True)
class Fact:
    fact_id: str
    kind: str
    text: str
    episode_id: str
    episode_version: int
    start: int
    end: int


@dataclass(frozen=True)
class Episode:
    episode_id: str
    version: int
    owner_id: str
    activity_id: str
    title: str
    raw_text: str
    facts: tuple[Fact, ...]


@dataclass(frozen=True)
class Request:
    request_id: str
    data_kind: str
    project_id: str
    owner_id: str
    company_id: str
    job_role: str
    posting_version_id: str
    snapshot_id: str
    as_of: date
    question_id: str
    question_version: int
    question_text: str
    episode_versions: dict[str, int]
    source_version_ids: tuple[str, ...]
    episodes: tuple[Episode, ...]
    excluded_ids: tuple[str, ...]
    company_context_policy: str
    unknown_date_policy: str
    knowledge: dict
    top_k: int

    @classmethod
    def parse(cls, payload: Any) -> "Request":
        root = object_value(payload, "request")
        if root.get("evaluation_mode") == "CONTRACT_SAMPLE_DIAGNOSTIC":
            raise ContractError("DIAGNOSTIC_IS_NOT_RECOMMENDATION_INPUT", "schema_version")
        if root.get("schema_version") != INPUT_SCHEMA:
            raise ContractError("UNSUPPORTED_SCHEMA", "schema_version")
        if root.get("data_kind") not in ("SYNTHETIC", "REAL"):
            raise ContractError("INVALID_DATA_KIND", "data_kind")
        project = object_value(root.get("project"), "project")
        question = object_value(root.get("question"), "question")
        snapshot = object_value(root.get("snapshot"), "snapshot")
        question_version = integer_value(question.get("version"), "question.version")
        if integer_value(snapshot.get("question_version"), "snapshot.question_version") != question_version:
            raise ContractError("QUESTION_VERSION_MISMATCH", "snapshot.question_version")
        if snapshot.get("question_id") != question.get("question_id"):
            raise ContractError("QUESTION_ID_MISMATCH", "snapshot.question_id")
        as_of_text = string_value(snapshot.get("as_of"), "snapshot.as_of")
        try:
            as_of = date.fromisoformat(as_of_text)
        except ValueError:
            raise ContractError("INVALID_DATE", "snapshot.as_of") from None
        versions = object_value(snapshot.get("episode_versions"), "snapshot.episode_versions")
        versions = {
            string_value(key, "snapshot.episode_versions"): integer_value(
                value, "snapshot.episode_versions"
            )
            for key, value in versions.items()
        }
        episodes = []
        seen_ids = set()
        for index, value in enumerate(array_value(root.get("episodes"), "episodes")):
            field = f"episodes[{index}]"
            episode = object_value(value, field)
            episode_id = string_value(episode.get("episode_id"), f"{field}.episode_id")
            if episode_id in seen_ids:
                raise ContractError("DUPLICATE_EPISODE", f"{field}.episode_id")
            seen_ids.add(episode_id)
            facts = []
            fact_ids = set()
            for fact_index, fact_value in enumerate(array_value(episode.get("facts"), f"{field}.facts")):
                fact_field = f"{field}.facts[{fact_index}]"
                fact = object_value(fact_value, fact_field)
                fact_id = string_value(fact.get("fact_id"), f"{fact_field}.fact_id")
                if fact_id in fact_ids:
                    raise ContractError("DUPLICATE_FACT", f"{fact_field}.fact_id")
                fact_ids.add(fact_id)
                if fact.get("kind") not in ("ROLE", "ACTION", "RESULT"):
                    raise ContractError("INVALID_FACT_KIND", f"{fact_field}.kind")
                evidence = object_value(fact.get("evidence"), f"{fact_field}.evidence")
                facts.append(Fact(
                    fact_id=fact_id,
                    kind=fact["kind"],
                    text=string_value(fact.get("text"), f"{fact_field}.text"),
                    episode_id=string_value(evidence.get("episode_id"), f"{fact_field}.evidence.episode_id"),
                    episode_version=integer_value(evidence.get("episode_version"), f"{fact_field}.evidence.episode_version"),
                    start=integer_value(evidence.get("start"), f"{fact_field}.evidence.start", 0),
                    end=integer_value(evidence.get("end"), f"{fact_field}.evidence.end", 0),
                ))
            episodes.append(Episode(
                episode_id=episode_id,
                version=integer_value(episode.get("version"), f"{field}.version"),
                owner_id=string_value(episode.get("owner_id"), f"{field}.owner_id"),
                activity_id=string_value(episode.get("activity_id"), f"{field}.activity_id"),
                title=string_value(episode.get("title"), f"{field}.title"),
                raw_text=string_value(episode.get("raw_text"), f"{field}.raw_text"),
                facts=tuple(facts),
            ))
        policy = root.get("company_context_policy")
        if policy not in ("OPTIONAL", "REQUIRED"):
            raise ContractError("INVALID_CONTEXT_POLICY", "company_context_policy")
        date_policy = root.get("unknown_date_policy", "EXCLUDE")
        if date_policy not in ("EXCLUDE", "INCLUDE_WITH_LABEL"):
            raise ContractError("INVALID_DATE_POLICY", "unknown_date_policy")
        top_k = integer_value(root.get("top_k", 3), "top_k")
        if top_k > 20:
            raise ContractError("TOP_K_TOO_LARGE", "top_k")
        return cls(
            request_id=string_value(root.get("request_id"), "request_id"),
            data_kind=root["data_kind"],
            project_id=string_value(project.get("project_id"), "project.project_id"),
            owner_id=string_value(project.get("owner_id"), "project.owner_id"),
            company_id=string_value(project.get("company_id"), "project.company_id"),
            job_role=string_value(project.get("job_role"), "project.job_role"),
            posting_version_id=string_value(project.get("job_posting_version_id"), "project.job_posting_version_id"),
            snapshot_id=string_value(snapshot.get("snapshot_id"), "snapshot.snapshot_id"),
            as_of=as_of,
            question_id=string_value(question.get("question_id"), "question.question_id"),
            question_version=question_version,
            question_text=string_value(question.get("text"), "question.text"),
            episode_versions=versions,
            source_version_ids=strings(snapshot.get("source_version_ids"), "snapshot.source_version_ids"),
            episodes=tuple(episodes),
            excluded_ids=strings(root.get("excluded_episode_ids", []), "excluded_episode_ids"),
            company_context_policy=policy,
            unknown_date_policy=date_policy,
            knowledge=object_value(root.get("company_knowledge"), "company_knowledge"),
            top_k=top_k,
        )
