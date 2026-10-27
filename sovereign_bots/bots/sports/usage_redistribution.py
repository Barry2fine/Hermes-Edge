"""Teammate Injury Usage & Minute Redistribution Seat (Soft Edge Seat).

Models the non-linear surge in usage rate, touches, and minutes for secondary
creators when a high-usage starter is confirmed OUT before sportsbooks re-price
player props (`edge_seat = True`).
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


class InjuryUsageRedistributionBot(Bot):
    id = "usage_redistribution"
    mandate = ("Quantify usage-rate and minute redistribution to remaining starters "
               "when a primary teammate is ruled OUT.")
    domains = ("sports",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        usage_delta_pct = _num(ctx.get("projected_usage_delta_pct"))
        minutes_delta = _num(ctx.get("projected_minutes_delta"))
        side = str(ctx.get("side") or "over").lower()

        if usage_delta_pct is None and minutes_delta is None:
            return Vote(self.id, "abstain", 0.0, ["no_usage_redistribution_inputs"], {})

        u_d = usage_delta_pct or 0.0
        m_d = minutes_delta or 0.0
        evidence = {"projected_usage_delta_pct": u_d, "projected_minutes_delta": m_d, "side": side}

        if side == "over" and (u_d >= 3.0 or m_d >= 4.0):
            return Vote(self.id, "approve", 0.80, [f"usage_surge_over:+{u_d:.1f}%_usg,+{m_d:.1f}m"], evidence)
        if side == "under" and (u_d >= 3.0 or m_d >= 4.0):
            return Vote(self.id, "reject", 0.78, [f"usage_surge_opposes_under:+{u_d:.1f}%_usg"], evidence)
        return Vote(self.id, "abstain", 0.0, ["usage_redistribution_small"], evidence)
