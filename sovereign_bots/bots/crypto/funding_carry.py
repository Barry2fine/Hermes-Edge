"""Funding-carry bot — get paid to hold, or refuse to pay to hold.

Perpetuals pay funding every few hours. Over a day that is real money: 1bp per
8h is ~11% annualised. Two things follow:

* If our side **receives** funding, carry is a tailwind — a small edge every hour.
* If our side **pays** extreme funding, carry is a silent bleed that eats a thin
  edge. At 5bp/8h we are paying ~55% annualised just to be in the trade.

Also integrates `evaluate_cross_exchange_funding_and_basis` when mark/index basis
or Hyperliquid cross-venue funding (`hyperliquid_funding_8h_bps`) is supplied.
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

from ...core.autonomous_alpha_daemons import evaluate_cross_exchange_funding_and_basis
from ...core.bot import Bot, Vote

EXTREME_RATE = 0.0005      # 5bp per 8h = crowded
MIN_CARRY = 0.0001         # 1bp per 8h = worth noting


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def carry_read(ctx: Dict[str, Any]) -> Dict[str, Any]:
    rate = _num(ctx.get("funding_rate"))
    if rate is None:
        return {"receivable": None, "annual_pct": None, "known": 0}
    direction = str(ctx.get("direction") or "").strip().lower()

    # Positive funding = longs pay shorts.
    if direction == "long":
        receivable = -rate
    elif direction == "short":
        receivable = rate
    else:
        receivable = None

    annual = rate * 3 * 365 * 100.0   # 8h periods
    return {"receivable": receivable, "annual_pct": round(annual, 2),
            "rate": rate, "known": 1}


class FundingCarryBot(Bot):
    id = "funding_carry"
    edge_seat = True   # an approve means: carry edge on our side
    mandate = ("Never pay a crowd to hold a position: credit favourable carry, "
               "veto the side that is paying extreme funding, and harvest cross-exchange "
               "funding + mark-index basis dislocations.")
    domains = ("crypto",)
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        r = carry_read(ctx)
        oi = _num(ctx.get("oi_change_pct"))
        evidence: Dict[str, Any] = {"carry": r, "oi_change_pct": oi, "direction": ctx.get("direction")}

        mark_px = _num(ctx.get("mark_price"))
        idx_px = _num(ctx.get("index_price"))
        hl_f8 = _num(ctx.get("hyperliquid_funding_8h_bps"))
        if r["known"] == 1 and mark_px is not None and idx_px is not None and mark_px > 0 and idx_px > 0:
            basis_eval = evaluate_cross_exchange_funding_and_basis(
                symbol=str(ctx.get("symbol") or "BTCUSDTM"),
                kucoin_funding_8h_bps=float(r["rate"]) * 10000.0,
                kucoin_mark_price=mark_px,
                kucoin_index_price=idx_px,
                hyperliquid_funding_8h_bps=hl_f8,
                kucoin_turnover_24h_usd=float(_num(ctx.get("turnover_24h_usd")) or 5_000_000.0),
            )
            evidence["cross_exchange_basis"] = basis_eval

        if r["known"] == 0:
            return Vote(self.id, "abstain", 0.0, ["missing:funding_rate"], evidence)
        if r["receivable"] is None:
            return Vote(self.id, "abstain", 0.0, ["missing:direction"], evidence)

        rec = r["receivable"]
        if rec <= -EXTREME_RATE:
            conf = min(0.85, 0.55 + min(0.30, abs(rec) * 400))
            return Vote(self.id, "reject", conf,
                        [f"paying_extreme_funding:{rec*1e4:+.2f}bp/8h",
                         f"annual:{r['annual_pct']:+.1f}%"], evidence)
        if rec >= MIN_CARRY:
            conf = min(0.85, 0.50 + min(0.35, rec * 400))
            reasons = [f"receiving_carry:{rec*1e4:+.2f}bp/8h", f"annual:{r['annual_pct']:+.1f}%"]
            if "cross_exchange_basis" in evidence:
                reg = evidence["cross_exchange_basis"]["regime"]
                if reg != "NEUTRAL_NO_HARVEST":
                    reasons.append(f"basis_regime:{reg}")
            return Vote(self.id, "approve", conf, reasons, evidence)
        if rec <= 0:
            return Vote(self.id, "abstain", 0.20,
                        [f"paying_mild_funding:{rec*1e4:+.2f}bp/8h"], evidence)
        return Vote(self.id, "approve", 0.35,
                    [f"neutral_carry:{rec*1e4:+.2f}bp/8h"], evidence)
