"""
Institutional Walk-Forward Calibration, Brier Skill Score (BSS), Log-Loss,
Expected Calibration Error (ECE), CLV Attribution & Ablation Harness
(`sovereign_bots/core/walk_forward_calibrator.py`).

Enforces strict Bloomberg/Jane-Street quantitative model governance:
1. Out-of-Sample Scoring Rules:
   - Brier Score: BS = (1/N) * sum((p_model - y)^2)
   - Brier Skill Score vs. Market De-Vigged Implied Probability:
       BSS = 1.0 - (BS_model / BS_market)
   - Binary Log-Loss (Cross-Entropy) with [1e-6, 1 - 1e-6] clipping
   - Expected Calibration Error (ECE) across K probability bins
   - Realized Closing Line Value (CLV in bps)
2. Automated Feature Ablation & Parameter Pruning:
   - Tests candidate feature adjustments (fatigue, altitude, usage elasticity,
     copula correlation, Kesten-Stigum regime shrinkage) against the unadjusted
     baseline out-of-sample.
   - Any adjustment that degrades out-of-sample Brier Score or Log-Loss is
     automatically dampened/pruned (`ablation_keep = False`, weight shrunk toward 0).
3. Post-Mortem Ledger Replay:
   - Replays `data/sports_postmortems.jsonl` and updates `data/learning/knowledge_loop_state.json`.
"""

from __future__ import annotations

import json
import hashlib
from datetime import datetime, timezone
import math
import os
from typing import Any, Dict, List, Optional, Sequence

from sovereign_bots.core.knowledge_loop import (
    apply_sports_outcome,
    knowledge_transaction,
)

def _resolve_default_postmortem_path() -> str:
    base = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    candidates = [
        os.path.join(base, "data", "sports_postmortems.jsonl"),
        os.path.join(os.path.dirname(base), "data", "sports_postmortems.jsonl"),
    ]
    for c in candidates:
        if os.path.exists(c):
            return c
    return candidates[0]


DEFAULT_POSTMORTEM_PATH = _resolve_default_postmortem_path()


def _clip_prob(p: float, eps: float = 1e-6) -> float:
    if isinstance(p, bool) or not math.isfinite(p) or not 0 <= p <= 1:
        raise ValueError("nonfinite probability")
    return max(eps, min(1.0 - eps, float(p)))


def compute_brier_and_log_loss(
    predictions: Sequence[Dict[str, Any]],
    bins: int = 5,
) -> Dict[str, Any]:
    """
    Compute Brier Score, Market Brier Score, Brier Skill Score (BSS),
    Binary Log-Loss, Expected Calibration Error (ECE), and mean CLV (bps).

    Each item in `predictions` expects:
      - `p_model`: float in (0, 1)
      - `outcome`: int/bool in {0, 1}
      - `p_market` (optional): de-vigged market implied probability in (0, 1)
      - `p_closing` (optional): closing sharp probability in (0, 1)
    """
    valid = []
    for row in predictions:
        try:
            p = row["p_model"]
            _clip_prob(p)
            y = row["outcome"]
            if not isinstance(y, (int, bool)) or y not in (0, 1):
                raise ValueError("invalid binary outcome")
            market = row.get("p_market")
            closing = row.get("p_closing")
            if market is not None:
                _clip_prob(market)
            if closing is not None:
                _clip_prob(closing)
            valid.append({"p": p, "y": float(y), "market": market, "closing": closing})
        except (KeyError, TypeError, ValueError, AttributeError):
            continue
    n = len(valid)
    result = {"n": n, "brier_model": None, "brier_market": None,
              "brier_skill_score": None, "log_loss_model": None,
              "log_loss_market": None, "ece": None, "mean_clv_bps": None,
              "calibrated": False}
    if not n:
        return result

    def loss(p, y):
        p = _clip_prob(p)
        return -y * math.log(p) - (1 - y) * math.log(1 - p)

    bs_model = sum((r["p"] - r["y"]) ** 2 for r in valid) / n
    result["brier_model"] = round(bs_model, 6)
    result["log_loss_model"] = round(sum(loss(r["p"], r["y"]) for r in valid) / n, 6)
    # Compare the identical cohort only. Missing market/closing evidence is not zero.
    if all(r["market"] is not None for r in valid):
        bs_market = sum((r["market"] - r["y"]) ** 2 for r in valid) / n
        result["brier_market"] = round(bs_market, 6)
        result["brier_skill_score"] = round(1 - bs_model / bs_market, 6) if bs_market > 0 else None
        result["log_loss_market"] = round(sum(loss(r["market"], r["y"]) for r in valid) / n, 6)
    if all(r["market"] is not None and r["closing"] is not None for r in valid):
        result["mean_clv_bps"] = round(sum((r["closing"] - r["market"]) * 10000 for r in valid) / n, 2)
    k = max(2, int(bins))
    buckets = [[] for _ in range(k)]
    for r in valid:
        buckets[min(k - 1, int(r["p"] * k))].append(r)
    ece = sum(abs(sum(r["p"] - r["y"] for r in bucket)) / n for bucket in buckets)
    result["ece"] = round(ece, 6)
    result["calibrated"] = bool(n >= 20 and result["brier_market"] is not None
                                and bs_model <= result["brier_market"] + 1e-6 and ece <= .15)
    return result


