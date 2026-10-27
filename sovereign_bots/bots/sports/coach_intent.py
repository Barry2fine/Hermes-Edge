"""Coach-intent bot — minutes leverage is set by the coach, not the player.

Player props live and die on minutes. A coach protecting a lead, resting
starters before a playoff run, or chasing a blowout changes the distribution
far more than the player's talent does.

Signals: expected spread (blowout risk), playoff seeding locked, tanking,
load management, back-to-back rest policy, minutes trend.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from ...core.bot import Bot, Vote


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def coach_read(ctx: Dict[str, Any]) -> Dict[str, Any]:
    reasons: List[str] = []
    delta = 0.0
    known = 0

    spread = _num(ctx.get("expected_spread"))
    if spread is not None:
        known += 1
        mag = abs(spread)
        if mag >= 12:
            delta -= 7.0
            reasons.append(f"blowout_risk:spread={spread:+g}")
        elif mag >= 8:
            delta -= 3.0
            reasons.append(f"wide_spread:{spread:+g}")

    if ctx.get("seeding_locked") is True:
        known += 1
        delta -= 8.0
        reasons.append("seeding_locked:rest_risk")

    if ctx.get("tanking") is True:
        known += 1
        delta -= 9.0
        reasons.append("tanking:unpredictable_minutes")

    if ctx.get("load_management") is True:
        known += 1
        delta -= 10.0
        reasons.append("load_management")

    policy = _num(ctx.get("b2b_rest_policy"))
    if policy is not None:
        known += 1
        if policy >= 0.5:
            delta -= 5.0
            reasons.append(f"coach_rests_b2b:{policy:.0%}")

    trend = _num(ctx.get("minutes_trend"))
    if trend is not None:
        known += 1
        if trend <= -4:
            delta -= 5.0
            reasons.append(f"minutes_trend_down:{trend:+.1f}")
        elif trend >= 4:
            delta += 4.0
            reasons.append(f"minutes_trend_up:{trend:+.1f}")

    if ctx.get("must_play") is True:
        known += 1
        delta += 6.0
        reasons.append("must_play:full_minutes_expected")

    return {"delta": round(delta, 2), "known": known, "reasons": reasons}


class CoachIntentBot(Bot):
    id = "coach_intent"
    mandate = ("Minutes belong to the coach: fade blowout, rest, tanking and "
               "load-management spots; credit must-play games with a rising "
               "minutes trend.")
    domains = ("sports",)
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        r = coach_read(ctx)
        evidence = {"coach": r, "player": ctx.get("player")}
        if r["known"] == 0:
            return Vote(self.id, "abstain", 0.0, ["no_coach_signals"], evidence)

        conf = min(0.85, 0.30 + 0.12 * r["known"])
        if r["delta"] <= -7:
            return Vote(self.id, "reject", conf,
                        [f"minutes_risk:{r['delta']:+.0f}"] + r["reasons"][:3], evidence)
        if r["delta"] >= 4:
            return Vote(self.id, "approve", conf,
                        [f"minutes_support:{r['delta']:+.0f}"] + r["reasons"][:3], evidence)
        return Vote(self.id, "approve", max(0.30, conf - 0.10),
                    [f"neutral_minutes:{r['delta']:+.0f}"] + r["reasons"][:2], evidence)
