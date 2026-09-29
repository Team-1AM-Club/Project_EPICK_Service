"""The fixed synthetic bootstrap must work under the repository unittest runner."""

import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

from epick_w4.acceptance_bootstrap import build_acceptance_adapter
from examples.w4_w1_demo import SyntheticRunStore, fixture


class AcceptanceBootstrapTests(unittest.TestCase):
    def test_acceptance_bootstrap_is_explicitly_gated(self):
        with (patch.dict(os.environ, {}, clear=True),
              self.assertRaisesRegex(RuntimeError, "SYNTHETIC_ACCEPTANCE")):
            build_acceptance_adapter(store=object())

    def test_real_data_is_rejected_even_with_synthetic_opt_in(self):
        with patch.dict(os.environ, {
            "W4_RECOMMENDATION_SYNTHETIC_ACCEPTANCE": "YES",
            "W4_RECOMMENDATION_REAL_DATA_ENABLED": "true",
        }, clear=True), self.assertRaisesRegex(RuntimeError, "REAL"):
            build_acceptance_adapter(store=object())

    def test_acceptance_bootstrap_executes_w4_and_publishes_limited_result(self):
        binding, context = fixture()
        with TemporaryDirectory() as directory:
            store = SyntheticRunStore(Path(directory) / "run.sqlite", binding, context,
                                      check_sources=lambda knowledge: None)
            try:
                with patch.dict(os.environ, {
                    "W4_RECOMMENDATION_SYNTHETIC_ACCEPTANCE": "YES",
                    "W4_RECOMMENDATION_REAL_DATA_ENABLED": "false",
                }, clear=True):
                    adapter = build_acceptance_adapter(store=store)
                    adapter.execute(owner_user_id=binding.owner_user_id, run_id=binding.run_id)
                result = store.read_result(binding.owner_user_id, binding.run_id)
                self.assertIsNotNone(result)
                self.assertEqual(result["result_origin"], "ENGINE")
                self.assertTrue(result["limited_analysis"])
                self.assertIn("W4_FIXED_SYNTHETIC_CLIENT_NO_NETWORK", result["limitations"])
            finally:
                store.close()


if __name__ == "__main__":
    unittest.main()
