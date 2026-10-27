"""Data-integrity bot — is the feed itself trustworthy? VETO.

Rules (contract):
  * last_update_age_s missing / None / not a number        -> reject
  * last_update_age_s > 60                                 -> reject (stale)
  * mark and oracle_price both present and
    |mark - oracle| / oracle > 0.005                       -> reject
  * the last two candles_1h imply a > 10% jump             -> reject
  * spread_bps > 50                                        -> reject
  * crossed or one-sided book                              -> reject
  * spread_bps is only used when it is actually provided;
    with neither a rating nor a book the bot fails closed
  * anything required but missing                          -> reject

Pure: no network, no file IO, no clock, no randomness.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...core.bot import Bot, Vote

MAX_AGE_S = 60.0
MAX_DEV = 0.005
MAX_JUMP = 0.10
MAX_SPREAD_BPS = 50.0
MIN_CANDLES = 2


def _num(x: Any) -> Optional[float]:
    """Best-effort finite float, else None. Never raises."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(str(x).strip().rstrip("%")) if isinstance(x, str) else float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _closes(rows: Any) -> List[float]:
    out: List[float] = []
    if not isinstance(rows, (list, tuple)):
        return out
    for r in rows:
        v = None
        if isinstance(r, dict):
            for k in ("close", "c", "Close", "last", "price"):
                v = _num(r.get(k))
                if v is not None:
                    break
        elif isinstance(r, (list, tuple)) and r:
            v = _num(r[-1]) if len(r) < 6 else _num(r[4])
        else:
            v = _num(r)
        if v is not None and v > 0:
            out.append(v)
    return out


def _top(levels: Any, side: str) -> Optional[float]:
    """Best price on a side: max for bids, min for asks. None when empty."""
    prices: List[float] = []
    if not isinstance(levels, (list, tuple)):
        return None
    for lv in levels:
        p = s = None
        if isinstance(lv, dict):
            for k in ("price", "p", "px"):
                p = _num(lv.get(k))
                if p is not None:
                    break
            for k in ("size", "qty", "q", "amount"):
                s = _num(lv.get(k))
                if s is not None:
                    break
        elif isinstance(lv, (list, tuple)) and len(lv) >= 2:
            p, s = _num(lv[0]), _num(lv[1])
        if p is None or p <= 0:
            continue
        if s is not None and s <= 0:
            continue
        prices.append(p)
    if not prices:
        return None
    return max(prices) if side == "bid" else min(prices)


class DataIntegrityBot(Bot):
    id = "data_integrity"
    mandate = ("Refuse to act on a feed that is stale, off-oracle, jumping, "
               "wide or crossed: bad data is worse than no data.")
    domains = ("crypto",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        evidence: Dict[str, Any] = {
            "age_s": None, "mark": None, "oracle_price": None, "dev_bps": None,
            "jump_pct": None, "spread_bps": None, "book_spread_bps": None,
        }

        # 1) freshness -------------------------------------------------------
        age = _num(ctx.get("last_update_age_s"))
        evidence["age_s"] = age
        if "last_update_age_s" not in ctx or age is None:
            return Vote(self.id, "reject", 0.0, ["missing:last_update_age_s"], evidence)
        if age < 0:
            return Vote(self.id, "reject", 0.0,
                        [f"invalid:last_update_age_s={age:.2f}"], evidence)
        if age > MAX_AGE_S:
            return Vote(self.id, "reject", 0.95,
                        [f"stale:last_update_age_s={age:.1f}>{MAX_AGE_S:.0f}"], evidence)

        # 2) tape ------------------------------------------------------------
        bars = _closes(ctx.get("candles_1h"))
        if len(bars) < MIN_CANDLES:
            return Vote(self.id, "reject", 0.0,
                        [f"missing:candles_1h:{len(bars)}<{MIN_CANDLES}"], evidence)

        # 3) reference price -------------------------------------------------
        mark = _num(ctx.get("mark"))
        if mark is None:
            mark = _num(ctx.get("price"))
        evidence["mark"] = mark
        if mark is None or mark <= 0:
            return Vote(self.id, "reject", 0.0, ["missing:mark"], evidence)

        # 4) oracle divergence ----------------------------------------------
        oracle = _num(ctx.get("oracle_price"))
        evidence["oracle_price"] = oracle
        if oracle is not None and oracle > 0 and mark is not None:
            dev = abs(mark - oracle) / oracle
            evidence["dev_bps"] = round(dev * 1e4, 4)
            if dev > MAX_DEV:
                return Vote(self.id, "reject", 0.9,
                            [f"oracle_divergence:{dev * 100:.2f}%>{MAX_DEV * 100:.2f}%"],
                            evidence)
        elif oracle is not None and oracle <= 0:
            return Vote(self.id, "reject", 0.0,
                        [f"invalid:oracle_price={oracle}"], evidence)

        # 5) last two 1h candles --------------------------------------------
        prev, last = bars[-2], bars[-1]
        if prev > 0:
            jump = abs(last - prev) / prev
            evidence["jump_pct"] = round(jump * 100.0, 4)
            if jump > MAX_JUMP:
                return Vote(self.id, "reject", 0.9,
                            [f"jump:{jump * 100:.2f}%>{MAX_JUMP * 100:.0f}%"], evidence)

        # 6) spread ----------------------------------------------------------
        spread = _num(ctx.get("spread_bps"))
        evidence["spread_bps"] = spread
        if spread is not None:
            if spread < 0:
                return Vote(self.id, "reject", 0.0,
                            [f"invalid:spread_bps={spread}"], evidence)
            if spread > MAX_SPREAD_BPS:
                return Vote(self.id, "reject", 0.9,
                            [f"spread={spread:.1f}bps>{MAX_SPREAD_BPS:.0f}"], evidence)

        # 7) book ------------------------------------------------------------
        has_book = ("bids" in ctx) or ("asks" in ctx)
        if has_book:
            bids, asks = ctx.get("bids"), ctx.get("asks")
            bb, ba = _top(bids, "bid"), _top(asks, "ask")
            if bb is None or ba is None:
                return Vote(self.id, "reject", 0.0,
                            ["missing:book:one_sided"], evidence)
            if bb > ba:
                return Vote(self.id, "reject", 0.95,
                            [f"crossed_book:bid={bb},ask={ba}"], evidence)
            mid = 0.5 * (bb + ba)
            if mid > 0:
                book_bps = (ba - bb) / mid * 1e4
                evidence["book_spread_bps"] = round(book_bps, 4)
                if book_bps > MAX_SPREAD_BPS:
                    return Vote(self.id, "reject", 0.9,
                                [f"spread={book_bps:.1f}bps>{MAX_SPREAD_BPS:.0f}"],
                                evidence)
        elif spread is None:
            return Vote(self.id, "reject", 0.0, ["missing:book_or_spread_bps"], evidence)

        conf = 0.9 if age <= 10 else (0.75 if age <= 30 else 0.6)
        return Vote(self.id, "approve", conf,
                    [f"fresh:age={age:.1f}s", f"bars_1h={len(bars)}",
                     f"spread_bps={spread if spread is not None else evidence['book_spread_bps']}"],
                    evidence)