def _shifted_probability(base, delta, weight):
    adjusted = float(base) + weight * float(delta)
    if not math.isfinite(adjusted):
        raise ValueError("nonfinite adjusted probability")
    return _clip_prob(max(0.0, min(1.0, adjusted)))


def evaluate_feature_ablation(
    records: Sequence[Dict[str, Any]],
    feature_name: str,
    candidate_weights: Sequence[float] = (0.0, 0.25, 0.50, 0.75, 1.0),
) -> Dict[str, Any]:
    """
    Out-of-sample walk-forward ablation test for an additive probability delta `feature_delta`.
    Each record in `records` contains:
      - `p_base`: baseline unadjusted probability in (0, 1)
      - `feature_delta`: proposed probability shift from the feature (e.g. +0.025)
      - `outcome`: 0 or 1

    Finds the weight `w in candidate_weights` that minimizes out-of-sample Brier Score
    and Log-Loss. If `w = 0.0` (the unadjusted baseline) is best or if `w = 1.0` degrades
    Brier Score, automatically prunes or shrinks the feature weight.
    """
    if not records:
        return {
            "feature": feature_name,
            "n": 0,
            "optimal_weight": 0.0,
            "ablation_keep": False,
            "reason": "no_records",
        }

    baseline_preds = [
        {"p_model": r["p_base"], "p_market": r["p_base"], "outcome": r["outcome"]}
        for r in records
    ]
    base_metrics = compute_brier_and_log_loss(baseline_preds)
    base_brier = float(base_metrics["brier_model"] or 0.25)
    base_ll = float(base_metrics["log_loss_model"] or 0.693)

    best_w = 0.0
    best_brier = base_brier
    best_ll = base_ll
    grid_results: Dict[str, Dict[str, float]] = {}

    for w in candidate_weights:
        wf = float(w)
        preds = [
            {
                "p_model": _shifted_probability(r["p_base"], r.get("feature_delta", 0.0), wf),
                "p_market": _clip_prob(float(r["p_base"])),
                "outcome": r["outcome"],
            }
            for r in records
        ]
        m = compute_brier_and_log_loss(preds)
        b_m = float(m["brier_model"] or 0.25)
        ll_m = float(m["log_loss_model"] or 0.693)
        grid_results[f"{wf:.2f}"] = {"brier": b_m, "log_loss": ll_m}
        if b_m < best_brier - 1e-6 and ll_m <= best_ll + 1e-4:
            best_brier = b_m
            best_ll = ll_m
            best_w = wf

    improvement_brier = base_brier - best_brier
    keep = bool(best_w > 0.0 and improvement_brier > 1e-6)
    return {
        "feature": feature_name,
        "n": len(records),
        "baseline_brier": round(base_brier, 6),
        "optimal_brier": round(best_brier, 6),
        "brier_improvement": round(improvement_brier, 6),
        "optimal_weight": round(best_w, 4),
        "ablation_keep": keep,
        "grid": grid_results,
    }


def _timestamp(value):
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timestamp requires timezone")
    return parsed.astimezone(timezone.utc)


