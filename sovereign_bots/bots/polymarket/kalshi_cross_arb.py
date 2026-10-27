"""Kalshi / Sharp-Book Cross-Venue Bregman Arbitrage Seat (Soft Edge Seat).

Measures Kullback-Leibler / Bregman divergence between Polymarket CLOB YES/NO
prices and regulated Kalshi v2 / sharp sportsbook implied probabilities (`edge_seat = True`).
"""

from __future__ import annotations
import math
from typing import Any, Dict, Optional
from ...core.bot import Bot, Vote


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class KalshiPolymarketCrossArbBot(Bot):
    id = "kalshi_cross_arb"
    mandate = ("Detect cross-venue probability dislocations and Bregman KL "
               "divergence between Kalshi/sharp books and Polymarket CLOB.")
    domains = ("polymarket",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        kalshi_p = _num(ctx.get("kalshi_prob") or ctx.get("reference_prob"))
        poly_p = _num(ctx.get("poly_prob") or ctx.get("market_p"))

        if kalshi_p is None or poly_p is None:
            return Vote(self.id, "abstain", 0.0, ["no_cross_venue_reference_prob"], {})

        p = max(0.005, min(0.995, kalshi_p))
        q = max(0.005, min(0.995, poly_p))
        kl_div = p * math.log(p / q) + (1.0 - p) * math.log((1.0 - p) / (1.0 - q))
        diff = p - q
        evidence = {"reference_p": round(p, 4), "poly_p": round(q, 4), "prob_diff": round(diff, 4), "kl_divergence": round(kl_div, 5)}

        if diff >= 0.035 and kl_div >= 0.003:
            return Vote(self.id, "approve", min(0.92, 0.60 + diff * 3.0), [f"cross_venue_dislocation:+{diff*100:.1f}c"], evidence)
        if diff <= -0.035:
            return Vote(self.id, "reject", 0.78, [f"cross_venue_overpriced:{diff*100:.1f}c"], evidence)
        return Vote(self.id, "abstain", 0.0, ["cross_venue_inline"], evidence)
