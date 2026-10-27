"""Kelly portfolio bot — one sizing answer across a slate of candidates.

Each candidate is a binary payout priced in dollars per $1 of payout:

    {"name": "A", "p": 0.62, "price": 0.55, "cluster": "ai"}

Full Kelly for a contract bought at ``price`` with win probability ``p``.
Buying at ``price`` per $1 of payout means the *net* win is ``1 - price`` per
unit staked, so the net win/loss odds are ``b = (1 - price) / price`` and

    f = (p * (b + 1) - 1) / b = (p - price) / (1 - price)

``f`` is the fraction of bankroll to stake.  Note ``f`` is zero at ``p == price``
and negative whenever we would be overpaying (``p < price``); negative sizes are
clipped to zero by ``evaluate`` so the bot rejects instead of "sizing" a loser.
The desk runs **half Kelly**,
caps every candidate at 2%, every correlated cluster at 6%, and the whole
slate at 10%.  Caps are applied in that order (per-name -> cluster -> total),
so the returned weights are always tradeable as given.

Rules (no veto):
  * candidates missing / empty / not a list   -> abstain
  * total weight <= 0                         -> reject ("no_positive_edge")
  * otherwise                                 -> approve with the weight set

No network, no file IO, no clock, no randomness.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from ...core.bot import Bot, Vote

KELLY_FRACTION = 0.5     # half Kelly
PER_NAME_CAP = 0.02      # 2% of bankroll per candidate
CLUSTER_CAP = 0.06       # 6% per correlated cluster
TOTAL_CAP = 0.10         # 10% of bankroll for the whole slate
EPS = 1e-12


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(str(x).strip()) if isinstance(x, str) else float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(v):
        return None
    return v


def _prob(x: Any) -> Optional[float]:
    v = _num(x)
    if v is None or not (0.0 < v < 1.0):
        return None
    return v


def kelly_fraction(p: float, price: float) -> float:
    """Full Kelly stake fraction for a contract costing ``price`` per $1 payout.

    Net win/loss odds on a binary contract bought at ``price`` paying $1 are
    ``b = (1 - price) / price`` (you stake ``price`` to win ``1 - price``), so

        f* = (p * (b + 1) - 1) / b = (p - price) / (1 - price)

    ``kelly_fraction(0.50, 0.50) == 0.0`` (no edge, no bet) and
    ``kelly_fraction(0.40, 0.60) < 0`` (overpaying: clip to zero, do not approve).
    Using ``1/price`` as if it were the net payout double-counts the stake and
    sizes a losing bet as if it were a winner.
    """
    if not (0.0 < price < 1.0):       # no payout denominator: no size
        return 0.0
    f = (p - price) / (1.0 - price)
    if not math.isfinite(f):
        return 0.0
    return f


class KellyPortfolioBot(Bot):
    id = "kelly"
    mandate = ("Size the slate, not the story: half Kelly per name, never more "
               "than 2% on one candidate, 6% on one cluster, 10% in total.")
    domains = ("crypto",)
    veto = False
    edge_seat = True   # an approve means the slate carries positive Kelly edge

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        raw = ctx.get("candidates")
        evidence: Dict[str, Any] = {"weights": {}, "total": 0.0, "per_cluster": {}}

        if not isinstance(raw, (list, tuple)) or not raw:
            return Vote(self.id, "abstain", 0.0, ["missing:candidates"], evidence)

        weights: Dict[str, float] = {}
        cluster_of: Dict[str, str] = {}
        for i, cand in enumerate(raw):
            if not isinstance(cand, dict):
                continue
            p = _prob(cand.get("p"))
            price = _prob(cand.get("price"))
            if p is None or price is None:
                continue
            name = cand.get("name")
            name = str(name) if isinstance(name, (str, int)) and not isinstance(name, bool) else f"cand{i}"
            if name in weights:                      # duplicate names: keep the first
                continue
            f = kelly_fraction(p, price)
            w = max(0.0, f * KELLY_FRACTION)
            w = min(w, PER_NAME_CAP)
            if w <= EPS:
                continue
            weights[name] = w
            cluster = cand.get("cluster")
            cluster_of[name] = (str(cluster)
                                if isinstance(cluster, (str, int)) and not isinstance(cluster, bool)
                                and str(cluster) else name)

        # cluster cap: scale each over-weighted cluster back to CLUSTER_CAP
        per_cluster: Dict[str, float] = {}
        for name, w in weights.items():
            c = cluster_of[name]
            per_cluster[c] = per_cluster.get(c, 0.0) + w
        for c, total in per_cluster.items():
            if total > CLUSTER_CAP + EPS:
                scale = CLUSTER_CAP / total
                for name in weights:
                    if cluster_of[name] == c:
                        weights[name] *= scale

        # total cap: scale the whole slate back to TOTAL_CAP
        total = sum(weights.values())
        if total > TOTAL_CAP + EPS:
            scale = TOTAL_CAP / total
            for name in weights:
                weights[name] *= scale
            total = sum(weights.values())

        per_cluster = {}
        for name, w in weights.items():
            c = cluster_of[name]
            per_cluster[c] = per_cluster.get(c, 0.0) + w

        weights = {k: round(v, 8) for k, v in weights.items()}
        total = round(sum(weights.values()), 8)
        per_cluster = {k: round(v, 8) for k, v in per_cluster.items()}
        evidence.update({"weights": weights, "total": total, "per_cluster": per_cluster})

        if total <= EPS:
            return Vote(self.id, "reject", 0.8, ["no_positive_edge"], evidence)

        conf = 0.55 + 0.40 * min(1.0, total / TOTAL_CAP)
        return Vote(self.id, "approve", min(0.95, conf),
                    [f"total={total:.4f}_of_cap={TOTAL_CAP}",
                     f"names={len(weights)}"], evidence)
