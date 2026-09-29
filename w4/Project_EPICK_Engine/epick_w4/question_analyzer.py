"""Conservative Korean rule baseline; unsupported questions request confirmation."""

from dataclasses import dataclass
import re


@dataclass(frozen=True)
class Rule:
    key: str
    label: str
    question: str
    action: str


RULES = (
    Rule("collaboration", "협업·조율", r"협업|협력|팀워크|공동\s*목표|함께|갈등", r"조율|합의|의견.{0,12}공유|역할.{0,8}(?:나누|나눴|분담)|갈등.{0,12}해결"),
    Rule("problem_solving", "문제 해결", r"문제.{0,8}해결|문제해결|원인\s*분석|개선|오류.{0,8}해결", r"원인.{0,15}(?:찾|분석|파악)|오류.{0,15}(?:분석|수정)|가설.{0,12}검증|문제.{0,12}해결"),
    Rule("challenge", "도전·목표 달성", r"도전|어려운\s*목표|높은\s*목표|실패.{0,10}극복", r"목표.{0,12}(?:설정|세우|세웠)|실패.{0,12}(?:분석|개선)|재도전|반복.{0,8}시도"),
    Rule("leadership", "리더십", r"리더십|주도|이끌|이끈", r"팀.{0,8}이끌|역할.{0,8}분담|일정.{0,8}조정|의사결정.{0,8}주도"),
    Rule("learning", "학습·적용", r"학습|배우|배운|익힌|새로운\s*기술|처음.{0,8}기술", r"학습|공식\s*문서.{0,8}읽|실습|배워|익혀|익혔"),
    Rule("responsibility", "책임감", r"책임감|책임.{0,8}완수|끝까지", r"끝까지.{0,12}(?:담당|완수)|인수인계|일정.{0,12}지켰|책임.{0,12}완수"),
)

# Negated or contrastive statements need a semantic model/review. Do not award a
# positive competency merely because an excluded/negated term occurs in a quote.
UNCERTAIN = re.compile(r"않|못했|못하|없었|없다|아니|아닌|말고|제외|무관")
PERSONAL = re.compile(r"^\s*(?:저는|제가|나는|내가|본인은|본인이)")


def action_matches(text: str, criterion_id: str) -> bool:
    if UNCERTAIN.search(text):
        return False
    rule = next(rule for rule in RULES if rule.key == criterion_id)
    return re.search(rule.action, text, re.IGNORECASE) is not None


@dataclass(frozen=True)
class QuestionAnalysis:
    criteria: tuple[dict, ...]
    required_facts: tuple[str, ...]
    character_limit: int | None
    needs_confirmation: bool
    limitations: tuple[str, ...]
    method: str = "korean_rules/0.1"

    def to_dict(self) -> dict:
        return {
            "method": self.method,
            "criteria": list(self.criteria),
            "explicit_fact_requirements": list(self.required_facts),
            "character_limit": self.character_limit,
            "needs_confirmation": self.needs_confirmation,
            "limitations": list(self.limitations),
        }


class QuestionAnalyzer:
    def analyze(self, text: str) -> QuestionAnalysis:
        criteria = []
        for rule in RULES:
            match = re.search(rule.question, text, re.IGNORECASE)
            if match:
                criteria.append({
                    "criterion_id": rule.key,
                    "label": rule.label,
                    "question_evidence": {
                        "start": match.start(), "end": match.end(), "quote": match.group(),
                    },
                })
        required = tuple(
            kind for kind, pattern in (
                ("ROLE", r"역할"), ("ACTION", r"행동|노력|과정"), ("RESULT", r"결과|성과"),
            ) if re.search(pattern, text)
        )
        limit = re.search(r"(\d[\d,]*)\s*자\s*(?:이내|이하)", text)
        limitations = []
        if not criteria:
            limitations.append("지원하는 경험형 문항 기준을 찾지 못해 문항 해석 확인이 필요합니다.")
        if UNCERTAIN.search(text):
            limitations.append("부정·제외 조건이 있는 문항은 기본 규칙으로 확정하지 않습니다.")
        return QuestionAnalysis(
            criteria=tuple(criteria), required_facts=required,
            character_limit=int(limit.group(1).replace(",", "")) if limit else None,
            needs_confirmation=bool(limitations), limitations=tuple(limitations),
        )
