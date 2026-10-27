"""Risk-budget bot — the account's own survival veto. VETO.

Budget model: only 10% of the capital above the hard floor may be at risk in
one trade.

    risk   = size * |entry - stop|
    budget = (equity - floor) * 0.10

Rules:
  * equity/floor/size/entry/stop missing -> reject (fail-closed)
  * equity <= floor                      -> reject (floor breached)
  * risk > budget                        -> reject (oversized)
  * otherwise                            -> approve, confidence falls as the
                                            budget is used up
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

RISK_FRACTION = 0.10
MIN_RISK = 1e-12


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(str(x).strip().rstrip("%")) if isinstance(x, str) else float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


class RiskBudgetBot(Bot):
    id = "risk_budget"
    mandate = ("Size is a survival question: never risk more than 10% of the "
               "capital standing above the hard floor, and never trade a "
               "breached floor.")
    domains = ("crypto",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        equity = _num(ctx.get("equity"))
        floor = _num(ctx.get("floor"))
        size = _num(ctx.get("size"))
        entry = _num(ctx.get("entry"))
        stop = _num(ctx.get("stop"))
        evidence: Dict[str, Any] = {
            "equity": equity, "floor": floor, "size": size,
            "entry": entry, "stop": stop,
        }

        for name, val in (("equity", equity), ("floor", floor), ("size", size),
                          ("entry", entry), ("stop", stop)):
            if val is None:
                return Vote(self.id, "reject", 0.0, [f"missing:{name}"], evidence)
        if entry <= 0 or size <= 0:
            return Vote(self.id, "reject", 0.9, ["invalid:non_positive_entry_or_size"],
                        evidence)
        if equity <= floor:
            return Vote(self.id, "reject", 0.95,
                        [f"floor_breached:equity={equity:.2f}<=floor={floor:.2f}"],
                        evidence)

        budget = (equity - floor) * RISK_FRACTION
        risk = size * abs(entry - stop)
        util = risk / budget if budget > MIN_RISK else float("inf")
        evidence.update({"budget": round(budget, 6), "risk": round(risk, 6),
                         "utilisation": None if util == float("inf") else round(util, 4)})

        if budget <= MIN_RISK:
            return Vote(self.id, "reject", 0.95, ["invalid:zero_budget"], evidence)
        if risk > budget:
            return Vote(self.id, "reject", 0.9,
                        [f"oversized:risk={risk:.2f}>budget={budget:.2f}"
                         f"({util:.1%})"], evidence)

        conf = 0.60 + 0.35 * (1.0 - util)
        return Vote(self.id, "approve", min(0.95, conf),
                    [f"risk={risk:.2f}_of_budget={budget:.2f}({util:.1%})"], evidence)
