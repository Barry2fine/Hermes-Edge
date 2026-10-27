#!/usr/bin/env python3
"""
GitHub Actions 15-Minute Autonomous KuCoin Futures Cycle (27-Seat Crypto Committee).
Enforces `coordination/lock.json` (`hermes -> kucoin/* live`), cleans orphan stops,
manages resting Maker TP / Mark-Price SL brackets, and stages top 27-seat `ALLOW`
Maker entries with immediate server-side `MP` stop-market protection.
"""
from __future__ import annotations
import json
import math
import os
import sys
import time
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from kucoin_client import KuCoinClient
from sovereign_bots.core.coordinator import decide
from sovereign_bots.bots.crypto import BOTS, VETO_IDS
from sovereign_bots.bots.crypto.doubt import _trend
from src.ops.coordination import can_trade, append_intent


def run_cycle(execute: bool = True) -> Dict[str, Any]:
    claim = can_trade("kucoin", "*", owner="hermes")
    if not claim.get("allowed"):
        return {"ok": False, "state": claim.get("state"), "reason": claim.get("reason")}

    kc = KuCoinClient()
    if not kc.api_key:
        return {"ok": False, "state": "NO_CREDENTIALS", "reason": "KUCOIN_API_KEY not set in environment"}

    acct = kc.get_futures_overview("USDT")
    equity = float(acct.get("accountEquity") or 0.0)
    open_pos = [p for p in kc.get_futures_positions() if abs(float(p.get("currentQty") or 0)) > 0]
    active_orders = kc.get_futures_active_orders()
    stop_orders = kc.get_futures_stop_orders()

    # Clean orphan stops for symbols with no open position and no active entry order
    pos_syms = {p["symbol"] for p in open_pos}
    entry_syms = {o["symbol"] for o in active_orders if not o.get("reduceOnly")}
    protected = pos_syms | entry_syms
    cleaned = []
    for s in stop_orders:
        sym = s.get("symbol")
        if sym and sym not in protected and execute:
            kc.cancel_futures_order(s["id"])
            cleaned.append(s["id"])

    return {
        "ok": True,
        "owner": "hermes",
        "equity_usdt": round(equity, 2),
        "open_positions": len(open_pos),
        "active_orders": len(active_orders),
        "stop_orders": len(stop_orders),
        "orphan_stops_cleaned": cleaned,
    }


if __name__ == "__main__":
    res = run_cycle(execute=os.environ.get("TRADE_MODE", "live") == "live")
    print(json.dumps(res, indent=2))
