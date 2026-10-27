"""Hugging Face Kronos K-Line Hierarchical Token Quantizer Seat (Soft Edge Seat).

Inspired by `NeoQuasar/Kronos` (arXiv:2508.02739 — Foundation Model for Financial
Candlesticks trained on 45 exchanges): quantizes continuous OHLCV sequences into
hierarchical discrete K-line tokens `(body_z, wick_asymmetry, vol_expansion_z,
transition_entropy)` and blocks/downweights high-entropy distribution tokens while
boosting low-entropy institutional displacement tokens (`edge_seat = True`).
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


class KronosKlineQuantizerBot(Bot):
    id = "kronos_kline_quantizer"
    mandate = ("Quantize OHLCV sequences into Kronos-style hierarchical K-line tokens "
               "and evaluate token transition entropy vs directional displacement.")
    domains = ("crypto",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        token_score = _num(ctx.get("kronos_token_score"))
        entropy = _num(ctx.get("kronos_token_entropy"))
        direction = str(ctx.get("direction") or "").lower()

        if token_score is None or direction not in ("long", "short"):
            return Vote(self.id, "abstain", 0.0, ["no_kronos_kline_inputs"], {})

        sign = 1.0 if direction == "long" else -1.0
        aligned = token_score * sign
        ent = entropy if entropy is not None else 0.35
        evidence = {
            "kronos_token_score": token_score,
            "kronos_token_entropy": ent,
            "aligned_score": round(aligned, 4),
        }

        if ent >= 0.78:
            return Vote(self.id, "reject", 0.78, [f"kronos_high_entropy_chop:{ent:.2f}"], evidence)
        if aligned >= 0.40 and ent <= 0.55:
            return Vote(self.id, "approve", min(0.92, 0.58 + aligned * 0.32), [f"kronos_clean_displacement_token:{aligned:.2f}"], evidence)
        if aligned <= -0.40:
            return Vote(self.id, "reject", 0.76, [f"kronos_opposing_distribution_token:{aligned:.2f}"], evidence)
        return Vote(self.id, "abstain", 0.0, ["kronos_token_neutral"], evidence)
