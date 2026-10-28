from __future__ import annotations

import os
import sys
import tempfile
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "vendor")
if os.path.isdir(VENDOR) and VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sovereign_bots.core.walk_forward_calibrator import (
    compute_brier_and_log_loss,
    evaluate_feature_ablation,
    replay_sports_postmortems,
)


class WalkForwardCalibratorTests(unittest.TestCase):
    def test_brier_and_log_loss_skill_score(self):
        preds = [
            {"p_model": 0.80, "p_market": 0.65, "p_closing": 0.72, "outcome": 1},
            {"p_model": 0.75, "p_market": 0.60, "p_closing": 0.68, "outcome": 1},
            {"p_model": 0.20, "p_market": 0.40, "p_closing": 0.30, "outcome": 0},
            {"p_model": 0.68, "p_market": 0.55, "p_closing": 0.62, "outcome": 1},
        ]
        res = compute_brier_and_log_loss(preds)
        self.assertEqual(res["n"], 4)
        self.assertLess(res["brier_model"], res["brier_market"])
        self.assertGreater(res["brier_skill_score"], 0.0)
        self.assertLess(res["log_loss_model"], res["log_loss_market"])
        self.assertTrue(res["calibrated"])

    def test_feature_ablation_keeps_helpful_and_prunes_noisy(self):
        helpful_records = [
            {"p_base": 0.55, "feature_delta": +0.10, "outcome": 1},
            {"p_base": 0.52, "feature_delta": +0.12, "outcome": 1},
            {"p_base": 0.48, "feature_delta": -0.10, "outcome": 0},
            {"p_base": 0.60, "feature_delta": +0.08, "outcome": 1},
        ]
        keep_res = evaluate_feature_ablation(helpful_records, "usage_redistribution_delta")
        self.assertTrue(keep_res["ablation_keep"])
        self.assertGreater(keep_res["optimal_weight"], 0.0)
        self.assertGreater(keep_res["brier_improvement"], 0.0)

        harmful_records = [
            {"p_base": 0.70, "feature_delta": -0.25, "outcome": 1},
            {"p_base": 0.65, "feature_delta": -0.20, "outcome": 1},
            {"p_base": 0.30, "feature_delta": +0.25, "outcome": 0},
        ]
        prune_res = evaluate_feature_ablation(harmful_records, "overfit_noise_feature")
        self.assertFalse(prune_res["ablation_keep"])
        self.assertEqual(prune_res["optimal_weight"], 0.0)

    def test_replay_sports_postmortems_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            k_path = os.path.join(tmp, "knowledge_state.json")
            res = replay_sports_postmortems(knowledge_path=k_path, persist=True)
            self.assertTrue(res["ok"])
            self.assertGreaterEqual(res["tickets_evaluated"], 2)
            self.assertEqual(res["legs_evaluated"], 8)
            self.assertEqual(res["newly_recorded_legs"], 8)
            self.assertAlmostEqual(res["total_net_profit_cad"], 103.79, places=2)
            cal = res["calibration"]
            self.assertLess(cal["brier_model"], cal["brier_market"])
            self.assertGreater(cal["brier_skill_score"], 0.0)

            # Second replay is idempotent (0 newly recorded legs)
            res2 = replay_sports_postmortems(knowledge_path=k_path, persist=True)
            self.assertEqual(res2["newly_recorded_legs"], 0)


if __name__ == "__main__":
    unittest.main()
