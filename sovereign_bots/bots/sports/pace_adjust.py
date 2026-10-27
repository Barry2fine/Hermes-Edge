"""Pace-adjustment bot — possessions are the denominator of everything.

A player's counting stats are a function of how many possessions the game has.
The same 28-minute night is 20% more valuable in a 105-possession game than in
an 88-possession one. Books price the *player*, but the *matchup* sets the pace.

Projection:

    projected_pace = (team_pace + opp_pace) / 2
    factor         = projected_pace / league_pace
    adjusted_stat  = baseline_projection * factor

Then compare the adjusted projection to the line. This is why a good player in
a slow game can miss an over that looked safe.

(Simple, well-founded, and no public repo does it for you — every minutes/referee
"model" on GitHub is a 0-star dashboard.)
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

DEFAULT_LEAGUE_PACE = 99.0
MAX_FACTOR = 1.25


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def adjust(projection: float, team_pace: float, opp_pace: float,
           league_pace: float = DEFAULT_LEAGUE_PACE) -> Dict[str, Any]:
    if league_pace <= 0:
        return {"adjusted": projection, "factor": 1.0, "projected_pace": None}
    pace = (team_pace + opp_pace) / 2.0
    factor = max(1.0 / MAX_FACTOR, min(MAX_FACTOR, pace / league_pace))
    return {"adjusted": projection * factor, "factor": round(factor, 4),
            "projected_pace": round(pace, 2)}


class PaceAdjustBot(Bot):
    id = "pace_adjust"
    edge_seat = True   # an approve means: pace-adjusted projection vs the line
    mandate = ("Never price a counting stat without the possession count: slow "
               "games shrink every projection and fast games inflate them.")
    domains = ("sports",)
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        proj = _num(ctx.get("projection"))
        team = _num(ctx.get("team_pace"))
        opp = _num(ctx.get("opp_pace"))
        league = _num(ctx.get("league_pace")) or DEFAULT_LEAGUE_PACE
        line = _num(ctx.get("line"))
        side = str(ctx.get("side") or "over").strip().lower()

        evidence: Dict[str, Any] = {"projection": proj, "team_pace": team,
                                    "opp_pace": opp, "league_pace": league,
                                    "line": line, "side": side}
        if proj is None or team is None or opp is None:
            return Vote(self.id, "abstain", 0.0, ["missing:projection/pace"], evidence)

        a = adjust(proj, team, opp, league)
        evidence.update(a)

        if line is None:
            # No line: report the adjustment, take no view.
            return Vote(self.id, "abstain", 0.20,
                        [f"pace_adjusted:{a['adjusted']:.2f} (x{a['factor']:.2f})"], evidence)

        delta = (a["adjusted"] - line) if side == "over" else (line - a["adjusted"])
        scale = max(abs(line), 1.0)
        rel = delta / scale
        evidence["delta_vs_line"] = round(delta, 3)
        evidence["relative"] = round(rel, 4)

        if rel >= 0.05:
            return Vote(self.id, "approve", min(0.80, 0.50 + rel),
                        [f"pace_supports_{side}:{delta:+.2f} vs line {line:g}",
                         f"factor={a['factor']:.2f} pace={a['projected_pace']:g}"], evidence)
        if rel <= -0.05:
            return Vote(self.id, "reject", min(0.80, 0.50 + abs(rel)),
                        [f"pace_against_{side}:{delta:+.2f} vs line {line:g}"], evidence)
        return Vote(self.id, "abstain", 0.25,
                    [f"pace_neutral:{delta:+.2f}"], evidence)
