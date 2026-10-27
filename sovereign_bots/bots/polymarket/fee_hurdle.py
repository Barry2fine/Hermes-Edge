"""Fee-hurdle bot — some contracts are structurally untradeable, not merely bad.

A flat per-contract fee is brutal at the extremes. With a 2¢ fee per side:

    price 0.02 -> fee is 100% of stake     (you cannot win)
    price 0.10 -> fee is  20% of stake
    price 0.50 -> fee is   4% of stake
    price 0.90 -> fee is  2.2% of stake

So a cheap longshot is not "mispriced", it is unpriceable: the toll exceeds any
conceivable edge. This bot exists to say NO on arithmetic alone, before anyone
starts arguing about the model.

It also annualises: a 1¢ edge on a 90-day lock-up is ~4%/yr, which is a bad use
of capital compared with a 1¢ edge on a 3-day contract (~40%/yr).
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

DEFAULT_FEE = 0.02
MIN_EDGE = 0.005          # never trade below half a cent of true edge
MIN_ANNUAL = 0.10         # 10%/yr hurdle on locked capital
MAX_COST = 0.98           # above this there is no upside left


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def fee_math(price: float, fee: float = DEFAULT_FEE,
             model_p: Optional[float] = None, days: Optional[float] = None) -> Dict[str, Any]:
    cost = price + fee
    fee_share = (fee / cost) if cost > 0 else None
    edge = None if model_p is None else model_p - cost
    annual = None
    if edge is not None and days and days > 0 and cost > 0:
        annual = edge / cost * (365.0 / days)
    return {"cost": round(cost, 6), "fee_share": round(fee_share, 6) if fee_share else None,
            "edge": round(edge, 6) if edge is not None else None,
            "annualised": round(annual, 6) if annual is not None else None,
            "taxed_out": cost >= MAX_COST}


class FeeHurdleBot(Bot):
    id = "fee_hurdle"
    edge_seat = True   # an approve means: edge must survive the fee hurdle
    mandate = ("Refuse anything the fee has already eaten: no trade under half a "
               "cent of true edge, none above 98¢ cost, and none that cannot clear "
               "10%/yr on locked capital.")
    domains = ("polymarket",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        price = _num(ctx.get("price") if ctx.get("price") is not None else ctx.get("ask"))
        fee = _num(ctx.get("fee"))
        fee = DEFAULT_FEE if fee is None else max(0.0, fee)
        model_p = _num(ctx.get("model_p"))
        days = _num(ctx.get("days_to_resolve"))

        evidence: Dict[str, Any] = {"price": price, "fee": fee,
                                    "model_p": model_p, "days": days}
        if price is None or not (0.0 < price < 1.0):
            return Vote(self.id, "reject", 0.9, ["missing_or_invalid_price"], evidence)
        if model_p is not None and not (0.0 < model_p < 1.0):
            return Vote(self.id, "reject", 0.9, ["invalid_model_p"], evidence)

        m = fee_math(price, fee, model_p, days)
        evidence.update(m)

        if m["taxed_out"]:
            return Vote(self.id, "reject", 0.95,
                        [f"no_upside:cost={m['cost']:.3f}>= {MAX_COST}"], evidence)
        if m["edge"] is None:
            # No model probability: still enforce the fee share as a sanity cap.
            if m["fee_share"] and m["fee_share"] > 0.20:
                return Vote(self.id, "reject", 0.80,
                            [f"fee_share:{m['fee_share']:.0%}>20%"], evidence)
            return Vote(self.id, "abstain", 0.0, ["missing_model_p"], evidence)
        if m["edge"] < MIN_EDGE:
            return Vote(self.id, "reject", 0.90,
                        [f"sub_hurdle_edge:{m['edge']:+.4f}<{MIN_EDGE}"], evidence)
        if m["annualised"] is not None and m["annualised"] < MIN_ANNUAL:
            return Vote(self.id, "reject", 0.75,
                        [f"low_annualised:{m['annualised']:.1%}<{MIN_ANNUAL:.0%}",
                         f"edge={m['edge']:+.4f} over {days:g}d"], evidence)

        conf = min(0.90, 0.50 + min(0.40, m["edge"] * 4))
        return Vote(self.id, "approve", conf,
                    [f"edge={m['edge']:+.4f}", f"fee_share={m['fee_share']:.1%}",
                     f"annualised={m['annualised']:.0%}" if m["annualised"] is not None else "annualised=n/a"],
                    evidence)
