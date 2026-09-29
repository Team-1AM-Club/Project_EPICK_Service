"""Synthetic host and fake queue for local checks; NOT a W1 context adapter."""

# ruff: noqa: E402
# Direct file execution bootstraps the repository path before local imports.

import argparse
import hashlib
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from epick_w4.question_core_contract import ContractSource, QuestionCoreContract
from epick_w4.question_core_outbox import QuestionCoreOutbox
from epick_w4.question_core_producer import (
    JobContext,
    PolicyDecision,
    QuestionCoreProducer,
)
from epick_w4.question_core_relay import QuestionCoreRelay

FIXTURES = ROOT / "samples/question-core-local"
CANDIDATE_FIXTURES = ROOT / "samples/question-core-candidate-20260919"
ADOPTED_FIXTURES = ROOT / "samples/question-core-w1-runtime-20260919"


def read(name):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class LocalClock:
    def __init__(self, seconds=0):
        self.value = datetime(2026, 9, 19, tzinfo=timezone.utc) + timedelta(seconds=seconds)

    def __call__(self):
        return self.value

    def advance(self, seconds=301):
        self.value += timedelta(seconds=seconds)


class SyntheticContexts:
    def __init__(self, **changes):
        self.value = {**read("context.json"), **changes}
        self.calls = 0

    def load(self, context_key):
        self.calls += 1
        return JobContext.model_validate_json(json.dumps(self.value))


class SyntheticPolicies:
    def __init__(self, **changes):
        self.value = {**read("policy.json"), **changes}

    def load(self, context):
        return PolicyDecision.model_validate(self.value)


def local_contract():
    path = FIXTURES / "test-only.schema.json"
    return QuestionCoreContract(
        path, source=ContractSource(hashlib.sha256(path.read_bytes()).hexdigest(), "TEST_ONLY")
    )


def candidate_contract():
    return QuestionCoreContract(
        CANDIDATE_FIXTURES / "upstream/w4-question-core-decision.event.schema.json",
        source=ContractSource(
            "1eb0506d9b13e22198ea11fb5fccd628096de2903b0fe5b1da3d383928d8407a",
            "W1_CANDIDATE",
        ),
    )


def adopted_contract():
    return QuestionCoreContract(
        ADOPTED_FIXTURES / "question-core-decision.event.schema.json",
        source=ContractSource(
            "1d004ea5ea4bbd346926c6be878de759f25b43011b7117ff8700c4d48e8b71af",
            "W1_ADOPTED",
            "519b9127227ad3ca6483a61a5143d355c5eea5eb",
        ),
    )


def local_producer(path, *, contexts=None, policies=None, clock=None, contract=None):
    return QuestionCoreProducer(
        contract=contract if contract is not None else local_contract(),
        store=QuestionCoreOutbox(path),
        contexts=contexts or SyntheticContexts(),
        policies=policies or SyntheticPolicies(),
        clock=clock or LocalClock(),
    )


class LocalAcceptance:
    def __init__(self, log=None, *, lose_responses=0, crash=False):
        self.log, self.lose_responses, self.crash = log, lose_responses, crash
        self.bodies = []

    def send(self, body):
        self.bodies.append(body)
        if self.log:
            with Path(self.log).open("ab") as stream:
                stream.write((body + "\n").encode("utf-8"))
                stream.flush()
                os.fsync(stream.fileno())
        if self.crash:
            os._exit(23)  # Actual process exit AFTER fake acceptance, BEFORE marking sent.
        if self.lose_responses:
            self.lose_responses -= 1
            raise TimeoutError("synthetic private SDK detail must not enter metadata")
        return "synthetic-broker-id"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("prepare-crash", "relay", "accept-crash"))
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--acceptance-log", type=Path)
    parser.add_argument("--seconds", type=int, default=0)
    parser.add_argument("--revoke", action="store_true")
    parser.add_argument(
        "--contract", choices=("test-only", "w1-candidate", "w1-adopted"), default="test-only"
    )
    args = parser.parse_args()
    clock = LocalClock(args.seconds)
    producer = local_producer(
        args.db.resolve(),
        clock=clock,
        contexts=SyntheticContexts(revoked=args.revoke),
        contract={
            "test-only": local_contract,
            "w1-candidate": candidate_contract,
            "w1-adopted": adopted_contract,
        }[args.contract](),
    )
    if args.action == "prepare-crash":
        producer.prepare(submission_key="synthetic-submit-1", context_key="synthetic-w1-context-1")
        os._exit(17)  # Durable COMMIT completed; no send has occurred.
    sender = LocalAcceptance(args.acceptance_log, crash=args.action == "accept-crash")
    result = QuestionCoreRelay(producer=producer, sender=sender, clock=clock).run_once()
    print(json.dumps({"mode": "SYNTHETIC_LOCAL_FAKE_QUEUE", **result}))


if __name__ == "__main__":
    main()
