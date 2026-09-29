"""Seven new, explicit fictions for company-scope matching; draft labels only."""

from copy import deepcopy
import hashlib
import json

from epick_w4.c01_adapter import consume_c01
from epick_w4.c01_contract import C01ServerContext
from epick_w4.extraction_eval import build_extraction_requests
from epick_w4.model_eval import digest
from examples.w4_c01_demo import build_context

CLAIM = "소프트웨어 개발 기술을 꾸준히 학습한 경험을 참고합니다."


def supported(required, allowed=None):
    return {"statuses": ["SUPPORTED"], "required_units": required,
            "allowed_units": allowed if allowed is not None else required}


def absent(*, planned=False):
    return {"statuses": ["NOT_SHOWN", "AMBIGUOUS"] if planned else ["NOT_SHOWN"],
            "required_units": [], "allowed_units": ["u2", "u3"] if planned else []}


def cases():
    # Opaque IDs prevent conveying the intended answer through the episode ID.
    texts = [
        ["저는 예약 API의 개발 담당자였습니다.",
         "저는 다섯 주 동안 매주 Python의 자료형 검사 예제를 읽고 실행하며 배웠습니다.",
         "저는 배운 자료형 검사 방법을 예약 API 코드에 적용해 날짜 입력값을 검증했습니다.",
         "잘못된 날짜가 예약 데이터에 저장되는 건수가 네 건에서 영 건으로 줄었습니다."],
        ["저는 로그 조회 도구의 개발 담당자였습니다.",
         "저는 네 주 동안 매주 캐시 원리 자료를 읽고 예제를 풀며 학습했습니다.",
         "저는 배운 캐시 무효화 방식을 조회 코드에 구현했습니다.",
         "저는 그 코드의 설정 방법과 실행 순서를 설명하는 문서를 작성했습니다."],
        ["저는 동아리 행사 안내 담당자였습니다.",
         "저는 여섯 주 동안 매일 스페인어 교재로 길 안내 표현을 학습하고 말하기 연습을 했습니다.",
         "저는 배운 스페인어 표현을 행사 방문객에게 길을 안내할 때 사용했습니다.",
         "저는 안내 표현과 발음에 관한 학습 문서를 작성했습니다."],
        ["저는 프로젝트의 회의실 예약 담당자였습니다.",
         "동료 지수는 세 주 동안 매주 Python 검증 라이브러리를 학습했습니다.",
         "동료 지수는 배운 검사 방법을 업로드 코드에 적용해 입력값을 검증했습니다.",
         "저는 발표 회의를 위한 공간을 예약했습니다."],
        ["저는 파일 검사 도구의 개발 담당자였습니다.",
         "저는 다음 달부터 네 주 동안 매주 Python 자료형 검사 예제를 학습할 계획입니다.",
         "저는 학습한 내용을 업로드 코드의 입력값 검증에 적용할 계획입니다."],
        ["저는 달리기 기록 분석 프로그램의 개발 담당자였습니다.",
         "저는 여덟 주 동안 매주 CSV 파싱 안내서를 읽고 예제를 수정하며 학습했습니다.",
         "저는 배운 CSV 형식 검사 방법을 달리기 기록을 불러오는 프로그램 코드에 적용했습니다.",
         "저는 그 코드에서 거리와 날짜 입력값의 자료형을 검사하도록 구현했습니다."],
        ["저는 출석 명단 정리 담당자였습니다.",
         "저는 기존 Python 프로그램을 실행해 출석 명단 파일을 정렬했습니다.",
         "정렬된 명단 파일 한 개가 생성되었습니다."],
    ]
    descriptions = ["software_learning_and_validation", "software_learning_and_documentation",
                    "other_domain_learning_with_general_documentation", "colleague_not_self",
                    "future_plan_not_completed", "sports_subject_with_real_software_learning",
                    "existing_tool_use_without_learning"]
    result = []
    for number, lines in enumerate(texts, 1):
        cid = f"c01-semantic-{number:02d}"
        labels = []
        for i in range(len(lines)):
            kinds = ["ROLE"] if i == 0 else ["ACTION", "PERIOD"] if i == 1 and number != 7 else ["ACTION"]
            subject, assertion = "SELF", "AFFIRMED"
            if (number == 1 and i == 3) or (number == 7 and i == 2):
                kinds, subject = ["RESULT"], "UNSPECIFIED"
            if number == 4 and i in (1, 2):
                subject = "OTHER"
            if number == 5 and i in (1, 2):
                assertion = "PLANNED"
            labels.append({"unit_id": f"u{i + 1}", "kinds": kinds, "subject": subject,
                           "assertion": assertion, "issue": None})
        checks = {key: absent(planned=number == 5) for key in
                  ("expertise.specific_area", "expertise.sustained_learning", "expertise.practical_use")}
        claim = absent()
        leaves = {key: absent(planned=number == 5 and key != "documentation")
                  for key in ("apply", "validation", "documentation")}
        root = absent(planned=number == 5)
        if number in (1, 2, 3, 6):
            checks.update({"expertise.specific_area": supported(["u2"], ["u1", "u2", "u3"]),
                           "expertise.sustained_learning": supported(["u2"], ["u1", "u2", "u3"]),
                           "expertise.practical_use": supported(["u3"], ["u1", "u2", "u3", "u4"])})
        if number in (1, 2, 6):
            claim = supported(["u2"], ["u1", "u2", "u3", "u4"])
            leaves["apply"] = supported(["u3"], ["u1", "u2", "u3", "u4"])
            root = supported([])
        if number in (1, 6):
            unit = "u3" if number == 1 else "u4"
            leaves["validation"] = supported([unit], ["u1", "u2", "u3", "u4"])
        if number in (2, 3):
            leaves["documentation"] = supported(["u4"], ["u1", "u2", "u3", "u4"])
        result.append({"case_id": cid, "purpose": descriptions[number - 1], "lines": lines,
                       "extraction": {"units": labels}, "checks": checks, "claim": claim,
                       "leaves": leaves, "root": root})
    return result


