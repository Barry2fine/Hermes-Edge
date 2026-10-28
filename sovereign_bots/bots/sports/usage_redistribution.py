"""Teammate Injury Usage & Minute Redistribution Seat (Soft Edge Seat).

Models the non-linear surge in usage rate, touches, and minutes for secondary
creators when a high-usage starter is confirmed OUT before sportsbooks re-price
player props (`project_teammate_prop_under_star_absence`, `edge_seat = True`).
"""

from __future__ import annotations
from typing import Any, Dict, Optional
from ...core.bot import Bot, Vote
from ...core.domain_tables import project_teammate_prop_under_star_absence


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

        base_rate = _num(ctx.get("baseline_per_minute_rate"))
        base_mins = _num(ctx.get("baseline_minutes"))
        min_boost = _num(ctx.get("minutes_boost"))
        role_mult = _num(ctx.get("usage_or_role_multiplier"))
        milestone = _num(ctx.get("milestone_target"))
        if usage_delta_pct is None and None not in (base_rate, base_mins, min_boost, role_mult, milestone):
            proj = project_teammate_prop_under_star_absence(
                baseline_per_minute_rate=base_rate,
                baseline_minutes=base_mins,
                minutes_boost=min_boost,
                usage_or_role_multiplier=role_mult,
                stat_category=str(ctx.get("stat_category") or "assists"),
                milestone_target=int(milestone),
                offered_decimal_odds=float(_num(ctx.get("decimal_odds")) or 1.91),
            )
            usage_delta_pct = (role_mult - 1.0) * 100.0
            minutes_delta = float(proj["projected_minutes"]) - base_mins

        if usage_delta_pct is None and minutes_delta is None:
            return Vote(self.id, "abstain", 0.0, ["no_usage_redistribution_inputs"], {})

        u_d = usage_delta_pct or 0.0
        m_d = minutes_delta or 0.0
        evidence = {"projected_usage_delta_pct": round(u_d, 3), "projected_minutes_delta": round(m_d, 3), "side": side}

        if side == "over" and (u_d >= 3.0 or m_d >= 4.0):
            return Vote(self.id, "approve", 0.80, [f"usage_surge_over:+{u_d:.1f}%_usg,+{m_d:.1f}m"], evidence)
        if side == "under" and (u_d >= 3.0 or m_d >= 4.0):
            return Vote(self.id, "reject", 0.78, [f"usage_surge_opposes_under:+{u_d:.1f}%_usg"], evidence)
        return Vote(self.id, "abstain", 0.0, ["usage_redistribution_small"], evidence)
