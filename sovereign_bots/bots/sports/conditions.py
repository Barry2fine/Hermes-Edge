"""Conditions bot — weather, altitude, venue and travel realism.

Outdoor games are played in weather, not in spreadsheets. Wind suppresses
passing and kicking; rain raises fumbles and lowers pace; cold suppresses
scoring; altitude lifts distance and pace; a dome removes it all.

Only supplied conditions count. Unknown stays unknown.
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


def conditions_read(ctx: Dict[str, Any]) -> Dict[str, Any]:
    reasons: List[str] = []
    delta = 0.0
    known = 0

    indoor = ctx.get("indoor")
    if isinstance(indoor, bool):
        known += 1
        if indoor:
            reasons.append("indoor:weather_neutral")

    wind = _num(ctx.get("wind_mph"))
    if wind is not None:
        known += 1
        if wind >= 20:
            delta -= 8.0
            reasons.append(f"high_wind:{wind:g}mph")
        elif wind >= 12:
            delta -= 4.0
            reasons.append(f"wind:{wind:g}mph")

    precip = _num(ctx.get("precip_pct"))
    if precip is not None and precip > 1.0:
        precip = precip / 100.0
    if precip is not None:
        known += 1
        if precip >= 0.5:
            delta -= 6.0
            reasons.append(f"rain:{precip:.0%}")
        elif precip >= 0.25:
            delta -= 3.0
            reasons.append(f"damp:{precip:.0%}")

    temp = _num(ctx.get("temp_f"))
    if temp is not None:
        known += 1
        if temp <= 25:
            delta -= 5.0
            reasons.append(f"deep_cold:{temp:g}F")
        elif temp <= 38:
            delta -= 2.0
            reasons.append(f"cold:{temp:g}F")
        elif temp >= 92:
            delta -= 3.0
            reasons.append(f"extreme_heat:{temp:g}F")

    alt = _num(ctx.get("altitude_ft"))
    if alt is not None:
        known += 1
        if alt >= 4000:
            delta += 4.0
            reasons.append(f"altitude:{alt:.0f}ft")

    venue_change = _num(ctx.get("neutral_site")) if _num(ctx.get("neutral_site")) is not None else None
    if ctx.get("neutral_site") is True:
        known += 1
        delta -= 2.0
        reasons.append("neutral_site:no_home_edge")

    return {"delta": round(delta, 2), "known": known, "reasons": reasons}


class ConditionsBot(Bot):
    id = "conditions"
    mandate = ("Play the weather: wind, rain, cold and altitude change scoring, "
               "pace and kicking. Ignore conditions you were not given.")
    domains = ("sports",)
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        r = conditions_read(ctx)
        evidence = {"conditions": r}
        if r["known"] == 0:
            return Vote(self.id, "abstain", 0.0, ["no_conditions_data"], evidence)

        conf = min(0.75, 0.30 + 0.10 * r["known"])
        if r["delta"] <= -6:
            return Vote(self.id, "reject", conf,
                        [f"hostile_conditions:{r['delta']:+.0f}"] + r["reasons"][:3], evidence)
        if r["delta"] >= 3:
            return Vote(self.id, "approve", conf,
                        [f"favourable_conditions:{r['delta']:+.0f}"] + r["reasons"][:3], evidence)
        return Vote(self.id, "approve", max(0.30, conf - 0.10),
                    [f"neutral_conditions:{r['delta']:+.0f}"] + r["reasons"][:2], evidence)
