"""Memory / base-rate bot — what happened the last time the tape looked like this.

Pure nearest-neighbour reasoning over a case memory.  Each memory row is a
past decision with its feature vector, whether it won, and the realised R:

    {"features": [f0, f1, ...], "win": bool, "r": float}

The current ``features`` vector is compared by plain Euclidean distance to
every row of the same length (rows of a different length are not comparable
and are skipped, never coerced).  The ``k = min(10, n)`` closest rows define
the local base rate and the local expected R.

Rules (no veto):
  * features missing / empty / non-numeric, or fewer than 8 comparable rows
                                                   -> abstain (not enough evidence)
  * base_rate >= 0.55 and expected_r > 0           -> approve (conf = base_rate)
  * base_rate <= 0.35                              -> reject
  * otherwise                                      -> abstain

No network, no file IO, no clock, no randomness.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional, Tuple

from ...core.bot import Bot, Vote

K_MAX = 10
MIN_ROWS = 8
APPROVE_RATE = 0.55
REJECT_RATE = 0.35


def _num(x: Any) -> Optional[float]:
    """Finite float or None. Booleans and NaN/inf are not numbers here."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(str(x).strip()) if isinstance(x, str) else float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _vector(x: Any) -> Optional[List[float]]:
    """A non-empty list of finite floats, or None."""
    if not isinstance(x, (list, tuple)) or not x:
        return None
    out: List[float] = []
    for item in x:
        v = _num(item)
        if v is None:
            return None
        out.append(v)
    return out


def _win(x: Any) -> Optional[bool]:
    if isinstance(x, bool):
        return x
    if isinstance(x, str):
        s = x.strip().lower()
        if s in ("true", "yes", "win", "1"):
            return True
        if s in ("false", "no", "loss", "0"):
            return False
        return None
    v = _num(x)
    if v is None:
        return None
    return v != 0.0


def _dist(a: List[float], b: List[float]) -> float:
    s = 0.0
    for x, y in zip(a, b):
        d = x - y
        s += d * d
    return math.sqrt(s)


class MemoryBaseRateBot(Bot):
    id = "memory"
    mandate = ("Act only where the case memory speaks: in the nearest neighbours "
               "the historical base rate must clear 0.55 with a positive mean R, "
               "and a local base rate at or below 0.35 forbids the trade.")
    domains = ("crypto",)
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        features = _vector(ctx.get("features"))
        memory = ctx.get("memory")

        evidence: Dict[str, Any] = {
            "n": 0, "k": 0, "base_rate": None, "expected_r": None, "neighbors": [],
        }

        if features is None:
            return Vote(self.id, "abstain", 0.0, ["missing:features"], evidence)
        if not isinstance(memory, (list, tuple)):
            return Vote(self.id, "abstain", 0.0, ["missing:memory"], evidence)

        width = len(features)
        rows: List[Tuple[float, bool, float]] = []
        for row in memory:
            if not isinstance(row, dict):
                continue
            vec = _vector(row.get("features"))
            if vec is None or len(vec) != width:
                continue
            win = _win(row.get("win"))
            if win is None:
                continue
            r = _num(row.get("r"))
            rows.append((_dist(features, vec), win, 0.0 if r is None else r))

        n = len(rows)
        if n < MIN_ROWS:
            evidence["n"] = n
            return Vote(self.id, "abstain", 0.0,
                        [f"insufficient_memory:n={n}<{MIN_ROWS}"], evidence)

        rows.sort(key=lambda t: t[0])
        k = min(K_MAX, n)
        nearest = rows[:k]
        wins = sum(1 for _, w, _ in nearest if w)
        base_rate = wins / k
        expected_r = sum(r for _, _, r in nearest) / k

        evidence.update({
            "n": n,
            "k": k,
            "base_rate": round(base_rate, 6),
            "expected_r": round(expected_r, 6),
            "neighbors": [
                {"d": round(d, 6), "win": bool(w), "r": round(r, 6)}
                for d, w, r in nearest
            ],
        })

        if base_rate >= APPROVE_RATE and expected_r > 0.0:
            return Vote(self.id, "approve", base_rate,
                        [f"base_rate={base_rate:.3f}_over_k={k}",
                         f"expected_r={expected_r:+.3f}>0"], evidence)
        if base_rate <= REJECT_RATE:
            return Vote(self.id, "reject", min(0.95, 1.0 - base_rate),
                        [f"base_rate={base_rate:.3f}<={REJECT_RATE}",
                         f"expected_r={expected_r:+.3f}"], evidence)
        return Vote(self.id, "abstain", 0.0,
                    [f"base_rate={base_rate:.3f}_in_dead_zone",
                     f"expected_r={expected_r:+.3f}"], evidence)
