"""Schedule-spot bot — the game before and after is often the real story.

Teams and players give uneven effort by calendar, not by talent. Classic spots:

* **Letdown** — after beating a contender / a big primetime win.
* **Lookahead / trap** — a weak opponent right before a marquee game.
* **Sandwich** — a soft opponent between two hard ones.
* **4-in-5 / 3-in-4** — schedule density with real minutes.
* **Off a blowout** — starters rested (props under) or garbage time (bench over).

Sourced flags only. No invented narrative: a letdown claim needs a reason
supplied in `evidence`.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from ...core.bot import Bot, Vote

# (flag, points, requires evidence)
FLAGS = {
    "letdown": (-8.0, True),
    "lookahead": (-6.0, True),
    "sandwich": (-5.0, True),
    "trap_game": (-6.0, True),
    "revenge_spot": (6.0, True),
    "must_win": (7.0, True),
    "b2b": (-10.0, False),
    "three_in_four": (-6.0, False),
    "four_in_five": (-9.0, False),
    "rested": (5.0, False),
    "off_blowout_win": (-5.0, True),
    "off_blowout_loss": (4.0, True),
    "road_trip_game_4plus": (-6.0, False),
    "home_stand_start": (4.0, False),
}


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _as_list(x: Any) -> List[Any]:
    if isinstance(x, (list, tuple, set)):
        return list(x)
    if isinstance(x, str) and x.strip():
        return [p.strip() for p in x.split(",") if p.strip()]
    return []


def _evidence_text(ctx: Dict[str, Any]) -> str:
    """Flatten evidence: a list of strings, a dict of {flag: source}, or a string."""
    ev = ctx.get("evidence")
    parts: List[str] = []
    if isinstance(ev, dict):
        parts.extend(str(k) for k in ev.keys())
        parts.extend(str(v) for v in ev.values())
    elif isinstance(ev, (list, tuple, set)):
        parts.extend(str(v) for v in ev)
    elif isinstance(ev, str):
        parts.append(ev)
    return " ".join(parts).lower()


def evaluate_spot(ctx: Dict[str, Any]) -> Dict[str, Any]:
    reasons: List[str] = []
    total = 0.0
    known = 0
    unsourced: List[str] = []

    evidence = _evidence_text(ctx)
    evidence_norm = evidence.replace("_", " ").replace("-", " ")

    for raw in _as_list(ctx.get("spots")):
        key = str(raw).strip().lower()
        if key not in FLAGS:
            continue
        points, needs = FLAGS[key]
        known += 1
        if needs and key not in evidence and key.replace("_", " ") not in evidence_norm:
            unsourced.append(f"unsourced:{key}")
            continue
        total += points
        reasons.append(f"spot:{key}({points:+.0f})")

    rest = _num(ctx.get("rest_days"))
    if rest is not None:
        known += 1
        if rest <= 0:
            total += FLAGS["b2b"][0]
            reasons.append("spot:b2b(-10)")
        elif rest >= 3:
            total += FLAGS["rested"][0]
            reasons.append("spot:rested(+5)")

    back_to_backs = _num(ctx.get("back_to_backs_last_10"))
    if back_to_backs is not None:
        known += 1
        if back_to_backs >= 3:
            total += -4.0
            reasons.append(f"schedule:tight({back_to_backs:g}_b2b_in_10)")

    return {"delta": round(total, 2), "reasons": reasons, "known": known,
            "unsourced": unsourced}


class ScheduleSpotBot(Bot):
    id = "schedule_spot"
    mandate = ("Price the calendar, not just the roster: fade letdown, lookahead "
               "and sandwich spots; credit must-win and revenge games only when "
               "the reason is sourced.")
    domains = ("sports",)
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        r = evaluate_spot(ctx)
        evidence = {"schedule": r, "player": ctx.get("player"), "team": ctx.get("team")}
        if r["known"] == 0:
            return Vote(self.id, "abstain", 0.0, ["no_schedule_signals"], evidence)

        conf = min(0.85, 0.30 + 0.12 * r["known"])
        if r["unsourced"]:
            conf = max(0.20, conf - 0.15)

        if r["delta"] <= -8:
            return Vote(self.id, "reject", conf,
                        [f"bad_spot:{r['delta']:+.0f}"] + r["reasons"][:4], evidence)
        if r["delta"] >= 5:
            return Vote(self.id, "approve", conf,
                        [f"good_spot:{r['delta']:+.0f}"] + r["reasons"][:4], evidence)
        return Vote(self.id, "approve", max(0.30, conf - 0.10),
                    [f"neutral_spot:{r['delta']:+.0f}"] + r["reasons"][:3], evidence)
