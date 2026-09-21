"""Emit count-only W3 runtime health and fail the unit when operator action is required."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from collections import Counter
from pathlib import Path
from typing import Any


class InspectError(RuntimeError):
    pass


def _parse_last_json_line(output: str) -> dict[str, Any]:
    for line in reversed(output.splitlines()):
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            return value
    raise InspectError("W3_INSPECT_JSON_MISSING")


def summarize_report(report: dict[str, Any]) -> dict[str, Any]:
    deliveries = report.get("deliveries")
    if not isinstance(deliveries, list):
        raise InspectError("W3_INSPECT_DELIVERIES_INVALID")
    states: Counter[str] = Counter()
    for item in deliveries:
        if not isinstance(item, dict) or not isinstance(item.get("state"), str):
            raise InspectError("W3_INSPECT_DELIVERY_INVALID")
        states[item["state"]] += 1
    blockers = report.get("migration_blockers")
    if not isinstance(blockers, dict) or not all(
        isinstance(name, str) and isinstance(count, int) and count >= 0
        for name, count in blockers.items()
    ):
        raise InspectError("W3_INSPECT_BLOCKERS_INVALID")
    held_count = states.get("HELD", 0)
    blocker_count = sum(blockers.values())
    return {
        "status": "OPERATOR_ACTION_REQUIRED" if held_count or blocker_count else "OK",
        "policy_revision": report.get("policy_revision"),
        "delivery_state_counts": dict(sorted(states.items())),
        "held_count": held_count,
        "migration_blocker_count": blocker_count,
        "automatic_action_prohibited": bool(held_count),
    }


def inspect_compose(*, compose_file: Path, timeout_seconds: int) -> dict[str, Any]:
    command = [
        "docker",
        "compose",
        "--file",
        str(compose_file),
        "--profile",
        "w3-inspect",
        "run",
        "--rm",
        "w3-core-inspect",
    ]
    try:
        completed = subprocess.run(
            command,
            check=True,
            capture_output=True,
            text=True,
            timeout=timeout_seconds,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise InspectError("W3_INSPECT_COMMAND_FAILED") from error
    return summarize_report(_parse_last_json_line(completed.stdout))


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--compose-file", type=Path, required=True)
    parser.add_argument("--timeout-seconds", type=int, default=60)
    args = parser.parse_args()
    if not 1 <= args.timeout_seconds <= 240:
        print('{"status":"FAILED","code":"W3_INSPECT_TIMEOUT_INVALID"}')
        return 1
    try:
        summary = inspect_compose(
            compose_file=args.compose_file,
            timeout_seconds=args.timeout_seconds,
        )
    except InspectError as error:
        print(json.dumps({"status": "FAILED", "code": str(error)}, separators=(",", ":")))
        return 1
    print(json.dumps(summary, separators=(",", ":"), sort_keys=True))
    return 2 if summary["status"] == "OPERATOR_ACTION_REQUIRED" else 0


if __name__ == "__main__":
    sys.exit(main())
