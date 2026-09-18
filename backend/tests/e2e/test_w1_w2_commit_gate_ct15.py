"""Joint CT15 cases, intentionally unavailable until W2 supplies its runtime hook."""

from __future__ import annotations

import pytest

pytestmark = pytest.mark.w1_w2_commit_gate_e2e


@pytest.mark.parametrize(
    "case_id",
    (
        "CT15-01-normal",
        "CT15-02-duplicate",
        "CT15-03-id-conflict",
        "CT15-04-ack-loss-restart",
        "CT15-05-stale-fence-or-epoch",
        "CT15-06-cancel-abort",
        "CT15-07-delete-purge",
        "CT15-08-late-finalize-replay",
        "CT15-09-shared-source-isolation",
    ),
)
@pytest.mark.skip(
    reason=(
        "joint CT15 needs W2's immutable producer/consumer image, dedicated queues, "
        "and approved private-store inspection hook"
    )
)
def test_joint_w1_w2_commit_gate_ct15_placeholder(case_id: str) -> None:
    """Keep the joint acceptance matrix visible without fabricating W2 evidence."""

    assert case_id
