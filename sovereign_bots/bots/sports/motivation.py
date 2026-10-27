"""Motivation & fatigue bot — the human layer the numbers miss.

Humans are not machines. A player on the second night of a back-to-back, after a
3-hour flight and 38 minutes the night before, is not the same player as one
rested at home with the playoffs on the line. This bot scores that.

Two components:

* **Fatigue** (physical) — rest days, games in the last 7, travel, timezone
  shift, minutes load, age.
* **Motivation** (psychological) — stakes (playoff / contract year / return /
  dead rubber) and *sourced* narrative flags (revenge game, homecoming,
  benched publicly, trade rumour, personal issue).

Hard rule: **no invented vibes.** A narrative claim only counts when the
context supplies a source. Un sourced "he looks unmotivated" is `unknown`,
never a reason to size up or down. That is the difference between reading a
human and making one up.

Output is advisory (no veto): it can fade a weak spot or add confidence to a
strong one, but it can never override the risk bots.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from ...core.bot import Bot, Vote

# ---- thresholds -----------------------------------------------------------
LOW_TIER = 40.0
HIGH_TIER = 65.0
MIN_SIGNALS = 2          # at least this many known inputs to have an opinion
MAX_PROB_ADJUST = 0.04   # +/-4 points of probability at the extremes

# fatigue weights (points off 100)
W_B2B = 15.0
W_THREE_IN_FOUR = 10.0
W_TRAVEL = 0.008         # per mile, capped
W_TRAVEL_CAP = 8.0
W_TZ = 2.5               # per hour of shift, capped
W_TZ_CAP = 7.0
W_MINUTES = 0.18         # per minute above 32
W_AGE = 1.5              # per year above 33
W_AGE_CAP = 6.0

# motivation weights
STAKES = {
    "playoff": 12.0,
    "playoffs": 12.0,
    "elimination": 14.0,
    "contract_year": 8.0,
    "contract": 8.0,
    "return": 6.0,
    "return_from_injury": 6.0,
    "routine": 0.0,
    "regular": 0.0,
    "dead_rubber": -15.0,
    "meaningless": -15.0,
    "tanking": -12.0,
}

# narrative flags: (weight, requires evidence)
NARRATIVE = {
    "revenge": (6.0, True),
    "homecoming": (4.0, True),
    "milestone": (3.0, True),
    "benched_publicly": (5.0, True),
    "trade_rumor": (-4.0, True),
    "personal_issue": (-10.0, True),
    "feud": (4.0, True),
    "contract_talks_stalled": (-5.0, True),
}


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _clamp(v: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, v))


def _as_list(x: Any) -> List[Any]:
    if isinstance(x, (list, tuple, set)):
        return list(x)
    if isinstance(x, str) and x.strip():
        return [p.strip() for p in x.split(",") if p.strip()]
    return []


def fatigue_penalty(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Points off a 100-point energy budget, with the reasons that caused them."""
    reasons: List[str] = []
    total = 0.0
    known = 0

    rest = _num(ctx.get("rest_days"))
    if rest is not None:
        known += 1
        if rest <= 0:
            total += W_B2B
            reasons.append(f"back_to_back:{rest:g}d")
        elif rest == 1:
            total += W_B2B * 0.4
            reasons.append("one_day_rest")

    g7 = _num(ctx.get("games_last_7"))
    if g7 is not None:
        known += 1
        if g7 >= 4:
            total += W_THREE_IN_FOUR
            reasons.append(f"heavy_schedule:{g7:g}_in_7")

    miles = _num(ctx.get("travel_miles"))
    if miles is not None:
        known += 1
        if miles > 500:
            add = min(W_TRAVEL_CAP, miles * W_TRAVEL)
            total += add
            reasons.append(f"travel:{miles:.0f}mi")

    tz = _num(ctx.get("tz_shift"))
    if tz is not None:
        known += 1
        shift = abs(tz)
        if shift >= 2:
            add = min(W_TZ_CAP, shift * W_TZ)
            total += add
            reasons.append(f"tz_shift:{shift:g}h")

    mins = _num(ctx.get("minutes_last_game"))
    if mins is not None:
        known += 1
        if mins > 32:
            total += (mins - 32) * W_MINUTES
            reasons.append(f"minutes_load:{mins:g}")

    age = _num(ctx.get("age"))
    if age is not None:
        known += 1
        if age > 33:
            total += min(W_AGE_CAP, (age - 33) * W_AGE)
            reasons.append(f"age:{age:g}")

    return {"penalty": round(total, 2), "reasons": reasons, "known": known}


