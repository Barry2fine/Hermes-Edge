"""Starter gate: never price a market whose participants are not yet certain.

Player props and any starter-dependent derivative are only tradeable when a
confirmed lineup exists. Team-level markets (match winner, handicap, totals) do
not depend on one individual starter, so they pass the gate.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

# Tokens the contract treats as team-level markets.
TEAM_TOKENS = ("h2h", "spread", "total")
# Tokens that force player-level treatment even if a team token is present
# (e.g. "player_total_shots" is a prop, not a match total).
PLAYER_TOKENS = ("player", "prop", "anytime", "first_", "to_score")


def _as_bool(value: Any) -> Optional[bool]:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        if value == 1:
            return True
        if value == 0:
            return False
        return None
    if isinstance(value, str):
        s = value.strip().lower()
        if s in ("true", "yes", "y", "1", "confirmed"):
            return True
        if s in ("false", "no", "n", "0", "unconfirmed", "not_confirmed"):
            return False
    return None


def is_team_market(market: Any) -> bool:
    """True only for team-level markets; unknown/empty markets are NOT team-level."""
    text = str(market or "").strip().lower()
    if not text:
        return False
    if any(tok in text for tok in PLAYER_TOKENS):
        return False
    return any(tok in text for tok in TEAM_TOKENS)


class StarterGateBot(Bot):
    """Veto anything starter-dependent without a confirmed starter."""

    id = "starter_gate"
    mandate = (
        "Veto starter-dependent markets unless the starter is confirmed; team-level "
        "markets are exempt because no single starter decides them."
    )
    domains = ("sports",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        market = ctx.get("market")
        confirmed = _as_bool(ctx.get("starter_confirmed"))
        evidence = {
            "market": market,
            "starter_confirmed": ctx.get("starter_confirmed"),
            "team_level": is_team_market(market),
        }

        if is_team_market(market):
            return Vote(
                self.id, "approve", 0.90,
                [f"team-level market {market!r}: starter-independent"],
                evidence,
            )

        if confirmed is True:
            return Vote(
                self.id, "approve", 0.90,
                [f"starter confirmed for {market!r}"],
                evidence,
            )

        if confirmed is False:
            return Vote(
                self.id, "reject", 0.95,
                [f"starter not confirmed for player market {market!r}"],
                evidence,
            )

        return Vote(
            self.id, "reject", 0.80,
            [f"starter_confirmed missing/unparseable for {market!r}: fail closed"],
            evidence,
        )
