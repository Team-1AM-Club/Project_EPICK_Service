"""Reproduce a pinned W1 owner-epoch precheck gap using only its original expressions.

This is a synthetic expression evaluation, not a PostgreSQL/runtime CT-12 test.
"""

import argparse
import ast
import hashlib
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / "samples/question-core-w1-context-20260919/w4_question_core_context.py"
SOURCE_SHA256 = "28a5ec93081716127be636b6699431bcfd293e9a99efea93c2c2297eae6e688f"


def review():
    raw = SOURCE.read_bytes()
    if hashlib.sha256(raw).hexdigest() != SOURCE_SHA256:
        raise ValueError("W1_REVIEW_SOURCE_CHANGED")
    tree = ast.parse(raw)
    resolver = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == "resolve_w4_question_core_context"
    )
    expressions = {}
    for node in resolver.body:
        if (
            isinstance(node, ast.Assign)
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id in {"revoked", "processing_allowed"}
        ):
            expressions[node.targets[0].id] = compile(
                ast.Expression(node.value), str(SOURCE), "eval"
            )
    now = datetime.now(timezone.utc)
    context = SimpleNamespace(
        revoked_at=None,
        owner_user_id="synthetic-owner",
        owner_deletion_epoch=0,
        execution_fence=1,
        analysis_input_version="synthetic-v1",
        data_kind="SYNTHETIC",
        expires_at=now + timedelta(hours=1),
    )
    state = {
        "context": context,
        "owner": {"account_status": "ACTIVE", "deleted_at": None, "deletion_epoch": 0},
        "job": {
            "owner_user_id": "synthetic-owner",
            "owner_deletion_epoch": 0,
            "execution_fence": 1,
            "analysis_input_version": "synthetic-v1",
            "status": "WAITING_USER",
            "active_lease_id": None,
        },
        "now": now,
        "question_current": True,
        "source_active": True,
        "open_action": True,
    }

    def evaluate():
        state["revoked"] = eval(expressions["revoked"], {"__builtins__": {}}, state)
        return {
            "revoked": state["revoked"],
            "processing_allowed": eval(
                expressions["processing_allowed"], {"__builtins__": {}}, state
            ),
        }

    before = evaluate()
    state["owner"]["deletion_epoch"] = 1
    after = evaluate()
    gap = before == after == {"revoked": False, "processing_allowed": True}
    return {
        "w1_full_sha": "deda25c62a762e3f7f6ea5273c93a7e6a18c6412",
        "source_sha256": SOURCE_SHA256,
        "method": "SYNTHETIC_ORIGINAL_AST_EXPRESSION_EVALUATION_NO_DATABASE",
        "finding": "OWNER_EPOCH_PRECHECK_GAP_REPRODUCED" if gap else "EXPECTED_OBSERVATION_CHANGED",
        "before": before,
        "after_owner_epoch_only_increment": after,
        "required_after": {"revoked": True, "processing_allowed": False},
        "recommended_change": 'or owner["deletion_epoch"] != context.owner_deletion_epoch',
        "w1_database_reproduction": "NOT_RUN",
        "joint_ct12": "NOT_RUN",
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    report = review()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"finding": report["finding"], "method": report["method"]}))
    return 0 if report["finding"] == "OWNER_EPOCH_PRECHECK_GAP_REPRODUCED" else 1


if __name__ == "__main__":
    raise SystemExit(main())
