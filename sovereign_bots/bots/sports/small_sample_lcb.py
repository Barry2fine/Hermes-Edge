"""Small-Sample Overfit & Empirical-Bernstein LCB Gate (Hard VETO).

Blocks player-prop projections driven by <10 games of hot shooting/scoring
variance when the Empirical-Bernstein Lower Confidence Bound (LCB) falls below
the sportsbook implied probability (`lcb_edge < 0` on `sample_games < 10`).
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


class SmallSampleOverfitBot(Bot):
    id = "small_sample_lcb"
    mandate = ("Veto small-sample (<10 games) player prop streaks that fail "
               "Empirical-Bernstein lower-confidence-bound (LCB) edge verification.")
    domains = ("sports",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        n_games = _num(ctx.get("sample_games"))
        model_p = _num(ctx.get("model_p"))
        market_p = _num(ctx.get("market_p"))

        if n_games is None or model_p is None or market_p is None:
            return Vote(self.id, "abstain", 0.0, ["no_sample_games_telemetry"], {})

        n = max(1.0, n_games)
        sigma = math.sqrt(max(0.01, model_p * (1.0 - model_p)) / n)
        lcb_p = model_p - 1.28 * sigma
        evidence = {"sample_games": n, "model_p": model_p, "market_p": market_p, "lcb_p": round(lcb_p, 4)}

        if n < 10.0 and lcb_p <= market_p:
            return Vote(
                self.id,
                "reject",
                0.90,
                [f"small_sample_lcb_fail:n={int(n)},lcb_p={lcb_p:.3f}<=market_p={market_p:.3f}"],
                evidence,
            )
        return Vote(self.id, "approve", 0.77, [f"sample_lcb_ok:n={int(n)},lcb_p={lcb_p:.3f}"], evidence)
