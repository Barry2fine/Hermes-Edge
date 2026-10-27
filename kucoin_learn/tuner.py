"""Adaptive tuning from realized results.

Pure functions. Turns a trade ledger into tuned parameters with guardrails:
only act with enough samples, only block a direction when its Wilson lower
bound is below the floor, never block both directions, never touch risk.
"""

from __future__ import annotations

import datetime as _dt
import math
from typing import Any, Dict, List, Optional, Sequence

DEFAULTS: Dict[str, Any] = {
    "version": 1,
    "min_score": 0.0,
    "min_alignment": 2,
    "blocked_directions": [],
    "blocked_coins": [],
    "n": 0,
    "updated_at": None,
}

MIN_N_DIRECTION = 12
MIN_N_COIN = 15
MIN_N_OVERALL = 20
WR_FLOOR_DIR = 0.45
WR_FLOOR_COIN = 0.40


def wilson_lower(wins: int, n: int, z: float = 1.96) -> float:
    """Wilson score lower bound — a win rate you can actually trust."""
    if n <= 0:
        return 0.0
    p = wins / n
    denom = 1.0 + z * z / n
    centre = p + z * z / (2.0 * n)
    margin = z * math.sqrt((p * (1.0 - p) + z * z / (4.0 * n)) / n)
    return max(0.0, (centre - margin) / denom)


def bucket_stats(trades: Sequence[Dict[str, Any]]) -> Dict[str, Any]:
    trades = [t for t in (trades or []) if isinstance(t, dict)]
    n = len(trades)
    wins = sum(1 for t in trades if t.get("win"))
    by_dir: Dict[str, Dict[str, Any]] = {}
    by_coin: Dict[str, Dict[str, Any]] = {}

    def bump(bucket, key, t):
        if not key:
            return
        b = bucket.setdefault(key, {"n": 0, "wins": 0, "r_sum": 0.0})
        b["n"] += 1
        b["wins"] += 1 if t.get("win") else 0
        try:
            b["r_sum"] += float(t.get("r") or 0.0)
        except (TypeError, ValueError):
            pass

    for t in trades:
        bump(by_dir, t.get("direction"), t)
        bump(by_coin, str(t.get("coin") or "").upper(), t)

    def finish(b):
        for v in b.values():
            v["win_rate"] = round(v["wins"] / v["n"], 4) if v["n"] else None
            v["wilson_lower"] = round(wilson_lower(v["wins"], v["n"]), 4)
            v["avg_r"] = round(v["r_sum"] / v["n"], 4) if v["n"] else None
        return b

    return {
        "n": n,
        "wins": wins,
        "win_rate": round(wins / n, 4) if n else None,
        "wilson_lower": round(wilson_lower(wins, n), 4) if n else 0.0,
        "by_direction": finish(by_dir),
        "by_coin": finish(by_coin),
    }


def derive_params(trades: Sequence[Dict[str, Any]], base: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Compute tuned parameters from realized trades. Fail-soft to base."""
    try:
        p = dict(DEFAULTS)
        if base:
            p.update(base)
        p["blocked_directions"] = []
        p["blocked_coins"] = []
        st = bucket_stats(trades)
        n = st["n"]

        # Directions: only block with evidence; never both.
        losers = []
        for d in ("long", "short"):
            v = st["by_direction"].get(d)
            if v and v["n"] >= MIN_N_DIRECTION and v["wilson_lower"] < WR_FLOOR_DIR:
                losers.append((v["wilson_lower"], d))
        if len(losers) == 1:
            p["blocked_directions"] = [losers[0][1]]
        elif len(losers) >= 2:
            losers.sort()
            p["blocked_directions"] = [losers[0][1]]  # block only the worse

        # Coins: blacklist only with a solid sample and poor lower bound.
        for c, v in st["by_coin"].items():
            if c and v["n"] >= MIN_N_COIN and v["wilson_lower"] < WR_FLOOR_COIN:
                p["blocked_coins"].append(c)

        # Conviction bar from overall evidence.
        if n >= MIN_N_OVERALL and st["win_rate"] is not None:
            wr = st["win_rate"]
            if wr < 0.45:
                p["min_score"] = round(min(0.65, 0.10 + (0.45 - wr)), 3)
                p["min_alignment"] = 3
            elif wr > 0.60:
                p["min_score"] = 0.0
                p["min_alignment"] = 2

        p["n"] = n
        p["version"] = int(p.get("version") or 1) + 1
        p["updated_at"] = _dt.datetime.now(_dt.timezone.utc).isoformat()
        return p
    except Exception:  # noqa: BLE001
        return dict(base or DEFAULTS)


def apply_to_ranking(ranking: Sequence[Dict[str, Any]], params: Optional[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Filter a ranking through the tuned parameters (fail-closed)."""
    p = params or DEFAULTS
    blocked_dirs = set(p.get("blocked_directions") or [])
    blocked_coins = {str(c).upper() for c in (p.get("blocked_coins") or [])}
    min_score = float(p.get("min_score") or 0.0)
    min_align = int(p.get("min_alignment") or 2)
    out = []
    for r in ranking or []:
        if not isinstance(r, dict):
            continue
        if str(r.get("coin") or "").upper() in blocked_coins:
            continue
        if r.get("direction") in blocked_dirs:
            continue
        try:
            if float(r.get("score") or 0.0) < min_score:
                continue
            if int(r.get("alignment") or 0) < min_align:
                continue
        except (TypeError, ValueError):
            continue
        out.append(r)
    return out
