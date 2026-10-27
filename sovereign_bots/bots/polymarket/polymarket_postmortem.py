"""Polymarket Pattern Post-Mortem Gate (Hard VETO).

Blocks prediction-market `(category, direction, horizon)` patterns whose closed
trades were repeatedly graded POOR on process (`poor_rate >= 60%` over `n >= 3`).
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


class PolymarketPostmortemBot(Bot):
    id = "polymarket_postmortem"
    mandate = ("Refuse Polymarket category/horizon patterns that repeatedly "
               "graded POOR in post-mortem audits (poor_rate >= 60%, n >= 3).")
    domains = ("polymarket",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        table = ctx.get("polymarket_postmortem")
        key = str(ctx.get("pattern_key") or "")
        if not isinstance(table, dict) or not key or key not in table:
            return Vote(self.id, "abstain", 0.0, ["no_polymarket_postmortem_record"], {})

        rec = table.get(key) or {}
        n = _num(rec.get("n")) or 0.0
        poor_rate = _num(rec.get("poor_rate")) or 0.0
        blocked = bool(rec.get("blocked"))
        evidence = {"pattern_key": key, "n": n, "poor_rate": poor_rate, "blocked": blocked}

        if n >= 3 and (blocked or poor_rate >= 0.60):
            return Vote(self.id, "reject", 0.94, [f"poly_pattern_blocked:{key}:poor_rate={poor_rate:.2f}"], evidence)
        return Vote(self.id, "approve", 0.75, [f"poly_pattern_clean:{key}"], evidence)
