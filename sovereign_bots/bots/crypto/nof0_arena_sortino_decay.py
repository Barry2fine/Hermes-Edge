"""NOF0 Alpha Arena & Hermes Mnemosyne Rolling Sortino Decay Seat (Soft Edge Seat).

Inspired by `wquguru/nof0` (NOF1 Alpha Arena benchmark) and `NousResearch/hermes`
(`mnemosyne` episodic SQLite memory): tracks rolling per-archetype Sortino ratio
and win-rate decay across the last N closed trades, boosting archetypes in hot
regime alignment and rejecting decayed archetypes (`edge_seat = True`).
"""

from __future__ import annotations
from typing import Any, Dict, Optional
from ...core.bot import Bot, Vote


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class Nof0ArenaSortinoDecayBot(Bot):
    id = "nof0_arena_sortino_decay"
    mandate = ("Evaluate rolling per-archetype Sortino ratio and episodic memory decay "
               "inspired by NOF0 Alpha Arena & Hermes Mnemosyne.")
    domains = ("crypto",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        sortino = _num(ctx.get("archetype_rolling_sortino"))
        sample_n = _num(ctx.get("archetype_sample_n"))

        if sortino is None:
            return Vote(self.id, "abstain", 0.0, ["no_archetype_sortino_inputs"], {})

        n = int(sample_n) if sample_n is not None else 20
        evidence = {"archetype_rolling_sortino": sortino, "archetype_sample_n": n}

        if n < 5:
            return Vote(self.id, "abstain", 0.0, [f"insufficient_archetype_history:{n}"], evidence)
        if sortino <= -0.35:
            return Vote(self.id, "reject", 0.80, [f"archetype_sortino_decayed:{sortino:.2f}"], evidence)
        if sortino >= 1.35:
            return Vote(self.id, "approve", min(0.90, 0.62 + min(sortino, 3.0) * 0.09), [f"archetype_sortino_strong:{sortino:.2f}"], evidence)
        return Vote(self.id, "abstain", 0.0, [f"archetype_sortino_neutral:{sortino:.2f}"], evidence)
