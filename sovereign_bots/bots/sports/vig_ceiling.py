"""Sportsbook Juice & Vig Ceiling Gate (Hard VETO).

Vetoes any bet365 Ontario player prop or main-line wager where:
1. Two-way implied hold/vig exceeds 6.5% (`book_hold_pct > 0.065`) without an
   offsetting Pinnacle de-vigged edge >= 3.5%, OR
2. Raw American juice is worse than -135 (`decimal_odds < 1.74`) on a unboosted
   prop, preventing high-vig negative-EV grind.
"""

from __future__ import annotations
from typing import Any, Dict, Optional
from ...core.bot import Bot, Vote


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class JuiceAndVigCeilingBot(Bot):
    id = "vig_ceiling"
    mandate = ("Veto sports props with book hold > 6.5% or raw juice worse than "
               "-135 (decimal < 1.74) unless sharp de-vig edge >= 3.5%.")
    domains = ("sports",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        hold = _num(ctx.get("book_hold_pct"))
        dec_odds = _num(ctx.get("decimal_odds"))
        sharp_edge = _num(ctx.get("sharp_devig_edge")) or 0.0

        if hold is None and dec_odds is None:
            return Vote(self.id, "abstain", 0.0, ["no_vig_or_odds_telemetry"], {})

        reasons = []
        evidence = {"book_hold_pct": hold, "decimal_odds": dec_odds, "sharp_devig_edge": sharp_edge}

        if hold is not None and hold > 0.065 and sharp_edge < 0.035:
            reasons.append(f"excessive_book_vig:{hold:.3f}>0.065")
        if dec_odds is not None and dec_odds < 1.74 and sharp_edge < 0.035:
            reasons.append(f"heavy_juice_below_1.74:odds={dec_odds:.3f}")

        if reasons:
            return Vote(self.id, "reject", 0.91, reasons, evidence)
        return Vote(self.id, "approve", 0.78, ["vig_and_juice_within_cap"], evidence)
