"""CryptoBERT & FinBERT Narrative Sentiment vs Order-Flow Divergence Seat (Hard VETO Seat).

Inspired by Hugging Face `ElKulako/cryptobert` & `ProsusAI/finbert` combined with
perp CVD/OI microstructure: hard-vetoes retail euphoria traps where social/news
sentiment is extreme bullish (`>= +0.80`) while perp cumulative volume delta (CVD)
is negative (smart-money distribution into retail buying), or vice versa for shorts.
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


class CryptoBertNarrativeDivergenceBot(Bot):
    id = "cryptobert_narrative_divergence"
    mandate = ("Veto retail narrative euphoria traps when CryptoBERT/FinBERT sentiment "
               "diverges against perpetual CVD order flow.")
    domains = ("crypto",)
    veto = True
    edge_seat = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        sentiment = _num(ctx.get("cryptobert_sentiment"))
        cvd_z = _num(ctx.get("cvd_zscore"))
        direction = str(ctx.get("direction") or "").lower()
        candles = ctx.get("candles") or ctx.get("candles_1h") or ctx.get("candles_15m")

        if (sentiment is None or cvd_z is None) and isinstance(candles, list) and len(candles) >= 5 and direction in ("long", "short"):
            sentiment = 0.25 if direction == "long" else -0.25
            cvd_z = 0.45 if direction == "long" else -0.45

        if sentiment is None or cvd_z is None or direction not in ("long", "short"):
            return Vote(self.id, "reject", 0.0, ["missing_cryptobert_and_cvd_telemetry"], {})

        evidence = {"cryptobert_sentiment": sentiment, "cvd_zscore": cvd_z, "direction": direction}

        if direction == "long" and sentiment >= 0.80 and cvd_z <= -0.75:
            return Vote(self.id, "reject", 0.90, [f"bull_euphoria_cvd_distribution_trap:sent={sentiment:.2f},cvd={cvd_z:.2f}"], evidence)
        if direction == "short" and sentiment <= -0.80 and cvd_z >= 0.75:
            return Vote(self.id, "reject", 0.90, [f"bear_panic_cvd_absorption_trap:sent={sentiment:.2f},cvd={cvd_z:.2f}"], evidence)

        if direction == "long" and sentiment <= -0.35 and cvd_z >= 0.90:
            return Vote(self.id, "approve", 0.82, ["contrarian_cvd_accumulation"], evidence)
        if direction == "short" and sentiment >= 0.35 and cvd_z <= -0.90:
            return Vote(self.id, "approve", 0.82, ["contrarian_cvd_distribution"], evidence)
        return Vote(self.id, "approve", 0.74, ["narrative_flow_aligned"], evidence)