def motivation_delta(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Points added/subtracted for stakes and *sourced* narrative."""
    reasons: List[str] = []
    total = 0.0
    known = 0
    unsourced: List[str] = []

    stakes = str(ctx.get("stakes") or "").strip().lower()
    if stakes:
        known += 1
        if stakes in STAKES:
            total += STAKES[stakes]
            reasons.append(f"stakes:{stakes}({STAKES[stakes]:+.0f})")
        else:
            unsourced.append(f"stakes_unknown:{stakes}")

    _ev = ctx.get("evidence")
    if isinstance(_ev, dict):
        _parts = list(_ev.keys()) + list(_ev.values())
    elif isinstance(_ev, (list, tuple, set)):
        _parts = list(_ev)
    elif isinstance(_ev, str):
        _parts = [_ev]
    else:
        _parts = []
    evidence = " ".join(str(x) for x in _parts).lower()
    evidence_norm = evidence.replace("_", " ").replace("-", " ")
    for flag in _as_list(ctx.get("narrative")):
        key = str(flag).strip().lower()
        if key not in NARRATIVE:
            continue
        weight, needs_evidence = NARRATIVE[key]
        known += 1
        # Accept either spelling: "personal_issue" or "personal issue".
        if needs_evidence and key not in evidence and key.replace("_", " ") not in evidence_norm:
            unsourced.append(f"unsourced:{key}")
            continue
        total += weight
        reasons.append(f"narrative:{key}({weight:+.0f})")

    return {"delta": round(total, 2), "reasons": reasons, "known": known,
            "unsourced": unsourced}


def score(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """0..100 motivation/energy score plus the tier and adjustments."""
    fat = fatigue_penalty(ctx)
    mot = motivation_delta(ctx)
    base = 60.0
    raw = base - fat["penalty"] + mot["delta"]
    value = _clamp(raw, 0.0, 100.0)

    if value >= HIGH_TIER:
        tier = "high"
    elif value >= LOW_TIER:
        tier = "neutral"
    else:
        tier = "low"

    # Confidence: how much do we actually know, and how much of it is sourced?
    known = fat["known"] + mot["known"]
    conf = _clamp(0.30 + 0.10 * known, 0.30, 0.90)
    if mot["unsourced"]:
        conf = max(0.20, conf - 0.15)
    if known < MIN_SIGNALS:
        conf = 0.0

    adjust = ((value - 50.0) / 50.0) * MAX_PROB_ADJUST

    return {
        "score": round(value, 1),
        "tier": tier,
        "confidence": round(conf, 3),
        "fatigue": fat,
        "motivation": mot,
        "known_signals": known,
        "prob_adjust": round(adjust, 4),
        "reasons": fat["reasons"] + mot["reasons"] + mot["unsourced"],
    }


def apply_to_probability(model_p: Optional[float], result: Dict[str, Any]) -> Optional[float]:
    """Shift a model probability by the motivation read (only with confidence)."""
    try:
        p = float(model_p)
    except (TypeError, ValueError):
        return model_p
    if not math.isfinite(p):
        return model_p
    conf = float(result.get("confidence") or 0.0)
    if conf <= 0:
        return p
    return round(_clamp(p + result["prob_adjust"] * conf, 0.001, 0.999), 6)


class MotivationBot(Bot):
    id = "motivation"
    mandate = ("Read the human: rest, travel, minutes load, stakes and sourced "
               "narrative. Fade a tired or disengaged spot, back a motivated one, "
               "and never invent a mood that has no source.")
    domains = ("sports", "polymarket")
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        r = score(ctx)
        evidence = {"motivation": r, "player": ctx.get("player"),
                    "model_p": ctx.get("model_p"),
                    "adjusted_p": apply_to_probability(ctx.get("model_p"), r)}

        if r["known_signals"] < MIN_SIGNALS:
            return Vote(self.id, "abstain", 0.0,
                        [f"insufficient_signals:{r['known_signals']}<{MIN_SIGNALS}"],
                        evidence)

        conf = r["confidence"]
        if r["tier"] == "low":
            return Vote(self.id, "reject", conf,
                        [f"low_motivation:{r['score']:.0f}/100"] + r["reasons"][:4],
                        evidence)
        if r["tier"] == "high":
            return Vote(self.id, "approve", conf,
                        [f"high_motivation:{r['score']:.0f}/100"] + r["reasons"][:4],
                        evidence)
        return Vote(self.id, "approve", max(0.30, conf - 0.10),
                    [f"neutral_motivation:{r['score']:.0f}/100"] + r["reasons"][:3],
                    evidence)
