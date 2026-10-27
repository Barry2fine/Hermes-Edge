"""Whale-consensus bot — follow the wallets that are actually right.

Idea from open Polymarket whale trackers: instead of reading the price alone,
look at *who* holds the position. Rank wallets by realized performance, weight
their open exposure by rank and size, and measure net consensus on our side.

Caveats baked in:
* a whale is not automatically right — rank decays and size can be exit liquidity
* consensus is advisory only: it can add or remove conviction, never override risk
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from ...core.bot import Bot, Vote

MIN_WHALES = 3
STRONG = 0.35          # net weighted consensus to call it decisive
MIN_RANK = 0.50        # ignore wallets below this performance rank (0..1)


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def _side_sign(x: Any) -> int:
    s = str(x or "").strip().lower()
    if s in ("yes", "long", "buy", "true", "over"):
        return 1
    if s in ("no", "short", "sell", "false", "under"):
        return -1
    return 0


def consensus(whales: List[Any], our_side: str) -> Dict[str, Any]:
    sign_ours = _side_sign(our_side)
    total = 0.0
    net = 0.0
    used = 0
    skipped = 0

    for w in whales or []:
        if not isinstance(w, dict):
            continue
        sign = _side_sign(w.get("side") or w.get("positionSide"))
        size = _num(w.get("size") if w.get("size") is not None else w.get("notional"))
        rank = _num(w.get("rank"))
        if sign == 0 or size is None or size <= 0:
            skipped += 1
            continue
        if rank is not None and rank < MIN_RANK:
            skipped += 1
            continue
        weight = size * (rank if rank is not None else 0.6)
        total += weight
        net += weight * sign
        used += 1

    if used == 0 or total <= 0:
        return {"consensus": None, "n": used, "skipped": skipped, "strength": 0.0}

    c = net / total                              # -1 .. +1 (net direction)
    on_us = c * sign_ours if sign_ours else 0.0   # positive = whales agree with us
    return {"consensus": round(c, 4), "on_us": round(on_us, 4),
            "n": used, "skipped": skipped, "strength": abs(on_us)}


class WhaleConsensusBot(Bot):
    id = "whale_consensus"
    mandate = ("Weight the smart money, not the crowd: rank-adjusted whale "
               "exposure must agree with our side before conviction rises.")
    domains = ("polymarket", "sports")
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        whales = ctx.get("whales")
        our_side = ctx.get("side") or ctx.get("outcome") or "yes"
        r = consensus(whales if isinstance(whales, list) else [], our_side)
        evidence = {"whales": r, "our_side": our_side}

        if r["consensus"] is None or r["n"] < MIN_WHALES:
            return Vote(self.id, "abstain", 0.0,
                        [f"insufficient_whales:{r['n']}<{MIN_WHALES}"], evidence)

        conf = min(0.85, 0.30 + 0.10 * r["n"])
        on_us = r["on_us"]
        if on_us >= STRONG:
            return Vote(self.id, "approve", conf,
                        [f"whales_agree:{on_us:+.2f}", f"n={r['n']}"], evidence)
        if on_us <= -STRONG:
            return Vote(self.id, "reject", conf,
                        [f"whales_against:{on_us:+.2f}", f"n={r['n']}"], evidence)
        return Vote(self.id, "approve", max(0.25, conf - 0.15),
                    [f"whales_mixed:{on_us:+.2f}"], evidence)
