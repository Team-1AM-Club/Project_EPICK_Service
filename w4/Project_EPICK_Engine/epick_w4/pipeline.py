"""Connect W4 with the rule baseline or an explicitly injected semantic adapter."""

from .candidate_retriever import CandidateRetriever
from .candidate_validator import CandidateValidator
from .contracts import ContractError, OUTPUT_SCHEMA, Request, object_value, string_value
from .knowledge_gate import consume_knowledge
from .llm_contract import JsonClient, LLMError
from .question_analyzer import QuestionAnalysis, QuestionAnalyzer
from .recommendation_composer import RecommendationComposer
from .semantic_matching import SemanticMatcher
from .synthetic_policy import check_sample_inputs


def recommend(payload: dict, *, user_id: str, llm: JsonClient | None = None) -> dict:
    """user_id must come from trusted authentication, not the request body.

    This prototype uses explicit synthetic fixtures for development. VERIFIED
    records are upstream attestations; this function does not assert W3 was run.
    """
    string_value(user_id, "authenticated_user_id")
    root = object_value(payload, "request")
    if root.get("evaluation_mode") == "CONTRACT_SAMPLE_DIAGNOSTIC":
        raise ContractError("DIAGNOSTIC_IS_NOT_RECOMMENDATION_INPUT", "schema_version")
    project = object_value(root.get("project"), "project")
    if project.get("owner_id") != user_id:
        raise ContractError("PROJECT_ACCESS_DENIED", "project.owner_id")
    request = Request.parse(payload)
    knowledge = consume_knowledge(request)
    retriever = CandidateRetriever()
    semantic = SemanticMatcher(llm) if llm is not None else None
    scoped = []
    company_missing = request.company_context_policy == "REQUIRED" and not knowledge.claims
    if semantic:
        # The initial integration is restricted to synthetic fixtures. Real-data
        # transmission requires a separate, explicit product/consent integration.
        if request.data_kind != "SYNTHETIC":
            raise LLMError("LLM_REAL_DATA_NOT_ENABLED", "input")
        scoped = retriever.scoped_pool(request, user_id)
        semantic.preflight(request.question_text, scoped)
        if company_missing:
            analysis = QuestionAnalysis((), (), None, False, (), method="not_run")
        else:
            if not llm.simulated:
                check_sample_inputs(request.question_text, scoped)
            analysis = semantic.analyze(request.question_text)
    else:
        analysis = QuestionAnalyzer().analyze(request.question_text)
    version_mismatch = any(
        episode.owner_id == user_id and episode.episode_id not in request.excluded_ids
        and request.episode_versions.get(episode.episode_id) != episode.version
        for episode in request.episodes
    )
    retrieved = []
    candidates = []
    limitations = [
        ("실제 의미 판단을 연결한 개발 버전이며 Graph·Vector 검색과 사람이 검토한 정답을 통한 품질 확정은 아직 완료하지 않았습니다."
         if semantic else "한국어 규칙 기반 초기 버전으로, 자유로운 의미 해석·LLM·Graph·Vector 검색은 아직 연결하지 않았습니다."),
        "원문 일치 확인은 기록의 사실성이나 개인의 단독 성과를 독립적으로 입증하지 않습니다.",
    ]
    if semantic:
        limitations.append("기업 맥락 연결은 기존의 제한된 규칙이며, 설명은 선택된 원문과 기준으로 조립합니다.")
        if llm.simulated:
            limitations.append("미리 작성한 모의 모델 응답으로 연결을 검증했습니다. 실제 LLM 호출이나 품질 검증 결과가 아닙니다.")
    if request.data_kind == "SYNTHETIC":
        limitations.append("개발용 가상 문항·경험·기업정보입니다. SK하이닉스의 실제 정책 또는 지원자 평가 결과가 아닙니다.")
    if version_mismatch:
        limitations.append("현재 스냅샷과 버전이 다른 경험이 있어 검색에서 제외했습니다. 경험이 부족하다는 뜻은 아닙니다.")
    if analysis.needs_confirmation:
        status = "NEEDS_INPUT"
        limitations.extend(analysis.limitations)
    elif company_missing:
        status = "NEEDS_INPUT"
        limitations.append("호출자가 기업 근거를 필수로 지정했으나 사용 가능한 기업 진술이 없습니다.")
    else:
        retrieved = scoped if semantic else retriever.retrieve(request, analysis, user_id)
        validator = CandidateValidator()
        validated = [validator.validate(item, request, analysis, user_id, semantic=bool(semantic))
                     for item in retrieved]
        if semantic:
            validated = semantic.evaluate(request.question_text, analysis, validated)
        candidates = RecommendationComposer().compose(validated, analysis, knowledge, request.top_k)
        if not candidates:
            status = "NEEDS_INPUT" if version_mismatch else "NO_CANDIDATES"
            limitations.append("제공된 검색 범위에서 관련 경험을 찾지 못했습니다. 지원 자격 미충족 판정이 아닙니다.")
        elif version_mismatch or knowledge.diagnostics or any(item["status"] == "NEEDS_CONFIRMATION" for item in candidates):
            status = "COMPLETED_WITH_LIMITATIONS"
        else:
            status = "COMPLETED"
    # Never return foreign/excluded episode IDs, even through snapshot metadata.
    searched_versions = {item.episode.episode_id: item.episode.version for item in retrieved}
    used_sources = sorted({
        context["source_version_id"] for candidate in candidates for context in candidate["company_context"]
    })
    result = {
        "schema_version": OUTPUT_SCHEMA,
        "pipeline_version": "w4-semantic/0.2" if semantic else "w4-local-rules/0.1",
        "request_id": request.request_id,
        "data_kind": request.data_kind, "status": status,
        "project_id": request.project_id, "question_id": request.question_id,
        "question_analysis": analysis.to_dict(),
        "company_context_policy": request.company_context_policy,
        "unknown_date_policy": request.unknown_date_policy,
        "company_context": knowledge.summary(),
        "candidates": candidates,
        "eligibility_assessment": {
            "status": "NOT_ASSESSED",
            "reason": "경험의 문항 적합성과 전체 지원요건 충족은 별도입니다. 첫 버전은 지원 자격을 판정하지 않습니다.",
        },
        "snapshot": {
            "snapshot_id": request.snapshot_id, "as_of": request.as_of.isoformat(),
            "question_version": request.question_version,
            "job_posting_version_id": request.posting_version_id,
            "searched_episode_versions": searched_versions, "used_source_version_ids": used_sources,
        },
        "ranking_policy": ["후보 상태", "문항 기준 충족 개수", "근거 종류의 충실도", "기업 맥락 연결", "경험 ID"],
        "limitations": limitations,
    }
    if semantic:
        result["inference"] = semantic.metadata()
    return result
