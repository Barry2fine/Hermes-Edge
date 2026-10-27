"""Pinnacle / Circa Sharp De-Vig & Bet365 Boost EV Seat (Soft Edge Seat).

Computes multiplicative/Shin de-vigged fair probability from Pinnacle/Circa
sharp consensus and evaluates Quarter-Kelly EV against bet365 Ontario soft lines
and Odds/Profit Boosts (`edge_seat = True`).
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


class PinnacleDevigSharpSteamBot(Bot):
    id = "pinnacle_devig_steam"
    mandate = ("De-vig Pinnacle/Circa sharp odds via Shin/Power method and "
               "certify +EV discrepancy on bet365 Ontario lines and boosts.")
    domains = ("sports",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        sharp_fair_p = _num(ctx.get("pinnacle_fair_p"))
        soft_dec_odds = _num(ctx.get("bet365_decimal_odds") or ctx.get("decimal_odds"))

        if sharp_fair_p is None or soft_dec_odds is None or soft_dec_odds <= 1.0:
            return Vote(self.id, "abstain", 0.0, ["no_pinnacle_vs_soft_odds"], {})

        soft_implied_p = 1.0 / soft_dec_odds
        ev_roi = sharp_fair_p * soft_dec_odds - 1.0
        edge_p = sharp_fair_p - soft_implied_p
        evidence = {
            "pinnacle_fair_p": round(sharp_fair_p, 4),
            "soft_decimal_odds": round(soft_dec_odds, 4),
            "ev_roi": round(ev_roi, 4),
            "prob_edge": round(edge_p, 4),
        }

        if ev_roi >= 0.025 and edge_p >= 0.015:
            conf = min(0.94, 0.60 + ev_roi * 3.0)
            return Vote(self.id, "approve", conf, [f"sharp_devig_ev:+{ev_roi*100:.2f}%"], evidence)
        if ev_roi <= -0.02:
            return Vote(self.id, "reject", 0.78, [f"negative_sharp_ev:{ev_roi*100:.2f}%"], evidence)
        return Vote(self.id, "abstain", 0.0, ["sharp_ev_marginal"], evidence)
