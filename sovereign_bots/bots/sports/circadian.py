"""Circadian bot — the body clock is part of the matchup.

Travel research in pro sports (e.g. the NHL travel/circadian studies) finds
performance degrades with **eastward** travel and late local start times, and
that back-to-backs compound it. The mechanism is simple: travelling east
advances your body clock (you lose hours), travelling west delays it (easier).

This is a refinement of the generic travel penalty: direction and start time
matter, not just miles.

    shift = signed timezone change (+ = eastward = harder)
    local_start = tip-off hour in the team's body clock
    penalty = 3*max(0, shift) + 1*max(0, -shift) + late-start penalty + b2b
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

W_EAST = 3.0        # points of energy per hour moved east
W_WEST = 1.0        # west is easier
W_LATE = 2.0        # per hour of start after 19:00 local-body time
W_B2B = 4.0
CAP = 18.0


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def load(ctx: Dict[str, Any]) -> Dict[str, Any]:
    shift = _num(ctx.get("tz_shift"))            # + = eastward
    start = _num(ctx.get("start_hour_local"))    # local body-clock hour
    rest = _num(ctx.get("rest_days"))
    miles = _num(ctx.get("travel_miles"))

    known = sum(1 for v in (shift, start, rest, miles) if v is not None)
    penalty = 0.0
    reasons = []

    if shift is not None:
        if shift > 0:
            penalty += W_EAST * shift
            reasons.append(f"eastward_travel:{shift:g}h")
        elif shift < 0:
            penalty += W_WEST * abs(shift)
            reasons.append(f"westward_travel:{abs(shift):g}h")

    if start is not None and start > 19:
        penalty += W_LATE * (start - 19)
        reasons.append(f"late_start:{start:g}:00 body-clock")

    if rest is not None and rest <= 0:
        penalty += W_B2B
        reasons.append("back_to_back")

    if miles is not None and miles >= 2000:
        penalty += 2.0
        reasons.append(f"long_haul:{miles:.0f}mi")

    return {"penalty": round(min(CAP, penalty), 2), "known": known,
            "reasons": reasons, "shift": shift, "start": start}


class CircadianBot(Bot):
    id = "circadian"
    mandate = ("Respect the body clock: eastward travel, late body-clock starts "
               "and back-to-backs degrade performance beyond what miles suggest.")
    domains = ("sports",)
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        r = load(ctx)
        evidence = {"circadian": r}
        if r["known"] == 0:
            return Vote(self.id, "abstain", 0.0, ["no_travel_signals"], evidence)

        conf = min(0.80, 0.30 + 0.12 * r["known"])
        if r["penalty"] >= 8:
            return Vote(self.id, "reject", conf,
                        [f"circadian_risk:{r['penalty']:+.0f}"] + r["reasons"][:3], evidence)
        if r["penalty"] >= 4:
            return Vote(self.id, "approve", 0.40,
                        [f"circadian_drag:{r['penalty']:+.0f}"] + r["reasons"][:3], evidence)
        return Vote(self.id, "approve", conf,
                    [f"circadian_ok:{r['penalty']:+.0f}"] + r["reasons"][:2], evidence)
