"""Public-bias bot — the crowd is usually right on direction and wrong on price.

Sportsbooks and prediction markets both move on public money. When 80% of the
public is on one side, that side is usually the *worse* price: the book shades
the line, and you are paying a popularity premium.

This bot is deliberately modest: it never claims the public is wrong about the
outcome, only that a lopsided public book is a *cost* — so it fades heavy
public positions and credits the unpopular side, with the size of the effect
capped.

Also applies to sports-linked Polymarket contracts (same logic, different venue).
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

HEAVY_PUBLIC = 0.72      # public share that starts being a cost
EXTREME_PUBLIC = 0.85
FADE_POINTS = 8.0
CREDIT_POINTS = 4.0


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def public_read(ctx: Dict[str, Any]) -> Dict[str, Any]:
    """Compare the public's share of bets with the price on our side."""
    share = _num(ctx.get("public_bet_pct"))
    if share is not None and share > 1.0:      # accept 65 as 65%
        share = share / 100.0
    money = _num(ctx.get("public_money_pct"))
    if money is not None and money > 1.0:
        money = money / 100.0
    line_move = _num(ctx.get("line_move_bps"))
    side = str(ctx.get("side") or "").lower()

    reasons = []
    delta = 0.0
    known = 0

    if share is not None:
        known += 1
        on_us = share if side in ("over", "home", "yes", "fav", "favorite") else 1.0 - share
        if on_us >= EXTREME_PUBLIC:
            delta -= FADE_POINTS
            reasons.append(f"extreme_public_on_us:{on_us:.0%}")
        elif on_us >= HEAVY_PUBLIC:
            delta -= FADE_POINTS * 0.6
            reasons.append(f"heavy_public_on_us:{on_us:.0%}")
        elif on_us <= 1.0 - HEAVY_PUBLIC:
            delta += CREDIT_POINTS
            reasons.append(f"public_fading_us:{on_us:.0%}")

    if money is not None:
        known += 1
        if share is not None and abs(money - share) > 0.15:
            delta -= 3.0
            reasons.append(f"money_vs_tickets_divergence:{money - share:+.0%}")

    if line_move is not None:
        known += 1
        if line_move >= 30:
            delta -= 3.0
            reasons.append(f"line_steamed_against_us:{line_move:+.0f}bps")
        elif line_move <= -30:
            delta += 2.0
            reasons.append(f"line_moved_toward_us:{line_move:+.0f}bps")

    return {"delta": round(delta, 2), "known": known, "reasons": reasons,
            "public_share": share, "money_share": money}


class PublicBiasBot(Bot):
    id = "public_bias"
    mandate = ("Never pay the popularity premium: fade a lopsided public book, "
               "credit the unpopular side, and treat tickets-vs-money divergence "
               "as a warning.")
    domains = ("sports", "polymarket")
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        r = public_read(ctx)
        evidence = {"public": r, "side": ctx.get("side")}
        if r["known"] == 0:
            return Vote(self.id, "abstain", 0.0, ["no_public_data"], evidence)

        conf = min(0.80, 0.30 + 0.12 * r["known"])
        if r["delta"] <= -6:
            return Vote(self.id, "reject", conf,
                        [f"public_trap:{r['delta']:+.0f}"] + r["reasons"][:3], evidence)
        if r["delta"] >= 3:
            return Vote(self.id, "approve", conf,
                        [f"contrarian_edge:{r['delta']:+.0f}"] + r["reasons"][:3], evidence)
        return Vote(self.id, "approve", max(0.30, conf - 0.10),
                    [f"neutral_public:{r['delta']:+.0f}"] + r["reasons"][:2], evidence)
