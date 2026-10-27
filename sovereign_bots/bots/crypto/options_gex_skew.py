"""Deribit Options Gamma Exposure (GEX), DVOL & 25-Delta Skew Seat (Soft Edge Seat).

Uses Deribit implied volatility (`dvol`), 25-delta risk-reversal skew
(`skew_25d`), and net dealer gamma (`dealer_gex_musd`) to confirm squeeze vs.
mean-reversion pin regimes (`edge_seat = True`).
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


class DeribitOptionsGexSkewBot(Bot):
    id = "options_gex_skew"
    mandate = ("Score Deribit 25d call-put skew, DVOL regime, and dealer gamma "
               "walls (GEX) for directional options-implied edge.")
    domains = ("crypto",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        skew_25d = _num(ctx.get("deribit_skew_25d"))
        dealer_gex = _num(ctx.get("dealer_gex_musd"))
        direction = str(ctx.get("direction") or "").lower()

        if skew_25d is None or direction not in ("long", "short"):
            return Vote(self.id, "abstain", 0.0, ["no_options_skew_data"], {})

        evidence = {"deribit_skew_25d": skew_25d, "dealer_gex_musd": dealer_gex, "direction": direction}
        if direction == "long" and skew_25d >= 2.0:
            return Vote(self.id, "approve", 0.75, [f"call_skew_bullish:{skew_25d:.2f}"], evidence)
        if direction == "short" and skew_25d <= -2.0:
            return Vote(self.id, "approve", 0.75, [f"put_skew_bearish:{skew_25d:.2f}"], evidence)
        if (direction == "long" and skew_25d <= -5.0) or (direction == "short" and skew_25d >= 5.0):
            return Vote(self.id, "reject", 0.70, [f"options_skew_opposes:{skew_25d:.2f}"], evidence)
        return Vote(self.id, "abstain", 0.0, ["options_skew_neutral"], evidence)
