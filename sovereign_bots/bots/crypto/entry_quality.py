"""Entry-quality bot — is *this bar* a good place to press the trigger? VETO.

Everything is computed from ``candles_1h`` (``{open,high,low,close,volume}``);
at least 30 bars are required.

Indicators (all standard, documented for audit):
  * RSI14            Wilder smoothing on closes.
  * EMA20            seeded with the SMA20, then EMA(alpha = 2/21).
  * dist_pct         (last_close - EMA20) / EMA20 * 100.
  * ER (Kaufman)     |c[-1] - c[-11]| / sum(|delta|) over the last 10 bars.
  * Choppiness       100 * log10(sum(TR, 14) / (max(high,14) - min(low,14)))
                     / log10(14).  Below 61.8 is trending, above is chop.

Rules:
  * fewer than 30 valid bars / missing direction -> reject (fail-closed)
  * long with RSI > 68, short with RSI < 32          -> reject (chasing)
  * |dist_pct| > 5                                    -> reject (over-extended)
  * ER < 0.30                                         -> reject (no efficiency)
  * chop > 61.8                                       -> reject (chop)
  * otherwise                                         -> approve
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from ...core.bot import Bot, Vote

MIN_BARS = 30
RSI_LONG_MAX = 68.0
RSI_SHORT_MIN = 32.0
MAX_DIST_PCT = 5.0
MIN_ER = 0.30
MAX_CHOP = 61.8


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
        v = _num(r.get("volume", r.get("v"))) or 0.0
        if None in (o, h, l, c) or c <= 0 or h < l:
            continue
        out.append({"o": o, "h": h, "l": l, "c": c, "v": v})
    return out


def rsi(closes: List[float], period: int = 14) -> Optional[float]:
    if len(closes) < period + 1:
        return None
    gains = losses = 0.0
    for i in range(1, period + 1):
        d = closes[i] - closes[i - 1]
        gains += max(d, 0.0)
        losses += max(-d, 0.0)
    ag, al = gains / period, losses / period
    for i in range(period + 1, len(closes)):
        d = closes[i] - closes[i - 1]
        ag = (ag * (period - 1) + max(d, 0.0)) / period
        al = (al * (period - 1) + max(-d, 0.0)) / period
    if al == 0:
        return 100.0 if ag > 0 else 50.0
    rs = ag / al
    return 100.0 - (100.0 / (1.0 + rs))


def ema(values: List[float], period: int = 20) -> Optional[float]:
    if len(values) < period:
        return None
    seed = sum(values[:period]) / period
    e = seed
    k = 2.0 / (period + 1.0)
    for v in values[period:]:
        e = v * k + e * (1.0 - k)
    return e


def efficiency_ratio(closes: List[float], n: int = 10) -> Optional[float]:
    if len(closes) < n + 1:
        return None
    window = closes[-(n + 1):]
    net = abs(window[-1] - window[0])
    path = sum(abs(window[i] - window[i - 1]) for i in range(1, len(window)))
    if path <= 0:
        return 0.0
    return net / path


def choppiness(bars: List[Dict[str, float]], n: int = 14) -> Optional[float]:
    if len(bars) < n + 1:
        return None
    window = bars[-(n + 1):]
    tr_sum = 0.0
    for i in range(1, len(window)):
        prev_c = window[i - 1]["c"]
        tr = max(window[i]["h"] - window[i]["l"],
                 abs(window[i]["h"] - prev_c), abs(window[i]["l"] - prev_c))
        tr_sum += tr
    hi = max(b["h"] for b in window)
    lo = min(b["l"] for b in window)
    rng = hi - lo
    if rng <= 0 or tr_sum <= 0:
        return 50.0 if rng <= 0 else 100.0
    value = 100.0 * math.log10(tr_sum / rng) / math.log10(n)
    return max(0.0, min(100.0, value))


class EntryQualityBot(Bot):
    id = "entry_quality"
    mandate = ("Refuse to enter a stretched, inefficient or chopping tape: "
               "RSI exhaustion, >5% from EMA20, ER<0.30 or chop>61.8 all veto.")
    domains = ("crypto",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        bars = _bars(ctx.get("candles_1h"))
        direction = _dir(ctx.get("direction"))
        evidence: Dict[str, Any] = {"bars": len(bars), "direction": direction}

        if len(bars) < MIN_BARS:
            return Vote(self.id, "reject", 0.0,
                        [f"missing:candles_1h(n={len(bars)}<{MIN_BARS})"], evidence)
        if direction is None:
            return Vote(self.id, "reject", 0.0, ["missing:direction"], evidence)

        closes = [b["c"] for b in bars]
        last = closes[-1]
        r = rsi(closes, 14)
        e20 = ema(closes, 20)
        er = efficiency_ratio(closes, 10)
        ch = choppiness(bars, 14)
        dist = (last - e20) / e20 * 100.0 if e20 else None
        evidence.update({"last": last, "rsi14": None if r is None else round(r, 2),
                         "ema20": None if e20 is None else round(e20, 4),
                         "dist_pct": None if dist is None else round(dist, 3),
                         "er": None if er is None else round(er, 4),
                         "chop": None if ch is None else round(ch, 2)})

        if r is None or e20 is None or er is None or ch is None:
            return Vote(self.id, "reject", 0.0, ["missing:indicator_input"], evidence)

        if direction == "long" and r > RSI_LONG_MAX:
            return Vote(self.id, "reject", 0.85,
                        [f"rsi={r:.1f}>68(long_exhaustion)"], evidence)
        if direction == "short" and r < RSI_SHORT_MIN:
            return Vote(self.id, "reject", 0.85,
                        [f"rsi={r:.1f}<32(short_exhaustion)"], evidence)
        if abs(dist) > MAX_DIST_PCT:
            return Vote(self.id, "reject", 0.8,
                        [f"dist_from_ema20={dist:+.2f}%>|5%|"], evidence)
        if er < MIN_ER:
            return Vote(self.id, "reject", 0.75,
                        [f"efficiency_ratio={er:.2f}<0.30"], evidence)
        if ch > MAX_CHOP:
            return Vote(self.id, "reject", 0.75,
                        [f"choppiness={ch:.1f}>61.8"], evidence)

        conf = 0.55 + 0.20 * min(1.0, (er - MIN_ER) / 0.5) + 0.15 * min(1.0, (MAX_CHOP - ch) / 30.0)
        if dist is not None and (dist > 0) == (direction == "long"):
            conf += 0.05
        return Vote(self.id, "approve", min(0.95, conf),
                    [f"rsi={r:.1f}_ok", f"dist={dist:+.2f}%",
                     f"er={er:.2f}", f"chop={ch:.1f}"], evidence)
