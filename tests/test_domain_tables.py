"""Unit tests for expanded multi-domain tables and deterministic truth tables."""

import os
import tempfile
import unittest

from sovereign_bots.core.domain_tables import (
    CROSS_DOMAIN_SPILLOVER_TABLE,
    CRYPTO_REGIME_DOMAIN_TABLE,
    POLYMARKET_DOMAIN_TABLE,
    SPORTS_DOMAIN_TABLE,
    STAR_ABSENCE_SPILLOVER_TABLE,
    evaluate_live_cashout_truth_table,
    evaluate_multi_domain_signal_truth_table,
)
from sovereign_bots.core.knowledge_loop import (
    SPORTS_MARKET_DEFAULTS,
    record_sports_outcome,
)


class TestDomainTables(unittest.TestCase):
    def test_domain_tables_populated(self):
        self.assertIn("nba_player_assists_rebounds_alt", SPORTS_DOMAIN_TABLE)
        self.assertIn("bonus_bet_sgp_plus_conversion", SPORTS_DOMAIN_TABLE)
        self.assertIn("primary_ballhandler_out", STAR_ABSENCE_SPILLOVER_TABLE)
        self.assertIn("crypto", POLYMARKET_DOMAIN_TABLE)
        self.assertIn("btc_eth_macro_core", CRYPTO_REGIME_DOMAIN_TABLE)
        self.assertGreaterEqual(len(CROSS_DOMAIN_SPILLOVER_TABLE), 3)
        self.assertIn("nba_player_assists_rebounds_alt", SPORTS_MARKET_DEFAULTS)

    def test_live_cashout_truth_table_locks_profit_on_q1_surge(self):
        # Mirrors Oct 27 5-leg SGP+: 4 legs green + Luka at 16/30 pts (53.3%) in Q1
        res = evaluate_live_cashout_truth_table(
            total_legs=5,
            settled_legs_won=4,
            active_leg_progress_ratio=16.0 / 30.0,
            active_game_period=1,
            score_margin_abs=8,
            star_sub_or_injury_noul=0.25,
            cashout_to_fair_ev_ratio=0.87,
            is_bonus_bet=True,
        )
        self.assertEqual(res["action"], "LOCK_EARLY_CASHOUT")

    def test_multi_domain_signal_truth_table_tiers(self):
        # Tier-1 fresh injury wire
        t1 = evaluate_multi_domain_signal_truth_table(
            concrete_noul=0.92,
            impact_noul=0.81,
            already_priced_in_noul=0.12,
            ambiguity_or_rumor_noul=0.05,
            primary_desk="sports_betting",
            directional_lean="bullish_over_yes",
        )
        self.assertEqual(t1["tier"], "TIER_1_SNIPE_ALPHA")
        self.assertIn("bet365_ontario_player_props", t1["spillover_targets"])

        # Stale box-score recap -> ABSTAIN_ALREADY_PRICED_IN
        stale = evaluate_multi_domain_signal_truth_table(
            concrete_noul=0.88,
            impact_noul=0.60,
            already_priced_in_noul=0.85,
            ambiguity_or_rumor_noul=0.10,
            primary_desk="sports_betting",
            directional_lean="bullish_over_yes",
        )
        self.assertEqual(stale["tier"], "ABSTAIN_ALREADY_PRICED_IN")
        self.assertEqual(stale["recommended_stake_mult"], 0.0)

    def test_record_sports_outcome_updates_mwu(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "state.json")
            updated = record_sports_outcome(
                market_family="bonus_bet_sgp_plus_conversion",
                p_model=0.32,
                outcome=1,
                realized_roi_mult=4.15,
                clv_bps=28.0,
                early_cashout_used=True,
                path=path,
            )
            self.assertGreater(updated["mwu_weight"], 1.22)
            self.assertEqual(updated["early_cashout_saves"], 1)


if __name__ == "__main__":
    unittest.main()
