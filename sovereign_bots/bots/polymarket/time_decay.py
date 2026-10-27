"""Time-decay bot: near expiry, a 0.9+ price is mostly carry, not edge.

Non-veto. Late-stage tails abstain (reason "late_stage_tail") rather than
block: the barrier and liquidity bots already police the economics.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

TAIL_P = 0.85
NEAR_DAYS = 3.0


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


class TimeDecayBot(Bot):
    id = "time_decay"
    mandate = "Avoid paying up for near-expiry tails where the remaining upside is a few cents."
    domains = ("polymarket",)
    veto = False

    tail_p = TAIL_P
    near_days = NEAR_DAYS

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        days = _num(context.get("days_to_resolve"))
        market_p = _num(context.get("market_p"))

        evidence = {"days_to_resolve": days, "market_p": market_p,
                    "tail_p": self.tail_p, "near_days": self.near_days}

        if days is None or market_p is None:
            return Vote(self.id, "abstain", 0.0,
                        ["insufficient_data:days_to_resolve,market_p"], evidence)

        if days <= self.near_days and market_p > self.tail_p:
            upside = max(0.0, 1.0 - market_p)
            evidence["remaining_upside"] = round(upside, 4)
            return Vote(self.id, "abstain", 0.35,
                        ["late_stage_tail",
                         f"days={days:.1f}<= {self.near_days:.0f} and p={market_p:.3f}>{self.tail_p:.2f}",
                         f"remaining_upside={upside:.3f}"], evidence)

        if days <= self.near_days:
            conf = 0.60
            reasons = [f"near_expiry_without_tail:p={market_p:.3f}"]
        else:
            # Longer runway -> the price has room to move, which is what we want.
            conf = min(0.85, 0.60 + 0.01 * min(days, 20.0))
            reasons = [f"runway_days={days:.1f}"]

        evidence["late_stage_tail"] = False
        return Vote(self.id, "approve", conf, reasons, evidence)
