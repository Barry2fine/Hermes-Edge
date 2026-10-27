"""Post-trade decision audit — grade the PROCESS, not the P&L.

A win on a bad entry is luck; a loss on a clean entry is variance. Rewarding the
first and punishing the second is how a system learns to gamble. This module
separates them using excursion analysis (the one technique that generalises
across strategies):

    MFE  maximum favourable excursion  — how far price ran our way, in R
    MAE  maximum adverse excursion     — how far it went against, in R

From those we can tell the difference between
  * an exit error        (MFE >> realised  -> we left the move on the table)
  * an entry error       (MAE ~ -1R then immediate stop, MFE ~ 0)
  * a target error       (MFE never reached half the planned target)
  * a stop-placement error (stopped out, then the move happened anyway)
  * a lucky win          (profit, but we were deep underwater first)

Pure stdlib, no network, never raises.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Sequence

# Decision verdicts
SMART = "SMART"
MIXED = "MIXED"
POOR = "POOR"

# Canonical error tags
TAG_CHASED = "chased_entry"
TAG_STOP_TIGHT = "stop_too_tight"
TAG_TARGET_FAR = "target_unreachable"
TAG_EARLY_EXIT = "premature_exit"
TAG_OVERSIZED = "oversized"
TAG_NO_STOP = "missing_stop"

MIN_TARGET_R = 1.2        # below this the ticket was never worth taking
CLEAN_MAE_R = -1.1        # a stop-out that honoured the plan


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def excursions(trade: Dict[str, Any], path: Sequence[Dict[str, Any]] = ()) -> Dict[str, float]:
    """MFE/MAE in price and in R, from a candle path or precomputed fields."""
    entry = _num(trade.get("entry"))
    stop = _num(trade.get("stop"))
    if entry is None or stop is None or entry == stop:
        return {"mfe_r": 0.0, "mae_r": 0.0, "mfe_pct": 0.0, "mae_pct": 0.0}
    long = str(trade.get("direction") or "long").lower() == "long"
    stop_dist = abs(entry - stop)
    fav_px = adv_px = 0.0

    for c in path or ():
        if not isinstance(c, dict):
            continue
        hi, lo = _num(c.get("high")), _num(c.get("low"))
        if hi is None or lo is None:
            continue
        if long:
            fav_px = max(fav_px, hi - entry)
            adv_px = max(adv_px, entry - lo)
        else:
            fav_px = max(fav_px, entry - lo)
            adv_px = max(adv_px, hi - entry)

    if not path:
        fav_px = _num(trade.get("mfe_px")) or 0.0
        adv_px = _num(trade.get("mae_px")) or 0.0

    return {
        "mfe_r": round(max(0.0, fav_px) / stop_dist, 4),
        "mae_r": round(-max(0.0, adv_px) / stop_dist, 4),
        "mfe_pct": round(max(0.0, fav_px) / entry * 100.0, 4),
        "mae_pct": round(-max(0.0, adv_px) / entry * 100.0, 4),
    }


def grade(trade: Dict[str, Any], path: Sequence[Dict[str, Any]] = (),
          ctx: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Grade one closed trade. Returns a verdict, score and error tags.

    ``ctx`` is the decision context captured AT ENTRY (rsi, ema_dist_pct,
    atr_pct, alignment, risk_budget). Missing fields simply lose their points —
    they are never invented.
    """
    ctx = ctx or {}
    entry = _num(trade.get("entry"))
    stop = _num(trade.get("stop"))
    target = _num(trade.get("target"))
    r = _num(trade.get("r"))
    long = str(trade.get("direction") or "long").lower() == "long"
    tags: List[str] = []
    notes: List[str] = []

    if entry is None or stop is None or entry == stop:
        tags.append(TAG_NO_STOP)
        return {"verdict": POOR, "score": 0, "tags": tags, "outcome": "unknown",
                "notes": ["no usable entry/stop — cannot grade"], "excursions": {}}

    stop_dist = abs(entry - stop)
    rr_planned = (abs(target - entry) / stop_dist) if target is not None else None
    ex = excursions(trade, path)
    mfe_r, mae_r = ex["mfe_r"], ex["mae_r"]
    if r is None:                                   # derive R from the exit price
        exit_px = _num(trade.get("exit_price"))
        if exit_px is not None:
            r = ((exit_px - entry) if long else (entry - exit_px)) / stop_dist
    r = r if r is not None else 0.0
    outcome = "win" if r > 0 else ("loss" if r < 0 else "scratch")

    score = 50

    # --- 1. entry quality (was the decision taken at a sane price?) ----------
    rsi = _num(ctx.get("rsi"))
    ema_dist = _num(ctx.get("ema_dist_pct"))
    chased = False
    if rsi is not None:
        if (long and rsi > 68) or (not long and rsi < 32):
            chased = True
    if ema_dist is not None and abs(ema_dist) > 5.0:
        chased = True
    if chased:
        tags.append(TAG_CHASED)
        score -= 20
    else:
        score += 12
        notes.append("entry was not extended")

    # --- 2. stop placement vs the excursion it actually faced ---------------
    if mae_r <= -0.95 and mfe_r >= 1.0 and r < 0:
        # stopped out, yet price later offered a full R in our favour
        tags.append(TAG_STOP_TIGHT)
        score -= 15
    elif mae_r >= CLEAN_MAE_R:
        score += 8
        notes.append("stop honoured the plan")

    # --- 3. was the target realistic? ---------------------------------------
    if rr_planned is not None and rr_planned < MIN_TARGET_R:
        score -= 15
        notes.append(f"planned R:R only {rr_planned:.2f}")
    # A clean stop-out is not a target error: we were taken out at plan before
    # the target could be reached. Only flag targets when the trade was NOT a
    # planned stop-out (or price never moved at all).
    if (rr_planned is not None and mfe_r < rr_planned * 0.5 and mfe_r < 1.0
            and (r > -0.95 or mfe_r < 0.25)):
        tags.append(TAG_TARGET_FAR)
        score -= 12

    # --- 4. exit quality: did we capture what the market offered? -----------
    capture = (r / mfe_r) if mfe_r > 0.25 else None
    if capture is not None:
        if capture < 0.5 and mfe_r >= max(1.0, (rr_planned or 1.0)):
            tags.append(TAG_EARLY_EXIT)
            score -= 15
        elif capture >= 0.7:
            score += 12
            notes.append(f"captured {capture:.0%} of the favourable move")

    # --- 5. sizing discipline ------------------------------------------------
    risk = _num(trade.get("risk_usd"))
    budget = _num(ctx.get("risk_budget"))
    if risk is not None and budget is not None and budget > 0 and risk > budget * 1.05:
        tags.append(TAG_OVERSIZED)
        score -= 20

    score = max(0, min(100, score))
    verdict = SMART if score >= 65 else (POOR if score < 40 else MIXED)

    # --- outcome vs process: the honest label --------------------------------
    if outcome == "win" and verdict == POOR:
        label = "LUCKY_WIN"
    elif outcome == "win":
        label = "EARNED_WIN" if verdict == SMART else "MIXED_WIN"
    elif outcome == "loss" and verdict == SMART:
        label = "GOOD_LOSS"
    elif outcome == "loss":
        label = "BAD_LOSS" if verdict == POOR else "MIXED_LOSS"
    else:
        label = "SCRATCH"

    return {
        "verdict": verdict,
        "label": label,
        "score": score,
        "tags": tags,
        "outcome": outcome,
        "r": round(r, 4),
        "rr_planned": None if rr_planned is None else round(rr_planned, 3),
        "capture_ratio": None if capture is None else round(capture, 3),
        "notes": notes,
        "excursions": ex,
    }


def summarize(grades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    """Aggregate verdicts: process hit-rate and the dominant failure mode."""
    grades = [g for g in grades if isinstance(g, dict) and g.get("verdict")]
    n = len(grades)
    if not n:
        return {"n": 0, "smart_rate": 0.0, "poor_rate": 0.0, "lucky_wins": 0,
                "good_losses": 0, "top_tag": None, "tags": {}}
    smart = sum(1 for g in grades if g["verdict"] == SMART)
    poor = sum(1 for g in grades if g["verdict"] == POOR)
    tag_counts: Dict[str, int] = {}
    for g in grades:
        for t in g.get("tags") or []:
            tag_counts[t] = tag_counts.get(t, 0) + 1
    top = max(tag_counts.items(), key=lambda kv: kv[1])[0] if tag_counts else None
    return {
        "n": n,
        "smart_rate": round(smart / n, 4),
        "poor_rate": round(poor / n, 4),
        "lucky_wins": sum(1 for g in grades if g.get("label") == "LUCKY_WIN"),
        "good_losses": sum(1 for g in grades if g.get("label") == "GOOD_LOSS"),
        "top_tag": top,
        "tags": tag_counts,
    }
