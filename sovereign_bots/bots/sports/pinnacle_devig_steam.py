"""Pinnacle / Circa Sharp De-Vig & Bet365 Boost EV Seat (Soft Edge Seat).

Computes multiplicative/Shin/Power de-vigged fair probability from Pinnacle/Circa
sharp consensus (`devig_power_method`, `devig_shin_method_two_way`), applies
bet365 Early Payout boost (`compute_bet365_early_payout_ev_boost`) when enabled,
and evaluates Quarter-Kelly EV against bet365 Ontario lines (`edge_seat = True`).
"""

from __future__ import annotations
from typing import Any, Dict, Optional
from ...core.bot import Bot, Vote
from ...core.domain_tables import (
    compute_bet365_early_payout_ev_boost,
    devig_power_method,
    devig_shin_method_two_way,
)


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

        sharp_dec_a = _num(ctx.get("pinnacle_decimal_odds") or ctx.get("sharp_decimal_a"))
        sharp_dec_b = _num(ctx.get("pinnacle_opp_decimal_odds") or ctx.get("sharp_decimal_b"))
        devig_method = str(ctx.get("devig_method") or "power").lower()
        if sharp_fair_p is None and sharp_dec_a is not None and sharp_dec_b is not None:
            if sharp_dec_a > 1.0 and sharp_dec_b > 1.0:
                if devig_method == "shin":
                    p_a, _, _ = devig_shin_method_two_way(sharp_dec_a, sharp_dec_b)
                    sharp_fair_p = float(p_a)
                else:
                    power_probs = devig_power_method([sharp_dec_a, sharp_dec_b])
                    sharp_fair_p = float(power_probs[0])

        if sharp_fair_p is None or soft_dec_odds is None or soft_dec_odds <= 1.0:
            return Vote(self.id, "abstain", 0.0, ["no_pinnacle_vs_soft_odds"], {})

        early_payout_sport = ctx.get("early_payout_sport")
        eff_fair_p = sharp_fair_p
        ep_boost = 0.0
        if isinstance(early_payout_sport, str) and early_payout_sport.strip():
            ep_res = compute_bet365_early_payout_ev_boost(
                sport=early_payout_sport,
                fair_win_prob=sharp_fair_p,
                offered_decimal_odds=soft_dec_odds,
            )
            eff_fair_p = float(ep_res["effective_win_prob"])
            ep_boost = float(ep_res["p_early_payout_rescue"])

        soft_implied_p = 1.0 / soft_dec_odds
        ev_roi = eff_fair_p * soft_dec_odds - 1.0
        edge_p = eff_fair_p - soft_implied_p
        evidence = {
            "pinnacle_fair_p": round(sharp_fair_p, 4),
            "effective_fair_p": round(eff_fair_p, 4),
            "early_payout_prob_boost": round(ep_boost, 4),
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
