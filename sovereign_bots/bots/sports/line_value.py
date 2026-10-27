"""Line value: is our side getting the better of the number as it moves?

Closing line value is the cleanest proof of edge; failing that, a line moving
toward our side is weak evidence. A line running hard against us is a reject.
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


def _clv(value: Any) -> Optional[float]:
    """CLV as probability points; values above 1 are read as a percentage."""
    out = _num(value)
    if out is None:
        return None
    if abs(out) > 1.0:
        out = out / 100.0
    return out


class LineValueBot(Bot):
    """Approve when price movement/CLV says our number is on the right side."""

    id = "line_value"
    edge_seat = True   # an approve means: positive CLV / favourable line move
    mandate = (
        "Approve only when closing line value is positive or the line has moved in "
        "our favour; reject a strongly adverse move; abstain when the tape is silent."
    )
    domains = ("sports",)
    veto = False

    FAVOR_BPS = 15.0        # move this far our way counts as support
    ADVERSE_BPS = -25.0     # move this far against us is a hard no
    CLV_ADVERSE = -0.01     # -1 point of CLV is a hard no
    FLOOR = 0.05
    CAP = 0.95

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        clv = _clv(ctx.get("clv_estimate"))
        move = _num(ctx.get("line_move_bps"))
        side = ctx.get("side")
        evidence = {
            "clv_estimate": clv,
            "line_move_bps": move,
            "side": side,
            "favor_bps": self.FAVOR_BPS,
            "adverse_bps": self.ADVERSE_BPS,
        }

        if clv is not None and clv > 0.0:
            conf = max(self.FLOOR, min(self.CAP, 0.55 + clv * 8.0))
            return Vote(
                self.id, "approve", conf,
                [f"positive CLV {clv:+.4f} on side {side!r}"],
                evidence,
            )

        # Closing line value outranks a single line move: when the two disagree,
        # the tape that actually closed against us wins.
        if clv is not None and clv <= self.CLV_ADVERSE:
            conf = max(self.FLOOR, min(self.CAP, 0.55 + abs(clv) * 8.0))
            return Vote(
                self.id, "reject", conf,
                [f"strongly adverse CLV {clv:+.4f} on side {side!r}"],
                evidence,
            )

        if move is not None and move >= self.FAVOR_BPS:
            conf = max(self.FLOOR, min(self.CAP, 0.55 + (move - self.FAVOR_BPS) * 0.005))
            return Vote(
                self.id, "approve", conf,
                [f"line moved {move:+.1f}bps in favour of side {side!r}"],
                evidence,
            )

        if move is not None and move <= self.ADVERSE_BPS:
            conf = max(self.FLOOR, min(self.CAP, 0.55 + abs(move) * 0.005))
            return Vote(
                self.id, "reject", conf,
                [f"line moved {move:+.1f}bps against side {side!r}"],
                evidence,
            )

        if clv is None and move is None:
            return Vote(
                self.id, "abstain", 0.0,
                ["missing clv_estimate and line_move_bps: no tape to read"],
                evidence,
            )

        return Vote(
            self.id, "abstain", 0.0,
            [f"inconclusive tape (clv={clv}, move={move}bps): no edge signal"],
            evidence,
        )
