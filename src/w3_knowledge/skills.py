"""기술 표현을 보존하는 제한적 별칭 연결."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SkillMention:
    original: str
    canonical: str | None
    basis: str | None


def normalize_skill_mentions(
    terms: tuple[str, ...], approved_aliases: dict[str, tuple[str, str]]
) -> tuple[SkillMention, ...]:
    """정확히 등록된 별칭만 연결하며, 원문 표현을 대체하지 않는다."""
    result: list[SkillMention] = []
    for term in terms:
        alias = approved_aliases.get(term.casefold())
        if alias is None:
            result.append(SkillMention(original=term, canonical=None, basis=None))
        else:
            canonical, basis = alias
            result.append(SkillMention(original=term, canonical=canonical, basis=basis))
    return tuple(result)
