"""Liquidity gate: thin books are where honest prices go to die.

Below the floor (or with no measurable depth) the market is untradeable and this
veto bot refuses. Above it the bot reports a spread proxy so downstream sizing
knows what the fill will cost.
"""

from __future__ import annotations

from math import sqrt
from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote


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


def spread_proxy_bps(liquidity: float) -> float:
    """Depth -> implied half-spread proxy in bps (monotone decreasing in depth).

    Calibrated so ~$5k depth implies a punitive ~270bps, $50k ~85bps and
    $500k ~27bps. It is a proxy for cost, not a quote.
    """
    if liquidity is None:
        return float("inf")
    try:
        depth = float(liquidity)
    except (TypeError, ValueError):
        return float("inf")
    if depth <= 0.0:
        return float("inf")
    raw = 600.0 / sqrt(depth / 1000.0)
    return max(1.0, min(500.0, raw))


class LiquidityBot(Bot):
    """Veto illiquid or unmeasured markets."""

    id = "liquidity"
    mandate = (
        "Veto any market with less than $5k of measurable liquidity; report an "
        "implied spread proxy so thin-but-tradeable books are priced honestly."
    )
    domains = ("sports",)
    veto = True

    FLOOR_USD = 5000.0
    CAP = 0.95

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        raw = ctx.get("liquidity")
        liquidity = _num(raw)
        evidence = {
            "liquidity": raw,
            "floor_usd": self.FLOOR_USD,
            "spread_proxy_bps": None,
        }

        if liquidity is None:
            evidence["spread_proxy_bps"] = None
            return Vote(
                self.id, "reject", 0.80,
                ["liquidity missing/unparseable: fail closed"],
                evidence,
            )

        if liquidity <= 0.0:
            return Vote(
                self.id, "reject", 0.90,
                [f"liquidity {liquidity:,.0f} is non-positive: no book"],
                evidence,
            )

        bps = spread_proxy_bps(liquidity)
        evidence["spread_proxy_bps"] = round(bps, 2)

        if liquidity < self.FLOOR_USD:
            return Vote(
                self.id, "reject", 0.90,
                [f"liquidity ${liquidity:,.0f} < ${self.FLOOR_USD:,.0f} floor "
                 f"(spread proxy {bps:.0f}bps)"],
                evidence,
            )

        quality = liquidity / (liquidity + 25000.0)
        conf = min(self.CAP, 0.40 + 0.55 * quality)
        return Vote(
            self.id, "approve", conf,
            [f"liquidity ${liquidity:,.0f} clears floor (spread proxy {bps:.0f}bps)"],
            evidence,
        )
