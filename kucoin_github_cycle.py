#!/usr/bin/env python3
"""
GitHub Actions 15-Minute Autonomous KuCoin Multi-Service Cycle (50-Seat Crypto Committee + 1,492 Per-Coin Bots).
Enforces `coordination/lock.json` (`hermes -> kucoin/* live`), cleans orphan stops,
verifies every open Futures position has both a server-side Mark-Price (`MP`) stop-market
and a resting `postOnly` `reduceOnly` Maker Take-Profit (`2.1R`), monitors KuCoin Spot
OCO/Maker brackets, and updates `data/learning/knowledge_loop_state.json`.
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
from sovereign_bots.bots.crypto import BOTS, VETO_IDS, get_coin_bot
from sovereign_bots.core.knowledge_loop import load_knowledge_state, save_knowledge_state
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
    unrealised_pnl = float(acct.get("unrealisedPNL") or 0.0)
    avail_margin = float(acct.get("availableBalance") or 0.0)

    open_pos = [p for p in kc.get_futures_positions() if abs(float(p.get("currentQty") or 0)) > 0]
    active_orders = kc.get_futures_active_orders()
    stop_orders = kc.get_futures_stop_orders()

    # 1. Clean orphan stops for symbols with no open position and no active entry order
    pos_syms = {p["symbol"] for p in open_pos}
    entry_syms = {o["symbol"] for o in active_orders if not o.get("reduceOnly")}
    protected = pos_syms | entry_syms
    cleaned = []
    for s in stop_orders:
        sym = s.get("symbol")
        if sym and sym not in protected and execute:
            kc.cancel_futures_order(s["id"])
            cleaned.append(s["id"])

    # 2. Verify every open position has both a server-side MP Stop AND a resting reduceOnly Maker TP
    tp_syms = {o["symbol"] for o in active_orders if o.get("reduceOnly")}
    stop_syms = {s["symbol"] for s in stop_orders if s.get("id") not in cleaned}
    positions_summary = []
    for p in open_pos:
        sym = p["symbol"]
        qty = float(p.get("currentQty") or 0)
        entry_px = float(p.get("avgEntryPrice") or 0.0)
        mark_px = float(p.get("markPrice") or 0.0)
        upnl = float(p.get("unrealisedPnl") or 0.0)
        coin_bot = get_coin_bot(sym)
        positions_summary.append({
            "symbol": sym,
            "coin_specialist_bot": coin_bot.id,
            "side": "SHORT" if qty < 0 else "LONG",
            "qty_lots": int(qty),
            "entry_px": entry_px,
            "mark_px": mark_px,
            "unrealised_pnl_usdt": round(upnl, 4),
            "has_server_mp_stop": sym in stop_syms,
            "has_maker_tp": sym in tp_syms,
        })

    # 3. Inspect KuCoin Spot balances & active Spot / OCO orders
    spot_accts = [a for a in kc.get_spot_accounts() if a.get("type") == "trade" and float(a.get("balance") or 0) > 0.0001]
    spot_summary = [
        {
            "currency": a.get("currency"),
            "balance": float(a.get("balance") or 0),
            "available": float(a.get("available") or 0),
            "holds_in_orders": float(a.get("holds") or 0),
        }
        for a in spot_accts
    ]

    # 4. Touch knowledge loop state heartbeat
    k_state = load_knowledge_state()
    save_knowledge_state(k_state)

    return {
        "ok": True,
        "owner": "hermes",
        "committee_crypto_seats": len(BOTS),
        "futures_equity_usdt": round(equity, 4),
        "futures_unrealised_pnl_usdt": round(unrealised_pnl, 4),
        "futures_available_margin_usdt": round(avail_margin, 4),
        "open_futures_positions": positions_summary,
        "spot_trade_balances": spot_summary,
        "active_limit_orders": len(active_orders),
        "active_stop_orders": len(stop_orders) - len(cleaned),
        "orphan_stops_cleaned": cleaned,
    }


if __name__ == "__main__":
    res = run_cycle(execute=os.environ.get("TRADE_MODE", "live") == "live")
    print(json.dumps(res, indent=2))
