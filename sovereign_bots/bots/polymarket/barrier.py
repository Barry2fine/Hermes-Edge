"""Barrier bot: is the modelled probability far enough above the market?

Edge is measured against the lower bound when the model gives a range, because
the trade has to survive a pessimistic model, not the point estimate.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

DEFAULT_FEE = 0.02
HURDLE = 0.02


def _num(value: Any) -> Optional[float]:
    """Best-effort float; None for missing/garbage/bool."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _prob(value: Any) -> Optional[float]:
    out = _num(value)
    if out is None or out < 0.0 or out > 1.0:
        return None
    return out


def _clip(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, x))


class BarrierBot(Bot):
    id = "barrier"
    edge_seat = True   # an approve means: model fair value must beat price + fees
    mandate = "Trade only when modelled fair value beats the market by more than fees and the hurdle."
    domains = ("polymarket",)
    veto = False

    hurdle = HURDLE

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        market_p = _prob(context.get("market_p"))
        point_p = _prob(context.get("model_p"))
        lower_p = _prob(context.get("lower_bound_p"))
        raw_fee = context.get("fee", DEFAULT_FEE)
        fee = _num(raw_fee)
        if fee is None:
            fee = DEFAULT_FEE
        fee = max(0.0, fee)

        if market_p is None or (point_p is None and lower_p is None):
            return Vote(self.id, "abstain", 0.0,
                        ["missing_price:need market_p and model_p/lower_bound_p"],
                        {"market_p": context.get("market_p"),
                         "model_p": context.get("model_p"),
                         "lower_bound_p": context.get("lower_bound_p")})

        # Pessimistic anchor: lower bound when present, else the point estimate.
        anchor = lower_p if lower_p is not None else point_p
        anchor_name = "lower_bound_p" if lower_p is not None else "model_p"
        edge = anchor - market_p
        net = edge - fee

        evidence = {
            "anchor": anchor_name,
            "anchor_p": round(anchor, 6),
            "market_p": round(market_p, 6),
            "model_p": None if point_p is None else round(point_p, 6),
            "lower_bound_p": None if lower_p is None else round(lower_p, 6),
            "fee": round(fee, 6),
            "gross_edge": round(edge, 6),
            "net_edge": round(net, 6),
            "hurdle": self.hurdle,
        }

        if net > self.hurdle:
            # Confidence rises with the size of the net edge, saturating at 0.95.
            conf = _clip(0.55 + (net - self.hurdle) * 3.0, 0.55, 0.95)
            return Vote(self.id, "approve", conf,
                        [f"net_edge {net:.3f} > hurdle {self.hurdle:.2f} on {anchor_name}"],
                        evidence)

        if net > 0.0:
            return Vote(self.id, "abstain", _clip(0.30 + net * 4.0, 0.30, 0.55),
                        [f"thin_edge {net:.3f} <= hurdle {self.hurdle:.2f}"],
                        evidence)

        # No edge after fees: an explicit reject (non-veto, so it blocks the trade
        # through the coordinator's normal reject path rather than a veto).
        conf = _clip(0.55 + abs(net) * 2.0, 0.55, 0.95)
        return Vote(self.id, "reject", conf,
                    [f"negative_edge {net:.3f} after {fee:.3f} fee"],
                    evidence)
