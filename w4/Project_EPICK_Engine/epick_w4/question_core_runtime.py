"""CT-12 SYNTHETIC-only runtime: private HTTP context -> durable outbox -> SQS.

No public API, inferred Core policy, or W1 database connection is installed here.
The operator mounts a reviewed plan and rotates W4-only temporary credentials.
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path
from typing import Annotated, Literal

from pydantic import AwareDatetime, Field, ValidationError

from .c01_contract import UUIDText
from .handoff_contract import Identifier, Record
from .question_core_contract import (
    ContractSource,
    QuestionCoreContract,
    QuestionCoreError,
    canonical_json,
    parse_json,
    require,
)
from .question_core_http import BASE_URL, W1HttpContexts, W1HttpSettings
from .question_core_outbox import QuestionCoreOutbox
from .question_core_producer import PolicyDecision, QuestionCoreProducer, utc_now
from .question_core_relay import QuestionCoreRelay, SqsSendOnly, SqsSendSettings

ROOT = Path(__file__).resolve().parents[1]
W1_SHA = "deda25c62a762e3f7f6ea5273c93a7e6a18c6412"
SCHEMA_SHA256 = "1d004ea5ea4bbd346926c6be878de759f25b43011b7117ff8700c4d48e8b71af"
DEFAULT_PLAN = "/run/w4-plan/plan.json"
DEFAULT_SESSION = "/run/w4-aws/session.json"
DEFAULT_OUTBOX = "/var/lib/w4/outbox.sqlite3"


class PlanEntry(Record):
    submission_key: Annotated[str, Field(pattern=r"^[A-Za-z0-9_.:-]{1,200}$")]
    context_key: UUIDText
    policy: PolicyDecision


class SyntheticPlan(Record):
    schema_version: Literal["w4.ct12.synthetic-plan.v1"]
    purpose: Literal["CT12_SYNTHETIC_ONLY"]
    operator_review_reference: Identifier
    entries: Annotated[list[PlanEntry], Field(min_length=1, max_length=100)]


def read_plan(path):
    try:
        value = parse_json(Path(path).read_bytes(), maximum=128 * 1024)
        plan = SyntheticPlan.model_validate_json(canonical_json(value))
        for key in ("submission_key", "context_key"):
            require(
                len({getattr(entry, key) for entry in plan.entries}) == len(plan.entries),
                "CORE_POLICY_PLAN_INVALID",
            )
        return plan
    except (OSError, QuestionCoreError, ValidationError):
        raise QuestionCoreError("CORE_POLICY_PLAN_INVALID") from None


class PlanPolicies:
    """Explicit operator-reviewed CT-12 decisions, reloaded at every currentness check."""

    def __init__(self, path):
        self.path = path

    def load(self, context):
        for entry in read_plan(self.path).entries:
            if entry.context_key == context.context_key:
                return entry.policy
        raise QuestionCoreError("CORE_POLICY_PLAN_INVALID")


class SessionCredentials(Record):
    Version: Literal[1]
    AccessKeyId: Annotated[str, Field(pattern=r"^ASIA[A-Z0-9]{16}$", repr=False)]
    SecretAccessKey: Annotated[str, Field(min_length=1, max_length=256, repr=False)]
    SessionToken: Annotated[str, Field(min_length=1, max_length=16384, repr=False)]
    Expiration: AwareDatetime


def read_session(path, *, clock=utc_now):
    try:
        raw = Path(path).read_bytes()
        data = parse_json(raw, maximum=32 * 1024)
        require(
            isinstance(data, dict) and type(data.get("Version")) is int,
            "CORE_SQS_CREDENTIALS_INVALID",
        )
        value = SessionCredentials.model_validate_json(canonical_json(data))
        require(
            (value.Expiration - clock()).total_seconds() > 30,
            "CORE_SQS_CREDENTIALS_EXPIRED",
        )
        return value
    except (OSError, QuestionCoreError, ValidationError):
        raise QuestionCoreError("CORE_SQS_CREDENTIALS_UNAVAILABLE") from None


def runtime_contract():
    contract = QuestionCoreContract(
        ROOT / "samples/question-core-w1-context-20260919/question-core-decision.event.schema.json",
        source=ContractSource(SCHEMA_SHA256, "W1_ADOPTED", W1_SHA),
    )
    contract.require_adopted()
    return contract


class FileSessionSqsSender:
    """Read freshly rotated W4 STS credentials per send; never use the SDK credential chain.

    Only the W1 host assumes the W4 role. Its own credentials never enter this process.
    An expired/missing session causes an immutable outbox retry, not a fallback to IMDS.
    """

    def __init__(self, *, contract, settings, session_path):
        settings.validate()
        contract.require_adopted()
        self.contract, self.settings, self.session_path = contract, settings, session_path

    def send(self, body):
        credentials = read_session(self.session_path)
        try:
            import boto3
            from botocore.config import Config

            client = boto3.client(
                "sqs",
                region_name=self.settings.region,
                aws_access_key_id=credentials.AccessKeyId,
                aws_secret_access_key=credentials.SecretAccessKey,
                aws_session_token=credentials.SessionToken,
                config=Config(
                    connect_timeout=3,
                    read_timeout=10,
                    ignore_configured_endpoint_urls=True,
                    retries={"total_max_attempts": 1, "mode": "standard"},
                ),
            )
            try:
                return SqsSendOnly(
                    contract=self.contract, settings=self.settings, client=client
                ).send(body)
            finally:
                client.close()
        except Exception:  # noqa: BLE001 - never print credential or SDK details.
            raise QuestionCoreError("CORE_SEND_UNCONFIRMED") from None


def build_producer(environ, *, contexts=None):
    if "W4_IMAGE_REF" in environ:
        require(
            re.fullmatch(r"[^\s]+@sha256:[0-9a-f]{64}", environ["W4_IMAGE_REF"]),
            "CORE_RUNTIME_CONFIG_INVALID",
        )
    plan_path = environ.get("W4_CT12_PLAN_PATH", DEFAULT_PLAN)
    outbox_path = Path(environ.get("W4_OUTBOX_PATH", DEFAULT_OUTBOX))
    require(outbox_path.is_absolute(), "CORE_RUNTIME_CONFIG_INVALID")
    read_plan(plan_path)
    if contexts is None:
        contexts = W1HttpContexts(
            W1HttpSettings(
                bearer=environ.get("W1_W4_CONTEXT_BEARER", ""),
                base_url=environ.get("W1_W4_CONTEXT_BASE_URL", BASE_URL),
                principal=environ.get("W1_W4_CONTEXT_PRINCIPAL", "w4"),
            )
        )
    return QuestionCoreProducer(
        contract=runtime_contract(),
        store=QuestionCoreOutbox(outbox_path),
        contexts=contexts,
        policies=PlanPolicies(plan_path),
    ), plan_path


def build_sender(environ, contract):
    # The isolated runtime must be deliberately enabled after W1 provisions it.
    settings = SqsSendSettings(
        main_queue_url=environ.get("W4_SQS_MAIN_QUEUE_URL", ""),
        main_queue_arn=environ.get("W4_SQS_MAIN_QUEUE_ARN", ""),
        region=environ.get("W4_AWS_REGION", ""),
        enabled=environ.get("W4_SQS_SEND_ENABLED") == "true",
    )
    return FileSessionSqsSender(
        contract=contract,
        settings=settings,
        session_path=environ.get("W4_AWS_SESSION_PATH", DEFAULT_SESSION),
    )


def emit(value):
    print(json.dumps(value, ensure_ascii=True), flush=True)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("check", "prepare", "relay-once", "relay"))
    args = parser.parse_args(argv)
    try:
        producer, plan_path = build_producer(os.environ)
        if args.action == "check":
            # Configuration/SQLite only; not evidence that HTTP/AWS is reachable.
            emit({"status": "LOCAL_CONFIG_VALID", "REAL": "DISABLED", "network_calls": 0})
            return 0
        if args.action == "prepare":
            count = 0
            for entry in read_plan(plan_path).entries:
                producer.prepare(submission_key=entry.submission_key, context_key=entry.context_key)
                count += 1
            emit({"status": "PREPARED", "count": count, "sqs_calls": 0})
            return 0
        sender = build_sender(os.environ, producer.contract)
        relay = QuestionCoreRelay(producer=producer, sender=sender)
        while True:
            result = relay.run_once()
            emit(result)
            if result.get("error_code") == "CORE_CONTEXT_AUTH_FAILED":
                return 2  # Stop; replace configuration/secret before restarting.
            if args.action == "relay-once":
                return 2 if result["status"] in {"BLOCKED", "RETRY", "LEASE_LOST"} else 0
            time.sleep(5)
    except KeyboardInterrupt:
        return 130
    except QuestionCoreError as error:
        # QuestionCoreError codes from this path are fixed literals, never upstream text.
        code = (
            error.code if re.fullmatch(r"CORE_[A-Z_]{1,80}", error.code) else "CORE_RUNTIME_FAILED"
        )
        emit({"status": "FAILED", "error_code": code})
        return 2
    except Exception:  # noqa: BLE001 - safe operational boundary, no tracebacks with input.
        emit({"status": "FAILED", "error_code": "CORE_RUNTIME_FAILED"})
        return 2


if __name__ == "__main__":
    sys.exit(main())
