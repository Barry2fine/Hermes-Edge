"""Sports Pattern Post-Mortem Gate (Hard VETO).

Blocks `(league, market_type, side)` prop patterns that have accumulated >=3
graded wagers with a poor-process rate >= 60% (e.g., chasing unconfirmed backup
minutes or low-volume NHL SOG unders).
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


class SportsPostmortemBot(Bot):
    id = "sports_postmortem"
    mandate = ("Refuse sports prop patterns whose historical post-mortem process "
               "grade is repeatedly POOR (poor_rate >= 60% over n >= 3).")
    domains = ("sports",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        table = ctx.get("sports_postmortem")
        key = str(ctx.get("pattern_key") or "")
        if not isinstance(table, dict) or not key or key not in table:
            return Vote(self.id, "abstain", 0.0, ["no_sports_postmortem_record"], {})

        rec = table.get(key) or {}
        n = _num(rec.get("n")) or 0.0
        poor_rate = _num(rec.get("poor_rate")) or 0.0
        blocked = bool(rec.get("blocked"))
        evidence = {"pattern_key": key, "n": n, "poor_rate": poor_rate, "blocked": blocked}

        if n >= 3 and (blocked or poor_rate >= 0.60):
            return Vote(self.id, "reject", 0.94, [f"sports_pattern_blocked:{key}:poor_rate={poor_rate:.2f}"], evidence)
        return Vote(self.id, "approve", 0.75, [f"sports_pattern_clean:{key}"], evidence)
