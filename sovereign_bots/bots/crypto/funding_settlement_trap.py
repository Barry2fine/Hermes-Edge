"""Funding Settlement Trap & Fee-to-Stop Ratio Gate (Hard VETO)."""

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


class FundingSettlementTrapBot(Bot):
    id = "funding_settlement_trap"
    mandate = ("Block entries into imminent adverse funding settlements (<=15m) "
               "or where round-trip fee+spread exceeds 25% of stop distance.")
    domains = ("crypto",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        direction = str(ctx.get("direction") or "").lower()
        entry = _num(ctx.get("entry"))
        stop = _num(ctx.get("stop"))
        mins_to_funding = _num(ctx.get("minutes_to_funding"))
        funding_bps = _num(ctx.get("funding_rate_bps"))
        if funding_bps is None and _num(ctx.get("funding")) is not None:
            funding_bps = float(ctx["funding"]) * 10000.0
        stop_bps = _num(ctx.get("stop_distance_bps"))
        if stop_bps is None and entry and stop and entry > 0:
            stop_bps = abs(entry - stop) / entry * 10000.0
        rt_cost_bps = _num(ctx.get("round_trip_cost_bps"))
        if rt_cost_bps is None and entry and entry > 0 and _num(ctx.get("fees_per_unit")) is not None:
            spread_bps = _num(ctx.get("spread_bps")) or 0.0
            rt_cost_bps = (float(ctx["fees_per_unit"]) / entry) * 10000.0 + spread_bps
        fee_to_stop = _num(ctx.get("fee_to_stop_ratio"))

        if direction not in ("long", "short") and all(v is None for v in (mins_to_funding, stop_bps, rt_cost_bps, fee_to_stop)):
            return Vote(self.id, "reject", 0.0, ["missing_funding_and_fee_inputs"], {})

        reasons = []
        evidence: Dict[str, Any] = {}

        if mins_to_funding is not None and funding_bps is not None:
            adverse_bps = funding_bps if direction == "long" else (-funding_bps if direction == "short" else 0.0)
            evidence["minutes_to_funding"] = mins_to_funding
            evidence["adverse_funding_bps"] = adverse_bps
            if 0.0 <= mins_to_funding <= 15.0 and adverse_bps > 3.5:
                reasons.append(f"imminent_funding_trap:{mins_to_funding:.1f}m,adverse_bps={adverse_bps:.2f}")

        if fee_to_stop is None and stop_bps is not None and stop_bps > 0 and rt_cost_bps is not None:
            fee_to_stop = rt_cost_bps / stop_bps

        if fee_to_stop is not None:
            evidence["fee_to_stop_ratio"] = round(fee_to_stop, 4)
            if fee_to_stop > 0.25:
                reasons.append(f"fee_drag_exceeds_quarter_R:ratio={fee_to_stop:.3f}>0.25")

        if reasons:
            return Vote(self.id, "reject", 0.92, reasons, evidence)
        return Vote(self.id, "approve", 0.78, ["funding_and_fee_ratio_clear"], evidence)