def build_domain_context(now=None):
    # Separate IDs avoid sharing a cache generation with the older fiction.
    context = json.loads(json.dumps(build_context(now), ensure_ascii=False).replace(
        "10000000-0000-4000-8000-", "20000000-0000-4000-8000-"))
    context["context_version"] = "synthetic-c01-domain-context-v1"
    context["company_knowledge"]["knowledge_bundle_id"] = "synthetic-c01-domain-bundle-v1"
    for source in context["company_knowledge"]["sources"]:
        bundle = source["knowledge"]["bundle"]
        text = bundle["claims"][0]["statement"].split(":", 1)[0] + ": " + CLAIM
        bundle["claims"][0]["statement"] = text
        evidence = bundle["evidences"][0]
        evidence["excerpt"] = text
        evidence["observed_integrity"]["digest"] = hashlib.sha256(text.encode()).hexdigest()
        content = "\n".join(e["excerpt"] for e in bundle["evidences"])
        source["metadata"]["content_sha256"] = hashlib.sha256(content.encode()).hexdigest()
    context["episodes"] = [
        {"episode_id": c["case_id"], "version": 1, "owner_id": "user-demo",
         "activity_id": c["case_id"], "title": f"새 가상 경험 {i + 1}", "raw_text": "\n".join(c["lines"])}
        for i, c in enumerate(cases())]
    context["snapshot"] = {"snapshot_id": "synthetic-c01-domain-snapshot-v1",
                           "episode_versions": {e["episode_id"]: 1 for e in context["episodes"]}}
    return C01ServerContext.model_validate(context).model_dump()


def protocol():
    context = build_domain_context(1789603200)
    raw = {"schema_version": "w4-evidence-input/0.1", "request_id": "c01-domain-evaluation-v1",
           **{k: deepcopy(context[k]) for k in
              ("data_kind", "project", "snapshot", "episodes", "excluded_episode_ids")}}
    reference = {"schema_version": "w4-extraction-benchmark/0.1", "data_kind": "SYNTHETIC",
                 "review": {"status": "DRAFT", "author": "assistant", "human_reviewed": False},
                 "answers": {c["case_id"]: c["extraction"] for c in cases()},
                 "input_sha256": digest(raw), "protocol_sha256": digest(build_extraction_requests(raw)),
                 "case_splits": {c["case_id"]: "new_frozen_domain_cases_v1" for c in cases()}}
    company = consume_c01(context["company_knowledge"], context["question_scope_id"])
    return context, raw, reference, company
