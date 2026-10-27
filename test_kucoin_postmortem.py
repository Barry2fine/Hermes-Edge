#!/usr/bin/env python3
"""
Unit tests for KuCoin Futures MFE/MAE Post-Mortem Learning Loop + Pattern-Memory VETO + Propr Step-Off.
"""
import tempfile
import unittest
from unittest import mock

import autonomous_executor
import kucoin_postmortem_engine as kpm
import shared_desk_cli
from kucoin_learn import credit, postmortem, review, state
from kucoin_learn.postmortem_gate import PostmortemBot


class KuCoinPostmortemAndStepOffTests(unittest.TestCase):
    def test_excursions_and_honest_labels(self):
        # 1. EARNED_WIN: clean unchased entry, hits target and captures >= 75%
        t_win = {"direction": "short", "entry": 100.0, "stop": 102.0, "target": 96.0, "r": 1.8, "risk_usd": 1.8}
        path_win = [{"high": 100.4, "low": 95.8}, {"high": 97.0, "low": 96.2}]
        g_win = postmortem.grade(t_win, path_win, {"rsi": 52.0, "ema_dist_pct": -1.2, "risk_budget": 2.0})
        self.assertEqual(g_win["verdict"], "SMART")
        self.assertEqual(g_win["label"], "EARNED_WIN")
        self.assertEqual(g_win["tags"], [])

        # 2. LUCKY_WIN: chased entry + oversized + won on a coin-flip -> credits nobody!
        t_lucky = {"direction": "long", "entry": 100.0, "stop": 98.0, "target": 104.0, "r": 1.2, "risk_usd": 3.5}
        path_lucky = [{"high": 103.0, "low": 98.1}]
        g_lucky = postmortem.grade(t_lucky, path_lucky, {"rsi": 74.0, "ema_dist_pct": 6.5, "risk_budget": 2.0})
        self.assertEqual(g_lucky["verdict"], "POOR")
        self.assertEqual(g_lucky["label"], "LUCKY_WIN")
        self.assertIn("chased_entry", g_lucky["tags"])
        self.assertIn("oversized", g_lucky["tags"])

        # 3. GOOD_LOSS: clean entry, moved >= 0.5R in favor, reversed cleanly to stop -> never punished!
        t_good_loss = {"direction": "short", "entry": 100.0, "stop": 102.0, "target": 96.0, "r": -1.0, "risk_usd": 1.9}
        path_gl = [{"high": 100.2, "low": 98.6}, {"high": 102.0, "low": 99.5}]
        g_gl = postmortem.grade(t_good_loss, path_gl, {"rsi": 50.0, "ema_dist_pct": -0.8, "risk_budget": 2.0})
        self.assertEqual(g_gl["verdict"], "SMART")
        self.assertEqual(g_gl["label"], "GOOD_LOSS")

        # 4. BAD_LOSS: target_unreachable + oversized (like ZEC short into vertical squeeze)
        t_bad = {"direction": "short", "entry": 1162.0, "stop": 1203.0, "target": 1088.0, "r": -1.02, "risk_usd": 2.55}
        path_bad = [{"high": 1205.0, "low": 1160.0}]
        g_bad = postmortem.grade(t_bad, path_bad, {"rsi": 65.0, "ema_dist_pct": 4.2, "risk_budget": 1.98})
        self.assertEqual(g_bad["verdict"], "POOR")
        self.assertEqual(g_bad["label"], "BAD_LOSS")
        self.assertIn("target_unreachable", g_bad["tags"])
        self.assertIn("oversized", g_bad["tags"])

    def test_pattern_memory_veto_and_seat_credit(self):
        with tempfile.TemporaryDirectory() as tmp:
            t_bad = {
                "trade_id": "t1", "coin": "ZEC", "direction": "short",
                "entry": 1200.0, "stop": 1220.0, "target": 1160.0, "r": -1.0, "risk_usd": 2.5,
                "opened_ts": 1000.0, "closed_ts": 2000.0,
            }
            candles = lambda coin: [{"t": 1500.0, "high": 1222.0, "low": 1198.0}]
            ctxs = {
                f"t{i}": {
                    "rsi": 28.0, "ema_dist_pct": -6.0, "risk_budget": 1.98,
                    "votes": [
                        {"bot": "squeeze_gate", "decision": "reject"},
                        {"bot": "momentum", "decision": "approve"},
                    ],
                }
                for i in range(1, 4)
            }
            trades = [{**t_bad, "trade_id": f"t{i}"} for i in range(1, 4)]
            res = review.review_trades(trades, candles_for=candles, contexts=ctxs, base=tmp, regime="bear_squeeze")
            self.assertEqual(res["count"], 3)
            self.assertTrue(res["patterns"]["ZEC|short|bear_squeeze"]["blocked"])

            # Verify PostmortemBot VETO triggers on ZEC|short|bear_squeeze and abstains on ETH|long
            gate_zec = kpm.check_kucoin_postmortem_gate("ZECUSDTM", "SHORT", regime="bear_squeeze", base=tmp)
            self.assertFalse(gate_zec["allowed"])
            self.assertEqual(gate_zec["decision"], "reject")

            gate_eth = kpm.check_kucoin_postmortem_gate("ETHUSDTM", "LONG", regime="bear_squeeze", base=tmp)
            self.assertTrue(gate_eth["allowed"])
            self.assertEqual(gate_eth["decision"], "abstain")

            # Verify seat credit rewarded squeeze_gate (+3) and penalized momentum (-3)
            sc = {row["bot"]: row["credit"] for row in credit.seat_scorecard(base=tmp)}
            self.assertEqual(sc["squeeze_gate"], 3.0)
            self.assertEqual(sc["momentum"], -3.0)

    def test_propr_stepped_off_in_autonomous_and_watchdog(self):
        import os; os.makedirs('data/runtime', exist_ok=True); os.makedirs('logs', exist_ok=True)
        with mock.patch.object(autonomous_executor, "ProprClient", side_effect=AssertionError("ProprClient must not be called")):
            res = autonomous_executor.manage_and_execute_propr({}, [], set(), execute=True)
            self.assertTrue(res.get("disabled_by_owner"))
            self.assertEqual(res.get("newly_executed_trades"), [])

        fake_kc = mock.MagicMock()
        fake_kc.get_futures_overview.return_value = {"accountEquity": 192.97, "availableBalance": 192.97}
        fake_kc.get_futures_positions.return_value = []
        fake_kc.get_futures_active_orders.return_value = []
        fake_kc.get_futures_stop_orders.return_value = []
        fake_kc.request.return_value = {"data": []}
        with mock.patch.object(shared_desk_cli, "ProprClient", side_effect=AssertionError("ProprClient must not be called by poet_v5_watchdog")), \
             mock.patch.object(shared_desk_cli, "KuCoinClient", return_value=fake_kc):
            r = shared_desk_cli.run_ratchet(execute=True, agent_name="poet_v5_watchdog")
            self.assertTrue(r.get("ok"))
            self.assertEqual(r.get("propr_ratchet_actions"), [])


if __name__ == "__main__":
    unittest.main()
