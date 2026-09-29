"""python -m epick_w4 --input samples/w4_collaboration.json --user-id user-demo"""

import argparse
import json
from pathlib import Path
import sys

from .contracts import ContractError
from .evidence_extraction import extract_evidence
from .llm_contract import LLMError
from .pipeline import recommend


def main(argv=None, *, client_factories=None) -> int:
    parser = argparse.ArgumentParser(description="W4 추천 또는 원문 근거 추출 실행")
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--operation", choices=("recommend", "extract"), default="recommend")
    parser.add_argument("--user-id", required=True, help="개발용 인증 사용자. 실제 API는 인증 컨텍스트를 사용해야 합니다.")
    parser.add_argument("--knowledge-file", type=Path, help="기업정보 입력만 교체하여 진단/제한 추천을 점검")
    parser.add_argument("--output", type=Path, help="생략하면 결과를 stdout으로 출력")
    parser.add_argument("--engine", choices=("rules", "evaluated", "solar"), default="rules",
                        help="evaluated는 평가 1위 모델, solar는 단독 통신 점검용. 실제 호출에는 키와 비용이 필요합니다.")
    parser.add_argument("--benchmark", type=Path, help="검토 완료된 모델 평가 정답 세트")
    parser.add_argument("--policy", type=Path, help="해당 평가 정답과 연결된 검토 완료 정책")
    parser.add_argument("--run", type=Path, action="append", help="실제 모델 응답 기록. 후보별로 반복 지정")
    args = parser.parse_args(argv)
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        # Check before a potentially paid call; exclusive creation below handles races.
        if args.output and args.output.exists():
            raise FileExistsError
        if args.operation == "extract" and (args.engine == "rules" or args.knowledge_file):
            raise ContractError("EXTRACTION_REQUIRES_LLM_WITHOUT_KNOWLEDGE_FILE", "operation")
        if args.engine == "evaluated" and not (args.benchmark and args.policy and args.run):
            raise LLMError("LLM_EVALUATION_FILES_REQUIRED", "configuration")
        if args.engine != "evaluated" and (args.benchmark or args.policy or args.run):
            raise ContractError("EVALUATION_ARGUMENTS_REQUIRE_EVALUATED_ENGINE", "engine")
        payload = json.loads(args.input.read_text(encoding="utf-8-sig"))
        if args.knowledge_file:
            if not isinstance(payload, dict):
                raise ContractError("EXPECTED_OBJECT", "request")
            payload["company_knowledge"] = json.loads(args.knowledge_file.read_text(encoding="utf-8-sig"))
        client = None
        if args.engine == "solar":
            from .solar_client import SolarClient
            client = SolarClient.from_env()
        elif args.engine == "evaluated":
            from .evaluate import read_document
            from .model_selection import available_client_factories, select_evaluated_client
            runs = [read_document(path) for path in args.run]
            factories = (available_client_factories(runs) if client_factories is None
                         else client_factories)
            client = select_evaluated_client(read_document(args.benchmark), read_document(args.policy),
                                             runs, client_factories=factories)
        operation = extract_evidence if args.operation == "extract" else recommend
        result = operation(payload, user_id=args.user_id, llm=client)
        encoded = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
        if args.output:
            # Refuse an accidental overwrite of input or another artifact.
            args.output.parent.mkdir(parents=True, exist_ok=True)
            with args.output.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(encoded)
            print(json.dumps({"status": result["status"], "output": str(args.output)}, ensure_ascii=False))
        else:
            print(encoded, end="")
        return 0
    except ContractError as error:
        error_result = {"error": error.code, "field": error.field}
    except LLMError as error:
        error_result = {"error": error.code, "stage": error.stage}
    except FileExistsError:
        error_result = {"error": "OUTPUT_ALREADY_EXISTS"}
    except PermissionError:
        error_result = {"error": "FILE_ACCESS_DENIED"}
    except FileNotFoundError:
        error_result = {"error": "FILE_NOT_FOUND"}
    except json.JSONDecodeError:
        error_result = {"error": "INVALID_JSON"}
    except UnicodeError:
        error_result = {"error": "INVALID_TEXT_ENCODING"}
    except OSError:
        error_result = {"error": "FILE_IO_ERROR"}
    print(json.dumps(error_result, ensure_ascii=False), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
