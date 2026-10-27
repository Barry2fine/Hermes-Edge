"""Timing: a great price on a stale or already-started market is not a trade.

Too close to the off means the lineup/news edge is gone and the fill is
adverse-selected; too far out means the number is stale and will be re-made.
Neither is a veto - simply no opinion.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote


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


class TimingBot(Bot):
    """Approve only inside the tradeable window before the event."""

    id = "timing"
    mandate = (
        "Approve inside the 0.5h-72h window before the event; abstain when it is "
        "too late to act or too early to trust the number."
    )
    domains = ("sports",)
    veto = False

    MIN_H = 0.5
    MAX_H = 72.0
    SWEET_MIN_H = 2.0
    SWEET_MAX_H = 48.0
    EDGE_CONF = 0.65
    SWEET_CONF = 0.90

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        raw = ctx.get("time_to_start_h")
        hours = _num(raw)
        evidence = {
            "time_to_start_h": raw,
            "min_h": self.MIN_H,
            "max_h": self.MAX_H,
        }

        if hours is None:
            return Vote(
                self.id, "abstain", 0.0,
                ["time_to_start_h missing/unparseable: cannot judge timing"],
                evidence,
            )

        if hours < self.MIN_H:
            return Vote(
                self.id, "abstain", 0.0,
                [f"{hours:.2f}h to start < {self.MIN_H}h: too late, edge is gone"],
                evidence,
            )

        if hours > self.MAX_H:
            return Vote(
                self.id, "abstain", 0.0,
                [f"{hours:.1f}h to start > {self.MAX_H:.0f}h: stale line, will re-make"],
                evidence,
            )

        if self.SWEET_MIN_H <= hours <= self.SWEET_MAX_H:
            return Vote(
                self.id, "approve", self.SWEET_CONF,
                [f"{hours:.1f}h to start is inside the tradeable window"],
                evidence,
            )

        return Vote(
            self.id, "approve", self.EDGE_CONF,
            [f"{hours:.1f}h to start is inside the window but near an edge"],
            evidence,
        )
