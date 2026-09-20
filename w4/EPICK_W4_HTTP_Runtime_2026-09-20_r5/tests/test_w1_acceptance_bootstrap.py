from __future__ import annotations

import os
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

import pytest

from epick_w4.acceptance_bootstrap import build_acceptance_adapter
from examples.w4_w1_demo import SyntheticRunStore, fixture


def test_acceptance_bootstrap_is_explicitly_gated() -> None:
    with (
        patch.dict(os.environ, {}, clear=True),
        pytest.raises(RuntimeError, match="SYNTHETIC_ACCEPTANCE"),
    ):
        build_acceptance_adapter(store=object())


def test_acceptance_bootstrap_executes_w4_and_publishes_limited_result() -> None:
    binding, context = fixture()
    with TemporaryDirectory() as directory:
        adapter = None
        store = SyntheticRunStore(
            Path(directory) / "run.sqlite",
            binding,
            context,
            check_sources=lambda knowledge: None,
        )
        try:
            with patch.dict(
                os.environ,
                {
                    "W4_RECOMMENDATION_SYNTHETIC_ACCEPTANCE": "YES",
                    "W4_RECOMMENDATION_REAL_DATA_ENABLED": "false",
                },
                clear=True,
            ):
                adapter = build_acceptance_adapter(store=store)
                adapter.execute(
                    owner_user_id=binding.owner_user_id,
                    run_id=binding.run_id,
                )
            result = store.read_result(binding.owner_user_id, binding.run_id)
            assert result is not None
            assert result["result_origin"] == "ENGINE"
            assert result["limited_analysis"] is True
            assert "W4_FIXED_SYNTHETIC_CLIENT_NO_NETWORK" in result["limitations"]
        finally:
            del adapter
            store.close()
