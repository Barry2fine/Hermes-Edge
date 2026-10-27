"""Liquidity bot (VETO): can we get in and out without paying the spread?

Fail-closed on missing data. Thin books and wide spreads are non-negotiable
stops regardless of how good the edge looks.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

MIN_LIQUIDITY = 10_000.0
MIN_VOLUME = 5_000.0
MAX_SPREAD_BPS = 200.0


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


class LiquidityBot(Bot):
    id = "liquidity"
    mandate = "Refuse any book under $10k liquidity, $5k volume or 200bps spread."
    domains = ("polymarket",)
    veto = True

    min_liquidity = MIN_LIQUIDITY
    min_volume = MIN_VOLUME
    max_spread_bps = MAX_SPREAD_BPS

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        # Floors may be tightened by the context, never loosened below the class floor
        # unless an explicit override is supplied.
        min_liq = _num(context.get("min_liquidity"))
        min_vol = _num(context.get("min_volume"))
        max_bps = _num(context.get("max_spread_bps"))
        min_liq = self.min_liquidity if min_liq is None else max(0.0, min_liq)
        min_vol = self.min_volume if min_vol is None else max(0.0, min_vol)
        max_bps = self.max_spread_bps if max_bps is None else max(0.0, max_bps)

        liquidity = _num(context.get("liquidity"))
        volume = _num(context.get("volume"))
        spread = _num(context.get("spread_bps"))

        evidence = {
            "liquidity": liquidity,
            "volume": volume,
            "spread_bps": spread,
            "floors": {"liquidity": min_liq, "volume": min_vol, "spread_bps": max_bps},
        }

        missing = [k for k, v in (("liquidity", liquidity), ("volume", volume),
                                  ("spread_bps", spread)) if v is None]
        if missing:
            return Vote(self.id, "reject", 0.90,
                        ["missing_data:" + ",".join(missing)], evidence)

        reasons = []
        if liquidity < min_liq:
            reasons.append(f"illiquid:{liquidity:.0f}<{min_liq:.0f}")
        if volume < min_vol:
            reasons.append(f"thin_volume:{volume:.0f}<{min_vol:.0f}")
        if spread > max_bps:
            reasons.append(f"wide_spread:{spread:.0f}bps>{max_bps:.0f}bps")
        if reasons:
            return Vote(self.id, "reject", 0.92, reasons, evidence)

        depth = min(liquidity / min_liq if min_liq else 99.0,
                    volume / min_vol if min_vol else 99.0)
        spread_headroom = 1.0 - (spread / max_bps if max_bps else 0.0)
        conf = 0.60 + 0.20 * min(1.0, max(0.0, depth - 1.0)) + 0.15 * max(0.0, spread_headroom)
        conf = max(0.0, min(0.95, conf))
        evidence.update({"depth_ratio": round(depth, 3),
                         "spread_headroom": round(spread_headroom, 3)})
        return Vote(self.id, "approve", conf,
                    [f"depth_ratio={depth:.2f}", f"spread={spread:.0f}bps"], evidence)
