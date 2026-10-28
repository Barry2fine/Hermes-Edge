"""VPIN Order-Flow Toxicity & L2 Microstructure Gate (Hard VETO)."""

from __future__ import annotations
from typing import Any, Dict, Optional
from ...core.bot import Bot, Vote
from ...core.institutional_risk_governor import compute_l2_kyle_lambda_and_vpin_routing


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class VpinToxicFlowBot(Bot):
    id = "vpin_toxicity"
    mandate = ("Block entries when L2 VPIN order-flow toxicity exceeds 0.72 "
               "or book imbalance strongly opposes trade direction.")
    domains = ("crypto",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        vpin = _num(ctx.get("vpin"))
        eobi = _num(ctx.get("eobi"))
        direction = str(ctx.get("direction") or "").lower()
        bids = ctx.get("bids")
        asks = ctx.get("asks")
        routing_meta: Dict[str, Any] = {}

        if isinstance(bids, list) and isinstance(asks, list) and bids and asks:
            try:
                notional = float(_num(ctx.get("order_notional_usd")) or 1000.0)
                r_info = compute_l2_kyle_lambda_and_vpin_routing(
                    bids=bids,
                    asks=asks,
                    side=direction or "long",
                    order_notional_usd=notional,
                )
                routing_meta = {
                    "kyle_lambda_bps_per_10k": r_info.get("kyle_lambda_bps_per_10k"),
                    "routing_decision": r_info.get("routing_decision"),
                    "recommended_post_only_px": r_info.get("recommended_post_only_px"),
                }
                if eobi is None:
                    eobi = r_info.get("eobi")
            except Exception:
                try:
                    b_vol = sum(float(x[1]) for x in bids[:5] if isinstance(x, (list, tuple)) and len(x) >= 2)
                    a_vol = sum(float(x[1]) for x in asks[:5] if isinstance(x, (list, tuple)) and len(x) >= 2)
                    if (b_vol + a_vol) > 0:
                        eobi = (b_vol - a_vol) / (b_vol + a_vol)
                except Exception:
                    pass

        if vpin is None and eobi is None:
            return Vote(self.id, "reject", 0.0, ["missing_vpin_and_orderbook_telemetry"], {})

        reasons = []
        evidence = {"vpin": vpin, "eobi": eobi, "direction": direction, **routing_meta}

        if vpin is not None and vpin > 0.72:
            reasons.append(f"toxic_orderflow_vpin:{vpin:.3f}>0.72")

        if eobi is not None:
            if direction == "long" and eobi < -0.45:
                reasons.append(f"heavy_ask_wall_against_long:eobi={eobi:.3f}")
            elif direction == "short" and eobi > 0.45:
                reasons.append(f"heavy_bid_wall_against_short:eobi={eobi:.3f}")

        if reasons:
            return Vote(self.id, "reject", 0.90, reasons, evidence)
        return Vote(self.id, "approve", 0.76, ["microstructure_flow_clean"], evidence)
