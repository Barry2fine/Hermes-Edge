import copy
import os
import unittest
from unittest.mock import patch
import polymarket_sports_github_cycle as cycle


def market():
    return {"question": "Inert fixture?", "outcomes": '["No", "Yes"]',
            "outcomePrices": '["0.7", "0.3"]', "volume24hr": 20000, "liquidityNum": 9000}

def event():
    return {"shortName": "Fixture A vs B", "competitions": [{
        "competitors": [{"homeAway": "home", "team": {"displayName": "A"}},
                        {"homeAway": "away", "team": {"displayName": "B"}}],
        "odds": [{"provider": {"name": "Fixture Book"},
                  "homeTeamOdds": {"moneyLine": -120}, "awayTeamOdds": {"moneyLine": 110}}]}]}


class EvidenceTests(unittest.TestCase):
    def poly(self, rows):
        with patch.object(cycle, "_http_json", return_value=rows):
            return cycle.scan_polymarket_gamma()

    def sports(self, rows):
        with patch.object(cycle, "_http_json", return_value={"events": rows}):
            return cycle.scan_sports_slate()

    def test_yes_label_mapping_and_liquidity_cannot_approve(self):
        result = self.poly([market()]); row = result["markets"][0]
        self.assertEqual(row["yes_prob"], .3)
        self.assertEqual(row["decision"], "ABSTAIN")
        self.assertIsNone(row["edge_bps"])
        self.assertEqual(row["stake"], 0)

    def test_invalid_poly_inputs_are_unavailable(self):
        for field, values in {
            "outcomes": [None, ["A", "B"], ["Yes", "Yes"], ["Yes"]],
            "outcomePrices": [None, [], [.5], [.3, "bad"], [.3, float("nan")], [True, .5], [0, 1]],
            "volume24hr": [None, float("inf"), -1, True],
            "liquidityNum": [None, float("nan"), -1, "bad"],
        }.items():
            for value in values:
                with self.subTest(field=field, value=value):
                    result = self.poly([{**market(), field: value}])
                    self.assertEqual(result["markets"], [])
                    self.assertEqual(result["invalid_records"], 1)
                    self.assertEqual(result["status"], "ABSTAIN")

    def test_actual_book_and_outcomes_retained_without_fair_probability(self):
        result = self.sports([event()]); row = result["edges"][0]
        self.assertEqual(row["book"], "Fixture Book")
        self.assertEqual(row["lines"][0]["selection"], "A")
        self.assertAlmostEqual(row["lines"][0]["decimal_odds"], 1 + 100 / 120)
        self.assertIsNone(row["fair_prob"])
        self.assertIsNone(row["ev_pct"])
        self.assertEqual(row["quarter_kelly_units"], 0)
        self.assertEqual(row["decision"], "ABSTAIN")
        self.assertIsNone(result["parlay_summary"])

    def test_missing_or_invalid_sports_odds_never_default(self):
        for value in (None, 0, 99, True, float("nan"), float("inf"), -110.5, "bad"):
            fixture = event()
            fixture["competitions"][0]["odds"][0]["homeTeamOdds"]["moneyLine"] = value
            self.assertEqual(self.sports([fixture])["edges"], [])
        for key in ("competitors", "odds"):
            fixture = event(); del fixture["competitions"][0][key]
            self.assertEqual(self.sports([fixture])["edges"], [])

    def test_empty_and_malformed_response_are_distinct(self):
        self.assertEqual(self.poly([])["status"], "EMPTY")
        self.assertEqual(self.sports([])["status"], "EMPTY")
        with patch.object(cycle, "_http_json", return_value={}):
            self.assertRaises(ValueError, cycle.scan_polymarket_gamma)
            self.assertRaises(ValueError, cycle.scan_sports_slate)

    def test_card_has_no_approval_or_invented_edge(self):
        card = cycle._observation_card("Sports", self.sports([event()]))
        self.assertIn("ABSTAIN", card)
        self.assertIn("Stake: 0", card)
        for claim in ("LIVE", "ALLOW", "Committee-Approved", "Copula", "bet365", "Pinnacle"):
            self.assertNotIn(claim, card)

    def test_required_acquisition_or_delivery_failures_exit_nonzero(self):
        empty = {"status": "EMPTY", "invalid_records": 0}
        for failed_scan in ("scan_polymarket_gamma", "scan_sports_slate"):
            with patch.object(cycle, "scan_polymarket_gamma", return_value=empty), \
                 patch.object(cycle, "scan_sports_slate", return_value=empty), \
                 patch.object(cycle, failed_scan, side_effect=OSError("fixture")), \
                 patch.dict(os.environ, {"SEND_TELEGRAM": "0"}), patch("builtins.print"):
                self.assertEqual(cycle.main(), 1)
        for delivery in ({"ok": False}, None, OSError("fixture")):
            with patch.object(cycle, "scan_polymarket_gamma", return_value=empty), \
                 patch.object(cycle, "scan_sports_slate", return_value=empty), \
                 patch.object(cycle, "send_institutional_telegram", side_effect=delivery if isinstance(delivery, Exception) else None, return_value=delivery), \
                 patch.dict(os.environ, {"SEND_TELEGRAM": "1"}), patch("builtins.print"):
                self.assertEqual(cycle.main(), 1)

    def test_valid_empty_cycle_succeeds_without_delivery(self):
        with patch.object(cycle, "_http_json", side_effect=[[], {"events": []}, {"events": []}]), \
             patch.object(cycle, "send_institutional_telegram") as send, \
             patch.dict(os.environ, {"SEND_TELEGRAM": "0"}), patch("builtins.print"):
            self.assertEqual(cycle.main(), 0)
            send.assert_not_called()
