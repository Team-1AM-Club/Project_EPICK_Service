"""Six unseen fictions frozen for the staged protocol; assistant draft labels."""

from copy import deepcopy

from epick_w4.extraction_eval import build_extraction_requests
from epick_w4.model_eval import digest
from examples.w4_c01_domain_demo import build_domain_context, supported


def cases():
    texts = [
        ["저는 수족관 관찰 일지 프로그램의 개발 담당자였습니다.",
         "저는 삼 주 동안 매주 JSON 형식 검사 강의를 수강하고 예제 코드를 수정하며 학습했습니다.",
         "저는 배운 JSON 검사 방법을 관찰 일지 코드에 적용해 수온 입력값을 검증했습니다."],
        ["저는 도서 목록 검색 도구의 개발 담당자였습니다.",
         "저는 열흘 동안 매일 인덱스 구조를 설명한 자료를 읽고 검색 예제를 풀며 학습했습니다.",
         "저는 배운 인덱스 구성 방법을 목록 검색 코드에 구현했습니다.",
         "저는 그 코드의 설치와 사용 방법을 설명하는 문서를 작성했습니다."],
        ["저는 지역 박물관의 통역 안내 담당자였습니다.",
         "저는 한 달 동안 매일 일본어 교재를 읽고 전시 설명 표현을 학습했습니다.",
         "저는 배운 일본어 표현을 관람객에게 전시물을 설명할 때 사용했습니다.",
         "저는 전시 설명 표현을 정리한 안내 문서를 작성했습니다."],
        ["저는 행사 신청 명단의 정리 담당자였습니다.",
         "저는 두 달 동안 매주 이미 완성된 프로그램의 실행 버튼을 눌러 명단을 정렬했습니다.",
         "정렬된 명단 파일이 여덟 개 생성되었습니다."],
        ["저는 센서 자료 업로드 도구의 개발 담당자였습니다.",
         "저는 다음 분기에 여섯 주 동안 매주 Python의 예외 처리 예제를 학습할 계획입니다.",
         "저는 학습한 예외 처리 방식을 업로드 코드의 입력값 검증에 적용할 계획입니다."],
        ["저는 전시 프로젝트의 회의 일정 담당자였습니다.",
         "동료 민지는 네 주 동안 매주 데이터 형식 검사 자료를 읽고 학습했습니다.",
         "동료 민지는 배운 형식 검사 방법을 전시 예약 코드에 적용해 입력값을 검증했습니다.",
         "저는 회의에 참석할 수 있는 날짜를 모아 일정을 확정했습니다."],
    ]
    result = []
    for n, lines in enumerate(texts, 1):
        allowed = [f"u{i + 1}" for i in range(len(lines))]
        def missing():
            # Uncertain omission and a false affirmative are separate outcomes.
            return {"statuses": ["NOT_SHOWN", "AMBIGUOUS"], "required_units": [], "allowed_units": allowed[:]}
        labels = []
        for i, _ in enumerate(lines):
            kinds = ["ROLE"] if i == 0 else ["ACTION", "PERIOD"] if i == 1 else ["ACTION"]
            subject, assertion = "SELF", "AFFIRMED"
            if n == 4 and i == 2:
                kinds, subject = ["RESULT"], "UNSPECIFIED"
            if n == 5 and i > 0:
                assertion = "PLANNED"
            if n == 6 and i in (1, 2):
                subject = "OTHER"
            labels.append({"unit_id": f"u{i + 1}", "kinds": kinds, "subject": subject,
                           "assertion": assertion, "issue": None})
        checks = {key: missing() for key in
                  ("expertise.specific_area", "expertise.sustained_learning", "expertise.practical_use")}
        leaves = {key: missing() for key in ("apply", "validation", "documentation")}
        claim, root = missing(), missing()
        if n in (1, 2, 3):
            for key, unit in zip(checks, ("u2", "u2", "u3")):
                checks[key] = supported([unit], allowed)
        if n in (1, 2):
            claim, leaves["apply"], root = supported(["u2"], allowed), supported(["u3"], allowed), supported([])
        if n == 1:
            leaves["validation"] = supported(["u3"], allowed)
        if n in (2, 3):
            leaves["documentation"] = supported(["u4"], allowed)
        result.append({"case_id": f"c01-fresh-{n:02d}", "lines": lines, "checks": checks,
                       "claim": claim, "leaves": leaves, "root": root, "extraction": {"units": labels}})
    return result


def protocol():
    context = build_domain_context(1789603200)
    context["context_version"] = "synthetic-c01-fresh-context-v1"
    context["episodes"] = [{"episode_id": c["case_id"], "version": 1, "owner_id": "user-demo",
        "activity_id": c["case_id"], "title": f"검증용 가상 경험 {i + 1}", "raw_text": "\n".join(c["lines"])}
        for i, c in enumerate(cases())]
    context["snapshot"] = {"snapshot_id": "synthetic-c01-fresh-snapshot-v1",
                           "episode_versions": {e["episode_id"]: 1 for e in context["episodes"]}}
    raw = {"schema_version": "w4-evidence-input/0.1", "request_id": "c01-fresh-evaluation-v1",
           **{k: deepcopy(context[k]) for k in ("data_kind", "project", "snapshot", "episodes", "excluded_episode_ids")}}
    reference = {"schema_version": "w4-extraction-benchmark/0.1", "data_kind": "SYNTHETIC",
        "review": {"status": "DRAFT", "author": "assistant", "human_reviewed": False},
        "answers": {c["case_id"]: c["extraction"] for c in cases()}, "input_sha256": digest(raw),
        "protocol_sha256": digest(build_extraction_requests(raw)),
        "case_splits": {c["case_id"]: "fresh_staged_v1" for c in cases()}}
    return context, raw, reference
