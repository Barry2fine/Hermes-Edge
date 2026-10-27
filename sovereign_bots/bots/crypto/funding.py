"""Funding bot — carry, not prophecy. No veto.

``funding`` is the per-period rate as a decimal (0.0001 = 1 bp).  Percent
strings ("0.0100%") are accepted and converted.

Sign convention: funding > 0 => longs pay shorts (crowded longs).
  * unknown funding                                     -> abstain
  * |funding| > 0.001 and the crowd is on our side       -> reject
  * favourable carry (long & funding < 0, or short &
    funding > 0), magnitude >= 1 bp                     -> approve
  * |funding| <= 0.0003 (neutral / cheap)               -> approve, low confidence
  * adverse but not extreme                             -> abstain
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

CROWD_EXTREME = 0.001
NEUTRAL = 0.0003
MIN_CARRY = 0.0001


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    if isinstance(x, dict):
        for k in ("funding", "rate", "value", "funding_rate"):
            if k in x:
                return _num(x.get(k))
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


class FundingBot(Bot):
    id = "funding"
    edge_seat = True   # an approve means: positive carry / not crowded
    mandate = ("Get paid to hold: demand positive carry, and refuse to join a "
               "crowd that is already paying through the nose on our side.")
    domains = ("crypto",)
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        funding = _num(ctx.get("funding"))
        direction = _dir(ctx.get("direction"))
        evidence: Dict[str, Any] = {"funding": funding, "direction": direction}

        if funding is None:
            return Vote(self.id, "abstain", 0.0, ["missing:funding"], evidence)
        if direction is None:
            return Vote(self.id, "abstain", 0.0, ["missing:direction"], evidence)

        magnitude = abs(funding)
        crowded = (funding > CROWD_EXTREME and direction == "long") or \
                  (funding < -CROWD_EXTREME and direction == "short")
        if crowded:
            return Vote(self.id, "reject", 0.8,
                        [f"crowded_extreme:funding={funding:.5f}_on_{direction}_side"],
                        evidence)

        favourable = (direction == "long" and funding < 0) or \
                     (direction == "short" and funding > 0)
        if favourable and magnitude >= MIN_CARRY:
            conf = min(0.85, 0.55 + 2000.0 * magnitude)
            return Vote(self.id, "approve", conf,
                        [f"positive_carry:{-funding if direction == 'long' else funding:.5f}"],
                        evidence)
        if magnitude <= NEUTRAL:
            return Vote(self.id, "approve", 0.50,
                        [f"neutral_funding={funding:.5f}"], evidence)

        return Vote(self.id, "abstain", 0.0,
                    [f"adverse_carry={funding:.5f}_below_extreme"], evidence)
