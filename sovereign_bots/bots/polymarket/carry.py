"""Carry bot — money locked until resolution is money you cannot use.

A contract that pays $1 in 365 days is not worth $1 today. With rate r the fair
midpoint is shrunk toward 50¢:

    d      = exp(-r * T/365)
    fair_yes = q * d          fair_no = (1-q) * d
    max midpoint shift = (1 - d)/2

At r=5% over 365 days that is up to **2.4¢** — larger than most edges. So part
of what looks like "longshots are cheap / favourites are dear" is simply the
price of money.

This bot refuses to pay above fair-on-carry plus fee, and never treats the
carry residual as alpha.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

DEFAULT_RATE = 0.05
DEFAULT_FEE = 0.02


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def carry(price: float, days: float, rate: float = DEFAULT_RATE) -> Dict[str, Any]:
    d = math.exp(-rate * days / 365.0)
    fair = price * d
    shift = (1.0 - d) / 2.0
    return {"discount": round(d, 6), "fair": round(fair, 6),
            "max_mid_shift": round(shift, 6),
            "carry_gap": round(price - fair, 6)}


class CarryBot(Bot):
    id = "carry"
    edge_seat = True   # an approve means: price must beat fair-on-carry + fee
    mandate = ("Charge for time: money locked to resolution is not free, and the "
               "price must beat fair-on-carry plus fee before it counts as edge.")
    domains = ("polymarket",)
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        price = _num(ctx.get("price") if ctx.get("price") is not None else ctx.get("ask"))
        days = _num(ctx.get("days_to_resolve"))
        rate = _num(ctx.get("rate"))
        rate = DEFAULT_RATE if rate is None else max(0.0, rate)
        fee = _num(ctx.get("fee"))
        fee = DEFAULT_FEE if fee is None else max(0.0, fee)
        model_p = _num(ctx.get("model_p"))

        evidence: Dict[str, Any] = {"price": price, "days": days, "rate": rate, "fee": fee}
        if price is None or not (0.0 < price < 1.0):
            return Vote(self.id, "abstain", 0.0, ["missing_price"], evidence)
        if days is None or days < 0:
            return Vote(self.id, "abstain", 0.0, ["missing_days"], evidence)

        c = carry(price, days, rate)
        evidence.update(c)

        # Paying more than fair-on-carry + fee is overpaying for time.
        ceiling = c["fair"] + fee
        if price > ceiling + 1e-9:
            return Vote(self.id, "reject", 0.75,
                        [f"overpaying_carry:{price:.3f}>{ceiling:.3f}",
                         f"carry_gap={c['carry_gap']:.4f} over {days:g}d"], evidence)

        if model_p is not None and 0.0 < model_p < 1.0:
            edge = model_p - ceiling
            evidence["edge_vs_carry"] = round(edge, 6)
            if edge > 0.02:
                return Vote(self.id, "approve", min(0.80, 0.50 + edge * 3),
                            [f"edge_vs_carry:{edge:+.4f}"], evidence)
            if edge < 0:
                return Vote(self.id, "reject", 0.70,
                            [f"negative_vs_carry:{edge:+.4f}"], evidence)
            return Vote(self.id, "abstain", 0.25, [f"thin_vs_carry:{edge:+.4f}"], evidence)

        # No model probability: carry alone is never a reason to trade.
        return Vote(self.id, "abstain", 0.20,
                    [f"carry_only_no_edge:{c['carry_gap']:.4f}"], evidence)
