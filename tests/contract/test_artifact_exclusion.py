from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_non_distributable_inputs_and_review_output_are_ignored() -> None:
    ignored = (ROOT / ".gitignore").read_text(encoding="utf-8").splitlines()

    for pattern in (
        "/Data/",
        "/source-collection-sample-20260907/",
        "/tmp/",
        "tmp/w3-local-review/",
        ".env",
        ".env.*",
    ):
        assert pattern in ignored


def test_public_test_fixtures_do_not_contain_observed_package_content() -> None:
    observed_root = ROOT / "source-collection-sample-20260907"
    fixture_root = ROOT / "tests" / "fixtures"

    if not observed_root.exists() or not fixture_root.exists():
        return

    observed_files = {path.name for path in observed_root.rglob("*") if path.is_file()}
    published_files = {path.name for path in fixture_root.rglob("*") if path.is_file()}

    assert not (observed_files & published_files)
