"""Execution bot — can this size actually be filled without eating the book? VETO.

Book input: ``bids`` / ``asks``, each a list of ``[price, size]`` pairs
(``{"price": p, "size": s}`` dicts also accepted), best price first or not —
the bot finds the best level itself.

Definitions (documented so the numbers can be audited):
  * ``mid``          = (best_bid + best_ask) / 2
  * ``spread_bps``   = (best_ask - best_bid) / mid * 10_000
  * ``depth``        = sum of resting size on all levels within 1% of mid,
                       in base units (same unit as ``size``), both sides.
  * ``participation``= size / depth

Rules:
  * missing/empty book          -> reject (fail-closed)
  * crossed book (bid >= ask)   -> reject
  * spread_bps > 15             -> reject
  * participation > 0.25        -> reject
  * otherwise                   -> approve
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

from ...core.bot import Bot, Vote

MAX_SPREAD_BPS = 15.0
MAX_PARTICIPATION = 0.25
BAND = 0.01


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


def _levels(raw: Any) -> List[Tuple[float, float]]:
    out: List[Tuple[float, float]] = []
    if not isinstance(raw, (list, tuple)):
        return out
    for lv in raw:
        price: Optional[float] = None
        size: Optional[float] = None
        if isinstance(lv, dict):
            for k in ("price", "p", "px"):
                price = _num(lv.get(k))
                if price is not None:
                    break
            for k in ("size", "qty", "amount", "q", "s", "volume"):
                size = _num(lv.get(k))
                if size is not None:
                    break
        elif isinstance(lv, (list, tuple)) and len(lv) >= 2:
            price, size = _num(lv[0]), _num(lv[1])
        if price is not None and size is not None and price > 0 and size >= 0:
            out.append((price, size))
    return out


class ExecutionBot(Bot):
    id = "execution"
    mandate = ("Veto any order the book cannot absorb: wide spread, crossed or "
               "missing book, or size above 25% of depth within 1% of mid.")
    domains = ("crypto",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        bids = _levels(ctx.get("bids"))
        asks = _levels(ctx.get("asks"))
        size = _num(ctx.get("size"))
        evidence: Dict[str, Any] = {
            "bid_levels": len(bids), "ask_levels": len(asks), "size": size,
        }

        if not bids or not asks:
            return Vote(self.id, "reject", 0.0,
                        [f"missing:book(bids={len(bids)},asks={len(asks)})"], evidence)

        best_bid = max(p for p, _ in bids)
        best_ask = min(p for p, _ in asks)
        evidence["best_bid"] = best_bid
        evidence["best_ask"] = best_ask
        if best_bid <= 0 or best_ask <= 0:
            return Vote(self.id, "reject", 0.0, ["invalid:non_positive_quote"], evidence)
        if best_bid >= best_ask:
            return Vote(self.id, "reject", 0.9,
                        [f"book:crossed({best_bid}>= {best_ask})"], evidence)

        mid = (best_bid + best_ask) / 2.0
        spread_bps = (best_ask - best_bid) / mid * 10000.0
        lo, hi = mid * (1.0 - BAND), mid * (1.0 + BAND)
        depth_units = sum(s for p, s in bids if p >= lo) + sum(s for p, s in asks if p <= hi)
        depth_usd = (sum(p * s for p, s in bids if p >= lo)
                     + sum(p * s for p, s in asks if p <= hi))
        evidence.update({"mid": mid, "spread_bps": round(spread_bps, 4),
                         "depth_units": round(depth_units, 6),
                         "depth_usd": round(depth_usd, 2)})

        if spread_bps > MAX_SPREAD_BPS:
            return Vote(self.id, "reject", 0.9,
                        [f"spread={spread_bps:.1f}bps>{MAX_SPREAD_BPS:.0f}bps"], evidence)
        if size is None:
            return Vote(self.id, "reject", 0.0, ["missing:size"], evidence)
        if size <= 0:
            return Vote(self.id, "reject", 0.9, [f"invalid:size={size}"], evidence)
        if depth_units <= 0:
            return Vote(self.id, "reject", 0.9, ["book:no_depth_within_1pct"], evidence)

        participation = size / depth_units
        evidence["participation"] = round(participation, 6)
        if participation > MAX_PARTICIPATION:
            return Vote(self.id, "reject", 0.9,
                        [f"size={size}/depth={depth_units:.4f}"
                         f"={participation:.1%}>{MAX_PARTICIPATION:.0%}"], evidence)

        quality = (1.0 - participation / MAX_PARTICIPATION) * (1.0 - spread_bps / MAX_SPREAD_BPS)
        return Vote(self.id, "approve", min(0.95, 0.55 + 0.40 * max(0.0, quality)),
                    [f"spread={spread_bps:.1f}bps",
                     f"participation={participation:.1%}_of_depth"], evidence)
