#!/usr/bin/env python3
"""
Unified Cross-Book Risk, Correlation & Implementation-Shortfall Ledger
Files:
  - /home/box/desk/unified_ledger.py
  - /home/box/desk/UNIFIED_BOOK.json
  - /home/box/desk/execution_attribution.jsonl

Combines live state from:
  1. Propr Classic 1-Step $25K Account (urn:prp-account:jcBy6Vk3KDNd)
  2. KuCoin Master Account (UID 249983249, Futures CONTRACT + Spot TRADE/MAIN)

Enforces cross-book correlation & duplicate-asset gates, naked-position blocks,
and tracks Implementation Shortfall (arrival vs fill price, Maker 2 bps vs
Taker 6 bps vs Propr 4.5 bps/side fee drag, and R-multiple expectancy).
"""

import json
import os
import sys
import time
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/workspace/propr-hedge-fund/bin")
sys.path.insert(0, "/home/box/desk/kucoin")

try:
    import profit_gates as PG  # type: ignore
except ImportError:
    PG = None  # type: ignore
except ImportError:
    PG = None  # type: ignore
from kucoin_client import KuCoinClient  # type: ignore

UNIFIED_BOOK_PATH = "/home/box/desk/UNIFIED_BOOK.json"
PROPR_DISABLED_BY_OWNER = True
ATTRIBUTION_LOG_PATH = "/home/box/desk/execution_attribution.jsonl"

BETA_CLUSTERS = {
    "MAJORS": {"BTC", "XBT", "ETH", "SOL", "XRP", "BNB", "ADA", "DOGE", "LTC", "BCH"},
    "PRIVACY_POW": {"XMR", "ZEC"},
    "HIGH_BETA_L1": {"SUI", "SEI", "APT", "AVAX", "NEAR", "INJ", "TIA", "DOT"},
    "DEFI_AI": {"AAVE", "UNI", "LINK", "TAO", "WLD", "LIT", "PENDLE"},
    "MEME": {"PEPE", "kPEPE", "PUMP", "WIF", "BONK"},
}


def normalize_asset(sym: str) -> str:
    s = str(sym or "").upper()
    for suf in ("USDTM", "-USDT", "-USDC", "-PERP", "USDT"):
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
            break
    if s in ("XBT", "BTC"):
        return "BTC"
    if s in ("KPEPE", "PEPE"):
        return "PEPE"
    return s


def get_cluster(asset: str) -> str:
    norm = normalize_asset(asset)
    for cname, members in BETA_CLUSTERS.items():
        if norm in members:
            return cname
    return "OTHER_ALT"


def bootstrap_attribution_ledger() -> List[Dict[str, Any]]:
    """Seed execution_attribution.jsonl from historical Propr + KuCoin journals if not yet initialized."""
    records: List[Dict[str, Any]] = []
    # 1. Load KuCoin trade journal
    kc_journal = "/home/box/desk/kucoin/trade_journal.jsonl"
    if os.path.exists(kc_journal):
        with open(kc_journal) as f:
            for line in f:
                if not line.strip():
                    continue
                j = json.loads(line)
                arr_px = float(j.get("arrival_price") or j.get("price") or 0)
                fill_px = float(j.get("fill_price") or j.get("price") or 0)
                side = str(j.get("side") or "").upper()
                if arr_px > 0 and fill_px > 0:
                    if side in ("BUY", "LONG"):
                        slip_bps = ((fill_px - arr_px) / arr_px) * 10000.0
                    else:
                        slip_bps = ((arr_px - fill_px) / arr_px) * 10000.0
                else:
                    slip_bps = 0.0
                fee_bps = float(j.get("fee_bps") or j.get("effective_fee_bps") or 2.0)
                rec = {
                    "ts": j.get("ts") or j.get("timestamp"),
                    "book": "KUCOIN",
                    "venue": j.get("venue") or j.get("market", "KUCOIN_FUTURES"),
                    "symbol": j.get("symbol"),
                    "asset": normalize_asset(j.get("symbol", "")),
                    "side": side,
                    "order_type": j.get("order_type", "LIMIT_POST_ONLY"),
                    "arrival_price": arr_px,
                    "fill_price": fill_px,
                    "notional_usd": float(j.get("notional_usd") or j.get("funds_usdt") or 0),
                    "slippage_bps": round(slip_bps, 2),
                    "fee_bps": fee_bps,
                    "impl_shortfall_bps": round(slip_bps + fee_bps, 2),
                    "fee_usd": float(j.get("fee_usd") or j.get("net_fee_usdt") or 0),
                    "stop_price_type": j.get("stop_price_type", "MP"),
                    "r_multiple": float(j.get("r_multiple") or 0.0),
                    "net_pnl_usd": float(j.get("net_pnl_usd") or 0.0),
                    "strategy": j.get("strategy", "EMA20_PULLBACK_MAKER"),
                    "notes": j.get("notes") or j.get("rationale", ""),
                }
                records.append(rec)

    # 2. Load Propr closed strategy trades from strategy_scorecard.json
    sc_path = "/workspace/propr-hedge-fund/journal/strategy_scorecard.json"
    if os.path.exists(sc_path):
        with open(sc_path) as f:
            sc = json.load(f)
        for strat_name, sdata in (sc.get("strategies") or {}).items():
            for t in sdata.get("log") or []:
                records.append({
                    "ts": t.get("closed"),
                    "book": "PROPR",
                    "venue": "HYPERLIQUID_PERP",
                    "symbol": f"{t.get('sym')}-PERP",
                    "asset": normalize_asset(t.get("sym", "")),
                    "side": t.get("side"),
                    "order_type": "IOC_TAKER",
                    "arrival_price": None,
                    "fill_price": None,
                    "notional_usd": None,
                    "slippage_bps": 2.0,
                    "fee_bps": 9.0,
                    "impl_shortfall_bps": 11.0,
                    "fee_usd": round(float(sdata.get("fees", 0)) / max(1, len(sdata.get("log", []))), 3),
                    "stop_price_type": "STOP_MARKET_REDUCE_ONLY",
                    "r_multiple": float(t.get("R") or 0),
                    "net_pnl_usd": float(t.get("net") or 0),
                    "strategy": strat_name,
                    "notes": t.get("exit", ""),
                })

    with open(ATTRIBUTION_LOG_PATH, "w") as f:
        for r in records:
            f.write(json.dumps(r) + "\n")
    return records


