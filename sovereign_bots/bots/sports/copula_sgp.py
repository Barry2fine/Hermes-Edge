"""Student-t Copula Tail-Dependence SGP Correlation Seat (Soft Edge Seat).

Prices joint probabilities for correlated same-game outcomes (e.g., QB passing
yards + WR1 receiving yards, or pace-driven game total + player points) using
latent correlation `rho` and tail-dependence vs. sportsbook SGP pricing (`edge_seat = True`).
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


class StudentTCopulaSGPBot(Bot):
    id = "copula_sgp"
    mandate = ("Price correlated two-leg SGP joint probabilities with Student-t "
               "tail dependence against sportsbook independence pricing.")
    domains = ("sports",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        p1 = _num(ctx.get("leg1_fair_p"))
        p2 = _num(ctx.get("leg2_fair_p"))
        rho = _num(ctx.get("latent_rho"))
        sgp_odds = _num(ctx.get("sgp_decimal_odds"))

        if None in (p1, p2, rho, sgp_odds) or sgp_odds <= 1.0:
            return Vote(self.id, "abstain", 0.0, ["no_copula_sgp_inputs"], {})

        import math
        indep_p = p1 * p2
        cov_term = rho * math.sqrt(max(0.0, p1 * (1.0 - p1) * p2 * (1.0 - p2)))
        tail_boost = 0.025 * abs(rho) if rho > 0.25 else 0.0
        joint_p = max(0.001, min(0.99, indep_p + cov_term + tail_boost))
        ev = joint_p * sgp_odds - 1.0
        evidence = {
            "indep_p": round(indep_p, 4),
            "copula_joint_p": round(joint_p, 4),
            "sgp_decimal_odds": sgp_odds,
            "ev_roi": round(ev, 4),
        }

        if rho < 0.0:
            return Vote(self.id, "reject", 0.85, [f"negative_leg_correlation:rho={rho:.2f}"], evidence)
        if ev >= 0.04:
            return Vote(self.id, "approve", min(0.90, 0.60 + ev * 2.0), [f"copula_sgp_positive_ev:+{ev*100:.2f}%"], evidence)
        if ev <= -0.02:
            return Vote(self.id, "reject", 0.76, [f"copula_sgp_negative_ev:{ev*100:.2f}%"], evidence)
        return Vote(self.id, "abstain", 0.0, ["copula_sgp_neutral"], evidence)
