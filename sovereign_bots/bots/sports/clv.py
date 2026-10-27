"""CLV judge (no veto): does the price we took beat the price that closed?

Closing line value is the cleanest forward proof of edge: if our bets
systematically close better than we bet them, we are being paid for information,
not for luck. This bot abstains rather than guesses:

* not enough bets (clv_n < 20) or no clv_mean -> abstain, no opinion
* clv_mean <= -0.01 -> reject: we are reliably on the wrong side of the close
* clv_mean > 0     -> approve, confidence grows with the size of the edge
* anything in between (a flat or mildly negative book) -> abstain

It holds no veto: a losing CLV sample argues against a bet, but it is not
grounds to overrule the rest of the desk.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

MIN_N = 20
LOSER_CLV = -0.01
BASE_CONF = 0.55
MAX_BONUS = 0.35
SCALE = 5.0


def _num(value: Any) -> Optional[float]:
    """Finite float or None. Bools, junk, NaN and infinities are not numbers."""
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    except Exception:  # noqa: BLE001 - a hostile __float__ must not raise
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _clamp(value: float, lo: float = 0.0, hi: float = 0.95) -> float:
    if value != value:  # NaN cannot reach Vote.confidence
        return lo
    return max(lo, min(hi, value))


class CLVBot(Bot):
    """Approve when closing line value is positive on a real sample."""

    id = "clv"
    edge_seat = True   # an approve means: mean closing-line value is positive
    mandate = (
        "Approve sports bets only when mean closing line value is positive over at "
        "least 20 bets; reject a reliably negative CLV book (<= -1%); abstain when "
        "the sample is too small or the book is flat."
    )
    domains = ("sports",)
    veto = False

    min_n = MIN_N
    loser_clv = LOSER_CLV
    base_conf = BASE_CONF
    max_bonus = MAX_BONUS
    scale = SCALE

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}

        clv_mean = _num(ctx.get("clv_mean"))
        clv_n = _num(ctx.get("clv_n"))
        clv_hit_rate = _num(ctx.get("clv_hit_rate"))
        evidence: Dict[str, Any] = {
            "clv_mean": clv_mean,
            "clv_n": clv_n,
            "clv_hit_rate": clv_hit_rate,
        }

        # ---- no measurable sample -> no opinion -----------------------------
        if clv_mean is None:
            return Vote(self.id, "abstain", 0.0,
                        ["missing_clv_mean:no closing-line history"], evidence)
        if clv_n is None or clv_n < self.min_n:
            shown = "missing" if clv_n is None else f"{clv_n:g}"
            return Vote(self.id, "abstain", 0.0,
                        [f"insufficient_sample:{shown}<{self.min_n}"], evidence)

        # ---- proven loser: our bets close worse than we bet them -------------
        if clv_mean <= self.loser_clv:
            conf = _clamp(0.70 + min(0.25, abs(clv_mean) * self.scale))
            return Vote(
                self.id, "reject", conf,
                [f"negative_clv:{clv_mean:+.4f}<={self.loser_clv:+.4f}"]
                + ([f"hit_rate={clv_hit_rate:.3f}"] if clv_hit_rate is not None else []),
                evidence,
            )

        # ---- positive edge ---------------------------------------------------
        if clv_mean > 0.0:
            conf = _clamp(self.base_conf + min(self.max_bonus, clv_mean * self.scale))
            reasons = [f"positive_clv:{clv_mean:+.4f}", f"n={clv_n:g}"]
            if clv_hit_rate is not None:
                reasons.append(f"hit_rate={clv_hit_rate:.3f}")
            return Vote(self.id, "approve", conf, reasons, evidence)

        # ---- flat to mildly negative: the tape has no verdict ----------------
        return Vote(
            self.id, "abstain", 0.0,
            [f"flat_clv:{clv_mean:+.4f} inside ({self.loser_clv:+.4f}, 0]"],
            evidence,
        )
