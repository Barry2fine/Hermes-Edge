"""Unit tests for the 18 newly added Hard VETO and Soft Edge bots across Crypto, Sports, and Polymarket (66 total seats)."""

from __future__ import annotations

import unittest
from sovereign_bots.core.registry import all_bots, domain_bots, coverage, load_manifest
from sovereign_bots.bots.crypto import (
    FundingSettlementTrapBot, VpinToxicFlowBot, CorrelatedClusterBetaBot, AdrExhaustionChaseBot,
    CrossVenueLeadLagBot, DeribitOptionsGexSkewBot, LiquidationCascadeOIBot, CandlestickGeometryTensorBot,
)
from sovereign_bots.bots.sports import (
    JuiceAndVigCeilingBot, SmallSampleOverfitBot, SportsPostmortemBot,
    PinnacleDevigSharpSteamBot, InjuryUsageRedistributionBot, StudentTCopulaSGPBot,
)
from sovereign_bots.bots.polymarket import (
    PolymarketPostmortemBot, KalshiPolymarketCrossArbBot,
    MartingaleBeliefVolatilityBot, SmartMoneyOrderbookSweepBot,
)


class Expanded66SeatTests(unittest.TestCase):
    def test_total_roster_is_66_seats(self):
        self.assertEqual(len(domain_bots("crypto")), 30)
        self.assertEqual(len(domain_bots("sports")), 24)
        self.assertEqual(len(domain_bots("polymarket")), 18)
        self.assertEqual(len(all_bots()), 72)

    def test_crypto_hard_veto_seats(self):
        # 1. FundingSettlementTrapBot
        b1 = FundingSettlementTrapBot()
        self.assertTrue(b1.veto)
        self.assertEqual(b1.vote({"direction": "long", "minutes_to_funding": 8, "funding_rate_bps": 6.0}).decision, "reject")
        self.assertEqual(b1.vote({"direction": "short", "stop_distance_bps": 30.0, "round_trip_cost_bps": 12.0}).decision, "reject")
        # A veto seat must not report "clear" when funding telemetry is absent:
        # unknown is not favourable. Supply funding, then it can approve.
        self.assertEqual(b1.vote({"direction": "short", "stop_distance_bps": 80.0,
                                  "round_trip_cost_bps": 12.0}).decision, "reject")
        self.assertEqual(b1.vote({"direction": "short", "stop_distance_bps": 80.0,
                                  "round_trip_cost_bps": 12.0,
                                  "funding_rate_bps": 1.0,
                                  "minutes_to_funding": 40.0}).decision, "approve")

        # 2. VpinToxicFlowBot
        b2 = VpinToxicFlowBot()
        self.assertTrue(b2.veto)
        self.assertEqual(b2.vote({"direction": "short", "vpin": 0.81, "eobi": 0.1}).decision, "reject")
        self.assertEqual(b2.vote({"direction": "short", "vpin": 0.40, "eobi": 0.55}).decision, "reject")
        self.assertEqual(b2.vote({"direction": "short", "vpin": 0.40, "eobi": -0.20}).decision, "approve")

        # 3. CorrelatedClusterBetaBot
        b3 = CorrelatedClusterBetaBot()
        self.assertTrue(b3.veto)
        self.assertEqual(
            b3.vote({"coin": "ZEC", "direction": "short", "open_cluster_positions": [{"coin": "XMR", "direction": "short"}]}).decision,
            "reject",
        )
        self.assertEqual(
            b3.vote({"coin": "ZEC", "direction": "short", "open_cluster_positions": [{"coin": "SOL", "direction": "short"}]}).decision,
            "approve",
        )

        # 4. AdrExhaustionChaseBot
        b4 = AdrExhaustionChaseBot()
        self.assertTrue(b4.veto)
        self.assertEqual(b4.vote({"adr_consumed_pct": 0.92, "vwap_distance_atr": 1.2}).decision, "reject")
        self.assertEqual(b4.vote({"adr_consumed_pct": 0.55, "vwap_distance_atr": 1.1}).decision, "approve")

    def test_crypto_soft_edge_seats(self):
        self.assertEqual(CrossVenueLeadLagBot().vote({"direction": "long", "leader_return_bps": 18.0, "venue_lag_bps": 7.0}).decision, "approve")
        self.assertEqual(DeribitOptionsGexSkewBot().vote({"direction": "short", "deribit_skew_25d": -3.5}).decision, "approve")
        self.assertEqual(LiquidationCascadeOIBot().vote({"direction": "long", "liquidation_imbalance": 0.50}).decision, "approve")
        self.assertEqual(CandlestickGeometryTensorBot().vote({"direction": "short", "candle_tensor_score": -0.65, "relative_volume": 1.5}).decision, "approve")

    def test_sports_hard_and_soft_seats(self):
        # Hard VETO
        self.assertEqual(JuiceAndVigCeilingBot().vote({"book_hold_pct": 0.078, "decimal_odds": 1.80, "sharp_devig_edge": 0.01}).decision, "reject")
        self.assertEqual(JuiceAndVigCeilingBot().vote({"book_hold_pct": 0.045, "decimal_odds": 1.91}).decision, "approve")
        self.assertEqual(SmallSampleOverfitBot().vote({"sample_games": 4, "model_p": 0.56, "market_p": 0.52}).decision, "reject")
        self.assertEqual(SmallSampleOverfitBot().vote({"sample_games": 25, "model_p": 0.58, "market_p": 0.52}).decision, "approve")
        self.assertEqual(
            SportsPostmortemBot().vote({"pattern_key": "NHL|SOG|under", "sports_postmortem": {"NHL|SOG|under": {"n": 5, "poor_rate": 0.80, "blocked": True}}}).decision,
            "reject",
        )

        # Soft Edge
        self.assertEqual(PinnacleDevigSharpSteamBot().vote({"pinnacle_fair_p": 0.57, "bet365_decimal_odds": 1.91}).decision, "approve")
        self.assertEqual(InjuryUsageRedistributionBot().vote({"projected_usage_delta_pct": 4.2, "projected_minutes_delta": 5.5, "side": "over"}).decision, "approve")
        self.assertEqual(StudentTCopulaSGPBot().vote({"leg1_fair_p": 0.55, "leg2_fair_p": 0.55, "latent_rho": 0.35, "sgp_decimal_odds": 3.10}).decision, "approve")

    def test_polymarket_hard_and_soft_seats(self):
        self.assertEqual(
            PolymarketPostmortemBot().vote({"pattern_key": "crypto_barrier|yes|1d", "polymarket_postmortem": {"crypto_barrier|yes|1d": {"n": 4, "poor_rate": 0.75, "blocked": True}}}).decision,
            "reject",
        )
        self.assertEqual(KalshiPolymarketCrossArbBot().vote({"kalshi_prob": 0.62, "poly_prob": 0.54}).decision, "approve")
        self.assertEqual(MartingaleBeliefVolatilityBot().vote({"poly_prob": 0.50, "price_change_1h": 0.22, "confirmed_catalyst": False}).decision, "approve")
        self.assertEqual(SmartMoneyOrderbookSweepBot().vote({"clob_net_sweep_usd": 12000.0, "clob_book_imbalance": 0.42}).decision, "approve")


if __name__ == "__main__":
    unittest.main()
