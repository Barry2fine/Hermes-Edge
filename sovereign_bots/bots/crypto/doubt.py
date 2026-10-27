"""Doubt bot — the designated skeptic. VETO on anything unproven.

Checks, in order (first hit wins, every one is a veto):
  1. geometry      long needs target > entry > stop, short the mirror.
  2. HTF trend     4h or 1d trend (SMA20 vs SMA50, fallback first-vs-last
                   close when the series is short) against the direction.
  3. liquidity     ``day_volume_usd < 5_000_000``.
  4. crowding      ``|funding| > 0.001`` with the sign pointing at our side
                   (positive funding = longs paying = crowded longs).
  5. payoff        post-fee R:R
                     reward = |target-entry|*size
                     risk   = |entry-stop|*size
                     cost   = fees_per_unit*size*2   (round trip)
                     rr     = (reward - cost) / (risk + cost)  < 1.5 -> reject
  6. stop distance ``|entry-stop|/entry < 0.4%`` (noise stop) or
                   ``> 15%`` (not a stop, a hope).
  7. capacity      ``position_count >= max_positions``.
  8. freshness     ``data_health`` not ok, or ``ranking_age_h > 6``.

Missing inputs reject (fail-closed); ``fees_per_unit`` defaults to 0.0.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from ...core.bot import Bot, Vote

MIN_DAY_VOLUME = 5_000_000.0
FUNDING_CROWD = 0.001
MIN_RR = 1.5
MIN_STOP_PCT = 0.004
MAX_STOP_PCT = 0.15
MAX_RANKING_AGE_H = 6.0


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, str):
        s = x.strip()
        try:
            if s.endswith("%"):
                return float(s[:-1]) / 100.0
            return float(s)
        except ValueError:
            return None
    try:
        v = float(x)
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
        elif isinstance(r, (list, tuple)) and len(r) >= 5:
            v = _num(r[4])
        else:
            v = _num(r)
        if v is not None and v > 0:
            out.append(v)
    return out


def _trend(rows: Any) -> Optional[str]:
    """'up' | 'down' | 'flat' | None for an HTF candle series."""
    cs = _closes(rows)
    if len(cs) < 5:
        return None
    if len(cs) >= 50:
        fast = sum(cs[-20:]) / 20.0
        slow = sum(cs[-50:]) / 50.0
    else:
        half = len(cs) // 2
        fast = sum(cs[-half:]) / half
        slow = sum(cs[:half]) / half
    if slow <= 0:
        return None
    if fast > slow * 1.001:
        return "up"
    if fast < slow * 0.999:
        return "down"
    return "flat"


def _health_ok(x: Any) -> bool:
    if isinstance(x, bool):
        return x
    if isinstance(x, dict):
        for k in ("ok", "healthy", "status", "state"):
            if k in x:
                return _health_ok(x.get(k))
        return False
    if isinstance(x, (int, float)) and not isinstance(x, bool):
        return float(x) >= 1.0
    if isinstance(x, str):
        return x.strip().lower() in ("ok", "healthy", "good", "green", "true", "1")
    return False


class DoubtBot(Bot):
    id = "doubt"
    mandate = ("Assume the trade is wrong until proven otherwise: kill it on HTF "
               "conflict, thin volume, crowded funding, poor post-fee R:R, bad "
               "stop placement, full slots, or stale/unhealthy data.")
    domains = ("crypto",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        direction = _dir(ctx.get("direction"))
        entry = _num(ctx.get("entry"))
        stop = _num(ctx.get("stop"))
        target = _num(ctx.get("target"))
        size = _num(ctx.get("size"))
        fees = _num(ctx.get("fees_per_unit"))
        fees = 0.0 if fees is None else max(0.0, fees)
        day_vol = _num(ctx.get("day_volume_usd"))
        funding = _num(ctx.get("funding"))
        pcount = _num(ctx.get("position_count"))
        pmax = _num(ctx.get("max_positions"))
        rank_age = _num(ctx.get("ranking_age_h"))
        health = ctx.get("data_health")
        t4 = _trend(ctx.get("candles_4h"))
        t1 = _trend(ctx.get("candles_1d"))

        evidence: Dict[str, Any] = {
            "direction": direction, "entry": entry, "stop": stop, "target": target,
            "size": size, "fees_per_unit": fees, "day_volume_usd": day_vol,
            "funding": funding, "position_count": pcount, "max_positions": pmax,
            "ranking_age_h": rank_age, "htf_4h": t4, "htf_1d": t1,
            "data_health": health,
        }

        for name, val in (("direction", direction), ("entry", entry), ("stop", stop),
                          ("target", target), ("size", size)):
            if val is None:
                return Vote(self.id, "reject", 0.0, [f"missing:{name}"], evidence)
        if entry <= 0 or stop <= 0 or target <= 0 or size <= 0:
            return Vote(self.id, "reject", 0.9, ["invalid:non_positive_price_or_size"],
                        evidence)

        if direction == "long" and not (target > entry > stop):
            return Vote(self.id, "reject", 0.9, ["geometry:long_needs_target>entry>stop"],
                        evidence)
        if direction == "short" and not (target < entry < stop):
            return Vote(self.id, "reject", 0.9, ["geometry:short_needs_target<entry<stop"],
                        evidence)

        if t4 is None and t1 is None:
            return Vote(self.id, "reject", 0.0, ["missing:htf_candles"], evidence)
        want = "up" if direction == "long" else "down"
        against = [(tf, t) for tf, t in (("4h", t4), ("1d", t1)) if t and t != "flat" and t != want]
        if against:
            return Vote(self.id, "reject", 0.8,
                        [f"htf_conflict:{tf}={t}" for tf, t in against], evidence)

        if day_vol is None:
            return Vote(self.id, "reject", 0.0, ["missing:day_volume_usd"], evidence)
        if day_vol < MIN_DAY_VOLUME:
            return Vote(self.id, "reject", 0.8,
                        [f"illiquid:day_volume={day_vol:.0f}<{MIN_DAY_VOLUME:.0f}"], evidence)

        if funding is None:
            return Vote(self.id, "reject", 0.0, ["missing:funding"], evidence)
        crowded = (funding > FUNDING_CROWD and direction == "long") or \
                  (funding < -FUNDING_CROWD and direction == "short")
        if crowded:
            return Vote(self.id, "reject", 0.75,
                        [f"crowded_funding={funding:.5f}_against_{direction}"], evidence)

        reward = abs(target - entry) * size
        risk = abs(entry - stop) * size
        cost = fees * size * 2.0
        evidence.update({"reward": round(reward, 6), "risk": round(risk, 6),
                         "cost": round(cost, 6)})
        if risk <= 0:
            return Vote(self.id, "reject", 0.9, ["invalid:zero_risk"], evidence)
        rr = (reward - cost) / (risk + cost)
        evidence["rr_post_fee"] = round(rr, 4)
        if rr < MIN_RR:
            return Vote(self.id, "reject", 0.75,
                        [f"rr_post_fee={rr:.2f}<{MIN_RR}"], evidence)

        stop_pct = abs(entry - stop) / entry
        evidence["stop_pct"] = round(stop_pct, 5)
        if stop_pct < MIN_STOP_PCT:
            return Vote(self.id, "reject", 0.7,
                        [f"stop_too_tight={stop_pct:.2%}<0.4%"], evidence)
        if stop_pct > MAX_STOP_PCT:
            return Vote(self.id, "reject", 0.7,
                        [f"stop_too_wide={stop_pct:.2%}>15%"], evidence)

        if pcount is None or pmax is None:
            return Vote(self.id, "reject", 0.0, ["missing:position_slots"], evidence)
        if pcount >= pmax:
            return Vote(self.id, "reject", 0.7,
                        [f"slots_full:{pcount:.0f}/{pmax:.0f}"], evidence)

        if not _health_ok(health):
            return Vote(self.id, "reject", 0.7, [f"data_health_not_ok:{health!r}"], evidence)
        if rank_age is None:
            return Vote(self.id, "reject", 0.0, ["missing:ranking_age_h"], evidence)
        if rank_age > MAX_RANKING_AGE_H:
            return Vote(self.id, "reject", 0.6,
                        [f"stale_ranking:{rank_age:.1f}h>{MAX_RANKING_AGE_H:.0f}h"], evidence)

        conf = 0.55 + 0.20 * min(1.0, (rr - MIN_RR) / 2.0)
        conf += 0.10 * min(1.0, day_vol / (MIN_DAY_VOLUME * 6.0))
        conf += 0.05 * (1 if t4 == want else 0)
        return Vote(self.id, "approve", min(0.95, conf),
                    [f"rr_post_fee={rr:.2f}", f"stop={stop_pct:.2%}",
                     f"vol={day_vol:.0f}", f"slots={pcount:.0f}/{pmax:.0f}",
                     "no_doubt_triggered"], evidence)
