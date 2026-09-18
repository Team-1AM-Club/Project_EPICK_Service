from __future__ import annotations

import json
from pathlib import Path

INFRA_ROOT = Path(__file__).parents[2] / "infra"


def _load(name: str) -> dict[str, object]:
    return json.loads((INFRA_ROOT / name).read_text(encoding="utf-8"))


def test_w2_producer_queue_policy_is_send_only_to_the_dedicated_main_queue() -> None:
    policy = _load("w2-commit-gate-queue-policy.template.json")
    statements = policy["Statement"]
    assert isinstance(statements, list) and len(statements) == 1
    statement = statements[0]
    assert isinstance(statement, dict)
    assert statement["Action"] == "sqs:SendMessage"
    assert statement["Resource"] == "${W2_COMMIT_GATE_QUEUE_ARN}"
    assert statement["Principal"] == {"AWS": "${W2_COMMIT_GATE_PRODUCER_ROLE_ARN}"}


def test_w1_worker_policy_has_no_purge_or_send_permission() -> None:
    policy = _load("w2-commit-gate-worker-policy.template.json")
    encoded = json.dumps(policy, sort_keys=True)
    assert "PurgeQueue" not in encoded
    assert "SendMessage" not in encoded
    statements = policy["Statement"]
    assert isinstance(statements, list) and len(statements) == 2
    main, dlq = statements
    assert isinstance(main, dict) and isinstance(dlq, dict)
    assert main["Resource"] == "${W2_COMMIT_GATE_QUEUE_ARN}"
    assert main["Action"] == [
        "sqs:ReceiveMessage",
        "sqs:DeleteMessage",
        "sqs:ChangeMessageVisibility",
        "sqs:GetQueueAttributes",
    ]
    assert dlq["Resource"] == "${W2_COMMIT_GATE_DLQ_ARN}"
    assert dlq["Action"] == "sqs:GetQueueAttributes"
