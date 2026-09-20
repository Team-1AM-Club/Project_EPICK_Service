"""Local CLI: python -m epick_w4.evaluate --help. No network calls."""

import argparse
import json
from pathlib import Path
import sys

from .llm_contract import LLMError, parse_json_object
from .model_eval import (EvaluationError, approve_document, build_requests, digest,
                         document_digest, evaluate_runs, validate_benchmark, validate_policy)


def read_document(path):
    return parse_json_object(path.read_text(encoding="utf-8-sig"), "evaluation", allow_fence=False)


def report_markdown(report):
    def cell(value):
        return str(value).replace("|", "\\|").replace("\n", " ").replace("\r", " ")
    lines = ["# W4 모델 평가 결과", "", f"상태: **{report['status']}**", "",
             "PREVIEW_ONLY의 점수·순위는 검증용이며 실제 모델 추천이 아닙니다.", "",
             "| 후보 | 제공자 / 모델 | 문항 해석 | 경험 매칭 | 종합 | 순위 | 출처 |",
             "|---|---|---:|---:|---:|---:|---|"]
    for row in report["results"]:
        lines.append(f"| {cell(row['candidate_id'])} | {cell(row['provider'])} / {cell(row['model'])} "
                     f"| {row['scores']['question']:.2f} | {row['scores']['matching']:.2f} "
                     f"| {row['total_score']:.2f} | {row['rank']} | {cell(row['provenance'])} |")
    lines += ["", "추천 상태: " + report["recommendation"]["status"], "",
              "추천 후보: " + (", ".join(map(cell, report["recommendation"]["candidate_ids"])) or "없음")]
    if report["recommendation"]["blockers"]:
        lines += ["", "보류 이유:", ""]
        lines += ["- " + cell(reason) for reason in report["recommendation"]["blockers"]]
    lines += ["", "사례별 정오답·누락 근거·추가 근거는 같은 실행의 JSON 보고서에 있습니다.",
              "최고 관측 점수는 이 평가 세트·프롬프트·실행 설정에 한정되며 통계적 우월성을 뜻하지 않습니다.", ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="사람이 승인한 정답과 가중치로 W4 모델 응답 비교")
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export", help="정답을 제외한 공통 모델 요청 묶음 내보내기")
    export.add_argument("--benchmark", type=Path, required=True)
    export.add_argument("--policy", type=Path, required=True)
    score = commands.add_parser("score", help="기록된 모델 응답을 채점하고 추천/보류 보고서 생성")
    score.add_argument("--benchmark", type=Path, required=True)
    score.add_argument("--policy", type=Path, required=True)
    score.add_argument("--run", type=Path, action="append", required=True)
    score.add_argument("--markdown", type=Path)
    approve = commands.add_parser("approve", help="사람이 실제 검토한 문서에 승인자와 내용 해시 기록")
    approve.add_argument("--input", type=Path, required=True)
    approve.add_argument("--reviewer", required=True)
    approve.add_argument("--benchmark", type=Path, help="정책 승인 시 필요한 해당 벤치마크")
    for command in (export, score, approve):
        command.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        destinations = [args.output] + ([args.markdown] if getattr(args, "markdown", None) else [])
        if len({path.resolve() for path in destinations}) != len(destinations):
            raise EvaluationError("DUPLICATE_OUTPUT_PATH")
        for destination in destinations:
            if destination.exists():
                raise FileExistsError
        if args.command == "approve":
            document = read_document(args.input)
            version = document.get("schema_version")
            if version == "w4-model-benchmark/0.1":
                validate_benchmark(document)
            elif version == "w4-model-policy/0.1":
                if args.benchmark is None:
                    raise EvaluationError("POLICY_APPROVAL_NEEDS_BENCHMARK")
                validate_policy(read_document(args.benchmark), document)
            elif version != "w4-model-run/0.1" or document.get("provenance") != "IMPORTED":
                raise EvaluationError("UNSUPPORTED_REVIEW_DOCUMENT")
            result = approve_document(document, args.reviewer)
        else:
            benchmark, policy = read_document(args.benchmark), read_document(args.policy)
            validate_policy(benchmark, policy)
            if args.command == "export":
                requests = build_requests(benchmark)
                result = {"schema_version": "w4-model-requests/0.1",
                          "benchmark_sha256": document_digest(benchmark),
                          "protocol_sha256": digest(requests),
                          "repetitions": policy["repetitions"], "requests": requests}
            else:
                result = evaluate_runs(benchmark, policy, [read_document(path) for path in args.run])
        args.output.parent.mkdir(parents=True, exist_ok=True)
        with args.output.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n")
        if getattr(args, "markdown", None):
            args.markdown.parent.mkdir(parents=True, exist_ok=True)
            with args.markdown.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(report_markdown(result))
        print(json.dumps({"output": str(args.output), "status": result.get("status", "WRITTEN")},
                         ensure_ascii=False))
        return 0
    except (EvaluationError, LLMError) as error:
        code = error.code if isinstance(error, LLMError) else str(error)
    except (KeyError, TypeError, ValueError, AttributeError):
        code = "INVALID_EVALUATION_DOCUMENT"
    except FileExistsError:
        code = "OUTPUT_ALREADY_EXISTS"
    except OSError:
        code = "FILE_IO_ERROR"
    print(json.dumps({"error": code}), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
