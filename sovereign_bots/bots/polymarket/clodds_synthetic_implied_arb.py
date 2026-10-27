"""CloddsBot Synthetic Implied Probability & Orderbook Imbalance Seat (Soft Edge Seat).

Inspired by `alsk1992/CloddsBot` (121-skill prediction-market & futures terminal):
compares Polymarket CLOB mid probability against synthetic implied probability
derived from live perp/options/sports books (`clodds_synthetic_p`) plus L2 order-book
imbalance (`clodds_obi`), capturing cross-venue lead-lag alpha (`edge_seat = True`).
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


class CloddsSyntheticImpliedArbBot(Bot):
    id = "clodds_synthetic_implied_arb"
    mandate = ("Compare Polymarket CLOB probability against CloddsBot-style synthetic "
               "derivatives/sportsbook implied probability and L2 order-book imbalance.")
    domains = ("polymarket",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        poly_p = _num(ctx.get("market_p"))
        synth_p = _num(ctx.get("clodds_synthetic_p"))
        obi = _num(ctx.get("clodds_obi"))

        if poly_p is None or synth_p is None:
            return Vote(self.id, "abstain", 0.0, ["no_clodds_synthetic_inputs"], {})

        div = synth_p - poly_p
        obi_val = obi if obi is not None else 0.0
        evidence = {"market_p": poly_p, "clodds_synthetic_p": synth_p, "divergence": round(div, 4), "clodds_obi": obi_val}

        if div >= 0.045 and obi_val >= -0.10:
            return Vote(self.id, "approve", min(0.92, 0.60 + div * 3.0), [f"positive_synthetic_lead_arb:+{div*100:.1f}c"], evidence)
        if div <= -0.040:
            return Vote(self.id, "reject", 0.80, [f"negative_synthetic_divergence:{div*100:.1f}c"], evidence)
        return Vote(self.id, "abstain", 0.0, ["synthetic_spread_within_band"], evidence)
