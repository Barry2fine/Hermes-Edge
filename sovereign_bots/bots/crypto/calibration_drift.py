"""Calibration-drift bot — the veto that asks "is the model still better than
the market, and is it getting worse?". VETO.

Inputs are Brier scores (lower is better, 0..1):

    brier_model      the desk's own forecasts, full sample
    brier_baseline   the market / base-rate forecast, same sample
    recent_brier     the desk, recent window
    prior_brier      the desk, the window before that
    n                number of resolved forecasts behind the comparison

Rules (fail-closed):
  * brier_model / brier_baseline missing or invalid     -> reject
  * n < 30                (cannot certify a model on 30 samples)
                                                        -> reject
  * brier_model > brier_baseline (worse than the market) -> reject
  * recent_brier > prior_brier * 1.2 and
    recent_brier > prior_brier + 0.01   (real degradation) -> reject
  * otherwise                                            -> approve

No network, no file IO, no clock, no randomness.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

MIN_SAMPLE = 30
DRIFT_MULT = 1.2
DRIFT_ABS = 0.01
EPS = 1e-12


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(str(x).strip().rstrip("%")) if isinstance(x, str) else float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    if isinstance(x, str) and x.strip().endswith("%"):
        v /= 100.0
    return v


def _count(x: Any) -> Optional[int]:
    v = _num(x)
    if v is None or v < 0:
        return None
    return int(v)


class CalibrationDriftBot(Bot):
    id = "calibration_drift"
    # Certification seat: proves the model beat the market historically. It does
    # NOT price *this* ticket, so it must never satisfy the edge requirement.
    edge_seat = False   # an approve means: model must beat market Brier
    mandate = ("Certify nothing on small samples and nothing that loses to the "
               "market: the model must beat the market Brier score on at least "
               "30 resolved forecasts, and show no recent degradation.")
    domains = ("crypto",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        brier_model = _num(ctx.get("brier_model"))
        brier_baseline = _num(ctx.get("brier_baseline"))
        recent = _num(ctx.get("recent_brier"))
        prior = _num(ctx.get("prior_brier"))
        n = _count(ctx.get("n"))

        drift = (round(recent - prior, 6)
                 if recent is not None and prior is not None else None)
        evidence: Dict[str, Any] = {
            "brier_model": brier_model,
            "brier_baseline": brier_baseline,
            "recent_brier": recent,
            "prior_brier": prior,
            "drift": drift,
        }

        if brier_model is None:
            return Vote(self.id, "reject", 1.0, ["missing:brier_model"], evidence)
        if brier_baseline is None:
            return Vote(self.id, "reject", 1.0, ["missing:brier_baseline"], evidence)
        if n is None:
            return Vote(self.id, "reject", 1.0, ["missing:n"], evidence)
        if n < MIN_SAMPLE:
            return Vote(self.id, "reject", 0.9,
                        [f"insufficient_sample:n={n}<{MIN_SAMPLE}"], evidence)
        if brier_model > brier_baseline:
            return Vote(self.id, "reject", 0.95,
                        [f"model_worse_than_market:{brier_model:.4f}"
                         f">{brier_baseline:.4f}"], evidence)
        if (recent is not None and prior is not None
                and recent > prior * DRIFT_MULT and recent > prior + DRIFT_ABS):
            return Vote(self.id, "reject", 0.9,
                        [f"degrading:recent={recent:.4f}>prior={prior:.4f}"
                         f"(drift={drift:+.4f})"], evidence)

        margin = (brier_baseline - brier_model) / max(EPS, brier_baseline) if brier_baseline > 0 else 0.0
        conf = 0.60 + 0.35 * max(0.0, min(1.0, margin * 10.0))
        return Vote(self.id, "approve", min(0.95, conf),
                    [f"model={brier_model:.4f}<=market={brier_baseline:.4f}",
                     f"n={n}drift={drift if drift is not None else 'n/a'}"], evidence)

