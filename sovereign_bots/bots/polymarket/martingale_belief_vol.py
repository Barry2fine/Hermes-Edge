"""Bounded Martingale Belief-Volatility & Overreaction Fade Seat (Soft Edge Seat).

Uses the bounded-martingale quadratic variation bound (`Var(dp) <= p*(1-p)`) to
distinguish unanchored retail overreaction spikes on Polymarket from fundamental
information jumps (`edge_seat = True`).
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


class MartingaleBeliefVolatilityBot(Bot):
    id = "martingale_belief_vol"
    mandate = ("Compare realized belief volatility against the bounded-martingale "
               "p*(1-p) ceiling to fade retail overreactions.")
    domains = ("polymarket",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        poly_p = _num(ctx.get("poly_prob") or ctx.get("market_p"))
        realized_move = _num(ctx.get("price_change_1h"))
        has_hard_news = bool(ctx.get("confirmed_catalyst", False))

        if poly_p is None or realized_move is None:
            return Vote(self.id, "abstain", 0.0, ["no_belief_volatility_inputs"], {})

        p = max(0.01, min(0.99, poly_p))
        martingale_sd = math.sqrt(p * (1.0 - p))
        excess_ratio = abs(realized_move) / max(0.05, martingale_sd)
        evidence = {"poly_p": p, "price_change_1h": realized_move, "martingale_sd": round(martingale_sd, 4), "excess_ratio": round(excess_ratio, 4)}

        if not has_hard_news and excess_ratio >= 0.35:
            return Vote(self.id, "approve", 0.77, [f"martingale_overreaction_fade:ratio={excess_ratio:.2f}"], evidence)
        return Vote(self.id, "abstain", 0.0, ["belief_volatility_normal"], evidence)
