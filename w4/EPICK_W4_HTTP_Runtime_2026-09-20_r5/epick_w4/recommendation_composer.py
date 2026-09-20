"""Build explanations from admitted evidence; question fit has ranking priority."""

import re

from .candidate_validator import ValidatedCandidate
from .knowledge_gate import KnowledgeContext
from .question_analyzer import QuestionAnalysis, RULES, UNCERTAIN


ISSUE_TEXT = {
    "EPISODE_BOUNDARY_MISMATCH": "다른 경험 또는 버전의 근거가 연결되어 해당 진술을 제외했습니다.",
    "INVALID_EVIDENCE_SPAN": "근거 위치가 경험 원문 범위를 벗어나 해당 진술을 제외했습니다.",
    "TECHNOLOGY_DISTORTION": "근거에 없는 기술명이 포함되어 해당 진술을 제외했습니다.",
    "NUMBER_DISTORTION": "근거와 다른 수치가 포함되어 해당 진술을 제외했습니다.",
    "QUOTE_MISMATCH": "진술과 원문 발췌가 일치하지 않아 해당 진술을 제외했습니다.",
    "PERSONAL_CONTRIBUTION_UNCLEAR": "팀의 활동과 구분되는 본인의 역할·행동을 확인해야 합니다.",
    "NEGATED_OR_AMBIGUOUS_FACT": "부정·제외 표현이 포함된 진술은 의미 확인이 필요합니다.",
    "MISSING_PERSONAL_FACT": "문항 답변에 필요한 역할·행동·결과 근거가 일부 부족합니다.",
    "INSUFFICIENT_CONTEXT": "기록된 문맥만으로 해당 근거의 의미를 확정하기 어렵습니다.",
    "INSTRUCTION_IN_SOURCE": "경험 근거 대신 AI 지시가 포함된 진술을 제외했습니다.",
    "SEMANTIC_MATCH_UNCERTAIN": "현재 경험 기록과 문항 기준의 의미적 연결에 추가 확인이 필요합니다.",
}


def company_connections(candidate: ValidatedCandidate, knowledge: KnowledgeContext) -> list[dict]:
    connections = []
    for claim in knowledge.claims:
        if UNCERTAIN.search(claim["statement"]):
            continue
        related = [
            rule.key for rule in RULES if rule.key in candidate.matches
            and re.search(rule.question, claim["statement"], re.IGNORECASE)
        ]
        if related:
            connections.append({**claim, "related_criterion_ids": related})
    return connections


class RecommendationComposer:
    def compose(
        self, candidates: list[ValidatedCandidate], analysis: QuestionAnalysis,
        knowledge: KnowledgeContext, top_k: int,
    ) -> list[dict]:
        labels = {item["criterion_id"]: item["label"] for item in analysis.criteria}
        context = {
            item.candidate.episode.episode_id: company_connections(item, knowledge)
            for item in candidates if not item.rejected
        }
        order = {"DIRECT_MATCH": 0, "PARTIAL_MATCH": 1, "NEEDS_CONFIRMATION": 2}
        eligible = [item for item in candidates if not item.rejected]
        eligible.sort(key=lambda item: (
            order[item.status], -len(item.matches),
            -len({fact.kind for fact in item.admitted_facts}),
            # Multiple claims from one evidence segment are not independent votes.
            -len({key for link in context[item.candidate.episode.episode_id]
                  for key in link["related_criterion_ids"]}), item.candidate.episode.episode_id,
        ))
        results = []
        for rank, item in enumerate(eligible[:top_k], start=1):
            episode = item.candidate.episode
            missing = [key for key in labels if key not in item.matches]
            reasons = [{
                "criterion_id": key,
                "text": f"본인 행동 원문에서 ‘{labels[key]}’ 기준에 해당하는 행동이 확인됩니다.",
                "episode_fact_ids": list(fact_ids),
            } for key, fact_ids in item.matches.items()]
            if item.method != "rules":
                for reason in reasons:
                    quoted = [fact.text for fact in item.admitted_facts
                              if fact.fact_id in reason["episode_fact_ids"]]
                    reason["text"] = (
                        f"문항의 ‘{labels[reason['criterion_id']]}’ 기준과 연결된 본인 행동: "
                        + " / ".join(f"‘{quote}’" for quote in quoted)
                    )
                    reason["assessment_method"] = item.method
            limitations = list(dict.fromkeys(ISSUE_TEXT[issue["code"]] for issue in item.issues))
            if missing:
                limitations.append("현재 근거에서 확인되지 않은 문항 기준: " + ", ".join(labels[key] for key in missing))
            if not knowledge.claims:
                limitations.append("사용 가능한 기업 근거가 없어 문항·경험 근거로만 비교했습니다.")
            strengths = [labels[key] for key in item.matches]
            if any(fact.kind == "RESULT" for fact in item.admitted_facts):
                strengths.append("결과 원문이 함께 기록되어 있습니다.")
            results.append({
                "candidate_id": f"{episode.episode_id}@{episode.version}",
                "rank": rank, "episode_id": episode.episode_id,
                "episode_version": episode.version, "activity_id": episode.activity_id,
                "title": episode.title, "status": item.status,
                "question_fit": {"matched_criterion_ids": list(item.matches), "missing_criterion_ids": missing},
                "reasons": reasons, "strengths": strengths, "limitations": limitations,
                "confirmation_items": list(item.issues),
                "episode_evidence": [{
                    "fact_id": fact.fact_id, "kind": fact.kind,
                    "episode_id": episode.episode_id, "episode_version": episode.version,
                    "start": fact.start, "end": fact.end, "exact_quote": fact.text,
                } for fact in item.admitted_facts],
                "company_context": context[episode.episode_id],
            })
        return results