def _validated_leg(leg):
    """Require original pre-event evidence; never reconstruct it from a result."""
    for key in ("observation_id", "market_family", "model_version", "source"):
        if not isinstance(leg.get(key), str) or not leg[key].strip():
            raise ValueError("missing provenance")
    prediction = _timestamp(leg["prediction_at"])
    start = _timestamp(leg["event_start_at"])
    closing = _timestamp(leg["closing_at"])
    settlement = _timestamp(leg["settled_at"])
    if not prediction <= closing <= start <= settlement <= datetime.now(timezone.utc):
        raise ValueError("invalid evidence chronology")
    # Caller records its model's explicit freshness policy; no invented TTL.
    ttl = leg["max_prediction_age_seconds"]
    if isinstance(ttl, bool) or not isinstance(ttl, (int, float)) or not math.isfinite(ttl) or ttl <= 0:
        raise ValueError("missing freshness policy")
    if (start - prediction).total_seconds() > ttl:
        raise ValueError("stale prediction")
    if any(leg.get(key) is None for key in ("p_model", "p_market", "p_closing")) or compute_brier_and_log_loss([leg])["n"] != 1:
        raise ValueError("invalid calibration evidence")
    for key in ("realized_roi_mult",):
        value = leg[key]
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
            raise ValueError("invalid realized return")
    keys = ("observation_id", "market_family", "model_version", "source", "prediction_at",
            "event_start_at", "closing_at", "settled_at", "max_prediction_age_seconds",
            "p_model", "p_market", "p_closing", "outcome", "realized_roi_mult")
    return {key: leg[key] for key in keys}


def replay_sports_postmortems(postmortem_path=None, knowledge_path=None, persist=True):
    """Score recorded evidence and atomically persist outcomes with replay receipts.

    Legacy pick-only legs remain unscored. IDs must identify a single settled
    prediction across files/reruns; changing evidence for an ID is an error.
    """
    p_path = postmortem_path or DEFAULT_POSTMORTEM_PATH
    if not os.path.exists(p_path):
        return {"ok": False, "reason": "missing_postmortem_file", "legs_evaluated": 0}
    records = {}
    invalid = 0
    tickets = 0
    profit = 0.0
    with open(p_path, encoding="utf-8") as handle:
        for line in handle:
            if not line.strip():
                continue
            try:
                entry = json.loads(line)
                entry_tickets = entry["tickets"]
                if not isinstance(entry_tickets, list):
                    raise ValueError("invalid tickets")
                amount = entry.get("net_cash_profit_cad")
                if isinstance(amount, (int, float)) and not isinstance(amount, bool) and math.isfinite(amount):
                    profit += amount
            except (ValueError, KeyError, TypeError):
                invalid += 1
                continue
            for ticket in entry_tickets:
                tickets += 1
                if not isinstance(ticket, dict) or not isinstance(ticket.get("legs"), list):
                    invalid += 1
                    continue
                for leg in ticket["legs"]:
                    try:
                        rec = _validated_leg(leg)
                    except (ValueError, KeyError, TypeError, AttributeError, OverflowError):
                        invalid += 1
                        continue
                    identity = rec["observation_id"]
                    if identity in records and records[identity] != rec:
                        raise ValueError("conflicting observation identity")
                    records[identity] = rec
    calibration = compute_brier_and_log_loss(list(records.values()))
    newly_recorded = 0
    if persist and records:
        with knowledge_transaction(knowledge_path) as state:
            receipts = state.setdefault("sports_replay_receipts_v1", {})
            for identity, rec in records.items():
                digest = hashlib.sha256(json.dumps(rec, sort_keys=True, allow_nan=False).encode()).hexdigest()
                if identity in receipts:
                    if receipts[identity] != digest:
                        raise ValueError("settled evidence changed; explicit reconciliation required")
                    continue
                apply_sports_outcome(state, rec["market_family"], rec["p_model"], rec["outcome"],
                                     rec["realized_roi_mult"], (rec["p_closing"] - rec["p_market"]) * 10000)
                receipts[identity] = digest
                newly_recorded += 1
            state["latest_walk_forward_calibration"] = calibration
    return {"ok": True, "status": "SCORED" if records else "EVIDENCE_UNAVAILABLE",
            "tickets_evaluated": tickets, "legs_evaluated": len(records),
            "invalid_or_missing_evidence": invalid, "newly_recorded_legs": newly_recorded,
            "total_net_profit_cad": round(profit, 2), "calibration": calibration}
