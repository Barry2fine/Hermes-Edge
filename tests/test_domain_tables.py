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
    DOMAIN_EQUATION_ATLAS,
    avellaneda_stoikov_maker_quote,
    bregman_kl_prediction_market_edge,
    dimension_free_lsi_tail_risk_bound,
    kesten_stigum_regime_gate,
    matroid_prophet_slate_threshold,
    skellam_nhl_soccer_probs,
    adjust_prop_for_schedule_fatigue_and_officials,
    james_stein_empirical_bayes_shrinkage,
    solve_live_hedge_and_middle_locked_profit,
    solve_multi_asset_correlated_kelly,
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



    def test_openai_2026_catalog_breakthrough_solvers(self):
        # #229 Kesten-Stigum + #119 Hellinger Contraction
        ks_pass = kesten_stigum_regime_gate(
            independent_source_count=3,
            channel_fidelities=[0.85, 0.80, 0.78],
            noise_correlation=0.10,
        )
        self.assertTrue(ks_pass["reconstructible"])
        self.assertEqual(ks_pass["action"], "PASS_REGIME_RECONSTRUCTIBLE")

        ks_fail = kesten_stigum_regime_gate(
            independent_source_count=2,
            channel_fidelities=[0.35, 0.40],
            noise_correlation=0.50,
        )
        self.assertFalse(ks_fail["reconstructible"])

        # #093 Dimension-Free Subgaussian Log-Sobolev Tail Bound
        lsi = dimension_free_lsi_tail_risk_bound(
            position_weights=[0.15, 0.15, 0.15, 0.15, 0.15],
            marginal_subgaussian_sigmas=[0.02, 0.025, 0.018, 0.022, 0.019],
            target_drawdown_pct=0.06,
            log_concavity_kappa=1.1,
        )
        self.assertTrue(lsi["safe_at_95pct_confidence"])

        # #111 One-Sample Matroid Prophet Inequality Slate Gate
        prophet = matroid_prophet_slate_threshold(
            historical_sample_evs=[8.4, 7.1, 6.2, 5.5, 4.8, 3.9, 2.8],
            max_slate_slots_k=4,
            slots_remaining_r=2,
            candidate_ev_pct=4.5,
        )
        self.assertTrue(prophet["accept"])

    def test_skellam_bregman_and_avellaneda_stoikov_solvers(self):
        sk = skellam_nhl_soccer_probs(lambda_home=3.2, lambda_away=2.8)
        self.assertAlmostEqual(
            sk["p_reg_home_win"] + sk["p_reg_draw_ot"] + sk["p_reg_away_win"],
            1.0,
            places=4,
        )
        self.assertGreater(sk["p_away_puckline_plus_1_5"], 0.60)

        kl = bregman_kl_prediction_market_edge(
            p_model=0.64,
            q_market_price=0.52,
            orderbook_vpin_toxicity=0.25,
            bankroll_usd=200.0,
        )
        self.assertTrue(kl["actionable"])
        self.assertEqual(kl["side"], "YES")

        as_quote = avellaneda_stoikov_maker_quote(
            mid_price=70000.0,
            inventory_signed_units=0.5,
            risk_aversion_gamma=0.1,
            volatility_bps_per_min=8.0,
            horizon_minutes=15.0,
        )
        self.assertLess(as_quote["optimal_post_only_bid"], as_quote["mid_price"])
        self.assertGreater(as_quote["optimal_post_only_ask"], as_quote["mid_price"])
        self.assertEqual(len(DOMAIN_EQUATION_ATLAS), 8)



    def test_cross_desk_hedge_kelly_fatigue_and_bayes_solvers(self):
        # 1. Synthetic live hedge beats shaved book cashout offer
        hedge = solve_live_hedge_and_middle_locked_profit(
            initial_stake_cash=25.0,
            is_bonus_bet=True,
            ticket_payout_gross=143.0,
            book_cashout_offer=103.79,
            hedge_opposite_decimal_odds=4.20,
            middle_overlap_prob=0.04,
        )
        self.assertEqual(hedge["best_route"], "SYNTHETIC_OPPOSITE_SIDE_HEDGE")
        self.assertGreater(hedge["guaranteed_hedge_net_profit_usd"], 103.79)

        # 2. Multi-asset correlated Kelly scales down positively correlated legs
        kelly_corr = solve_multi_asset_correlated_kelly(
            candidate_edges=[0.06, 0.05],
            candidate_variances=[0.25, 0.25],
            pairwise_corr_matrix=[[1.0, 0.65], [0.65, 1.0]],
            fractional_kelly_lambda=0.25,
            max_total_leverage=0.20,
        )
        kelly_uncorr = solve_multi_asset_correlated_kelly(
            candidate_edges=[0.06, 0.05],
            candidate_variances=[0.25, 0.25],
            pairwise_corr_matrix=[[1.0, 0.0], [0.0, 1.0]],
            fractional_kelly_lambda=0.25,
            max_total_leverage=0.20,
        )
        self.assertLess(kelly_corr["total_allocation"], kelly_uncorr["total_allocation"])

        # 3. Road back-to-back at Denver altitude reduces scoring projection
        fatigue = adjust_prop_for_schedule_fatigue_and_officials(
            base_projected_mean=26.5,
            stat_category="points",
            rest_days=0,
            is_road_back_to_back=True,
            third_game_in_four_nights=True,
            altitude_venue="DEN",
            referee_foul_rate_zscore=-0.5,
        )
        self.assertLess(fatigue["adjusted_mean"], 24.5)
        self.assertGreater(fatigue["fano_dispersion_multiplier"], 1.10)

        # 4. Empirical-Bayes shrinks noisy 3-game hot streak toward prior
        shrunk = james_stein_empirical_bayes_shrinkage(
            observed_rates=[0.60, 0.20, 0.38],
            sample_sizes=[4, 4, 50],
            grand_prior_mean=0.365,
            prior_strength_m=20.0,
        )
        self.assertLess(shrunk[0], 0.45)
        self.assertGreater(shrunk[1], 0.30)


if __name__ == "__main__":
    unittest.main()
