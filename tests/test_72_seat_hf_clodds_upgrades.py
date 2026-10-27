"""Unit tests for the 6 new AI/HF/CloddsBot/NOF0/SNR committee seats (72-seat expansion) and Free API Hub."""

from __future__ import annotations
import unittest
from sovereign_bots.core.registry import domain_bots, all_bots
from sovereign_bots.core.free_feeds import FREE_API_REGISTRY, list_free_feeds
from sovereign_bots.bots.crypto.kronos_kline_quantizer import KronosKlineQuantizerBot
from sovereign_bots.bots.crypto.chronos_conformal_interval import ChronosConformalIntervalBot
from sovereign_bots.bots.crypto.cryptobert_narrative_divergence import CryptoBertNarrativeDivergenceBot
from sovereign_bots.bots.crypto.nof0_arena_sortino_decay import Nof0ArenaSortinoDecayBot
from sovereign_bots.bots.polymarket.clodds_synthetic_implied_arb import CloddsSyntheticImpliedArbBot
from sovereign_bots.bots.sports.bonus_bet_snr_optimizer import BonusBetSNROptimizerBot


class Test72SeatHFCloddsUpgrades(unittest.TestCase):
    def test_total_72_seat_committee_counts(self):
        c = domain_bots("crypto")
        p = domain_bots("polymarket")
        s = domain_bots("sports")
        self.assertEqual(len(c), 30)
        self.assertEqual(len(p), 18)
        self.assertEqual(len(s), 24)
        self.assertEqual(len(all_bots()), 72)

    def test_kronos_kline_quantizer(self):
        bot = KronosKlineQuantizerBot()
        self.assertEqual(bot.evaluate({"direction": "long", "kronos_token_score": 0.65, "kronos_token_entropy": 0.30}).decision, "approve")
        self.assertEqual(bot.evaluate({"direction": "long", "kronos_token_score": 0.50, "kronos_token_entropy": 0.85}).decision, "reject")
        self.assertEqual(bot.evaluate({"direction": "short", "kronos_token_score": 0.60, "kronos_token_entropy": 0.40}).decision, "reject")

    def test_chronos_conformal_interval(self):
        bot = ChronosConformalIntervalBot()
        self.assertEqual(bot.evaluate({"chronos_q50_r": 1.45, "chronos_adverse_q_r": -0.60}).decision, "approve")
        self.assertEqual(bot.evaluate({"chronos_q50_r": 1.20, "chronos_adverse_q_r": -1.20}).decision, "reject")
        self.assertEqual(bot.evaluate({"chronos_q50_r": 0.40, "chronos_adverse_q_r": -0.50}).decision, "reject")

    def test_cryptobert_narrative_divergence(self):
        bot = CryptoBertNarrativeDivergenceBot()
        self.assertEqual(bot.evaluate({"direction": "long", "cryptobert_sentiment": 0.88, "cvd_zscore": -1.10}).decision, "reject")
        self.assertEqual(bot.evaluate({"direction": "short", "cryptobert_sentiment": -0.85, "cvd_zscore": 0.95}).decision, "reject")
        self.assertEqual(bot.evaluate({"direction": "long", "cryptobert_sentiment": -0.50, "cvd_zscore": 1.20}).decision, "approve")

    def test_nof0_arena_sortino_decay(self):
        bot = Nof0ArenaSortinoDecayBot()
        self.assertEqual(bot.evaluate({"archetype_rolling_sortino": 1.80, "archetype_sample_n": 25}).decision, "approve")
        self.assertEqual(bot.evaluate({"archetype_rolling_sortino": -0.50, "archetype_sample_n": 20}).decision, "reject")
        self.assertEqual(bot.evaluate({"archetype_rolling_sortino": -0.80, "archetype_sample_n": 3}).decision, "abstain")

    def test_clodds_synthetic_implied_arb(self):
        bot = CloddsSyntheticImpliedArbBot()
        self.assertEqual(bot.evaluate({"market_p": 0.48, "clodds_synthetic_p": 0.56, "clodds_obi": 0.25}).decision, "approve")
        self.assertEqual(bot.evaluate({"market_p": 0.55, "clodds_synthetic_p": 0.49, "clodds_obi": -0.20}).decision, "reject")

    def test_bonus_bet_snr_optimizer(self):
        bot = BonusBetSNROptimizerBot()
        self.assertEqual(bot.evaluate({"is_bonus_bet": False, "model_p": 0.65, "decimal_odds": 1.58}).decision, "abstain")
        self.assertEqual(bot.evaluate({"is_bonus_bet": True, "model_p": 0.60, "decimal_odds": 1.55}).decision, "reject")
        self.assertEqual(bot.evaluate({"is_bonus_bet": True, "model_p": 0.1624, "decimal_odds": 10.74}).decision, "approve")

    def test_free_feeds_registry(self):
        feeds = list_free_feeds()
        self.assertEqual(len(feeds), 8)
        self.assertIn("hyperliquid_info", FREE_API_REGISTRY)
        self.assertIn("kucoin_futures_public", FREE_API_REGISTRY)
        self.assertIn("polymarket_gamma", FREE_API_REGISTRY)
        self.assertIn("espn_sports_core", FREE_API_REGISTRY)


if __name__ == "__main__":
    unittest.main()
