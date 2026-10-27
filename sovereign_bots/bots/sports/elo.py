"""Elo / model-price bot: our number minus their number minus the cost of trading.

Pure maths, no network, no state. The bot never knows the outcome; it only knows
whether the price we believe in is better than the price the book is offering
after fees and vig are taken out.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote


def _prob(value: Any) -> Optional[float]:
    """Coerce a probability. Rejects bools, NaN, and anything outside [0, 1]."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):  # NaN / inf
        return None
    if out < 0.0 or out > 1.0:
        return None
    return out


def _cost(value: Any) -> float:
    """Coerce a cost. Fractions are used as-is; values > 1 are read as percent."""
    if value is None or isinstance(value, bool):
        return 0.0
    try:
        out = float(value)
    except (TypeError, ValueError):
        return 0.0
    if out != out or out in (float("inf"), float("-inf")):
        return 0.0
    if out < 0.0:
        return 0.0
    if out > 1.0:
        out = out / 100.0
    return out


class EloBot(Bot):
    """Approve when the model's edge clears fees, vig and the minimum threshold."""

    id = "elo"
    edge_seat = True   # an approve means: model p must beat market p + vig
    mandate = (
        "Buy only when the model probability beats the market probability by more "
        "than fees, vig and a minimum edge of 2 probability points."
    )
    domains = ("sports",)
    veto = False

    THRESHOLD = 0.02   # required net edge, probability points
    SCALE = 4.0        # confidence per probability point of net edge
    FLOOR = 0.05
    CAP = 0.95

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        model_p = _prob(ctx.get("model_p"))
        market_p = _prob(ctx.get("market_p"))

        if model_p is None or market_p is None:
            return Vote(
                self.id, "abstain", 0.0,
                ["missing or invalid model_p/market_p: cannot price the edge"],
                {"model_p": ctx.get("model_p"), "market_p": ctx.get("market_p")},
            )

        fee = _cost(ctx.get("fee"))
        vig = _cost(ctx.get("vig"))
        edge = model_p - market_p - fee - vig
        evidence = {
            "model_p": round(model_p, 6),
            "market_p": round(market_p, 6),
            "fee": round(fee, 6),
            "vig": round(vig, 6),
            "edge": round(edge, 6),
            "threshold": self.THRESHOLD,
        }

        if edge > self.THRESHOLD:
            conf = max(self.FLOOR, min(self.CAP, 0.50 + edge * self.SCALE))
            return Vote(
                self.id, "approve", conf,
                [f"net edge {edge:+.4f} > {self.THRESHOLD:.2f} after fee/vig"],
                evidence,
            )

        return Vote(
            self.id, "abstain", 0.0,
            [f"net edge {edge:+.4f} <= {self.THRESHOLD:.2f}: no honest premium"],
            evidence,
        )
