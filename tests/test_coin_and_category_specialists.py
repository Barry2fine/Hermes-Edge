"""Unit tests for all 1,492 Per-Coin Specialist Bots, Multi-Service Allocators,
11 Per-Category Polymarket Specialist Bots, and the Self-Improving Knowledge Loop."""

from __future__ import annotations

import os
import tempfile
import unittest

from sovereign_bots.bots.crypto import (
    ALL_1492_COIN_BOTS,
    EXCHANGE_COIN_REGISTRY,
    CoinSolSpecialistBot,
    HyperliquidFundingBasisArbBot,
    HyperliquidProprMicrostructureBot,
    KuCoinMultiServiceAllocatorBot,
    KuCoinSpotOcoGridBot,
    UniversalCatalogCoinRouterBot,
    get_coin_bot,
    get_coin_bots_for_venue,
)
from sovereign_bots.bots.polymarket import (
    POLY_CATEGORY_BOTS,
    PolyCryptoCategoryBot,
    PolyPoliticsCategoryBot,
    PolyUniversalTagRouterBot,
    resolve_canonical_category,
)
from sovereign_bots.core.knowledge_loop import (
    load_knowledge_state,
    record_coin_outcome,
    record_polymarket_outcome,
)


class CoinAndCategorySpecialistTests(unittest.TestCase):
    def test_1492_coins_and_venues_indexed(self):
        self.assertGreaterEqual(len(EXCHANGE_COIN_REGISTRY), 1400)
        self.assertEqual(len(ALL_1492_COIN_BOTS), len(EXCHANGE_COIN_REGISTRY))
        self.assertGreaterEqual(len(get_coin_bots_for_venue("kucoin_usdtm_perp")), 680)
        self.assertGreaterEqual(len(get_coin_bots_for_venue("kucoin_spot")), 800)
        self.assertGreaterEqual(len(get_coin_bots_for_venue("hyperliquid_propr_perp")), 170)
        self.assertGreaterEqual(len(get_coin_bots_for_venue("hyperliquid_hip2_spot")), 300)

    def test_per_coin_bot_specialization_and_abstention(self):
        sol_bot = CoinSolSpecialistBot()
        # Abstains on ETH
        self.assertEqual(sol_bot.vote({"coin": "ETH", "daily_vol_pct": 3.0, "spread_bps": 2.0, "rr": 2.1}).decision, "abstain")
        # Approves clean SOL setup
        v_sol = sol_bot.vote({"coin": "SOL-USDT", "daily_vol_pct": 3.2, "spread_bps": 2.5, "rr": 2.2, "side": "short", "funding_rate": 0.0003})
        self.assertEqual(v_sol.decision, "approve")
        # Universal router routes tail coins (e.g. IRYS, PUMP, PENDLE) to their exact coin bot
        router = UniversalCatalogCoinRouterBot()
        v_irys = router.vote({"coin": "IRYSUSDTM", "daily_vol_pct": 4.5, "spread_bps": 6.0, "rr": 2.1, "side": "long", "funding_rate": -0.0013})
        self.assertEqual(v_irys.decision, "approve")
        self.assertTrue(any("routed_to:coin_irys" in r for r in v_irys.reasons))

    def test_kucoin_and_hyperliquid_venue_allocators(self):
        kc_alloc = KuCoinMultiServiceAllocatorBot()
        v_kc = kc_alloc.vote({"coin": "SOL", "rr": 2.1, "spread_bps": 3.0, "funding_rate": 0.0004, "side": "short"})
        self.assertEqual(v_kc.decision, "approve")
        self.assertEqual(KuCoinSpotOcoGridBot().vote({"daily_vol_pct": 2.8, "spread_bps": 3.5, "rr": 1.9}).decision, "approve")
        self.assertEqual(HyperliquidProprMicrostructureBot().vote({"spread_bps": 2.2, "est_slippage_bps": 3.0, "rr": 2.0, "daily_loss_used_pct": 0.4}).decision, "approve")
        self.assertEqual(HyperliquidFundingBasisArbBot().vote({"funding_rate": 0.0008, "side": "short", "basis_bps": 9.5}).decision, "approve")

    def test_polymarket_category_bots_and_518_tag_router(self):
        self.assertEqual(len(POLY_CATEGORY_BOTS), 11)
        self.assertEqual(resolve_canonical_category({"tags": ["governor-midterms"]}), "politics")
        self.assertEqual(resolve_canonical_category({"tags": ["bitcoin", "up-or-down"]}), "crypto")
        self.assertEqual(resolve_canonical_category({"question": "Will the Fed cut rates at the FOMC meeting?"}), "macro")

        p_crypto = PolyCryptoCategoryBot()
        self.assertEqual(p_crypto.vote({"category": "politics", "model_p": 0.62, "market_p": 0.50}).decision, "abstain")
        self.assertEqual(p_crypto.vote({"category": "crypto", "model_p": 0.64, "market_p": 0.50, "spread_bps": 80}).decision, "approve")

        p_router = PolyUniversalTagRouterBot()
        v_tag = p_router.vote({"tags": ["senate-midterms"], "model_p": 0.63, "market_p": 0.49, "spread_bps": 60})
        self.assertEqual(v_tag.decision, "approve")
        self.assertTrue(any("routed_category:politics" in r for r in v_tag.reasons))

    def test_self_improving_knowledge_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            p = os.path.join(tmp, "state.json")
            c_after = record_coin_outcome("SOL", "kucoin_spot_oco", realized_r=1.85, clv_bps=11.0, p_pred=0.65, win=True, path=p)
            self.assertGreater(c_after["mwu_weight"], 1.0)
            self.assertEqual(c_after["preferred_venues"][0], "kucoin_spot_oco")

            poly_after = record_polymarket_outcome("macro", p_model=0.72, p_market=0.54, outcome=1, clv_cents=3.2, path=p)
            self.assertGreater(poly_after["mwu_weight"], 1.0)
            loaded = load_knowledge_state(p)
            self.assertIn("SOL", loaded["coins"])
            self.assertIn("macro", loaded["polymarket_categories"])


if __name__ == "__main__":
    unittest.main()