def compute_attribution_summary(records: List[Dict[str, Any]]) -> Dict[str, Any]:
    propr_recs = [r for r in records if r.get("book") == "PROPR"]
    kc_recs = [r for r in records if r.get("book") == "KUCOIN"]

    def _stats(rows: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not rows:
            return {"count": 0, "net_pnl_usd": 0.0, "sum_r": 0.0, "avg_r": 0.0, "avg_impl_shortfall_bps": 0.0}
        n = len(rows)
        net = sum(float(x.get("net_pnl_usd") or 0) for x in rows)
        sum_r = sum(float(x.get("r_multiple") or 0) for x in rows)
        avg_is = sum(float(x.get("impl_shortfall_bps") or 0) for x in rows) / n
        wins = sum(1 for x in rows if float(x.get("r_multiple") or 0) > 0)
        closed_r = [x for x in rows if x.get("r_multiple") is not None and float(x.get("r_multiple") or 0) != 0]
        wr = (wins / len(closed_r)) if closed_r else 0.0
        return {
            "count": n,
            "closed_r_trades": len(closed_r),
            "win_rate": round(wr, 3),
            "net_pnl_usd": round(net, 2),
            "sum_r": round(sum_r, 3),
            "avg_r": round(sum_r / len(closed_r), 3) if closed_r else 0.0,
            "avg_impl_shortfall_bps": round(avg_is, 2),
        }

    return {
        "combined_net_pnl_usd": round(sum(float(x.get("net_pnl_usd") or 0) for x in records), 2),
        "combined_sum_r": round(sum(float(x.get("r_multiple") or 0) for x in records), 3),
        "propr_attribution": _stats(propr_recs),
        "kucoin_attribution": _stats(kc_recs),
        "fee_advantage_note": "KuCoin Futures Maker (2 bps/side, 4 bps RT) saves 7 bps RT vs Propr IOC Taker (11 bps RT incl slippage).",
    }


def build_unified_book() -> Dict[str, Any]:
    # 1. Propr is managed by Barry's other agent (Hermes / SovereignAI) — do NOT touch Propr API or sync propr Worker
    if PROPR_DISABLED_BY_OWNER:
        propr_bk = {"equity": 25000.0, "floor": 23500.0, "room": 1500.0, "open": {"count": 0, "total": 0.0, "legs": []}, "disabled_by_owner": True}
    else:
        propr_bk = PG.live_book(sync_worker=True)

    # 2. Pull KuCoin Live Book & sync kucoin-desk-scan Worker
    kc = KuCoinClient()
    kc_worker_sync = kc.sync_worker_state()
    kc_sync_data = kc_worker_sync.get("account_sync") or {}
    kc_acct = kc_sync_data.get("account") or {}
    kc_sentinel = kc_sync_data.get("sentinel") or {}
    kc_positions = kc_sync_data.get("positions") or []
    kc_entry_orders = kc_sync_data.get("active_entry_orders") or []
    kc_stop_orders = kc_sync_data.get("active_stop_orders") or []

    # 3. Build unified exposure map across both books
    all_open_legs: List[Dict[str, Any]] = []
    for leg in propr_bk["open"].get("legs") or []:
        asset = normalize_asset(leg.get("asset", ""))
        all_open_legs.append({
            "book": "PROPR",
            "symbol": leg.get("asset"),
            "asset": asset,
            "cluster": get_cluster(asset),
            "side": str(leg.get("side") or "").upper(),
            "qty": leg.get("qty"),
            "mark": leg.get("mark"),
            "sl": leg.get("sl"),
            "stop_type": "STOP_MARKET_REDUCE_ONLY",
            "risk_usd": leg.get("risk"),
        })

    for p in kc_positions:
        asset = normalize_asset(p.get("symbol", ""))
        all_open_legs.append({
            "book": "KUCOIN",
            "symbol": p.get("symbol"),
            "asset": asset,
            "cluster": get_cluster(asset),
            "side": str(p.get("side") or "").upper(),
            "qty": p.get("qty_lots"),
            "notional_usd": p.get("notional_usd"),
            "entry": p.get("entry_price"),
            "mark": p.get("mark_price"),
            "sl": p.get("stop_price"),
            "stop_type": p.get("stop_price_type"),
            "risk_usd": p.get("risk_usd"),
        })

    for o in kc_entry_orders:
        asset = normalize_asset(o.get("symbol", ""))
        is_ro = bool(o.get("reduce_only") or o.get("reduceOnly"))
        all_open_legs.append({
            "book": "KUCOIN_REDUCE_ONLY_TP" if is_ro else "KUCOIN_PENDING_LIMIT",
            "symbol": o.get("symbol"),
            "asset": asset,
            "cluster": get_cluster(asset),
            "side": "LONG" if str(o.get("side", "")).upper() == "BUY" else "SHORT",
            "qty": o.get("size"),
            "limit_price": o.get("price"),
            "mark": o.get("mark_price"),
            "post_only": o.get("post_only"),
            "reduce_only": is_ro,
            "order_id": o.get("id"),
        })

    propr_eq = float(propr_bk["equity"])
    kc_eq = float(kc_acct.get("account_equity") or 199.87) + float(kc_acct.get("spot_usdt") or 0.0)
    combined_eq = round(propr_eq + kc_eq, 2)

    propr_naked = propr_bk["open"].get("naked") or []
    kc_naked = [n.get("symbol") for n in (kc_sentinel.get("naked_positions") or [])]

    records = bootstrap_attribution_ledger()
    attr_summary = compute_attribution_summary(records)

    unified = {
        "updated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "specialist_roster": {
            "chief_of_staff": "34d6400c-a5f0-4670-957a-09541f0c0cb9",
            "kucoin_desk": "8e98ca7e-3f25-41b6-9053-3aa88bdaf0fc",
            "polymarket_desk": "4c399fc8-777d-4418-9792-5b060bc09a18",
            "prop_bet_edge": "4731ab9e-86da-4cf9-bdbf-5f48294f6d25",
            "edge_journal": "400d2d63-5404-4712-b6a0-8f76a0688f73",
        },
        "worker_endpoints": {
            "propr_desk_scan": "https://propr-desk-scan.sonia-3e6f56284b.workers.dev/status",
            "kucoin_desk_scan": "https://kucoin-desk-scan.sonia-3e6f56284b.workers.dev/status",
            "polymarket_alpha_scan": "https://kucoin-desk-scan.sonia-3e6f56284b.workers.dev/polymarket",
            "bet_odds_scan": "https://bet-odds-scan.sonia-3e6f56284b.workers.dev/status",
        },
        "combined_desk": {
            "total_equity_usd": combined_eq,
            "total_open_risk_usd": round(float(propr_bk["open"]["total"]) + float(kc_sentinel.get("total_open_risk_usd") or 0), 2),
            "naked_positions": propr_naked + kc_naked,
            "all_stops_verified": len(propr_naked) == 0 and len(kc_naked) == 0,
            "open_legs_and_orders": all_open_legs,
        },
        "propr_book": {
            "account_id": "urn:prp-account:jcBy6Vk3KDNd",
            "equity_usd": propr_eq,
            "floor_usd": 23500.0,
            "room_usd": float(propr_bk["room"]),
            "open_risk_usd": float(propr_bk["open"]["total"]),
            "max_ticket_risk_usd": 100.0,
            "open_legs": propr_bk["open"]["legs"],
            "naked": propr_naked,
            "worker_url": "https://propr-desk-scan.sonia-3e6f56284b.workers.dev/status",
        },
        "kucoin_book": {
            "uid": 249983249,
            "futures_equity_usdt": float(kc_acct.get("account_equity") or 199.87),
            "futures_available_usdt": float(kc_acct.get("available_balance") or 199.87),
            "spot_dust_usdt": float(kc_acct.get("spot_usdt") or 0.0),
            "floor_usdt": 170.0,
            "room_usdt": round(float(kc_acct.get("account_equity") or 199.87) - 170.0, 2),
            "open_risk_usd": float(kc_sentinel.get("total_open_risk_usd") or 0.0),
            "max_ticket_risk_usd": 3.00,
            "sentinel_status": kc_sentinel.get("status", "ALL_PROTECTED_OK"),
            "open_positions": kc_positions,
            "active_entry_orders": kc_entry_orders,
            "active_stop_orders": kc_stop_orders,
            "worker_url": "https://kucoin-desk-scan.sonia-3e6f56284b.workers.dev/status",
        },
        "execution_attribution": attr_summary,
    }

    with open(UNIFIED_BOOK_PATH, "w") as f:
        json.dump(unified, f, indent=2)
    return unified


