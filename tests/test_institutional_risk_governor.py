from __future__ import annotations

import os
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
VENDOR = os.path.join(ROOT, "vendor")
if os.path.isdir(VENDOR) and VENDOR not in sys.path:
    sys.path.insert(0, VENDOR)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from sovereign_bots.core.institutional_risk_governor import (
    compute_cppi_drawdown_adaptive_kelly,
    compute_l2_kyle_lambda_and_vpin_routing,
    evaluate_live_sgp_plus_state_machine,
)


class InstitutionalRiskGovernorTests(unittest.TestCase):
    def test_cppi_drawdown_adaptive_kelly_states(self):
        # 1. Healthy baseline account
        normal = compute_cppi_drawdown_adaptive_kelly(
            current_equity_usd=25200.0,
            starting_equity_usd=25000.0,
            static_drawdown_floor_usd=23500.0,
            profit_target_usd=27500.0,
            daily_starting_equity_usd=25100.0,
        )
        self.assertTrue(normal["trading_allowed"])
        self.assertEqual(normal["regime"], "NORMAL_CPPI_EXECUTION")
        self.assertGreater(normal["max_risk_usd"], 50.0)

        # 2. Target proximity taper (within $375 of $27,500 pass line)
        near_target = compute_cppi_drawdown_adaptive_kelly(
            current_equity_usd=27350.0,
            starting_equity_usd=25000.0,
            static_drawdown_floor_usd=23500.0,
            profit_target_usd=27500.0,
            daily_starting_equity_usd=27200.0,
        )
        self.assertTrue(near_target["trading_allowed"])
        self.assertEqual(near_target["regime"], "TARGET_PROXIMITY_TAPER")
        self.assertLess(near_target["max_risk_usd"], normal["max_risk_usd"])

        # 3. Two consecutive losses hard pause
        paused = compute_cppi_drawdown_adaptive_kelly(
            current_equity_usd=24900.0,
            consecutive_losses=2,
        )
        self.assertFalse(paused["trading_allowed"])
        self.assertEqual(paused["regime"], "TWO_CONSECUTIVE_LOSSES_SESSION_PAUSE")
        self.assertEqual(paused["max_risk_usd"], 0.0)

        # 4. Personal daily floor breached
        daily_stop = compute_cppi_drawdown_adaptive_kelly(
            current_equity_usd=24500.0,
            daily_starting_equity_usd=25000.0,
        )
        self.assertFalse(daily_stop["trading_allowed"])
        self.assertEqual(daily_stop["regime"], "PERSONAL_DAILY_FLOOR_HIT")

    def test_l2_kyle_lambda_and_vpin_routing(self):
        bids = [[100.00, 120.0], [99.98, 200.0], [99.95, 300.0]]
        asks = [[100.02, 120.0], [100.04, 200.0], [100.08, 300.0]]

        # Low toxicity, tight spread, small notional -> IOC_SNIPE_ALLOWED
        ioc = compute_l2_kyle_lambda_and_vpin_routing(
            bids=bids,
            asks=asks,
            side="BUY",
            order_notional_usd=5000.0,
            recent_buy_volume_usd=52000.0,
            recent_sell_volume_usd=48000.0,
        )
        self.assertTrue(ioc["executable"])
        self.assertEqual(ioc["routing_decision"], "IOC_SNIPE_ALLOWED")

        # High VPIN flow toxicity -> HARD_VETO_TOXIC
        toxic = compute_l2_kyle_lambda_and_vpin_routing(
            bids=bids,
            asks=asks,
            side="BUY",
            order_notional_usd=5000.0,
            recent_buy_volume_usd=92000.0,
            recent_sell_volume_usd=8000.0,
        )
        self.assertFalse(toxic["executable"])
        self.assertEqual(toxic["routing_decision"], "HARD_VETO_TOXIC")

    def test_live_sgp_plus_state_machine_cashout_and_hedge(self):
        # Reconstructs Barry's Tuesday Oct 27 5-leg NBA Bonus Bet SGP+ where 4 legs settled WON
        # and Luka Doncic had 16/30 points early with a strong early cashout offer
        cashout_case = evaluate_live_sgp_plus_state_machine(
            stake_cad=25.0,
            is_bonus_bet=True,
            combined_decimal_odds=5.60,
            total_legs=5,
            settled_legs_won=4,
            active_leg_current_stat=16,
            active_leg_target_milestone=30,
            active_leg_baseline_per_min=0.92,
            active_leg_minutes_remaining=24.0,
            live_score_margin_abs=19,
            book_cashout_offer_cad=103.79,
            final_leg_opposite_hedge_decimal_odds=1.45,
        )
        self.assertEqual(cashout_case["state"], "4_OF_5_SETTLED")
        self.assertIn(
            cashout_case["recommended_action"],
            ("EXECUTE_EARLY_CASHOUT", "LOCK_SYNTHETIC_HEDGE_ARB"),
        )
        self.assertGreater(cashout_case["cashout_net_profit_cad"], 100.0)


if __name__ == "__main__":
    unittest.main()
