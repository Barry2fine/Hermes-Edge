"""Calibration drift (VETO): is the model better than its baseline, and is it rotting?

A prediction-market model can look profitable on price and still be worse than
the naive base rate. This bot asks three questions, and any single bad answer is
a veto:

1. Do we even have enough settled markets to know? (n < 30 -> no)
2. Is the model's Brier score worse than the naive baseline it must beat?
3. Has the score drifted materially worse than the prior window?
   (both > 20% relative *and* > 1 point absolute, so a tiny base can't trip it)

Unreadable inputs are a veto as well. A drift guard that approves on missing
data is not a guard.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

MIN_SAMPLE = 30
DRIFT_RATIO = 1.2
DRIFT_ABS = 0.01
EPS = 1e-9


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


def _score(value: Any) -> Optional[float]:
    """A Brier score: finite and non-negative. Anything else is unreadable."""
    out = _num(value)
    if out is None or out < 0.0:
        return None
    return out


def _count(value: Any) -> Optional[float]:
    """A sample size: finite and non-negative."""
    out = _num(value)
    if out is None or out < 0.0:
        return None
    return out


def _clamp(value: float, lo: float = 0.0, hi: float = 0.95) -> float:
    if value != value:  # NaN cannot be allowed into Vote.confidence (min() would eat it)
        return lo
    return max(lo, min(hi, value))


def _rel(a: float, b: float) -> float:
    """Relative gap (a-b)/b, clipped to [0, 1]; 0 when b is not positive."""
    if b <= 0.0:
        return 0.0
    return max(0.0, min(1.0, (a - b) / max(b, EPS)))


class CalibrationDriftBot(Bot):
    """Approve only when the model is sampled, beats baseline and is not drifting."""

    id = "calibration_drift"
    # Certification seat: proves the model beat the market historically. It does
    # NOT price *this* ticket, so it must never satisfy the edge requirement.
    edge_seat = False   # an approve means: model must beat market Brier
    mandate = (
        "Veto any polymarket model with fewer than 30 settled markets, a Brier score "
        "worse than the naive baseline, or a Brier score degrading by more than 20% "
        "relative and 1 point absolute against its prior window."
    )
    domains = ("polymarket",)
    veto = True

    min_sample = MIN_SAMPLE
    drift_ratio = DRIFT_RATIO
    drift_abs = DRIFT_ABS

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}

        n = _count(ctx.get("n"))
        brier_model = _score(ctx.get("brier_model"))
        brier_baseline = _score(ctx.get("brier_baseline"))
        recent = _score(ctx.get("recent_brier"))
        prior = _score(ctx.get("prior_brier"))

        drift = None if (recent is None or prior is None) else recent - prior
        drift_rel = (
            None if (recent is None or prior is None or prior <= 0.0) else (recent / prior)
        )
        degraded = bool(
            recent is not None
            and prior is not None
            and recent > prior * self.drift_ratio
            and recent > prior + self.drift_abs
        )

        evidence: Dict[str, Any] = {
            "brier_model": brier_model,
            "brier_baseline": brier_baseline,
            "recent_brier": recent,
            "prior_brier": prior,
            "drift": drift,
            "drift_ratio": drift_rel,
            "n": n,
            "min_sample": self.min_sample,
            "drift_limits": {"relative": self.drift_ratio, "absolute": self.drift_abs},
            "degraded": degraded,
        }

        # ---- unreadable inputs: a veto has to fail closed -------------------
        unreadable = []
        if n is None:
            unreadable.append("n")
        if brier_model is None:
            unreadable.append("brier_model")
        if brier_baseline is None:
            unreadable.append("brier_baseline")
        if unreadable:
            return Vote(
                self.id, "reject", 0.90,
                ["missing_or_invalid:" + ",".join(unreadable)],
                evidence,
            )

        # ---- gate 1: sample size --------------------------------------------
        if n < self.min_sample:
            return Vote(
                self.id, "reject", 0.85,
                [f"insufficient_sample:{n:g}<{self.min_sample}"],
                evidence,
            )

        # ---- gate 2: must beat the naive baseline ---------------------------
        if brier_model > brier_baseline:
            conf = 0.72 + 0.20 * min(1.0, 3.0 * _rel(brier_model, brier_baseline))
            return Vote(
                self.id, "reject", _clamp(conf),
                [f"worse_than_baseline:{brier_model:.4f}>{brier_baseline:.4f}"],
                evidence,
            )

        # ---- gate 3: no material drift against the prior window -------------
        if degraded:
            worse = _rel(recent, prior) if prior > 0.0 else 1.0
            conf = 0.70 + 0.20 * min(1.0, 2.0 * worse)
            return Vote(
                self.id, "reject", _clamp(conf),
                [
                    "calibration_drift:"
                    f"recent={recent:.4f}>prior*{self.drift_ratio:g}={prior * self.drift_ratio:.4f}"
                    f" and drift={drift:+.4f}>{self.drift_abs:g}"
                ],
                evidence,
            )

        # ---- approve ---------------------------------------------------------
        gap = brier_baseline - brier_model            # >= 0 by gate 2
        rel_gain = gap / brier_baseline if brier_baseline > 0.0 else 0.0
        edge_bonus = min(0.20, rel_gain * 0.8)
        sample_bonus = min(0.10, max(0.0, (n - self.min_sample)) / 300.0)
        stability_bonus = 0.05 if (drift is not None and drift <= 0.0) else 0.0
        conf = _clamp(0.55 + edge_bonus + sample_bonus + stability_bonus)

        reasons = [
            f"sample_ok:n={n:g}>={self.min_sample}",
            f"beats_baseline:{brier_model:.4f}<={brier_baseline:.4f}",
        ]
        if drift is None:
            reasons.append("drift_unmeasured:no prior window supplied")
        elif drift_rel is None:
            reasons.append(f"stable:drift={drift:+.4f} rel=n/a")  # prior window was 0
        else:
            reasons.append(f"stable:drift={drift:+.4f} rel={drift_rel:.3f}")
        evidence.update({"gap": gap, "relative_gain": rel_gain, "confidence": conf})
        return Vote(self.id, "approve", conf, reasons, evidence)
