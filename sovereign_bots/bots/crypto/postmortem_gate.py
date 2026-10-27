"""Postmortem gate — a learned veto over patterns that keep failing on process.

Reads the accumulated decision audit (see ``src/learn/credit.py``) and refuses a
new trade whose (coin, direction, regime) pattern has repeatedly produced POOR
decisions — regardless of how attractive the current setup looks.

It cannot invent an opinion: with no history it abstains. It only ever blocks;
it never approves anything the risk layers would not.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

MIN_SAMPLE = 3
POOR_RATE_BLOCK = 0.60


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v


class PostmortemBot(Bot):
    id = "postmortem"
    mandate = ("Refuse patterns whose closed trades were repeatedly graded POOR on "
               "process: the same mistake must not stay available.")
    domains = ("crypto",)   # registered in the crypto pack
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        coin = str(ctx.get("coin") or "").upper()
        direction = str(ctx.get("direction") or "")
        regime = str(ctx.get("regime") or "any")

        # An explicit engine-side block (already resolved) is authoritative.
        explicit = ctx.get("pattern_block")
        if isinstance(explicit, str) and explicit.strip():
            return Vote(self.id, "reject", 0.95, [explicit],
                        {"pattern_block": explicit})

        table = ctx.get("postmortem")
        if not isinstance(table, dict) or not table:
            return Vote(self.id, "abstain", 0.0,
                        ["no_postmortem_history"], {"checked": []})

        keys = []
        if coin and direction:
            keys.append(f"{coin}|{direction}|{regime}")
            if regime != "any":
                keys.append(f"{coin}|{direction}|any")
        checked = []
        for key in keys:
            rec = table.get(key)
            if not isinstance(rec, dict):
                continue
            n = _num(rec.get("n")) or 0.0
            poor_rate = _num(rec.get("poor_rate")) or 0.0
            checked.append({"pattern": key, "n": n, "poor_rate": poor_rate,
                            "blocked": bool(rec.get("blocked"))})
            if n >= MIN_SAMPLE and (rec.get("blocked") or poor_rate >= POOR_RATE_BLOCK):
                return Vote(
                    self.id, "reject", 0.95,
                    [f"pattern_blocked:{key}:poor_rate={poor_rate:.2f},n={int(n)}"],
                    {"checked": checked},
                )

        if not checked:
            return Vote(self.id, "abstain", 0.0,
                        [f"no_history:{','.join(keys) or 'unknown_pattern'}"],
                        {"checked": []})

        return Vote(self.id, "approve", 0.70,
                    [f"history_ok:{len(checked)} pattern(s) checked"], {"checked": checked})
