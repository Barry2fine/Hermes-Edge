"""3-Bar Candlestick Geometry Tensor & Volume Effort-vs-Result Seat (Soft Edge Seat).

Encodes each candle as `(body_to_range, upper_wick_ratio, lower_wick_ratio,
rel_volume_z)` and evaluates 3-bar Markov transition geometry + Wyckoff volume
effort-vs-result (`edge_seat = True`).
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


class CandlestickGeometryTensorBot(Bot):
    id = "candle_geometry_tensor"
    mandate = ("Evaluate 3-bar candlestick body/wick geometry tensor and volume "
               "effort-vs-result displacement quality.")
    domains = ("crypto",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        tensor_score = _num(ctx.get("candle_tensor_score"))  # [-1.0, +1.0] signed displacement quality
        rel_vol = _num(ctx.get("relative_volume"))
        direction = str(ctx.get("direction") or "").lower()

        if tensor_score is None or direction not in ("long", "short"):
            return Vote(self.id, "abstain", 0.0, ["no_candle_tensor_inputs"], {})

        sign = 1.0 if direction == "long" else -1.0
        aligned = tensor_score * sign
        vol_mult = 1.15 if (rel_vol is not None and rel_vol >= 1.3) else 1.0
        effective = aligned * vol_mult
        evidence = {"candle_tensor_score": tensor_score, "relative_volume": rel_vol, "effective_score": round(effective, 4)}

        if effective >= 0.45:
            return Vote(self.id, "approve", min(0.90, 0.55 + effective * 0.35), [f"strong_displacement_geometry:{effective:.2f}"], evidence)
        if effective <= -0.45:
            return Vote(self.id, "reject", 0.74, [f"opposing_wick_rejection_geometry:{effective:.2f}"], evidence)
        return Vote(self.id, "abstain", 0.0, ["geometry_inconclusive"], evidence)
