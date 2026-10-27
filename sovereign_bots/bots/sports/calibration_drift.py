"""Calibration drift (VETO) for sports: brier and log-loss must both hold up.

Sports models are graded on two proper scoring rules at once. A model can have a
respectable Brier score and still be badly mispriced on the tails, so each
supplied pair is checked on its own and the worst one decides:

* n < 30 settled bets -> veto: we cannot know anything yet
* model worse than its own naive baseline on either score -> veto
* model degrading against its prior window on either score -> veto
  (both > 20% relative and > 1 point absolute, so a small base cannot trip it)

A pair that is half supplied (a model score with no baseline) is unreadable and
vetoes. At least one complete pair must exist. Drift windows are supplementary
telemetry: both ends are needed to read a drift, and a lone window is not a veto.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ...core.bot import Bot, Vote

MIN_SAMPLE = 30
DRIFT_RATIO = 1.2
DRIFT_ABS = 0.01
EPS = 1e-9

# (message prefix, context keys) for the two proper scoring rules we grade on.
PAIRS: Tuple[Tuple[str, str, str], ...] = (
    ("brier", "brier_model", "brier_baseline"),
    ("logloss", "logloss_model", "logloss_baseline"),
)
DRIFTS: Tuple[Tuple[str, str, str], ...] = (
    ("brier", "recent_brier", "prior_brier"),
    ("logloss", "recent_logloss", "prior_logloss"),
)


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
    """A proper scoring-rule value: finite and non-negative."""
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
    if value != value:  # NaN cannot reach Vote.confidence
        return lo
    return max(lo, min(hi, value))


def _excess(a: float, b: float) -> float:
    """Relative excess (a-b)/b clipped to [0, 1]; 0 when b is not positive."""
    if b <= 0.0:
        return 1.0 if a > 0.0 else 0.0
    return max(0.0, min(1.0, (a - b) / max(b, EPS)))


class CalibrationDriftBot(Bot):
    """Veto sports models that are under-sampled, worse than baseline, or drifting."""

    id = "calibration_drift"
    # Certification seat: proves the model beat the market historically. It does
    # NOT price *this* ticket, so it must never satisfy the edge requirement.
    edge_seat = False   # an approve means: model must beat market Brier
    mandate = (
        "Veto any sports model with fewer than 30 settled bets, a Brier or log-loss "
        "score worse than its naive baseline, or either score degrading by more than "
        "20% relative and 1 point absolute against its prior window."
    )
    domains = ("sports",)
    veto = True

    min_sample = MIN_SAMPLE
    drift_ratio = DRIFT_RATIO
    drift_abs = DRIFT_ABS

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}

        n = _count(ctx.get("n"))
        pairs: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
        for prefix, model_key, base_key in PAIRS:
            pairs[prefix] = (_score(ctx.get(model_key)), _score(ctx.get(base_key)))
        drifts: Dict[str, Tuple[Optional[float], Optional[float]]] = {}
        for prefix, recent_key, prior_key in DRIFTS:
            drifts[prefix] = (_score(ctx.get(recent_key)), _score(ctx.get(prior_key)))

        degraded: Dict[str, bool] = {}
        for prefix, (recent, prior) in drifts.items():
            degraded[prefix] = bool(
                recent is not None
                and prior is not None
                and recent > prior * self.drift_ratio
                and recent > prior + self.drift_abs
            )

        evidence: Dict[str, Any] = {
            "brier_model": pairs["brier"][0],
            "brier_baseline": pairs["brier"][1],
            "logloss_model": pairs["logloss"][0],
            "logloss_baseline": pairs["logloss"][1],
            "recent_brier": drifts["brier"][0],
            "prior_brier": drifts["brier"][1],
            "recent_logloss": drifts["logloss"][0],
            "prior_logloss": drifts["logloss"][1],
            "drift_brier": None if None in drifts["brier"] else drifts["brier"][0] - drifts["brier"][1],
            "drift_logloss": None if None in drifts["logloss"] else drifts["logloss"][0] - drifts["logloss"][1],
            "degraded": degraded,
            "n": n,
            "min_sample": self.min_sample,
            "drift_limits": {"relative": self.drift_ratio, "absolute": self.drift_abs},
        }

        # ---- unreadable inputs: a veto fails closed --------------------------
        unreadable: List[str] = []
        if n is None:
            unreadable.append("n")
        for prefix, model_key, base_key in PAIRS:
            model, base = pairs[prefix]
            if (model is None) != (base is None):  # half a pair is unreadable
                unreadable.append(f"{prefix}_pair")
        if unreadable:
            return Vote(
                self.id, "reject", 0.90,
                ["missing_or_invalid:" + ",".join(unreadable)],
                evidence,
            )
        if all(pairs[prefix] == (None, None) for prefix, _, _ in PAIRS):
            return Vote(
                self.id, "reject", 0.90,
                ["missing_or_invalid:no_calibration_pair"],
                evidence,
            )

        # ---- gate 1: sample size ---------------------------------------------
        if n < self.min_sample:
            return Vote(
                self.id, "reject", 0.85,
                [f"insufficient_sample:{n:g}<{self.min_sample}"],
                evidence,
            )

        # ---- gate 2: every supplied score must beat its baseline --------------
        worse: List[str] = []
        worst_excess = 0.0
        for prefix, _, _ in PAIRS:
            model, base = pairs[prefix]
            if model is None or base is None:
                continue
            if model > base:
                worse.append(f"{prefix}:{model:.4f}>{base:.4f}")
                worst_excess = max(worst_excess, _excess(model, base))
        if worse:
            conf = 0.72 + 0.20 * min(1.0, 3.0 * worst_excess)
            return Vote(self.id, "reject", _clamp(conf),
                        ["worse_than_baseline:" + ";".join(worse)], evidence)

        # ---- gate 3: no material drift on either score -----------------------
        drifting: List[str] = []
        worst_drift = 0.0
        for prefix, recent_key, prior_key in DRIFTS:
            recent, prior = drifts[prefix]
            if not degraded[prefix]:
                continue
            drifting.append(
                f"{prefix}:{recent:.4f}>prior*{self.drift_ratio:g}={prior * self.drift_ratio:.4f}"
                f",+{recent - prior:.4f}"
            )
            worst_drift = max(worst_drift, _excess(recent, prior))
        if drifting:
            conf = 0.70 + 0.20 * min(1.0, 2.0 * worst_drift)
            return Vote(self.id, "reject", _clamp(conf),
                        ["calibration_drift:" + ";".join(drifting)], evidence)

        # ---- approve ---------------------------------------------------------
        gains: List[float] = []
        for prefix, _, _ in PAIRS:
            model, base = pairs[prefix]
            if model is None or base is None:
                continue
            gains.append((base - model) / base if base > 0.0 else 0.0)
        rel_gain = sum(gains) / len(gains) if gains else 0.0
        edge_bonus = min(0.20, max(0.0, rel_gain) * 0.8)
        sample_bonus = min(0.10, max(0.0, n - self.min_sample) / 400.0)
        stable = all(not degraded[p] for p, _, _ in DRIFTS) and any(
            drifts[p] != (None, None) for p, _, _ in DRIFTS
        )
        conf = _clamp(0.55 + edge_bonus + sample_bonus + (0.05 if stable else 0.0))

        reasons = [f"sample_ok:n={n:g}>={self.min_sample}"]
        for prefix, _, _ in PAIRS:
            model, base = pairs[prefix]
            if model is not None and base is not None:
                reasons.append(f"{prefix}_ok:{model:.4f}<={base:.4f}")
        reasons.append("stable" if stable else "drift_unmeasured")
        evidence.update({"relative_gain": rel_gain, "confidence": conf})
        return Vote(self.id, "approve", conf, reasons, evidence)
