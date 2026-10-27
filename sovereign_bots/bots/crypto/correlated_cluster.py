"""Ramsey / Spectral Correlated Beta Cluster Gate (Hard VETO)."""

from __future__ import annotations
from typing import Any, Dict, List, Set
from ...core.bot import Bot, Vote

CLUSTERS: Dict[str, Set[str]] = {
    "majors": {"BTC", "ETH"},
    "sol_l1": {"SOL", "SUI", "SEI", "APT", "AVAX", "INJ"},
    "ai_compute": {"TAO", "FET", "RENDER", "RNDR", "NEAR", "WLD", "AKT"},
    "privacy_pow": {"ZEC", "XMR", "DASH", "LTC", "BCH"},
    "memes": {"DOGE", "PEPE", "SHIB", "WIF", "BONK", "FLOKI"},
    "defi_infra": {"LINK", "UNI", "AAVE", "PENDLE", "ONDO", "ENA"},
}


def _cluster_for(coin: str) -> str:
    c = (coin or "").upper().replace("USDTM", "").replace("USDT", "").replace("-PERP", "")
    for name, members in CLUSTERS.items():
        if c in members:
            return name
    return f"idiosyncratic_{c}"


class CorrelatedClusterBetaBot(Bot):
    id = "correlated_cluster"
    mandate = ("Block new entries that stack >=2 same-direction positions inside "
               "the same correlated beta cluster (Ramsey clique guard).")
    domains = ("crypto",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        coin = str(ctx.get("coin") or ctx.get("symbol") or "").upper()
        direction = str(ctx.get("direction") or "").lower()
        open_positions = ctx.get("open_cluster_positions")
        if open_positions is None:
            open_positions = ctx.get("broker_positions")

        if not coin or not isinstance(open_positions, list):
            return Vote(self.id, "reject", 0.0, ["missing_coin_or_cluster_positions"], {})

        target_cluster = _cluster_for(coin)
        same_cluster_hits: List[str] = []
        for pos in open_positions:
            if not isinstance(pos, dict):
                continue
            p_coin = str(pos.get("coin") or pos.get("symbol") or "").upper()
            p_dir = str(pos.get("direction") or pos.get("side") or "").lower()
            if _cluster_for(p_coin) == target_cluster and (not direction or p_dir == direction):
                same_cluster_hits.append(p_coin)

        max_per_cluster = int(ctx.get("max_positions_per_cluster") or 1)
        evidence = {
            "coin": coin,
            "cluster": target_cluster,
            "same_cluster_open": same_cluster_hits,
            "max_positions_per_cluster": max_per_cluster,
        }
        if len(same_cluster_hits) >= max_per_cluster:
            return Vote(
                self.id,
                "reject",
                0.93,
                [f"cluster_crowded:{target_cluster}:open={','.join(same_cluster_hits)}"],
                evidence,
            )
        return Vote(self.id, "approve", 0.80, [f"cluster_ok:{target_cluster}"], evidence)
