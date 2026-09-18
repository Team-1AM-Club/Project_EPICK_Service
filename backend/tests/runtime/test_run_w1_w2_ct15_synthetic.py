from __future__ import annotations

import pytest

from scripts.run_w1_w2_ct15_synthetic import Ct15ScenarioError, _safe_id


def test_ct15_runner_redacts_queue_identifiers_deterministically() -> None:
    queue_url = "https://sqs.ap-northeast-2.amazonaws.com/123/epick-t059-ct15-main"

    assert _safe_id(queue_url).startswith("sha256:")
    assert _safe_id(queue_url) == _safe_id(queue_url)
    assert queue_url not in _safe_id(queue_url)


@pytest.mark.parametrize(
    ("raw_value", "message"),
    [
        ("no", "W1_CT15_EXECUTE_SYNTHETIC"),
        ("", "W1_CT15_EXECUTE_SYNTHETIC"),
    ],
)
def test_ct15_runner_requires_an_explicit_execution_opt_in(
    monkeypatch: pytest.MonkeyPatch, raw_value: str, message: str
) -> None:
    from scripts.run_w1_w2_ct15_synthetic import _require_configuration

    monkeypatch.setenv("W1_CT15_EXECUTE_SYNTHETIC", raw_value)
    with pytest.raises(Ct15ScenarioError, match=message):
        _require_configuration()
