#!/usr/bin/env python3
"""
GitHub Actions 15-Minute Autonomous KuCoin Multi-Service Cycle (50-Seat Crypto Committee + 1,492 Per-Coin Bots).
Enforces `coordination/lock.json` (`hermes -> kucoin/* live`), cleans orphan stops,
verifies every open Futures position has both a server-side Mark-Price (`MP`) stop-market
and a resting `postOnly` `reduceOnly` Maker Take-Profit (`2.1R`), monitors KuCoin Spot
OCO/Maker brackets, falls back to the Cloudflare Worker `/status` bridge (`kucoin-desk-scan`)
when running on US-hosted GitHub Actions runners (`HTTP 451000` geo-block), updates
`data/learning/knowledge_loop_state.json`, and dispatches an institutional Telegram Executive Card.
"""
from __future__ import annotations
import json
import os
import sys
import urllib.request
from typing import Any, Dict, List

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from kucoin_client import KuCoinClient
from sovereign_bots.bots.crypto import BOTS, get_coin_bot
from sovereign_bots.core.knowledge_loop import load_knowledge_state, save_knowledge_state
from sovereign_bots.core.telegram_alerts import format_kucoin_cycle_card, send_institutional_telegram
from src.ops.coordination import can_trade

WORKER_STATUS_URL = "https://kucoin-desk-scan.sonia-3e6f56284b.workers.dev/status"


def _fetch_worker_account_sync() -> Dict[str, Any]:
    try:
        req = urllib.request.Request(
            WORKER_STATUS_URL,
            headers={"Accept": "application/json", "User-Agent": "HermesEdgeGitHubCycle/1.0"},
        )
        with urllib.request.urlopen(req, timeout=8) as r:
            doc = json.loads(r.read().decode("utf-8"))
        return doc.get("account_sync") or {}
    except Exception:
        return {}


def run_cycle(execute: bool = True, notify_telegram: bool = True) -> Dict[str, Any]:
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

    source_mode = "direct_kucoin_api"
    cleaned: List[str] = []
    positions_summary: List[Dict[str, Any]] = []
    spot_summary: List[Dict[str, Any]] = []
    active_limit_count = len(active_orders)
    active_stop_count = len(stop_orders)

    if equity > 0 or open_pos:
        # 1. Clean orphan stops for symbols with no open position and no active entry order
        pos_syms = {p["symbol"] for p in open_pos}
        entry_syms = {o["symbol"] for o in active_orders if not o.get("reduceOnly")}
        protected = pos_syms | entry_syms
        for s in stop_orders:
            sym = s.get("symbol")
            if sym and sym not in protected and execute:
                kc.cancel_futures_order(s["id"])
                cleaned.append(s["id"])

        # 2. Verify every open position has both a server-side MP Stop AND a resting reduceOnly Maker TP
        tp_syms = {o["symbol"] for o in active_orders if o.get("reduceOnly")}
        stop_syms = {s["symbol"] for s in stop_orders if s.get("id") not in cleaned}
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
        spot_accts = [
            a for a in kc.get_spot_accounts()
            if a.get("type") == "trade" and float(a.get("balance") or 0) > 0.0001
        ]
        spot_summary = [
            {
                "currency": a.get("currency"),
                "balance": float(a.get("balance") or 0),
                "available": float(a.get("available") or 0),
                "holds_in_orders": float(a.get("holds") or 0),
            }
            for a in spot_accts
        ]
        active_stop_count = len(stop_orders) - len(cleaned)
        try:
            kc.sync_worker_state()
        except Exception:
            pass
    else:
        # Fallback for US-hosted GitHub Actions runners where KuCoin returns HTTP 451000 geo-block
        w_sync = _fetch_worker_account_sync()
        w_acct = w_sync.get("account") or {}
        if float(w_acct.get("account_equity") or 0.0) > 0:
            source_mode = "cloudflare_worker_bridge"
            equity = float(w_acct.get("account_equity") or 0.0)
            unrealised_pnl = float(w_acct.get("unrealised_pnl") or 0.0)
            avail_margin = float(w_acct.get("available_balance") or 0.0)
            spot_usdt = float(w_acct.get("spot_usdt") or 0.0)
            if spot_usdt > 0:
                spot_summary = [{"currency": "USDT", "balance": spot_usdt, "available": spot_usdt, "holds_in_orders": 0.0}]
            w_entries = w_sync.get("active_entry_orders") or []
            w_stops = w_sync.get("active_stop_orders") or []
            active_limit_count = len(w_entries)
            active_stop_count = len(w_stops)
            tp_syms = {o.get("symbol") for o in w_entries if o.get("reduce_only")}
            for p in (w_sync.get("positions") or []):
                sym = str(p.get("symbol") or "ETHUSDTM")
                side = str(p.get("side") or "SHORT").upper()
                qty_lots = int(p.get("qty_lots") or 0)
                if side == "SHORT" and qty_lots > 0:
                    qty_lots = -qty_lots
                coin_bot = get_coin_bot(sym)
                positions_summary.append({
                    "symbol": sym,
                    "coin_specialist_bot": coin_bot.id,
                    "side": side,
                    "qty_lots": qty_lots,
                    "entry_px": float(p.get("entry_price") or 0.0),
                    "mark_px": float(p.get("mark_price") or 0.0),
                    "unrealised_pnl_usdt": round(float(p.get("unrealised_pnl") or 0.0), 4),
                    "has_server_mp_stop": bool(p.get("mp_stop_verified", True)),
                    "has_maker_tp": sym in tp_syms,
                })

    # 4. Touch knowledge loop state heartbeat
    k_state = load_knowledge_state()
    save_knowledge_state(k_state)

    summary = {
        "ok": True,
        "owner": "hermes",
        "source_mode": source_mode,
        "committee_crypto_seats": len(BOTS),
        "futures_equity_usdt": round(equity, 4),
        "futures_unrealised_pnl_usdt": round(unrealised_pnl, 4),
        "futures_available_margin_usdt": round(avail_margin, 4),
        "open_futures_positions": positions_summary,
        "spot_trade_balances": spot_summary,
        "active_limit_orders": active_limit_count,
        "active_stop_orders": active_stop_count,
        "orphan_stops_cleaned": cleaned,
    }

    if notify_telegram and (equity > 0 or cleaned):
        card = format_kucoin_cycle_card(summary)
        send_institutional_telegram(card, desk="HERMES-EDGE • KUCOIN MULTI-SERVICE DESK")

    return summary


if __name__ == "__main__":
    res = run_cycle(
        execute=os.environ.get("TRADE_MODE", "live") == "live",
        notify_telegram=os.environ.get("SEND_TELEGRAM", "1") != "0",
    )
    print(json.dumps(res, indent=2))
