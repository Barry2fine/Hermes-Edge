"""Regime bot — is the whole tape safe enough to hold risk? VETO.

Rules (contract):
  * risk-off when ``vol_pct > 8`` or ``breadth_ratio < 0.35``      -> reject
  * BTC 1d or 4h trend opposes the trade direction                -> reject
  * otherwise                                                     -> approve

``btc_1d`` / ``btc_4h`` are accepted in several shapes because the
context builder is free to send either a trend label, a signed number or
raw candles: str ("up"/"down"/...), int/float (+/-), {"trend": ...},
{"candles": [...]} or a bare candle list.  Each bot keeps its own copy of
the tiny parsing helpers on purpose: bots must stay independent, so a
parsing bug can never cascade across the pack.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...core.bot import Bot, Vote

_UP = {"up", "bull", "bullish", "long", "risk_on", "rising", "uptrend", "bid"}
_DOWN = {"down", "bear", "bearish", "short", "risk_off", "falling",
         "downtrend", "offer"}
_FLAT = {"flat", "neutral", "range", "ranging", "sideways", "chop", "mixed"}


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


def _dir(x: Any) -> Optional[str]:
    if not isinstance(x, str):
        return None
    s = x.strip().lower()
    if s in ("long", "buy", "bull", "bullish", "up"):
        return "long"
    if s in ("short", "sell", "bear", "bearish", "down"):
        return "short"
    return None


def _closes(rows: Any) -> List[float]:
    out: List[float] = []
    if not isinstance(rows, (list, tuple)):
        return out
    for r in rows:
        v = None
        if isinstance(r, dict):
            for k in ("close", "c", "Close", "last"):
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


def _trend_from_closes(cs: List[float]) -> Optional[str]:
    if len(cs) < 4:
        return None
    fast = sum(cs[-3:]) / 3.0
    slow = sum(cs) / float(len(cs))
    if slow <= 0:
        return None
    if fast > slow * 1.0005:
        return "up"
    if fast < slow * 0.9995:
        return "down"
    return "flat"


def _trend(src: Any) -> Optional[str]:
    """Normalise a trend source to 'up' | 'down' | 'flat' | None (unknown)."""
    if src is None:
        return None
    if isinstance(src, str):
        s = src.strip().lower()
        if s in _UP:
            return "up"
        if s in _DOWN:
            return "down"
        if s in _FLAT:
            return "flat"
        return None
    if isinstance(src, bool):
        return None
    if isinstance(src, (int, float)):
        v = float(src)
        return "up" if v > 0 else ("down" if v < 0 else "flat")
    if isinstance(src, dict):
        for k in ("trend", "direction", "bias", "state", "regime"):
            if k in src:
                t = _trend(src.get(k))
                if t is not None:
                    return t
        for k in ("candles", "candles_1d", "candles_4h", "closes", "ohlcv", "bars"):
            if k in src:
                t = _trend_from_closes(_closes(src.get(k)))
                if t is not None:
                    return t
        for k in ("close", "price", "last"):
            v = _num(src.get(k))
            if v is not None:
                return _trend(v if abs(v) < 1 else 0.0)
        return None
    if isinstance(src, (list, tuple)):
        return _trend_from_closes(_closes(src))
    return None


class RegimeBot(Bot):
    id = "regime"
    mandate = ("Refuse to trade into a risk-off tape: high volatility, collapsed "
               "breadth, or a BTC higher-timeframe trend that fights the trade.")
    domains = ("crypto",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        vol = _num(ctx.get("vol_pct"))
        breadth = _num(ctx.get("breadth_ratio"))
        direction = _dir(ctx.get("direction"))
        b1 = _trend(ctx.get("btc_1d"))
        b4 = _trend(ctx.get("btc_4h"))
        evidence: Dict[str, Any] = {
            "vol_pct": vol, "breadth_ratio": breadth, "direction": direction,
            "btc_1d": b1, "btc_4h": b4,
        }

        if vol is None:
            return Vote(self.id, "reject", 0.0, ["missing:vol_pct"], evidence)
        if vol > 8.0:
            return Vote(self.id, "reject", 0.9,
                        [f"risk_off:vol_pct={vol:.2f}>8"], evidence)
        if breadth is None:
            return Vote(self.id, "reject", 0.0, ["missing:breadth_ratio"], evidence)
        if breadth < 0.35:
            return Vote(self.id, "reject", 0.9,
                        [f"risk_off:breadth={breadth:.2f}<0.35"], evidence)
        if direction is None:
            return Vote(self.id, "reject", 0.0, ["missing:direction"], evidence)
        if b1 is None and b4 is None:
            return Vote(self.id, "reject", 0.0, ["missing:btc_trend"], evidence)

        want = "up" if direction == "long" else "down"
        opposed = [tf for tf, t in (("btc_1d", b1), ("btc_4h", b4)) if t and t != "flat" and t != want]
        if opposed:
            return Vote(self.id, "reject", 0.85,
                        [f"btc_trend_opposes:{tf}={b1 if tf == 'btc_1d' else b4}"
                         for tf in opposed], evidence)

        agree = sum(1 for t in (b1, b4) if t == want)
        conf = 0.55 + 0.20 * min(1.0, max(0.0, (breadth - 0.35) / 0.65)) + 0.10 * agree
        return Vote(self.id, "approve", min(0.95, conf),
                    [f"regime_ok:vol_pct={vol:.2f}",
                     f"breadth={breadth:.2f}", f"btc_trend_agree={agree}/2"], evidence)
