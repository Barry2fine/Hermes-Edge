"""Open Interest (OI) Delta & Liquidation Cascade Squeeze Seat (Soft Edge Seat).

Detects trapped levered longs/shorts via 15m-1h Open Interest expansion paired
with price absorption or cascade flush (`edge_seat = True`).
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


class LiquidationCascadeOIBot(Bot):
    id = "liquidation_oi"
    mandate = ("Detect OI-clean breakouts and post-liquidation-cascade squeeze "
               "setups from Open Interest delta and liquidation imbalance.")
    domains = ("crypto",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        oi_change_pct = _num(ctx.get("oi_change_1h_pct"))
        liq_imbalance = _num(ctx.get("liquidation_imbalance"))  # +1 = shorts liquidated, -1 = longs liquidated
        direction = str(ctx.get("direction") or "").lower()

        if oi_change_pct is None and liq_imbalance is None:
            return Vote(self.id, "abstain", 0.0, ["no_oi_or_liquidation_data"], {})

        evidence = {"oi_change_1h_pct": oi_change_pct, "liquidation_imbalance": liq_imbalance, "direction": direction}
        if liq_imbalance is not None:
            if direction == "long" and liq_imbalance >= 0.35:
                return Vote(self.id, "approve", 0.79, [f"short_squeeze_fuel:liq_imb={liq_imbalance:.2f}"], evidence)
            if direction == "short" and liq_imbalance <= -0.35:
                return Vote(self.id, "approve", 0.79, [f"long_flush_cascade:liq_imb={liq_imbalance:.2f}"], evidence)
        return Vote(self.id, "abstain", 0.0, ["oi_liquidation_neutral"], evidence)
