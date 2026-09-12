"""로컬 진단 전용 CLI. 원문·응답 전문을 출력하지 않는다."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

from .adapters.collection_sample import load_collection_sample
from .adapters.observed_package import load_observed_package
from .models import (
    Artifact,
    Audience,
    CheckStatus,
    EvaluationMode,
    ExecutionContext,
    IntegrityAssertion,
    NativeLocator,
    PolicyDecision,
    Purpose,
    SourceInput,
    SourceRef,
    StructureRequest,
    ValidationCheck,
)
from .service import W3Ports, structure


class _DiagnosticPolicy:
    def decide(self, *, context: ExecutionContext, operation: str) -> PolicyDecision:
        return PolicyDecision.ALLOW


class _DiagnosticRetainedText:
    def can_use(self, *, context: ExecutionContext, source: object) -> PolicyDecision:
        return PolicyDecision.ALLOW


class _NoExtraction:
    def extract_claims(self, *, source: object) -> tuple[()]:
        return ()

    def extract_requirements(self, *, source: object) -> tuple[()]:
        return ()


class _NoValidation:
    def validate_claim(self, candidate: object) -> tuple[ValidationCheck, ...]:
        return (
            ValidationCheck(
                check_id="diagnostic",
                status=CheckStatus.NOT_REQUIRED,
                code="DIAGNOSTIC",
                message="진단 모드",
            ),
        )

    def validate_requirement(self, candidate: object) -> tuple[ValidationCheck, ...]:
        return (
            ValidationCheck(
                check_id="diagnostic",
                status=CheckStatus.NOT_REQUIRED,
                code="DIAGNOSTIC",
                message="진단 모드",
            ),
        )

    def verify_claim(self, candidate: object) -> tuple[ValidationCheck, ...]:
        return (
            ValidationCheck(
                check_id="diagnostic-extra",
                status=CheckStatus.NOT_REQUIRED,
                code="DIAGNOSTIC",
                message="진단 모드",
            ),
        )

    def verify_requirement(self, candidate: object) -> tuple[ValidationCheck, ...]:
        return (
            ValidationCheck(
                check_id="diagnostic-extra",
                status=CheckStatus.NOT_REQUIRED,
                code="DIAGNOSTIC",
                message="진단 모드",
            ),
        )


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="w3-knowledge")
    sub = parser.add_subparsers(dest="command", required=True)
    validate = sub.add_parser("validate")
    validate.add_argument("--fixture", required=True)
    validate.add_argument("--mode", choices=["synthetic"], required=True)
    diagnose = sub.add_parser("diagnose-sample")
    group = diagnose.add_mutually_exclusive_group(required=True)
    group.add_argument("--input", type=Path)
    group.add_argument("--package", type=Path)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        if args.command == "validate":
            fixture = Path(args.fixture)
            source_path = fixture / "source.json"
            if not source_path.is_file():
                raise FileNotFoundError("합성 fixture 경로가 없습니다.")
            payload = json.loads(source_path.read_text(encoding="utf-8"))
            source_ref = SourceRef(
                source_id=payload["source_id"],
                source_version_id=payload["source_version_id"],
                source_kind="synthetic_fixture",
            )
            artifact = Artifact(
                artifact_id=payload["artifact_id"],
                source_ref=source_ref,
                text=payload["text"],
                native_locator=NativeLocator(
                    locator_type="json_pointer", value="source.json#/text", reproducible=True
                ),
                expected_integrity=IntegrityAssertion(digest=payload["sha256"]),
                collection_status="SUCCESS",
                parsing_status="PARSED",
                access_status="AVAILABLE",
                retention_status="RETAINED",
            )
            context = ExecutionContext(
                actor_id="synthetic-cli",
                audience=Audience.W2_REVIEW,
                purpose=Purpose.SYNTHETIC_ACCEPTANCE,
                mode=EvaluationMode.SYNTHETIC,
                request_id="synthetic-cli",
            )
            validator = _NoValidation()
            response = structure(
                StructureRequest(
                    sources=(SourceInput(source_ref=source_ref, artifacts=(artifact,)),),
                    context=context,
                ),
                W3Ports(
                    _DiagnosticPolicy(),
                    _DiagnosticRetainedText(),
                    _NoExtraction(),
                    validator,
                    validator,
                ),
            )
            print(
                f"mode=SYNTHETIC status={response.status.value} limitations={len(response.bundle.limitations)}"
            )
            return 0
        sources = (
            load_collection_sample(args.input)
            if args.input
            else load_observed_package(args.package)
        )
        context = ExecutionContext(
            actor_id="local-diagnostic",
            audience=Audience.W2_REVIEW,
            purpose=Purpose.SAMPLE_DIAGNOSTIC,
            mode=EvaluationMode.SAMPLE_DIAGNOSTIC,
            request_id="local-diagnostic",
        )
        validator = _NoValidation()
        ports = W3Ports(
            _DiagnosticPolicy(), _DiagnosticRetainedText(), _NoExtraction(), validator, validator
        )
        response = structure(StructureRequest(sources=sources, context=context), ports)
        print(
            f"mode={context.mode.value} status={response.status.value} sources={len(sources)} limitations={len(response.bundle.limitations)} errors={len(response.errors)}"
        )
        return 0
    except (FileNotFoundError, ValueError) as exc:
        print(f"error=INPUT_CONTRACT_INVALID message={str(exc)}")
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
