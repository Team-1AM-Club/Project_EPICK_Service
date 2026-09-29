"""Build input guards do not execute Docker or read credentials."""

import pytest
from scripts.build_ct15_image import validate_inputs


@pytest.mark.parametrize(
    ("sha", "base", "tag"),
    [
        ("HEAD", "python@sha256:" + "a" * 64, "epick-w2-ct15:check"),
        ("b" * 40, "python:3.12-slim", "epick-w2-ct15:check"),
        ("b" * 40, "python@sha256:" + "a" * 64, "epick-production:check"),
        ("b" * 40, "python@sha256:" + "a" * 64, "ct15;echo-secret"),
    ],
)
def test_build_requires_pinned_source_base_and_ct15_tag(sha: str, base: str, tag: str) -> None:
    with pytest.raises(ValueError):
        validate_inputs(sha, base, tag)


def test_build_accepts_immutable_inputs() -> None:
    validate_inputs("b" * 40, "python@sha256:" + "a" * 64, "epick-w2-ct15:check")
