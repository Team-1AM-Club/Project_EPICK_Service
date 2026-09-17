"""Verify W2 CollectionResult fixtures with the pinned W2 Pydantic parser.

This is an explicit cross-repository verification command, not a replacement for
the Service repository's JSON Schema tests.  It requires the exact crawler
revision pinned in ``contracts/w2/v1/import-manifest.json``.
"""

from __future__ import annotations

import argparse
import json
import sys
from copy import deepcopy
from pathlib import Path
from typing import Any

BACKEND_ROOT = Path(__file__).resolve().parents[1]
CONTRACT_ROOT = BACKEND_ROOT / "contracts"
FIXTURE_ROOT = CONTRACT_ROOT / "fixtures" / "v1" / "w2"
MANIFEST_PATH = CONTRACT_ROOT / "w2" / "v1" / "import-manifest.json"
VALID_FIXTURES = (
    "source-collection-result-complete.json",
    "source-collection-result-partial.json",
    "source-collection-result-failure.json",
    "source-collection-result-policy-failure.json",
)


def _load_json(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as stream:
        value: dict[str, Any] = json.load(stream)
    return value


def _crawler_head(crawler_root: Path) -> str:
    git_directory = crawler_root / ".git"
    head_path = git_directory / "HEAD"
    if not head_path.is_file():
        raise SystemExit("--crawler-root must be a Git worktree")
    head = head_path.read_text(encoding="utf-8").strip()
    if not head.startswith("ref: "):
        return head
    reference_path = git_directory / head.removeprefix("ref: ")
    if reference_path.is_file():
        return reference_path.read_text(encoding="utf-8").strip()
    raise SystemExit("crawler HEAD reference cannot be resolved without Git metadata")


def _expect_parser_rejection(parser: Any, value: dict[str, Any], *, case: str) -> None:
    try:
        parser.model_validate(value)
    except ValueError:
        return
    raise AssertionError(f"W2 parser accepted invalid case: {case}")


def main() -> None:
    argument_parser = argparse.ArgumentParser()
    argument_parser.add_argument("--crawler-root", required=True, type=Path)
    args = argument_parser.parse_args()

    crawler_root = args.crawler_root.resolve()
    contract_path = crawler_root / "src" / "epick_engine" / "source_collection" / "contracts.py"
    if not contract_path.is_file():
        raise SystemExit(
            "--crawler-root must contain src/epick_engine/source_collection/contracts.py"
        )

    manifest = _load_json(MANIFEST_PATH)
    pinned_commit = manifest["source_commit"]
    if not isinstance(pinned_commit, str) or not pinned_commit:
        raise SystemExit("the W2 import manifest does not pin a crawler commit")
    if _crawler_head(crawler_root) != pinned_commit:
        raise SystemExit("crawler HEAD does not match contracts/w2/v1/import-manifest.json")

    sys.path.insert(0, str(crawler_root / "src"))
    from epick_engine.source_collection.contracts import CollectionResult

    fixtures = {name: _load_json(FIXTURE_ROOT / name) for name in VALID_FIXTURES}
    for payload in fixtures.values():
        CollectionResult.model_validate(payload)

    invalid_action = deepcopy(fixtures["source-collection-result-partial.json"])
    invalid_action["required_actions"][0]["code"] = "continue_limited"
    _expect_parser_rejection(CollectionResult, invalid_action, case="standalone continue_limited")

    invalid_null_policy = deepcopy(fixtures["source-collection-result-policy-failure.json"])
    invalid_null_policy["failures"][0]["stage"] = "fetch"
    _expect_parser_rejection(
        CollectionResult,
        invalid_null_policy,
        case="null policy_revision after policy",
    )

    invalid_zero_policy = deepcopy(fixtures["source-collection-result-policy-failure.json"])
    invalid_zero_policy["policy_revision"] = 0
    _expect_parser_rejection(CollectionResult, invalid_zero_policy, case="zero policy_revision")

    print(f"W2 CollectionResult parser verification passed: {pinned_commit}")


if __name__ == "__main__":
    main()
