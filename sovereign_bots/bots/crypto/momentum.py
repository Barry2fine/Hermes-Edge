"""Momentum bot — rider, not gatekeeper. No veto.

Rules (contract):
  * ``score >= 0.5`` and ``alignment >= 2`` -> approve, confidence = score
  * anything less, or missing inputs          -> abstain

Confidence is deliberately the raw score (not inflated) so the coordinator's
``min_confidence`` filter can use it directly.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

MIN_SCORE = 0.5
MIN_ALIGNMENT = 2


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(str(x).strip().rstrip("%")) if isinstance(x, str) else float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


class MomentumBot(Bot):
    id = "momentum"
    mandate = "Speak up only when the score is real and at least two timeframes agree."
    domains = ("crypto",)
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        score = _num(ctx.get("score"))
        alignment = _num(ctx.get("alignment"))
        evidence: Dict[str, Any] = {"score": score, "alignment": alignment}

        if score is None or alignment is None:
            return Vote(self.id, "abstain", 0.0, ["missing:score/alignment"], evidence)
        if score < MIN_SCORE:
            return Vote(self.id, "abstain", 0.0,
                        [f"weak_score={score:.2f}<0.50"], evidence)
        if alignment < MIN_ALIGNMENT:
            return Vote(self.id, "abstain", 0.0,
                        [f"thin_alignment={alignment:.0f}<2"], evidence)

        return Vote(self.id, "approve", min(1.0, score),
                    [f"score={score:.2f}", f"alignment={alignment:.0f}"], evidence)
