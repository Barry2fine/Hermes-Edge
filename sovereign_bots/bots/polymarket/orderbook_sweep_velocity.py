"""CLOB Taker Sweep Velocity & Informed Orderflow Imbalance Seat (Soft Edge Seat).

Measures net aggressive CLOB taker sweep volume (`clob_net_sweep_usd`) and book
replenishment asymmetry on `clob.polymarket.com` (`edge_seat = True`).
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


class SmartMoneyOrderbookSweepBot(Bot):
    id = "orderbook_sweep_velocity"
    mandate = ("Track aggressive CLOB taker sweep velocity and L2 depth "
               "imbalance on Polymarket order books.")
    domains = ("polymarket",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        net_sweep_usd = _num(ctx.get("clob_net_sweep_usd"))
        book_imb = _num(ctx.get("clob_book_imbalance"))

        if net_sweep_usd is None and book_imb is None:
            return Vote(self.id, "abstain", 0.0, ["no_clob_sweep_telemetry"], {})

        sweep = net_sweep_usd or 0.0
        imb = book_imb or 0.0
        evidence = {"clob_net_sweep_usd": sweep, "clob_book_imbalance": imb}

        if sweep >= 5000.0 and imb >= 0.25:
            return Vote(self.id, "approve", 0.81, [f"informed_clob_sweep:+${sweep:.0f},imb={imb:.2f}"], evidence)
        if sweep <= -5000.0 and imb <= -0.25:
            return Vote(self.id, "reject", 0.79, [f"opposing_clob_sweep:-${abs(sweep):.0f}"], evidence)
        return Vote(self.id, "abstain", 0.0, ["clob_sweep_neutral"], evidence)