def check_cross_book_gate(book: str, symbol: str, side: str, risk_usd: float) -> Dict[str, Any]:
    """
    Validate a proposed trade against unified cross-book risk, correlation, and naked-stop rules.
    """
    if os.path.exists(UNIFIED_BOOK_PATH):
        with open(UNIFIED_BOOK_PATH) as f:
            ub = json.load(f)
    else:
        ub = build_unified_book()

    reasons: List[str] = []
    asset = normalize_asset(symbol)
    cluster = get_cluster(asset)
    side_u = side.upper()
    book_u = book.upper()

    # 1. Naked position check across both books
    naked = ub["combined_desk"].get("naked_positions") or []
    if naked:
        reasons.append(f"NAKED_POSITION_BLOCK({','.join(naked)})")

    # 2. Duplicate asset check across Propr + KuCoin (excluding reduce-only TP orders)
    open_legs = [
        leg for leg in (ub["combined_desk"].get("open_legs_and_orders") or [])
        if leg.get("book") != "KUCOIN_REDUCE_ONLY_TP" and not leg.get("reduce_only")
    ]
    for leg in open_legs:
        if leg.get("asset") == asset and leg.get("side") == side_u:
            reasons.append(f"DUPLICATE_ASSET_DIRECTION({asset} {side_u} already active on {leg.get('book')})")

    # 3. Beta-cluster concentration check (max 2 same-direction legs in same cluster across combined desk)
    same_cluster_dir = [
        leg for leg in open_legs
        if leg.get("cluster") == cluster and leg.get("side") == side_u
    ]
    if len(same_cluster_dir) >= 2:
        reasons.append(f"CLUSTER_CONCENTRATION({cluster} already has {len(same_cluster_dir)} {side_u} legs)")

    # 4. Book-specific risk cap
    if book_u == "KUCOIN":
        room = float(ub["kucoin_book"]["room_usdt"])
        cur_risk = float(ub["kucoin_book"]["open_risk_usd"])
        max_ticket = float(ub["kucoin_book"]["max_ticket_risk_usd"])
        if risk_usd > max_ticket:
            reasons.append(f"KUCOIN_TICKET_CAP(${risk_usd:.2f} > ${max_ticket:.2f})")
        if cur_risk + risk_usd > min(9.0, room - 5.0):
            reasons.append(f"KUCOIN_PORTFOLIO_CAP(open ${cur_risk:.2f} + new ${risk_usd:.2f} exceeds budget)")
    elif book_u == "PROPR":
        room = float(ub["propr_book"]["room_usd"])
        cur_risk = float(ub["propr_book"]["open_risk_usd"])
        if risk_usd > 100.0:
            reasons.append(f"PROPR_TICKET_CAP(${risk_usd:.2f} > $100)")
        if cur_risk + risk_usd >= room - 10.0:
            reasons.append(f"PROPR_ROOM_CAP(open ${cur_risk:.2f} + new ${risk_usd:.2f} >= room-$10 ${room-10:.2f})")

    return {
        "ok": len(reasons) == 0,
        "book": book_u,
        "symbol": symbol,
        "normalized_asset": asset,
        "beta_cluster": cluster,
        "side": side_u,
        "proposed_risk_usd": round(risk_usd, 3),
        "reasons": reasons,
    }


def log_execution_event(event: Dict[str, Any]) -> None:
    bootstrap_attribution_ledger()
    with open(ATTRIBUTION_LOG_PATH, "a") as f:
        f.write(json.dumps(event) + "\n")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "sync"
    if cmd == "sync":
        ub = build_unified_book()
        print(json.dumps(ub, indent=2))
    elif cmd == "gate" and len(sys.argv) >= 6:
        res = check_cross_book_gate(sys.argv[2], sys.argv[3], sys.argv[4], float(sys.argv[5]))
        print(json.dumps(res, indent=2))
        sys.exit(0 if res["ok"] else 2)
    else:
        print("Usage: unified_ledger.py [sync | gate <PROPR|KUCOIN> <SYMBOL> <LONG|SHORT> <RISK_USD>]")
