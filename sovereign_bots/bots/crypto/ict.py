"""ICT structure bot — FVG, order blocks, premium/discount. No veto.

Only ``candles_1h`` is used, with a deliberately small, documented model:

  FVG (fair value gap), 3-candle imbalance:
      bullish when ``low[i] > high[i-2]``  (gap between bar i-2 high and bar i low)
      bearish when ``high[i] < low[i-2]``
    A gap counts as "live" while price has not closed back through its far
    edge (bullish: close still above the gap's lower edge).  The last 8 bars
    are scanned; the most recent live gap per side is kept.

  Order block (OB), last opposing candle before an impulse:
      bullish OB = last bearish candle followed by a bullish bar whose body is
      >= 1.2x the 20-bar average body.  Zone = that candle's [low, high].
      bearish OB is the mirror.  Last 20 bars scanned, most recent kept.
    "At the OB" means the last close sits inside the zone widened by
    0.35x ATR(14) on the impulse side.

  Premium/discount, dealing range = last 30 bars:
      pos = (close - low) / (high - low); <= 0.40 discount, >= 0.60 premium.

Scoring for the trade direction: +1 discount / bullish FVG / bullish OB,
-1 premium / bearish FVG / bearish OB.  Approve when net support >= +1,
otherwise abstain.  This bot never rejects (no veto).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...core.bot import Bot, Vote

FVG_LOOKBACK = 8
OB_LOOKBACK = 20
RANGE_LOOKBACK = 30
OB_IMPULSE_MULT = 1.2
OB_SLACK_ATR = 0.35


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


def _dir(x: Any) -> Optional[str]:
    if not isinstance(x, str):
        return None
    s = x.strip().lower()
    if s in ("long", "buy", "bull", "bullish", "up"):
        return "long"
    if s in ("short", "sell", "bear", "bearish", "down"):
        return "short"
    return None


def _bars(rows: Any) -> List[Dict[str, float]]:
    out: List[Dict[str, float]] = []
    if not isinstance(rows, (list, tuple)):
        return out
    for r in rows:
        if not isinstance(r, dict):
            continue
        o = _num(r.get("open", r.get("o")))
        h = _num(r.get("high", r.get("h")))
        l = _num(r.get("low", r.get("l")))
        c = _num(r.get("close", r.get("c")))
        if None in (o, h, l, c) or c <= 0 or h < l:
            continue
        out.append({"o": o, "h": h, "l": l, "c": c})
    return out


def _atr(bars: List[Dict[str, float]], n: int = 14) -> float:
    if len(bars) < 2:
        return 0.0
    window = bars[-(n + 1):]
    trs = []
    for i in range(1, len(window)):
        pc = window[i - 1]["c"]
        trs.append(max(window[i]["h"] - window[i]["l"],
                       abs(window[i]["h"] - pc), abs(window[i]["l"] - pc)))
    return sum(trs) / len(trs) if trs else 0.0


def _avg_body(bars: List[Dict[str, float]], n: int = 20) -> float:
    window = bars[-n:]
    if not window:
        return 0.0
    return sum(abs(b["c"] - b["o"]) for b in window) / len(window)


def last_fvg(bars: List[Dict[str, float]]) -> Dict[str, Optional[List[float]]]:
    """Most recent live bullish/bearish FVG as [bottom, top] or None."""
    bull = bear = None
    last = bars[-1]["c"]
    start = max(2, len(bars) - FVG_LOOKBACK)
    for i in range(start, len(bars)):
        a, c = bars[i - 2], bars[i]
        if c["l"] > a["h"] and last > a["h"]:            # bullish gap, not filled
            bull = [a["h"], c["l"]]
        if c["h"] < a["l"] and last < a["l"]:            # bearish gap, not filled
            bear = [c["h"], a["l"]]
    return {"bull": bull, "bear": bear}


def last_ob(bars: List[Dict[str, float]], atr: float) -> Dict[str, Optional[List[float]]]:
    bull = bear = None
    avg_body = _avg_body(bars) or 1e-12
    start = max(1, len(bars) - OB_LOOKBACK)
    for i in range(start, len(bars) - 1):
        prev, cur = bars[i - 1], bars[i]
        body = abs(cur["c"] - cur["o"])
        if body < OB_IMPULSE_MULT * avg_body:
            continue
        if cur["c"] > cur["o"] and prev["c"] < prev["o"]:
            bull = [prev["l"], prev["h"]]
        elif cur["c"] < cur["o"] and prev["c"] > prev["o"]:
            bear = [prev["l"], prev["h"]]
    return {"bull": bull, "bear": bear}


class ICTBot(Bot):
    id = "ict"
    edge_seat = True   # an approve means: structure-based net edge
    mandate = ("Read structure, not opinion: only lean in when price sits in "
               "discount/premium or on a live FVG or order block that agrees.")
    domains = ("crypto",)
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        bars = _bars(ctx.get("candles_1h"))
        direction = _dir(ctx.get("direction"))
        evidence: Dict[str, Any] = {"bars": len(bars), "direction": direction}

        if len(bars) < 12:
            return Vote(self.id, "abstain", 0.0,
                        [f"missing:candles_1h(n={len(bars)}<12)"], evidence)
        if direction is None:
            return Vote(self.id, "abstain", 0.0, ["missing:direction"], evidence)

        atr = _atr(bars, 14)
        last = bars[-1]["c"]
        window = bars[-RANGE_LOOKBACK:]
        hi = max(b["h"] for b in window)
        lo = min(b["l"] for b in window)
        pos = (last - lo) / (hi - lo) if hi > lo else 0.5
        fvg = last_fvg(bars)
        ob = last_ob(bars, atr)
        slack = OB_SLACK_ATR * atr

        def near(zone: Optional[List[float]]) -> bool:
            if not zone:
                return False
            z_lo, z_hi = zone
            return (z_lo - slack) <= last <= (z_hi + slack)

        bull_pts: List[str] = []
        bear_pts: List[str] = []
        if pos <= 0.40:
            bull_pts.append(f"discount(pos={pos:.2f})")
        if pos >= 0.60:
            bear_pts.append(f"premium(pos={pos:.2f})")
        if fvg["bull"]:
            bull_pts.append(f"bull_fvg={fvg['bull']}")
        if fvg["bear"]:
            bear_pts.append(f"bear_fvg={fvg['bear']}")
        if near(ob["bull"]):
            bull_pts.append(f"bull_ob={ob['bull']}")
        if near(ob["bear"]):
            bear_pts.append(f"bear_ob={ob['bear']}")

        evidence.update({
            "dealing_range": [lo, hi], "pos": round(pos, 4), "atr14": round(atr, 6),
            "fvg": fvg, "order_block": ob,
            "bull_points": len(bull_pts), "bear_points": len(bear_pts),
        })

        support = len(bull_pts) if direction == "long" else len(bear_pts)
        against = len(bear_pts) if direction == "long" else len(bull_pts)
        net = support - against
        evidence["net_support"] = net
        own = bull_pts if direction == "long" else bear_pts
        opp = bear_pts if direction == "long" else bull_pts

        if net >= 1:
            return Vote(self.id, "approve", min(0.90, 0.50 + 0.10 * net),
                        [f"structure_supports_{direction}:net={net}"] + own[:3], evidence)
        if net <= -1:
            return Vote(self.id, "abstain", 0.0,
                        [f"structure_opposes_{direction}:net={net}"] + opp[:3], evidence)
        return Vote(self.id, "abstain", 0.0,
                    [f"structure_unclear:net={net}",
                     f"pos={pos:.2f}", f"own={len(own)}", f"opp={len(opp)}"], evidence)
