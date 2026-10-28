"""Comprehensive unit tests for quantitative domain solvers and deterministic truth tables."""

import os
import tempfile
import unittest

from sovereign_bots.core.domain_tables import (
    CROSS_DOMAIN_SPILLOVER_TABLE,
    CRYPTO_REGIME_DOMAIN_TABLE,
    POLYMARKET_DOMAIN_TABLE,
    SPORTS_DOMAIN_TABLE,
    STAR_ABSENCE_SPILLOVER_TABLE,
    american_to_decimal,
    compute_bet365_early_payout_ev_boost,
    compute_bivariate_copula_joint_prob,
    compute_kalshi_taker_fee_cents,
    decimal_to_american,
    devig_power_method,
    devig_shin_method_two_way,
    evaluate_crypto_perp_execution_truth_table,
    evaluate_live_cashout_truth_table,
    evaluate_multi_domain_signal_truth_table,
    evaluate_polymarket_kalshi_cross_arb,
    negbinom_prob_at_least,
    poisson_prob_at_least,
    price_crypto_intraday_barrier_contract,
    price_sgp_plus_with_correlation_and_bonus_snr,
    project_teammate_prop_under_star_absence,
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
        self.assertEqual(len(POLYMARKET_DOMAIN_TABLE), 10)
        self.assertIn("btc_eth_macro_core", CRYPTO_REGIME_DOMAIN_TABLE)
        self.assertGreaterEqual(len(CROSS_DOMAIN_SPILLOVER_TABLE), 3)
        self.assertIn("nba_player_assists_rebounds_alt", SPORTS_MARKET_DEFAULTS)

    def test_power_and_shin_devigging(self):
        self.assertAlmostEqual(american_to_decimal(-110), 1.909091, places=5)
        self.assertEqual(decimal_to_american(2.50), 150)
        fair_power = devig_power_method([1.909091, 1.909091])
        self.assertAlmostEqual(fair_power[0], 0.50, places=4)
        p_a, p_b, z = devig_shin_method_two_way(1.40, 3.10)
        self.assertAlmostEqual(p_a + p_b, 1.0, places=5)
        self.assertGreater(z, 0.0)

    def test_negbinom_and_teammate_prop_projection(self):
        # Randle/DiVincenzo assists projection when Anthony Edwards is out
        proj = project_teammate_prop_under_star_absence(
            baseline_per_minute_rate=0.175,
            baseline_minutes=33.0,
            minutes_boost=2.8,
            usage_or_role_multiplier=1.24,
            stat_category="assists",
            milestone_target=7,
            offered_decimal_odds=2.05,
            blowout_spread_abs=4.5,
        )
        self.assertGreater(proj["fair_probability"], 0.50)
        self.assertGreater(proj["ev_pct"], 5.0)
        self.assertTrue(proj["actionable"])

    def test_sgp_copula_and_bonus_bet_snr(self):
        joint = compute_bivariate_copula_joint_prob(0.65, 0.60, 0.30)
        self.assertGreater(joint, 0.65 * 0.60)
        sgp = price_sgp_plus_with_correlation_and_bonus_snr(
            leg_fair_probs=[0.78, 0.76, 0.72, 0.68, 0.52],
            pairwise_correlations={(0, 1): 0.15, (2, 3): 0.18},
            offered_combined_decimal=5.72,
            is_bonus_bet=True,
            bankroll_cash=138.68,
        )
        self.assertTrue(sgp["bonus_bet_optimal_zone"])
        self.assertGreater(sgp["bonus_bet_snr_conversion_pct"], 70.0)

    def test_bet365_early_payout_boost(self):
        nhl = compute_bet365_early_payout_ev_boost(
            sport="NHL",
            fair_win_prob=0.45,
            offered_decimal_odds=2.25,
        )
        self.assertGreater(nhl["effective_win_prob"], nhl["raw_fair_win_prob"])
        self.assertGreater(nhl["ev_boost_bps"], 50.0)

    def test_polymarket_kalshi_cross_arb_and_barrier(self):
        fee_c = compute_kalshi_taker_fee_cents(0.50, 100)
        self.assertAlmostEqual(fee_c, 1.75, places=2)
        arb = evaluate_polymarket_kalshi_cross_arb(
            poly_yes_ask=0.48,
            poly_no_ask=0.53,
            kalshi_yes_ask=0.54,
            kalshi_no_ask=0.47,
            days_to_resolution=5.0,
            oracle_mismatch_noul=0.05,
        )
        self.assertTrue(arb["executable_arb"])
        self.assertGreater(arb["net_arb_bps"], 200.0)

        barrier = price_crypto_intraday_barrier_contract(
            spot_price=70250.0,
            strike_price=70000.0,
            minutes_to_expiry=15.0,
            annualized_iv=0.48,
            perp_funding_8h_bps=2.5,
            orderbook_imbalance=0.30,
            direction="ABOVE",
        )
        self.assertGreater(barrier["fair_probability"], 0.65)

    def test_crypto_perp_execution_truth_table(self):
        # Tight stop vetoed by 2 bps Maker + 6 bps Taker stop friction
        tight = evaluate_crypto_perp_execution_truth_table(
            symbol="BTCUSDTM",
            side="LONG",
            entry_price=70000.0,
            stop_price=69900.0,  # only 14.3 bps stop -> 8.5 bps friction is 59% of 1R!
            target_price=70300.0,
            funding_8h_bps=1.0,
            spread_bps=2.0,
        )
        self.assertEqual(tight["action"], "VETO_STOP_TOO_TIGHT_FOR_FEES")

        # Clean structural stop passes
        clean = evaluate_crypto_perp_execution_truth_table(
            symbol="BTCUSDTM",
            side="LONG",
            entry_price=70000.0,
            stop_price=69300.0,  # 100 bps stop -> 8.5 bps friction is only 8.5% of 1R
            target_price=71600.0,
            funding_8h_bps=2.0,
            spread_bps=2.0,
            etf_net_inflow_musd=145.0,
        )
        self.assertEqual(clean["action"], "EXECUTE_BRACKET_ORDER")
        self.assertTrue(clean["etf_tailwind"])

    def test_live_cashout_truth_table_locks_profit_on_q1_surge(self):
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
        t1 = evaluate_multi_domain_signal_truth_table(
            concrete_noul=0.92,
            impact_noul=0.81,
            already_priced_in_noul=0.12,
            ambiguity_or_rumor_noul=0.05,
            primary_desk="sports_betting",
            directional_lean="bullish_over_yes",
            age_minutes=2.0,
        )
        self.assertEqual(t1["tier"], "TIER_1_SNIPE_ALPHA")
        self.assertIn("bet365_ontario_player_props", t1["spillover_targets"])

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
