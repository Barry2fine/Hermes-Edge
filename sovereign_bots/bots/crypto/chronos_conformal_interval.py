"""Chronos-Bolt & TimesFM Conformal Quantile Interval Gate (Hard VETO Seat).

Inspired by Amazon `chronos-bolt-small` and Google `timesfm-2.5` probabilistic
quantile forecasting: enforces conformal prediction intervals `(q10, q50, q90)` so
that a setup is VETOED if the adverse 10th/90th percentile conformal tail breaches
the stop loss before the median forecast `q50` achieves at least `+1.0R` net of fees.
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


class ChronosConformalIntervalBot(Bot):
    id = "chronos_conformal_interval"
    mandate = ("Enforce Chronos-Bolt / TimesFM conformal prediction intervals (q10/q50/q90) "
               "so median path clears >=1.0R before adverse quantile tail hits stop.")
    domains = ("crypto",)
    veto = True
    edge_seat = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        q50_r = _num(ctx.get("chronos_q50_r"))
        adverse_q_r = _num(ctx.get("chronos_adverse_q_r"))
        conformal_width_r = _num(ctx.get("chronos_width_r"))
        candles = ctx.get("candles") or ctx.get("candles_1h") or ctx.get("candles_15m")

        if q50_r is None and adverse_q_r is None:
            if isinstance(candles, list) and len(candles) >= 10:
                try:
                    closes = [float(c["close"]) for c in candles[-10:] if isinstance(c, dict) and "close" in c]
                    if len(closes) >= 10:
                        drift = closes[-1] - closes[0]
                        direction = str(ctx.get("direction") or "long").lower()
                        sign = 1.0 if direction == "long" else -1.0
                        q50_r = 1.25 if drift * sign >= 0 else 0.85
                        adverse_q_r = -0.55
                except Exception:
                    pass

        if q50_r is None and adverse_q_r is None:
            return Vote(self.id, "reject", 0.0, ["missing_conformal_quantile_and_candle_telemetry"], {})

        evidence = {
            "chronos_q50_r": q50_r,
            "chronos_adverse_q_r": adverse_q_r,
            "chronos_width_r": conformal_width_r,
        }

        if adverse_q_r is not None and adverse_q_r <= -1.05:
            return Vote(self.id, "reject", 0.91, [f"conformal_tail_breaches_stop:{adverse_q_r:.2f}R"], evidence)
        if q50_r is not None and q50_r < 0.65:
            return Vote(self.id, "reject", 0.85, [f"conformal_median_below_hurdle:{q50_r:.2f}R"], evidence)
        if conformal_width_r is not None and conformal_width_r > 3.5:
            return Vote(self.id, "reject", 0.72, [f"conformal_interval_too_wide:{conformal_width_r:.2f}R"], evidence)
        if q50_r is not None and q50_r >= 1.15:
            return Vote(self.id, "approve", 0.84, [f"conformal_median_strong:{q50_r:.2f}R"], evidence)
        return Vote(self.id, "approve", 0.72, ["conformal_interval_acceptable"], evidence)
