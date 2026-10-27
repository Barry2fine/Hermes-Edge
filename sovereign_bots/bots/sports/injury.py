"""Injury gate: an unavailable player has no distribution worth pricing.

For player markets this is a veto: confirmed out or unknown status is refused,
questionable is allowed only with low confidence. Team-level markets survive a
single injury, so they are graded rather than vetoed.
"""

from __future__ import annotations

from typing import Any, Dict

from ...core.bot import Bot, Vote
from .starter_gate import is_team_market

CONFIRMED_OUT = "confirmed_out"
QUESTIONABLE = "questionable"
HEALTHY = "healthy"
UNKNOWN = "unknown"

_OUT_ALIASES = {"confirmed_out", "out", "injured_out", "out_for_season", "inactive", "ir"}
_QUESTIONABLE_ALIASES = {
    "questionable", "doubtful", "gtd", "game_time_decision", "day_to_day",
    "limited", "probable_limited",
}
_HEALTHY_ALIASES = {"healthy", "active", "cleared", "ok", "fit", "probable", "available"}


def normalize_status(value: Any) -> str:
    """Map a raw status onto one of the four contract values (default unknown)."""
    if value is None or isinstance(value, bool):
        return UNKNOWN
    text = str(value).strip().lower().replace(" ", "_").replace("-", "_")
    if text in _OUT_ALIASES:
        return CONFIRMED_OUT
    if text in _QUESTIONABLE_ALIASES:
        return QUESTIONABLE
    if text in _HEALTHY_ALIASES:
        return HEALTHY
    return UNKNOWN


class InjuryBot(Bot):
    """Veto player markets with unverified or confirmed-absent players."""

    id = "injury"
    mandate = (
        "Veto player markets when the player is confirmed out or the status is "
        "unknown; allow questionable only at low confidence; pass healthy cleanly."
    )
    domains = ("sports",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        market = ctx.get("market")
        raw = ctx.get("injury_status")
        status = normalize_status(raw)
        team_level = is_team_market(market)
        evidence = {
            "market": market,
            "injury_status": raw,
            "normalized": status,
            "team_level": team_level,
        }

        if team_level:
            conf = {
                HEALTHY: 0.85,
                QUESTIONABLE: 0.70,
                UNKNOWN: 0.60,
                CONFIRMED_OUT: 0.55,
            }[status]
            return Vote(
                self.id, "approve", conf,
                [f"team-level market {market!r}: single-player injury is not decisive "
                 f"(status={status})"],
                evidence,
            )

        if status == CONFIRMED_OUT:
            return Vote(
                self.id, "reject", 0.95,
                [f"player confirmed out for {market!r}: no position"],
                evidence,
            )

        if status == UNKNOWN:
            return Vote(
                self.id, "reject", 0.75,
                [f"injury status unknown/unrecognised for {market!r}: fail closed"],
                evidence,
            )

        if status == QUESTIONABLE:
            return Vote(
                self.id, "approve", 0.35,
                [f"player questionable for {market!r}: allowed, low confidence"],
                evidence,
            )

        return Vote(
            self.id, "approve", 0.85,
            [f"player healthy for {market!r}"],
            evidence,
        )
