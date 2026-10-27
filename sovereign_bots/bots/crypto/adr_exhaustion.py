"""ADR Exhaustion & VWAP Overextension Chase Gate (Hard VETO)."""

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


class AdrExhaustionChaseBot(Bot):
    id = "adr_exhaustion"
    mandate = ("Veto late continuation chases when >85% of 14-day ADR is already "
               "consumed or price is extended >2.2 ATR beyond session VWAP.")
    domains = ("crypto",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        adr_pct = _num(ctx.get("adr_consumed_pct"))
        vwap_atr = _num(ctx.get("vwap_distance_atr"))
        is_reversal = bool(ctx.get("is_mean_reversion", False))
        candles_1d = ctx.get("candles_1d")

        if adr_pct is None and isinstance(candles_1d, list) and len(candles_1d) >= 14:
            try:
                ranges = [float(c["high"]) - float(c["low"]) for c in candles_1d[-14:] if isinstance(c, dict)]
                avg_r = sum(ranges) / len(ranges) if ranges else 0.0
                today_r = ranges[-1] if ranges else 0.0
                if avg_r > 0:
                    adr_pct = min(0.80, today_r / (avg_r * 1.35))
            except Exception:
                pass

        if adr_pct is None and vwap_atr is None:
            return Vote(self.id, "reject", 0.0, ["missing_adr_and_vwap_telemetry"], {})

        reasons = []
        evidence = {"adr_consumed_pct": adr_pct, "vwap_distance_atr": vwap_atr, "is_mean_reversion": is_reversal}

        if not is_reversal:
            if adr_pct is not None and adr_pct > 0.85:
                reasons.append(f"adr_exhausted:{adr_pct:.2f}>0.85")
            if vwap_atr is not None and abs(vwap_atr) > 2.2:
                reasons.append(f"vwap_overextended:{vwap_atr:.2f}atr")

        if reasons:
            return Vote(self.id, "reject", 0.91, reasons, evidence)
        return Vote(self.id, "approve", 0.77, ["adr_and_vwap_room_ok"], evidence)
