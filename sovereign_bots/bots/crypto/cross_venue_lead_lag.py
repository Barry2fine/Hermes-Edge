"""Cross-Venue Lead-Lag & Basis Dislocation Seat (Soft Edge Seat).

Scores directional lead-lag impulses from faster discovery venues (Binance Perp,
Hyperliquid L1, Coinbase Spot, DexScreener on-chain pools) into KuCoin Futures.
Establishes edge (`edge_seat = True`) when cross-venue basis and 1m-5m leader
impulse align with the proposed trade direction.
"""

from __future__ import annotations
from typing import Any, Dict, Optional
from ...core.bot import Bot, Vote


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class CrossVenueLeadLagBot(Bot):
    id = "cross_venue_lead_lag"
    mandate = ("Exploit lead-lag price discovery between Binance/Hyperliquid/Spot "
               "leaders and KuCoin Futures perp basis.")
    domains = ("crypto",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        leader_ret_bps = _num(ctx.get("leader_return_bps"))
        lag_gap_bps = _num(ctx.get("venue_lag_bps"))
        direction = str(ctx.get("direction") or "").lower()

        if leader_ret_bps is None or lag_gap_bps is None or direction not in ("long", "short"):
            return Vote(self.id, "abstain", 0.0, ["no_cross_venue_lead_lag_data"], {})

        sign = 1.0 if direction == "long" else -1.0
        aligned_impulse = leader_ret_bps * sign
        aligned_gap = lag_gap_bps * sign
        evidence = {
            "leader_return_bps": leader_ret_bps,
            "venue_lag_bps": lag_gap_bps,
            "aligned_impulse_bps": aligned_impulse,
            "aligned_gap_bps": aligned_gap,
        }

        if aligned_impulse >= 12.0 and aligned_gap >= 4.0:
            conf = min(0.92, 0.58 + (aligned_impulse + aligned_gap) / 80.0)
            return Vote(self.id, "approve", conf, [f"lead_lag_edge:{aligned_gap:.1f}bps_lag"], evidence)
        if aligned_impulse <= -12.0:
            return Vote(self.id, "reject", 0.72, [f"leader_moving_against:{leader_ret_bps:.1f}bps"], evidence)
        return Vote(self.id, "abstain", 0.0, ["lead_lag_neutral"], evidence)
