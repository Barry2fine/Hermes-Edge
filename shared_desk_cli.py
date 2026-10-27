#!/usr/bin/env python3
r"""
SHARED GROK BOT + CHATGPT DUAL-BOOK EXECUTION CLI & FAILOVER ENGINE (v5.0)
==========================================================================
File:
  - PC (`poet`): C:\Users\Simpl\grok-propr-bot\shared_desk_cli.py
  - Box:         /home/box/desk/shared_desk_cli.py

Gives ChatGPT (Desktop App, Codex CLI, Local Terminal) AND `poet_sentinel.py`
full, safety-gated, conflict-free access to both live trading books:
  1. Propr Classic 1-Step $25K Evaluation (`urn:prp-account:jcBy6Vk3KDNd` on Hyperliquid Perps)
     - Static Floor: $23,500.00 | Pass Target: $27,500.00 | Max Slots: 2
     - Hard Rules: <= $320 notional/trade (~$6-$12.50 1R risk), <= 2x isolated leverage,
       mandatory server-side `stop_market` SL (`reduceOnly=True`) + `take_profit_market` TP (`+1.85R`).
  2. KuCoin Perpetual Futures (`UID 249983249`)
     - Equity Floor: $170.00 USDT | Max Slots: 3
     - Hard Rules: `postOnly=True` Maker limit entries & TPs (2 bps Maker vs 6 bps Taker),
       mandatory server-side `MP` (Mark Price) `reduceOnly=True` stop-loss on every position/entry.

Commands:
  python shared_desk_cli.py status [--json]
  python shared_desk_cli.py ratchet [--dry-run] [--json]
  python shared_desk_cli.py auto-trade [--dry-run] [--json]
  python shared_desk_cli.py trade-propr --asset AVAX --side SHORT --notional 310 --sl 9.12 --tp 8.62
  python shared_desk_cli.py trade-kucoin --symbol SOLUSDTM --side SHORT --lots 32 --price 101.44 --sl 102.15 --tp 100.12
  python shared_desk_cli.py close --venue <propr|kucoin> --symbol <ASSET_OR_SYMBOL>
"""

import argparse
import datetime
import functools
import json
import logging
import math
import os
import sys
import threading
import time
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

logger = logging.getLogger("desk_engine")

SCRIPT_DIR = os.path.dirname(os.path.abspath(__file__))
for p in [
    SCRIPT_DIR,
    r"C:\Users\Simpl\grok-propr-bot",
    "/home/box/desk",
    "/home/box/desk/kucoin",
    "/workspace/grok-propr-bot",
]:
    if os.path.exists(p) and p not in sys.path:
        sys.path.insert(0, p)

from kucoin_client import KuCoinClient
from propr_sdk import ProprClient

PROPR_ACCOUNT_ID = os.environ.get("PROPR_ACCOUNT_ID", "urn:prp-account:jcBy6Vk3KDNd")
KUCOIN_UID = 249983249
PROPR_STATIC_FLOOR = 23500.0
PROPR_PASS_TARGET = 27500.0
PROPR_MIN_EQUITY_FOR_AUTO = 23560.0
KUCOIN_STATIC_FLOOR = 170.0
MAX_PROPR_SLOTS = 2
MAX_KUCOIN_SLOTS = 2
MAX_PROPR_NOTIONAL_USD = 320.0
DEFAULT_PROPR_NOTIONAL_USD = 310.0

KUCOIN_WORKER_URL = "https://kucoin-desk-scan.sonia-3e6f56284b.workers.dev"
PROPR_WORKER_URL = "https://propr-desk-scan.sonia-3e6f56284b.workers.dev"

IS_WINDOWS = os.name == "nt"
STATE_DIR = r"C:\Users\Simpl\grok-propr-bot" if IS_WINDOWS else "/home/box/desk"
LOCK_FILE = os.path.join(STATE_DIR, "shared_desk_lock.json")
AUDIT_LOG_FILE = os.path.join(STATE_DIR, "shared_desk_actions.jsonl")
POET_TELEMETRY_FILE = os.path.join(STATE_DIR, "latest_poet_max_telemetry.json")

HL_SZ_DECIMALS = {
    "BTC": 5, "ETH": 4, "SOL": 2, "XRP": 0, "DOGE": 0,
    "AVAX": 2, "LINK": 1, "SUI": 1, "TAO": 3, "XMR": 3,
    "NEAR": 1, "AAVE": 2, "UNI": 1, "SEI": 0, "PEPE": 0,
    "INJ": 1, "OP": 1, "ARB": 0, "APT": 2, "TIA": 1, "ZEC": 2,
}
HL_PX_DECIMALS = {
    "BTC": 1, "ETH": 2, "SOL": 2, "XRP": 4, "DOGE": 5,
    "AVAX": 3, "LINK": 3, "SUI": 4, "TAO": 2, "XMR": 2,
    "NEAR": 3, "AAVE": 2, "UNI": 3, "SEI": 4, "INJ": 3,
    "OP": 4, "ARB": 4, "APT": 3, "TIA": 3, "ZEC": 2,
}

DEFAULT_GENOME = {
    "atr_stop_mult": 1.65,
    "tp_r_mult": 1.85,
    "trail_activation_r": 0.75,
    "min_composite_edge": 78.0,
    "funding_snipe_min_bps": 16.5,
    "bernstein_delta": 0.08,
}


def _now_iso() -> str:
    return datetime.datetime.now(datetime.timezone.utc).isoformat()


def _now_edt() -> str:
    edt = datetime.timezone(datetime.timedelta(hours=-4))
    return datetime.datetime.now(datetime.timezone.utc).astimezone(edt).strftime("%Y-%m-%d %H:%M:%S EDT")


def _fmt_hl_qty(asset: str, qty: float) -> str:
    dec = HL_SZ_DECIMALS.get(asset.upper(), 2)
    if dec == 0:
        return str(max(1, int(round(qty))))
    val = round(qty, dec)
    return f"{val:.{dec}f}".rstrip("0").rstrip(".")


def _fmt_hl_px(asset: str, px: float) -> str:
    dec = HL_PX_DECIMALS.get(asset.upper(), 2)
    val = round(px, dec)
    return f"{val:.{dec}f}".rstrip("0").rstrip(".")


def _fmt_kc_px(tick: float, px: float) -> str:
    decimals = max(0, -int(round(math.log10(tick)))) if 0 < tick < 1 else 0
    step_px = round(round(px / tick) * tick, decimals) if tick > 0 else round(px, decimals)
    return f"{step_px:.{decimals}f}"


def normalize_asset(symbol: str) -> str:
    s = str(symbol or "").upper().strip()
    for suf in ("USDTM", "USDCM", "USDT", "USDC", "-PERP", "/USDC", "/USDT"):
        if s.endswith(suf) and len(s) > len(suf):
            s = s[: -len(suf)]
    if s in ("XBT", "WBTC"):
        return "BTC"
    if s == "KPEPE":
        return "PEPE"
    return s


_LOCK_GUARD = threading.RLock()
_LOCK_HANDLE = None
_LOCK_OWNER = None
_LOCK_DEPTH = 0


def acquire_lock(agent_name: str = "shared_cli", ttl_sec: float = 45.0) -> bool:
    del ttl_sec
    global _LOCK_HANDLE, _LOCK_OWNER, _LOCK_DEPTH
    ident = (os.getpid(), threading.get_ident())
    with _LOCK_GUARD:
        if _LOCK_HANDLE is not None:
            if _LOCK_OWNER == ident:
                _LOCK_DEPTH += 1
                return True
            return False
        handle = None
        try:
            parent = os.path.dirname(LOCK_FILE)
            if parent:
                os.makedirs(parent, exist_ok=True)
            handle = open(LOCK_FILE, "a+b")
            handle.seek(0, os.SEEK_END)
            if handle.tell() == 0:
                handle.write(b" ")
                handle.flush()
            handle.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            payload = json.dumps({"agent": agent_name, "pid": ident[0], "thread": ident[1], "epoch": time.time(), "ts_utc": _now_iso()}).encode("utf-8")
            handle.seek(0)
            handle.truncate()
            handle.write(max(payload, b" "))
            handle.flush()
            _LOCK_HANDLE = handle
            _LOCK_OWNER = ident
            _LOCK_DEPTH = 1
            return True
        except Exception:
            logger.error("LOCK_UNAVAILABLE")
            if handle is not None:
                try:
                    handle.close()
                except Exception:
                    logger.error("LOCK_HANDLE_CLOSE_FAILED")
            return False


def release_lock() -> None:
    global _LOCK_HANDLE, _LOCK_OWNER, _LOCK_DEPTH
    ident = (os.getpid(), threading.get_ident())
    with _LOCK_GUARD:
        if _LOCK_HANDLE is None or _LOCK_OWNER != ident:
            raise RuntimeError("LOCK_NOT_OWNED")
        _LOCK_DEPTH -= 1
        if _LOCK_DEPTH > 0:
            return
        try:
            _LOCK_HANDLE.seek(0)
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(_LOCK_HANDLE.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl
                fcntl.flock(_LOCK_HANDLE.fileno(), fcntl.LOCK_UN)
        finally:
            try:
                _LOCK_HANDLE.close()
            finally:
                _LOCK_HANDLE = None
                _LOCK_OWNER = None
                _LOCK_DEPTH = 0


def mutation_guard(function):
    @functools.wraps(function)
    def wrapped(*args, **kwargs):
        execute = kwargs.get("execute", True)
        if not isinstance(execute, bool):
            return {"ok": False, "error": "INVALID_EXECUTE_FLAG"}
        if not execute:
            return {
                "ok": True,
                "dry_run": True,
                "validated": False,
                "action": function.__name__,
                "note": "No provider reads or writes performed",
            }
        agent_name = str(kwargs.get("agent_name") or function.__name__)
        if not acquire_lock(agent_name=agent_name):
            return {"ok": False, "reason": "LOCK_HELD_BY_PEER_AGENT"}
        try:
            return function(*args, **kwargs)
        except Exception:
            logger.error("MUTATION_FAILED:%s", function.__name__)
            return {"ok": False, "error": "ACTION_FAILED", "requires_operator_review": True}
        finally:
            release_lock()
    return wrapped


def log_action(record: Dict[str, Any]) -> None:
    record["ts_utc"] = _now_iso()
    record["ts_edt"] = _now_edt()
    record["host"] = "poet_windows_pc" if IS_WINDOWS else "grok_linux_box"
    try:
        with open(AUDIT_LOG_FILE, "a", encoding="utf-8") as f:
            f.write(json.dumps(record) + "\n")
        if (
            record.get("venue") == "KUCOIN"
            and record.get("execute", True) is not False
            and os.path.abspath(AUDIT_LOG_FILE) == os.path.abspath(os.path.join(STATE_DIR, "shared_desk_actions.jsonl"))
        ):
            journal_entry = {
                "logged_at_edt": record["ts_edt"],
                "ts": record["ts_utc"],
                "event": f"SHARED_DESK_ENGINE_{record.get('action', 'KUCOIN_ACTION')}",
                "authorized_engine": "shared_desk_cli_v5_2",
                **record,
            }
            for jpath in (
                "/cursor/stores/store-a70dc5ba-9b55-40f1-acf2-47f0bfbddd3f/trade_journal.jsonl",
                "/home/box/desk/kucoin/trade_journal.jsonl",
            ):
                if os.path.exists(os.path.dirname(jpath)):
                    try:
                        with open(jpath, "a", encoding="utf-8") as jf:
                            jf.write(json.dumps(journal_entry) + "\n")
                    except Exception:
                        pass
    except Exception:
        logger.error("OPERATION_FAILED")


def fetch_json_safe(url: str, timeout: float = 4.5, method: str = "GET", payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    try:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"Accept": "application/json", "User-Agent": "SharedDeskCLI/5.0"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as r:
            res = json.loads(r.read().decode("utf-8"))
            return res if isinstance(res, dict) else {"data": res}
    except Exception as e:
        return {"error": str(e)}


def load_local_or_remote_json(local_paths: List[str], remote_url: str, max_age_sec: float = 300.0) -> Dict[str, Any]:
    now = time.time()
    for p in local_paths:
        if os.path.exists(p):
            try:
                age = now - os.path.getmtime(p)
                if age < -5 or age > max_age_sec:
                    continue
                with open(p, "r", encoding="utf-8") as f:
                    d = json.load(f)
                if isinstance(d, dict) and d and not d.get("error") and d.get("ok") is not False:
                    return d
            except Exception:
                logger.error("LOCAL_JSON_READ_FAILED")
    rem = fetch_json_safe(remote_url, timeout=4.5)
    if isinstance(rem, dict) and not rem.get("error") and rem.get("ok") is not False and rem:
        return rem
    raise RuntimeError("FRESH_SOURCE_UNAVAILABLE")


def _load_aux_json_graceful(local_paths: List[str], remote_url: str, max_age_sec: float = 900.0) -> Dict[str, Any]:
    try:
        return load_local_or_remote_json(local_paths, remote_url, max_age_sec=max_age_sec)
    except Exception:
        return {}


def get_propr_live_snapshot(pr: Optional[ProprClient] = None) -> Dict[str, Any]:
    if pr is None:
        pr = ProprClient()
        pr.setup(PROPR_ACCOUNT_ID)
    acct = pr.get_account()
    raw_positions = pr.get_open_positions()
    raw_orders = (pr.get_orders(status="pending", limit=100) or []) + (pr.get_orders(status="open", limit=100) or [])
    seen_oids = set()
    dedup_orders = []
    for o in raw_orders:
        oid = o.get("orderId")
        if oid and oid not in seen_oids:
            seen_oids.add(oid)
            dedup_orders.append(o)

    bal = float(acct.get("balance") or 0.0)
    upnl = float(acct.get("totalUnrealizedPnl") or 0.0)
    iso_margin = float(acct.get("isolatedPositionMargin") or 0.0)
    # Note: Propr balance field is settled USDC cash; marginBalance or balance + uPnL reflects live equity
    eq_raw = acct.get("equity") or acct.get("marginBalance")
    equity = round(float(eq_raw) if eq_raw is not None else (bal + upnl), 2)
    avail = round(float(acct.get("availableBalance") or bal), 2)
    room = round(equity - PROPR_STATIC_FLOOR, 2)

    legs = []
    total_open_risk = 0.0
    naked_count = 0

    for pos in raw_positions:
        asset = str(pos.get("asset") or pos.get("base") or "").upper()
        pos_id = pos.get("positionId")
        side = str(pos.get("positionSide") or "").upper()
        qty = abs(float(pos.get("quantity") or 0.0))
        if qty <= 0:
            continue
        entry_px = float(pos.get("entryPrice") or 0.0)
        mark_px = float(pos.get("markPrice") or entry_px)
        notional = round(qty * mark_px, 2)
        pos_upnl = round(
            (mark_px - entry_px) * qty if side == "LONG" else (entry_px - mark_px) * qty,
            2,
        )

        pos_stops = [
            o for o in dedup_orders
            if (o.get("positionId") == pos_id or str(o.get("asset") or o.get("base") or "").upper() == asset)
            and o.get("type") in ("stop_market", "stop_limit")
        ]
        pos_tps = [
            o for o in dedup_orders
            if (o.get("positionId") == pos_id or str(o.get("asset") or o.get("base") or "").upper() == asset)
            and o.get("type") in ("take_profit_market", "take_profit_limit", "limit")
            and (o.get("reduceOnly") or o.get("type") in ("take_profit_market", "take_profit_limit"))
        ]

        sl_ord = pos_stops[0] if pos_stops else None
        tp_ord = pos_tps[0] if pos_tps else None
        sl_px = float(sl_ord.get("triggerPrice") or sl_ord.get("price") or 0.0) if sl_ord else None
        tp_px = float(tp_ord.get("triggerPrice") or tp_ord.get("price") or 0.0) if tp_ord else None

        if sl_px and sl_px > 0:
            per_unit_loss = max(0.0, (entry_px - sl_px) if side == "LONG" else (sl_px - entry_px))
            risk_usd = round(qty * per_unit_loss + notional * 0.0011, 2)
        else:
            naked_count += 1
            risk_usd = round(notional * 0.025, 2)

        total_open_risk = round(total_open_risk + risk_usd, 2)
        init_stop_dist = abs(entry_px - sl_px) if (sl_px and sl_px > 0) else (entry_px * 0.02)
        fav_move = (mark_px - entry_px) if side == "LONG" else (entry_px - mark_px)
        cur_r = round(fav_move / init_stop_dist, 3) if init_stop_dist > 0 else 0.0

        legs.append({
            "position_id": pos_id,
            "asset": asset,
            "side": side,
            "quantity": qty,
            "entry_price": entry_px,
            "mark_price": mark_px,
            "notional_usd": notional,
            "unrealized_pnl_usd": pos_upnl,
            "current_r": cur_r,
            "sl_price": sl_px,
            "tp_price": tp_px,
            "sl_order_id": sl_ord.get("orderId") if sl_ord else None,
            "tp_order_id": tp_ord.get("orderId") if tp_ord else None,
            "open_risk_usd": risk_usd,
            "protected": bool(sl_px and sl_px > 0),
        })

    return {
        "account_id": PROPR_ACCOUNT_ID,
        "equity_usd": equity,
        "balance_usd": round(bal, 2),
        "available_balance_usd": avail,
        "static_floor_usd": PROPR_STATIC_FLOOR,
        "pass_target_usd": PROPR_PASS_TARGET,
        "floor_buffer_usd": room,
        "distance_to_pass_usd": round(PROPR_PASS_TARGET - equity, 2),
        "open_positions_count": len(legs),
        "used_slots": len(legs),
        "max_slots": MAX_PROPR_SLOTS,
        "open_risk_usd": total_open_risk,
        "naked_positions_count": naked_count,
        "positions": legs,
        "raw_orders_count": len(dedup_orders),
    }


def get_kucoin_live_snapshot(kc: Optional[KuCoinClient] = None) -> Dict[str, Any]:
    if kc is None:
        kc = KuCoinClient()
    acct = kc.get_futures_overview("USDT")
    equity = round(float(acct.get("accountEquity") or 0.0), 2)
    avail = round(float(acct.get("availableBalance") or 0.0), 2)
    upnl = round(float(acct.get("unrealisedPNL") or 0.0), 2)

    contracts_raw = kc.request("https://api-futures.kucoin.com", "GET", "/api/v1/contracts/active", auth=False).get("data") or []
    c_map = {c["symbol"]: c for c in contracts_raw if isinstance(c, dict) and "symbol" in c}

    raw_positions = [p for p in kc.get_futures_positions() if abs(float(p.get("currentQty") or 0)) > 0]
    active_orders = kc.get_futures_active_orders()
    stop_orders = kc.get_futures_stop_orders()

    working_entries = [o for o in active_orders if not (o.get("reduceOnly") or o.get("reduce_only"))]
    working_tps = [o for o in active_orders if (o.get("reduceOnly") or o.get("reduce_only"))]

    legs = []
    total_open_risk = 0.0
    naked_count = 0

    for p in raw_positions:
        sym = p["symbol"]
        cs = c_map.get(sym) or {}
        mult = float(p.get("multiplier") or cs.get("multiplier") or 0.001)
        tick = float(cs.get("tickSize") or 0.0001)
        qty_signed = int(p.get("currentQty") or 0)
        lots = abs(qty_signed)
        side = "LONG" if qty_signed > 0 else "SHORT"
        entry_px = float(p.get("avgEntryPrice") or 0.0)
        mark_px = float(p.get("markPrice") or cs.get("markPrice") or entry_px)
        notional = round(lots * mult * mark_px, 2)
        pos_upnl = round(float(p.get("unrealisedPnl") or 0.0), 2)

        sym_stops = [s for s in stop_orders if s.get("symbol") == sym]
        sym_tps = [o for o in working_tps if o.get("symbol") == sym]

        sl_ord = sym_stops[0] if sym_stops else None
        tp_ord = sym_tps[0] if sym_tps else None
        sl_px = float(sl_ord.get("stopPrice") or 0.0) if sl_ord else None
        tp_px = float(tp_ord.get("price") or 0.0) if tp_ord else None

        if sl_px and sl_px > 0:
            per_unit_loss = max(0.0, (entry_px - sl_px) if side == "LONG" else (sl_px - entry_px))
            risk_usd = round(lots * mult * per_unit_loss + notional * 0.0008, 2)
        else:
            naked_count += 1
            risk_usd = round(notional * 0.025, 2)

        total_open_risk = round(total_open_risk + risk_usd, 2)
        init_stop_dist = abs(entry_px - sl_px) if (sl_px and sl_px > 0) else (entry_px * 0.02)
        fav_move = (mark_px - entry_px) if side == "LONG" else (entry_px - mark_px)
        cur_r = round(fav_move / init_stop_dist, 3) if init_stop_dist > 0 else 0.0

        legs.append({
            "symbol": sym,
            "asset": normalize_asset(sym),
            "side": side,
            "lots": lots,
            "multiplier": mult,
            "tick_size": tick,
            "entry_price": entry_px,
            "mark_price": mark_px,
            "notional_usd": notional,
            "unrealized_pnl_usd": pos_upnl,
            "current_r": cur_r,
            "mp_stop_price": sl_px,
            "maker_tp_price": tp_px,
            "mp_stop_order_id": sl_ord.get("id") if sl_ord else None,
            "maker_tp_order_id": tp_ord.get("id") if tp_ord else None,
            "open_risk_usd": risk_usd,
            "protected": bool(sl_px and sl_px > 0),
        })

    staged_entries = []
    for o in working_entries:
        sym = o.get("symbol")
        cs = c_map.get(sym) or {}
        mult = float(cs.get("multiplier") or 0.001)
        lots = int(o.get("size") or 0)
        px = float(o.get("price") or 0.0)
        side = "LONG" if str(o.get("side") or "").lower() == "buy" else "SHORT"
        sym_stops = [s for s in stop_orders if s.get("symbol") == sym]
        sl_px = float(sym_stops[0].get("stopPrice") or 0.0) if sym_stops else None
        staged_entries.append({
            "order_id": o.get("id"),
            "symbol": sym,
            "asset": normalize_asset(sym),
            "side": side,
            "lots": lots,
            "limit_price": px,
            "notional_usd": round(lots * mult * px, 2),
            "post_only": bool(o.get("postOnly")),
            "mp_stop_price": sl_px,
        })

    active_syms = {l["symbol"] for l in legs} | {e["symbol"] for e in staged_entries}
    return {
        "uid": KUCOIN_UID,
        "equity_usdt": equity,
        "available_balance_usdt": avail,
        "unrealized_pnl_usdt": upnl,
        "static_floor_usdt": KUCOIN_STATIC_FLOOR,
        "floor_buffer_usdt": round(equity - KUCOIN_STATIC_FLOOR, 2),
        "open_positions_count": len(legs),
        "working_entry_orders_count": len(staged_entries),
        "working_tp_orders_count": len(working_tps),
        "used_slots": len(active_syms),
        "max_slots": MAX_KUCOIN_SLOTS,
        "open_risk_usd": total_open_risk,
        "naked_positions_count": naked_count,
        "positions": legs,
        "working_entries": staged_entries,
        "_contracts_map": c_map,
        "_raw_stop_orders": stop_orders,
    }


def get_full_status(include_private_maps: bool = False) -> Dict[str, Any]:
    t0 = time.time()
    pr = ProprClient()
    pr.setup(PROPR_ACCOUNT_ID)
    kc = KuCoinClient()

    propr_snap = get_propr_live_snapshot(pr)
    kucoin_snap = get_kucoin_live_snapshot(kc)
    if not include_private_maps:
        kucoin_snap.pop("_contracts_map", None)
        kucoin_snap.pop("_raw_stop_orders", None)

    genome_doc = _load_aux_json_graceful(
        [os.path.join(STATE_DIR, "evolved_genome.json"), "/home/box/desk/evolved_genome.json"],
        f"{KUCOIN_WORKER_URL}/evolved-genome",
    )
    active_genome = genome_doc.get("active_genome") or DEFAULT_GENOME

    poet_tele = _load_aux_json_graceful(
        [POET_TELEMETRY_FILE, "/home/box/desk/latest_poet_max_telemetry.json"],
        f"{KUCOIN_WORKER_URL}/poet-node",
    )
    auto_exec_doc = _load_aux_json_graceful(
        [os.path.join(STATE_DIR, "latest_autonomous_execution.json"), "/home/box/desk/latest_autonomous_execution.json"],
        f"{KUCOIN_WORKER_URL}/auto-exec",
    )

    candidates = (auto_exec_doc.get("candidate_gate_decisions") or [])[:8]
    exec_ov = poet_tele.get("execution_overrides") or {}

    total_eq_usd = round(propr_snap["equity_usd"] + kucoin_snap["equity_usdt"] + 100.0, 2)
    total_risk_usd = round(propr_snap["open_risk_usd"] + kucoin_snap["open_risk_usd"], 2)

    return {
        "ok": True,
        "as_of_utc": _now_iso(),
        "as_of_edt": _now_edt(),
        "host": "poet_windows_pc" if IS_WINDOWS else "grok_linux_box",
        "elapsed_ms": round((time.time() - t0) * 1000.0, 1),
        "summary": {
            "total_controlled_equity_usd": total_eq_usd,
            "total_open_risk_usd": total_risk_usd,
            "total_naked_positions": propr_snap["naked_positions_count"] + kucoin_snap["naked_positions_count"],
            "propr_equity_usd": propr_snap["equity_usd"],
            "propr_floor_buffer_usd": propr_snap["floor_buffer_usd"],
            "propr_slots": f"{propr_snap['used_slots']}/{propr_snap['max_slots']}",
            "kucoin_equity_usdt": kucoin_snap["equity_usdt"],
            "kucoin_floor_buffer_usdt": kucoin_snap["floor_buffer_usdt"],
            "kucoin_slots": f"{kucoin_snap['used_slots']}/{kucoin_snap['max_slots']}",
        },
        "propr": propr_snap,
        "kucoin": kucoin_snap,
        "evolved_champion_genome": active_genome,
        "poet_microstructure_signals": {
            "engine_version": poet_tele.get("engine_version", "v5.0"),
            "vpin_toxicity_vetoes": exec_ov.get("vpin_toxicity_veto_symbols") or [],
            "hl_lead_lag_boosts": exec_ov.get("hl_lead_lag_boost_symbols") or [],
            "calibrated_maker_offsets_bps": exec_ov.get("calibrated_maker_offsets_bps") or {},
        },
        "top_6axiom_candidates": candidates,
        "cli_commands": {
            "status": r"python C:\Users\Simpl\grok-propr-bot\shared_desk_cli.py status",
            "ratchet": r"python C:\Users\Simpl\grok-propr-bot\shared_desk_cli.py ratchet",
            "auto_trade": r"python C:\Users\Simpl\grok-propr-bot\shared_desk_cli.py auto-trade",
            "trade_propr": r"python C:\Users\Simpl\grok-propr-bot\shared_desk_cli.py trade-propr --asset <ASSET> --side <LONG|SHORT> --notional 310 --sl <SL_PX> --tp <TP_PX>",
            "trade_kucoin": r"python C:\Users\Simpl\grok-propr-bot\shared_desk_cli.py trade-kucoin --symbol <SYM> --side <LONG|SHORT> --lots <LOTS> --price <LIMIT_PX> --sl <MP_STOP_PX> --tp <TP_PX>",
            "close": r"python C:\Users\Simpl\grok-propr-bot\shared_desk_cli.py close --venue <propr|kucoin> --symbol <SYM>",
        },
    }


@mutation_guard
def run_ratchet(execute: bool = True, agent_name: str = "shared_cli", include_propr: bool = True) -> Dict[str, Any]:
    """
    Audits and ratchets stops + stages missing Maker TPs + cleans orphan stops
    across BOTH Propr and KuCoin Futures directly.
    """
    t0 = time.time()
    if agent_name in ("poet_v5_watchdog", "poet_v5_failover", "grok_autonomous") or os.environ.get("GROK_DISABLE_PROPR") == "1":
        include_propr = False
    if True:
        pr = None
        if include_propr:
            pr = ProprClient()
            pr.setup(PROPR_ACCOUNT_ID)
        kc = KuCoinClient()

        genome_doc = _load_aux_json_graceful(
            [os.path.join(STATE_DIR, "evolved_genome.json"), "/home/box/desk/evolved_genome.json"],
            f"{KUCOIN_WORKER_URL}/evolved-genome",
        )
        genome = genome_doc.get("active_genome") or DEFAULT_GENOME
        trail_act_r = float(genome.get("trail_activation_r") or 0.75)
        tp_r_mult = float(genome.get("tp_r_mult") or 1.85)

        propr_snap = get_propr_live_snapshot(pr) if (include_propr and pr is not None) else {"positions": [], "equity_usd": 0.0, "floor_buffer_usd": 0.0, "open_risk_usd": 0.0, "used_slots": 0, "max_slots": MAX_PROPR_SLOTS, "naked_positions_count": 0}
        kucoin_snap = get_kucoin_live_snapshot(kc)
        c_map = kucoin_snap.pop("_contracts_map", {})
        raw_stops = kucoin_snap.pop("_raw_stop_orders", [])

        propr_actions = []
        kucoin_actions = []

        # ---------------------------------------------------------------------
        # 1. PROPR POSITION DEFENSE & RATCHET
        # ---------------------------------------------------------------------
        for leg in propr_snap["positions"]:
            asset = leg["asset"]
            pos_id = leg["position_id"]
            side = leg["side"]
            qty_str = _fmt_hl_qty(asset, leg["quantity"])
            entry_px = leg["entry_price"]
            mark_px = leg["mark_price"]
            cur_sl = leg["sl_price"]
            cur_tp = leg["tp_price"]

            close_side = "sell" if side == "LONG" else "buy"
            close_pos_side = "short" if side == "LONG" else "long"

            # 1a. Emergency stop if naked
            if not cur_sl or cur_sl <= 0:
                emerg_sl = entry_px * 0.98 if side == "LONG" else entry_px * 1.02
                sl_str = _fmt_hl_px(asset, emerg_sl)
                if execute:
                    r_sl = pr.create_order(
                        side=close_side,
                        position_side=close_pos_side,
                        order_type="stop_market",
                        asset=asset,
                        base=asset,
                        quote="USDC",
                        quantity=qty_str,
                        trigger_price=sl_str,
                        reduce_only=True,
                        position_id=pos_id,
                    )
                else:
                    r_sl = [{"dry_run": True}]
                cur_sl = float(sl_str)
                act = {"venue": "PROPR", "asset": asset, "action": "ATTACH_EMERGENCY_STOP", "sl_price": cur_sl, "execute": execute}
                propr_actions.append(act)
                log_action(act)

            # 1b. Stage TP if missing
            if not cur_tp or cur_tp <= 0:
                stop_dist = abs(entry_px - cur_sl) if cur_sl else entry_px * 0.02
                tp_target = (entry_px + tp_r_mult * stop_dist) if side == "LONG" else (entry_px - tp_r_mult * stop_dist)
                tp_str = _fmt_hl_px(asset, tp_target)
                if execute:
                    pr.create_order(
                        side=close_side,
                        position_side=close_pos_side,
                        order_type="take_profit_market",
                        asset=asset,
                        base=asset,
                        quote="USDC",
                        quantity=qty_str,
                        trigger_price=tp_str,
                        reduce_only=True,
                        position_id=pos_id,
                    )
                act = {"venue": "PROPR", "asset": asset, "action": "STAGE_MISSING_TP", "tp_price": float(tp_str), "execute": execute}
                propr_actions.append(act)
                log_action(act)

            # 1c. Ratchet stop at +0.75R -> +0.08R breakeven and +1.20R -> +0.60R trail
            init_stop_dist = abs(entry_px - cur_sl) if cur_sl else entry_px * 0.02
            fav_move = (mark_px - entry_px) if side == "LONG" else (entry_px - mark_px)
            cur_r = fav_move / init_stop_dist if init_stop_dist > 0 else 0.0

            new_sl = None
            reason = None
            if cur_r >= 1.20:
                cand = (entry_px + 0.60 * init_stop_dist) if side == "LONG" else (entry_px - 0.60 * init_stop_dist)
                if (side == "LONG" and cand > cur_sl * 1.001) or (side == "SHORT" and cand < cur_sl * 0.999):
                    new_sl = cand
                    reason = f"TRAIL_0.60R_AT_{cur_r:+.2f}R"
            elif cur_r >= trail_act_r:
                cand = (entry_px + 0.08 * init_stop_dist) if side == "LONG" else (entry_px - 0.08 * init_stop_dist)
                if (side == "LONG" and cand > cur_sl * 1.001) or (side == "SHORT" and cand < cur_sl * 0.999):
                    new_sl = cand
                    reason = f"BREAKEVEN_LOCK_AT_{cur_r:+.2f}R"

            if new_sl is not None:
                sl_str = _fmt_hl_px(asset, new_sl)
                if execute:
                    pr.create_order(
                        side=close_side,
                        position_side=close_pos_side,
                        order_type="stop_market",
                        asset=asset,
                        base=asset,
                        quote="USDC",
                        quantity=qty_str,
                        trigger_price=sl_str,
                        reduce_only=True,
                        position_id=pos_id,
                    )
                    if leg.get("sl_order_id"):
                        try:
                            pr.cancel_order(leg["sl_order_id"])
                        except Exception:
                            pass
                act = {
                    "venue": "PROPR",
                    "asset": asset,
                    "action": reason,
                    "old_sl": cur_sl,
                    "new_sl": float(sl_str),
                    "execute": execute,
                }
                propr_actions.append(act)
                log_action(act)

        # ---------------------------------------------------------------------
        # 2. KUCOIN FUTURES ORPHAN CLEANUP, POSITION DEFENSE & RATCHET
        # ---------------------------------------------------------------------
        protected_syms = {l["symbol"] for l in kucoin_snap["positions"]} | {
            e["symbol"] for e in kucoin_snap["working_entries"]
        }
        for s in raw_stops:
            sym = s.get("symbol")
            oid = s.get("id")
            if sym and sym not in protected_syms and oid:
                if execute:
                    try:
                        kc.cancel_futures_order(oid)
                    except Exception:
                        logger.error("OPERATION_FAILED")
                act = {"venue": "KUCOIN", "symbol": sym, "action": "CANCEL_ORPHAN_STOP", "order_id": oid, "execute": execute}
                kucoin_actions.append(act)
                log_action(act)

        for leg in kucoin_snap["positions"]:
            sym = leg["symbol"]
            side = leg["side"]
            lots = leg["lots"]
            tick = leg["tick_size"]
            entry_px = leg["entry_price"]
            mark_px = leg["mark_price"]
            cur_sl = leg["mp_stop_price"]
            cur_tp = leg["maker_tp_price"]

            stop_side = "sell" if side == "LONG" else "buy"
            stop_dir = "down" if side == "LONG" else "up"

            # 2a. Emergency MP stop if naked
            if not cur_sl or cur_sl <= 0:
                emerg_sl = entry_px * 0.98 if side == "LONG" else entry_px * 1.02
                sl_str = _fmt_kc_px(tick, emerg_sl)
                if execute:
                    kc.place_futures_stop_order(
                        symbol=sym,
                        side=stop_side,
                        size=lots,
                        stop_price=sl_str,
                        stop_dir=stop_dir,
                        order_type="market",
                        stop_price_type="MP",
                        reduce_only=True,
                    )
                cur_sl = float(sl_str)
                act = {"venue": "KUCOIN", "symbol": sym, "action": "ATTACH_EMERGENCY_MP_STOP", "sl_price": cur_sl, "execute": execute}
                kucoin_actions.append(act)
                log_action(act)

            # 2b. Stage resting postOnly Maker TP if missing
            if not cur_tp or cur_tp <= 0:
                stop_dist = abs(entry_px - cur_sl) if (cur_sl and cur_sl > 0) else (entry_px * 0.015)
                raw_tp = (entry_px + tp_r_mult * stop_dist) if side == "LONG" else (entry_px - tp_r_mult * stop_dist)
                # Ensure postOnly safety relative to current mark price
                if side == "LONG" and raw_tp <= mark_px:
                    raw_tp = mark_px + 2 * tick
                elif side == "SHORT" and raw_tp >= mark_px:
                    raw_tp = mark_px - 2 * tick
                tp_str = _fmt_kc_px(tick, raw_tp)
                tp_oid = None
                if execute:
                    r_tp = kc.place_futures_order(
                        symbol=sym,
                        side=stop_side,
                        size=lots,
                        order_type="limit",
                        price=tp_str,
                        post_only=True,
                        reduce_only=True,
                    )
                    tp_oid = (r_tp.get("data") or {}).get("orderId")
                act = {
                    "venue": "KUCOIN",
                    "symbol": sym,
                    "action": "STAGE_MISSING_MAKER_TP",
                    "tp_price": float(tp_str),
                    "order_id": tp_oid,
                    "execute": execute,
                }
                kucoin_actions.append(act)
                log_action(act)

            # 2c. Ratchet MP stop at +0.85R -> +0.15R fee-plus-buffer lock and +1.25R -> +0.60R trail (with >=35 bps mark buffer)
            init_stop_dist = abs(entry_px - cur_sl) if (cur_sl and cur_sl > 0) else (entry_px * 0.0185)
            fav_move = (mark_px - entry_px) if side == "LONG" else (entry_px - mark_px)
            cur_r = fav_move / init_stop_dist if init_stop_dist > 0 else 0.0
            kc_be_trigger_r = max(trail_act_r, 0.85)
            min_mark_buf = max(2 * tick, mark_px * 0.0035)

            new_sl = None
            reason = None
            if cur_r >= 1.25:
                cand = (entry_px + 0.60 * init_stop_dist) if side == "LONG" else (entry_px - 0.60 * init_stop_dist)
                if (side == "LONG" and cand > cur_sl + tick) or (side == "SHORT" and cand < cur_sl - tick):
                    new_sl = cand
                    reason = f"KUCOIN_TRAIL_0.60R_AT_{cur_r:+.2f}R"
            elif cur_r >= kc_be_trigger_r:
                cand = (entry_px + 0.15 * init_stop_dist) if side == "LONG" else (entry_px - 0.15 * init_stop_dist)
                if (side == "LONG" and cand > cur_sl + tick) or (side == "SHORT" and cand < cur_sl - tick):
                    new_sl = cand
                    reason = f"KUCOIN_BREAKEVEN_LOCK_AT_{cur_r:+.2f}R"

            if new_sl is not None:
                # Ensure stop has >= 35 bps buffer from mark_px so 5m noise never prematurely stops out
                if (side == "LONG" and new_sl < mark_px - min_mark_buf) or (side == "SHORT" and new_sl > mark_px + min_mark_buf):
                    sl_str = _fmt_kc_px(tick, new_sl)
                    if execute:
                        r_new = kc.place_futures_stop_order(
                            symbol=sym,
                            side=stop_side,
                            size=lots,
                            stop_price=sl_str,
                            stop_dir=stop_dir,
                            order_type="market",
                            stop_price_type="MP",
                            reduce_only=True,
                        )
                        if (r_new.get("data") or {}).get("orderId") and leg.get("mp_stop_order_id"):
                            try:
                                kc.cancel_futures_order(leg["mp_stop_order_id"])
                            except Exception:
                                logger.error("OPERATION_FAILED")
                    act = {
                        "venue": "KUCOIN",
                        "symbol": sym,
                        "action": reason,
                        "old_sl": cur_sl,
                        "new_sl": float(sl_str),
                        "execute": execute,
                    }
                    kucoin_actions.append(act)
                    log_action(act)

        if execute and (propr_actions or kucoin_actions):
            try:
                kc.sync_worker_state()
            except Exception:
                logger.error("OPERATION_FAILED")

        return {
            "ok": True,
            "as_of_utc": _now_iso(),
            "as_of_edt": _now_edt(),
            "host": "poet_windows_pc" if IS_WINDOWS else "grok_linux_box",
            "execute": execute,
            "elapsed_ms": round((time.time() - t0) * 1000.0, 1),
            "propr_ratchet_actions": propr_actions,
            "kucoin_ratchet_actions": kucoin_actions,
            "propr_equity_usd": propr_snap["equity_usd"],
            "kucoin_equity_usdt": kucoin_snap["equity_usdt"],
        }


@mutation_guard
def place_propr_trade_safe(
    asset: str,
    side: str,
    notional_usd: float = DEFAULT_PROPR_NOTIONAL_USD,
    sl_price: Optional[float] = None,
    tp_price: Optional[float] = None,
    stop_pct: float = 0.0185,
    tp_r_mult: float = 1.85,
    execute: bool = True,
) -> Dict[str, Any]:
    asset = normalize_asset(asset)
    side = side.upper().strip()
    if side not in ("LONG", "SHORT"):
        return {"ok": False, "error": "side must be LONG or SHORT"}
    if notional_usd > MAX_PROPR_NOTIONAL_USD:
        return {"ok": False, "error": f"notional_usd {notional_usd} exceeds challenge cap ${MAX_PROPR_NOTIONAL_USD}"}

    pr = ProprClient()
    pr.setup(PROPR_ACCOUNT_ID)
    snap = get_propr_live_snapshot(pr)

    if snap["used_slots"] >= MAX_PROPR_SLOTS:
        return {"ok": False, "error": f"Propr slots full ({snap['used_slots']}/{MAX_PROPR_SLOTS})"}
    if snap["equity_usd"] < PROPR_MIN_EQUITY_FOR_AUTO:
        return {"ok": False, "error": f"Propr equity ${snap['equity_usd']} below minimum auto threshold ${PROPR_MIN_EQUITY_FOR_AUTO}"}
    if any(p["asset"] == asset for p in snap["positions"]):
        return {"ok": False, "error": f"Asset {asset} already open on Propr"}

    # Fetch live Hyperliquid mid price if needed
    hl_mids = fetch_json_safe("https://api.hyperliquid.xyz/info", timeout=4.0, method="POST", payload={"type": "allMids"})
    ref_px = float(hl_mids.get(asset) or 0.0)
    if ref_px <= 0 and sl_price:
        ref_px = sl_price / (1.0 - stop_pct if side == "LONG" else 1.0 + stop_pct)
    if ref_px <= 0:
        return {"ok": False, "error": f"Unable to determine reference mark price for {asset}"}

    qty_str = _fmt_hl_qty(asset, notional_usd / ref_px)
    qty_val = float(qty_str)
    if qty_val * ref_px < 20.0:
        return {"ok": False, "error": f"Computed notional ${qty_val * ref_px:.2f} too small"}

    if not sl_price:
        sl_price = ref_px * (1.0 - stop_pct) if side == "LONG" else ref_px * (1.0 + stop_pct)
    if not tp_price:
        dist = abs(ref_px - sl_price)
        tp_price = (ref_px + tp_r_mult * dist) if side == "LONG" else (ref_px - tp_r_mult * dist)

    risk_usd = round(qty_val * abs(ref_px - sl_price) + (qty_val * ref_px) * 0.0011, 2)
    if snap["open_risk_usd"] + risk_usd > snap["floor_buffer_usd"] - 35.0:
        return {"ok": False, "error": f"Risk ${risk_usd} exceeds safe floor room (${snap['floor_buffer_usd']})"}

    if not execute:
        return {
            "ok": True,
            "dry_run": True,
            "venue": "PROPR",
            "asset": asset,
            "side": side,
            "quantity": qty_val,
            "ref_price": ref_px,
            "sl_price": float(_fmt_hl_px(asset, sl_price)),
            "tp_price": float(_fmt_hl_px(asset, tp_price)),
            "risk_1r_usd": risk_usd,
        }

    try:
        pr.set_leverage(asset, 2, margin_mode="isolated")
    except Exception:
        logger.error("LEVERAGE_CONFIGURATION_FAILED")
        return {"ok": False, "error": "LEVERAGE_CONFIGURATION_FAILED"}

    order_side = "buy" if side == "LONG" else "sell"
    pos_side = "long" if side == "LONG" else "short"
    close_side = "sell" if side == "LONG" else "buy"
    close_pos_side = "short" if side == "LONG" else "long"

    r_entry = pr.create_order(
        side=order_side,
        position_side=pos_side,
        order_type="market",
        asset=asset,
        base=asset,
        quote="USDC",
        quantity=qty_str,
        time_in_force="IOC",
        reduce_only=False,
    )
    entry_ord = r_entry[0] if isinstance(r_entry, list) and r_entry else {}
    pos_id = entry_ord.get("positionId")
    fill_px = float(entry_ord.get("averageFillPrice") or ref_px)

    if not pos_id:
        open_p = pr.get_open_positions(base=asset)
        if open_p:
            pos_id = open_p[0].get("positionId")

    sl_final = _fmt_hl_px(asset, sl_price)
    tp_final = _fmt_hl_px(asset, tp_price)
    sl_oid, tp_oid = None, None

    if pos_id:
        r_sl = pr.create_order(
            side=close_side,
            position_side=close_pos_side,
            order_type="stop_market",
            asset=asset,
            base=asset,
            quote="USDC",
            quantity=qty_str,
            trigger_price=sl_final,
            reduce_only=True,
            position_id=pos_id,
        )
        sl_oid = (r_sl[0] if isinstance(r_sl, list) and r_sl else {}).get("orderId")

        r_tp = pr.create_order(
            side=close_side,
            position_side=close_pos_side,
            order_type="take_profit_market",
            asset=asset,
            base=asset,
            quote="USDC",
            quantity=qty_str,
            trigger_price=tp_final,
            reduce_only=True,
            position_id=pos_id,
        )
        tp_oid = (r_tp[0] if isinstance(r_tp, list) and r_tp else {}).get("orderId")

    protected = bool(pos_id and sl_oid and tp_oid)
    res = {
        "ok": protected,
        "requires_operator_review": not protected,
        "venue": "PROPR",
        "asset": asset,
        "side": side,
        "quantity": qty_val,
        "fill_price": fill_px,
        "notional_usd": round(qty_val * fill_px, 2),
        "sl_price": float(sl_final),
        "tp_price": float(tp_final),
        "risk_1r_usd": risk_usd,
        "position_id": pos_id,
        "entry_order_id": entry_ord.get("orderId"),
        "sl_order_id": sl_oid,
        "tp_order_id": tp_oid,
    }
    log_action({"action": "TRADE_PROPR", **res})
    return res


@mutation_guard
def place_kucoin_trade_safe(
    symbol: str,
    side: str,
    lots: int,
    limit_price: float,
    mp_stop_price: float,
    tp_price: Optional[float] = None,
    execute: bool = True,
) -> Dict[str, Any]:
    symbol = symbol.upper().strip()
    if not symbol.endswith("USDTM"):
        symbol = f"{symbol}USDTM"
    side = side.upper().strip()
    if side not in ("LONG", "SHORT"):
        return {"ok": False, "error": "side must be LONG or SHORT"}

    kc = KuCoinClient()
    snap = get_kucoin_live_snapshot(kc)
    c_map = snap.get("_contracts_map") or {}
    if snap["used_slots"] >= MAX_KUCOIN_SLOTS:
        return {"ok": False, "error": f"KuCoin slots full ({snap['used_slots']}/{MAX_KUCOIN_SLOTS})"}
    if snap["equity_usdt"] < KUCOIN_STATIC_FLOOR:
        return {"ok": False, "error": f"KuCoin equity ${snap['equity_usdt']} below floor ${KUCOIN_STATIC_FLOOR}"}

    cs = c_map.get(symbol) or kc.get_futures_contract(symbol)
    tick = float(cs.get("tickSize") or 0.0001)
    mult = float(cs.get("multiplier") or 0.001)
    mark_px = float(cs.get("markPrice") or limit_price)

    orig_limit = float(limit_price)
    orig_stop = float(mp_stop_price)
    orig_valid_dir = (side == "LONG" and orig_stop < orig_limit) or (side == "SHORT" and orig_stop > orig_limit)
    orig_stop_pct = abs(orig_stop - orig_limit) / orig_limit if (orig_limit > 0 and orig_stop > 0 and orig_valid_dir) else 0.0

    if side == "SHORT" and limit_price <= mark_px:
        limit_price = mark_px + 2 * tick
    elif side == "LONG" and limit_price >= mark_px:
        limit_price = mark_px - 2 * tick

    # If entry limit was shifted by market drift (> 4 ticks) and original stop direction was valid, shift MP stop & TP to preserve >= 1.85% structural stop room
    if orig_valid_dir and abs(limit_price - orig_limit) > 4 * tick and limit_price > 0:
        eff_stop_pct = max(orig_stop_pct, 0.0185)
        mp_stop_price = limit_price * (1.0 + eff_stop_pct) if side == "SHORT" else limit_price * (1.0 - eff_stop_pct)
        if tp_price and tp_price > 0:
            tp_price = limit_price * (1.0 - 1.85 * eff_stop_pct) if side == "SHORT" else limit_price * (1.0 + 1.85 * eff_stop_pct)

    px_str = _fmt_kc_px(tick, limit_price)
    sl_str = _fmt_kc_px(tick, mp_stop_price)
    notional = round(lots * mult * float(px_str), 2)
    risk_usd = round(lots * mult * abs(float(px_str) - float(sl_str)) + notional * 0.0008, 2)
    numbers = (lots, float(px_str), float(sl_str), notional, risk_usd, snap["equity_usdt"], snap.get("open_risk_usd", float("nan")))
    if any(not math.isfinite(value) for value in numbers) or lots <= 0 or float(px_str) <= 0 or float(sl_str) <= 0:
        return {"ok": False, "error": "INVALID_RISK_INPUT"}
    if (side == "LONG" and float(sl_str) >= float(px_str)) or (side == "SHORT" and float(sl_str) <= float(px_str)):
        return {"ok": False, "error": "INVALID_STOP_DIRECTION"}
    if snap["open_risk_usd"] < 0 or risk_usd + snap["open_risk_usd"] > snap["equity_usdt"] - KUCOIN_STATIC_FLOOR:
        return {"ok": False, "error": "INSUFFICIENT_RISK_BUFFER"}

    if not execute:
        return {
            "ok": True,
            "dry_run": True,
            "venue": "KUCOIN",
            "symbol": symbol,
            "side": side,
            "lots": lots,
            "entry_limit_px": float(px_str),
            "mp_stop_px": float(sl_str),
            "notional_usd": notional,
            "risk_1r_usd": risk_usd,
        }

    order_side = "buy" if side == "LONG" else "sell"
    stop_side = "sell" if side == "LONG" else "buy"
    stop_dir = "down" if side == "LONG" else "up"

    r_entry = kc.place_futures_order(
        symbol=symbol,
        side=order_side,
        size=int(lots),
        order_type="limit",
        price=px_str,
        leverage="3",
        post_only=True,
        reduce_only=False,
        margin_mode="CROSS",
    )
    entry_oid = (r_entry.get("data") or {}).get("orderId")
    stop_oid = None
    if entry_oid:
        r_stop = kc.place_futures_stop_order(
            symbol=symbol,
            side=stop_side,
            size=int(lots),
            stop_price=sl_str,
            stop_dir=stop_dir,
            order_type="market",
            stop_price_type="MP",
            reduce_only=True,
            leverage="3",
            margin_mode="CROSS",
        )
        stop_oid = (r_stop.get("data") or {}).get("orderId")
        try:
            kc.sync_worker_state()
        except Exception:
            logger.error("OPERATION_FAILED")

    protected = bool(entry_oid and stop_oid)
    res = {
        "ok": protected,
        "requires_operator_review": not protected,
        "venue": "KUCOIN",
        "symbol": symbol,
        "side": side,
        "lots": int(lots),
        "entry_limit_px": float(px_str),
        "mp_stop_px": float(sl_str),
        "target_tp_px": tp_price,
        "notional_usd": notional,
        "risk_1r_usd": risk_usd,
        "entry_order_id": entry_oid,
        "mp_stop_order_id": stop_oid,
        "raw_response": None if entry_oid else r_entry,
    }
    log_action({"action": "TRADE_KUCOIN", **res})
    return res


@mutation_guard
def close_position_safe(venue: str, symbol: str, execute: bool = True) -> Dict[str, Any]:
    venue = venue.lower().strip()
    if venue == "propr":
        asset = normalize_asset(symbol)
        pr = ProprClient()
        pr.setup(PROPR_ACCOUNT_ID)
        res = pr.close_position(base=asset)
        cancelled = pr.cancel_all_orders(base=asset)
        out = {"ok": True, "venue": "PROPR", "asset": asset, "close_orders": res, "cancelled_orders": len(cancelled)}
        log_action({"action": "CLOSE_POSITION", **out})
        return out
    elif venue == "kucoin":
        sym = symbol.upper().strip()
        if not sym.endswith("USDTM"):
            sym = f"{sym}USDTM"
        kc = KuCoinClient()
        snap = get_kucoin_live_snapshot(kc)
        pos = [p for p in snap["positions"] if p["symbol"] == sym]
        close_resp = None
        if pos:
            leg = pos[0]
            close_side = "sell" if leg["side"] == "LONG" else "buy"
            close_resp = kc.place_futures_order(
                symbol=sym,
                side=close_side,
                size=leg["lots"],
                order_type="market",
                reduce_only=True,
            )
        # Cancel active + stop orders for sym
        for o in kc.get_futures_active_orders():
            if o.get("symbol") == sym and o.get("id"):
                kc.cancel_futures_order(o["id"])
        for s in kc.get_futures_stop_orders():
            if s.get("symbol") == sym and s.get("id"):
                kc.cancel_futures_order(s["id"])
        try:
            kc.sync_worker_state()
        except Exception:
            logger.error("OPERATION_FAILED")
        out = {"ok": True, "venue": "KUCOIN", "symbol": sym, "close_response": close_resp}
        log_action({"action": "CLOSE_POSITION", **out})
        return out
    return {"ok": False, "error": "venue must be 'propr' or 'kucoin'"}


@mutation_guard
def run_auto_trade(execute: bool = True, agent_name: str = "shared_cli", include_propr: bool = True) -> Dict[str, Any]:
    """
    Runs full stop-ratchet + missing TP staging first, then dispatches top
    6-Axiom + Evolved Genome + `poet` L2 VPIN/EOBI setups on Propr and KuCoin.
    """
    if agent_name in ("poet_v5_watchdog", "poet_v5_failover", "grok_autonomous") or os.environ.get("GROK_DISABLE_PROPR") == "1":
        include_propr = False
    ratchet_res = run_ratchet(execute=execute, agent_name=agent_name, include_propr=include_propr)
    if not ratchet_res.get("ok"):
        return ratchet_res

    pr = None
    if include_propr:
        pr = ProprClient()
        pr.setup(PROPR_ACCOUNT_ID)
    kc = KuCoinClient()

    propr_snap = get_propr_live_snapshot(pr) if (include_propr and pr is not None) else {"positions": [], "equity_usd": 0.0, "floor_buffer_usd": 0.0, "open_risk_usd": 0.0, "used_slots": MAX_PROPR_SLOTS, "max_slots": MAX_PROPR_SLOTS, "naked_positions_count": 0}
    kucoin_snap = get_kucoin_live_snapshot(kc)
    kucoin_snap.pop("_contracts_map", None)
    kucoin_snap.pop("_raw_stop_orders", None)

    genome_doc = load_local_or_remote_json(
        [os.path.join(STATE_DIR, "evolved_genome.json"), "/home/box/desk/evolved_genome.json"],
        f"{KUCOIN_WORKER_URL}/evolved-genome",
    )
    genome = genome_doc.get("active_genome") or DEFAULT_GENOME
    min_edge = float(genome.get("min_composite_edge") or 78.0)
    tp_r_mult = float(genome.get("tp_r_mult") or 1.85)

    auto_exec_doc = load_local_or_remote_json(
        [os.path.join(STATE_DIR, "latest_autonomous_execution.json"), "/home/box/desk/latest_autonomous_execution.json"],
        f"{KUCOIN_WORKER_URL}/auto-exec",
    )
    poet_tele = load_local_or_remote_json(
        [POET_TELEMETRY_FILE, "/home/box/desk/latest_poet_max_telemetry.json"],
        f"{KUCOIN_WORKER_URL}/poet-node",
    )
    exec_ov = poet_tele.get("execution_overrides") or {}
    vpin_veto_map = {
        v.get("kc_symbol"): v for v in (exec_ov.get("vpin_toxicity_veto_symbols") or []) if v.get("kc_symbol")
    }
    boost_map = {
        b.get("kc_symbol"): b for b in (exec_ov.get("hl_lead_lag_boost_symbols") or []) if b.get("kc_symbol")
    }

    if auto_exec_doc.get("auto_trade_disabled") or (agent_name == "poet_v5_failover" and os.environ.get("SHARED_DESK_ALLOW_AUTO_ENTRIES", "0") != "1"):
        candidates = []
    else:
        candidates = auto_exec_doc.get("candidate_gate_decisions") or []
    active_assets = (
        {p["asset"] for p in propr_snap["positions"]}
        | {p["asset"] for p in kucoin_snap["positions"]}
        | {e["asset"] for e in kucoin_snap["working_entries"]}
    )

    dispatched_propr = []
    dispatched_kucoin = []

    open_propr_slots = max(0, MAX_PROPR_SLOTS - propr_snap["used_slots"])
    open_kucoin_slots = max(0, MAX_KUCOIN_SLOTS - kucoin_snap["used_slots"])

    for cand in candidates:
        sym = cand.get("symbol")
        asset = normalize_asset(sym)
        side = str(cand.get("side") or "").upper()
        if not sym or side not in ("LONG", "SHORT") or asset in active_assets:
            continue

        mwu = float(cand.get("mwu_posterior_score") or 0.0)
        boost = boost_map.get(sym)
        if boost and str(boost.get("direction") or "").upper() == side:
            mwu += float(boost.get("boost_mwu_pts") or 0.0)

        vpin_v = vpin_veto_map.get(sym)
        if vpin_v and str(vpin_v.get("veto_passive_maker_side") or "").upper() == side:
            continue

        if not cand.get("lean_6_axioms_ok") or mwu < min_edge:
            continue
        if cand.get("decision") == "VETO_REASON" or cand.get("veto_reasons"):
            continue

        entry_px = float(cand.get("avellaneda_maker_entry_px") or 0.0)
        sl_px = float(cand.get("mp_stop_px") or 0.0)
        tp_px = float(cand.get("maker_tp_px") or 0.0)
        lots = int(cand.get("lots") or 0)

        if open_propr_slots > 0 and asset in HL_SZ_DECIMALS and propr_snap["floor_buffer_usd"] >= 60.0:
            raw_stop_pct = abs(entry_px - sl_px) / entry_px if entry_px > 0 else 0.0185
            stop_pct = max(raw_stop_pct, 0.0185)
            pr_res = place_propr_trade_safe(
                asset=asset,
                side=side,
                notional_usd=DEFAULT_PROPR_NOTIONAL_USD,
                stop_pct=stop_pct,
                tp_r_mult=tp_r_mult,
                execute=execute,
            )
            if pr_res.get("ok"):
                dispatched_propr.append(pr_res)
                active_assets.add(asset)
                open_propr_slots -= 1
                continue

        if open_kucoin_slots > 0 and lots > 0 and entry_px > 0 and sl_px > 0:
            try:
                import kucoin_postmortem_engine as _kpm
                _pm_g = _kpm.check_kucoin_postmortem_gate(asset, side, regime="bear_squeeze")
                if not _pm_g.get("allowed", True):
                    continue
            except Exception:
                pass
            raw_kc_stop_pct = abs(entry_px - sl_px) / entry_px if entry_px > 0 else 0.0185
            kc_stop_pct = min(max(raw_kc_stop_pct, 0.0185), 0.035)
            cs_live = kc.get_futures_contract(sym) if hasattr(kc, "get_futures_contract") else {}
            live_mark = float((cs_live or {}).get("markPrice") or entry_px)
            live_tick = float((cs_live or {}).get("tickSize") or 0.0001)
            live_mult = abs(float((cs_live or {}).get("multiplier") or 0.01))
            live_entry = (live_mark + 2 * live_tick) if side == "SHORT" else (live_mark - 2 * live_tick)
            live_sl = live_entry * (1.0 + kc_stop_pct) if side == "SHORT" else live_entry * (1.0 - kc_stop_pct)
            live_tp = live_entry * (1.0 - tp_r_mult * kc_stop_pct) if side == "SHORT" else live_entry * (1.0 + tp_r_mult * kc_stop_pct)
            live_stop_dist = abs(live_entry - live_sl)
            max_lots_cap = max(1, int(120.0 / max(1e-9, live_entry * live_mult)))
            live_lots = min(max_lots_cap, max(1, int(round(2.00 / max(1e-9, live_stop_dist * live_mult)))))
            kc_res = place_kucoin_trade_safe(
                symbol=sym,
                side=side,
                lots=live_lots,
                limit_price=live_entry,
                mp_stop_price=live_sl,
                tp_price=live_tp,
                execute=execute,
            )
            if kc_res.get("ok"):
                dispatched_kucoin.append(kc_res)
                active_assets.add(asset)
                open_kucoin_slots -= 1

    return {
        "ok": True,
        "as_of_utc": _now_iso(),
        "as_of_edt": _now_edt(),
        "host": "poet_windows_pc" if IS_WINDOWS else "grok_linux_box",
        "execute": execute,
        "ratchet": ratchet_res,
        "dispatched_propr_trades": dispatched_propr,
        "dispatched_kucoin_entries": dispatched_kucoin,
    }


def print_human_status(st: Dict[str, Any]) -> None:
    s = st["summary"]
    pr = st["propr"]
    kc = st["kucoin"]
    gen = st["evolved_champion_genome"]
    print("=" * 78)
    print(f"SHARED GROK + CHATGPT TRADING DESK STATUS ({st['as_of_edt']} | Host: {st['host']})")
    print("=" * 78)
    print(
        f"Total Controlled Equity: ${s['total_controlled_equity_usd']:,.2f} USD | "
        f"Total Open Risk: ${s['total_open_risk_usd']:.2f} USD | Naked Positions: {s['total_naked_positions']}"
    )
    print(
        f"\n[1] PROPR $25K HYPERLIQUID ({pr['account_id']})\n"
        f"    Equity: ${pr['equity_usd']:,.2f} | Floor: $23,500.00 (Buffer: +${pr['floor_buffer_usd']:.2f}) | "
        f"Target: $27,500.00 | Slots: {pr['used_slots']}/{pr['max_slots']}"
    )
    if pr["positions"]:
        for p in pr["positions"]:
            print(
                f"    -> OPEN {p['asset']} {p['side']} qty={p['quantity']} (${p['notional_usd']} notional) | "
                f"Entry={p['entry_price']} Mark={p['mark_price']} uPnL=${p['unrealized_pnl_usd']:+.2f} ({p['current_r']:+.2f}R) | "
                f"SL={p['sl_price']} TP={p['tp_price']} (1R=${p['open_risk_usd']})"
            )
    else:
        print("    -> 0 open positions (2/2 slots available)")

    print(
        f"\n[2] KUCOIN PERPETUAL FUTURES (UID {kc['uid']})\n"
        f"    Equity: ${kc['equity_usdt']:,.2f} USDT (Avail: ${kc['available_balance_usdt']:,.2f}) | "
        f"Floor: $170.00 (Buffer: +${kc['floor_buffer_usdt']:.2f}) | Slots: {kc['used_slots']}/{kc['max_slots']}"
    )
    if kc["positions"]:
        for p in kc["positions"]:
            print(
                f"    -> OPEN {p['symbol']} {p['side']} {p['lots']} lots (${p['notional_usd']} notional) | "
                f"Entry={p['entry_price']} Mark={p['mark_price']} uPnL=${p['unrealized_pnl_usd']:+.2f} ({p['current_r']:+.2f}R) | "
                f"MP_SL={p['mp_stop_price']} Maker_TP={p['maker_tp_price']} (1R=${p['open_risk_usd']})"
            )
    else:
        print("    -> 0 open positions")
    if kc["working_entries"]:
        for e in kc["working_entries"]:
            print(
                f"    -> WORKING MAKER ENTRY: {e['symbol']} {e['side']} {e['lots']} lots @ {e['limit_price']} "
                f"(${e['notional_usd']} notional) | MP_SL={e['mp_stop_price']}"
            )

    print(
        f"\n[3] EVOLVED CHAMPION GENOME & POET L2 SIGNALS\n"
        f"    Stop={gen.get('atr_stop_mult')}xATR | TP={gen.get('tp_r_mult')}R | "
        f"Trail={gen.get('trail_activation_r')}R | MinEdge={gen.get('min_composite_edge')}"
    )
    vpin_v = st["poet_microstructure_signals"]["vpin_toxicity_vetoes"]
    boosts = st["poet_microstructure_signals"]["hl_lead_lag_boosts"]
    print(f"    Active Poet VPIN Toxicity Vetoes ({len(vpin_v)}): {[v.get('kc_symbol') for v in vpin_v]}")
    print(f"    Active Poet HL->KC Lead-Lag Boosts ({len(boosts)}): {[b.get('kc_symbol') for b in boosts]}")
    print("=" * 78)


def main():
    parser = argparse.ArgumentParser(description="Shared Grok Bot + ChatGPT Dual-Book Trading CLI")
    sub = parser.add_subparsers(dest="cmd")

    p_st = sub.add_parser("status", help="Live status of Propr and KuCoin Futures")
    p_st.add_argument("--json", action="store_true", help="Output pure JSON")

    p_rt = sub.add_parser("ratchet", help="Run position defense, stop ratchet & missing Maker TP stager")
    p_rt.add_argument("--dry-run", action="store_true", help="Preview without placing orders")
    p_rt.add_argument("--json", action="store_true", help="Output pure JSON")

    p_at = sub.add_parser("auto-trade", help="Run ratchet + dispatch top 6-Axiom Evolved Genome setups")
    p_at.add_argument("--dry-run", action="store_true", help="Preview without placing orders")
    p_at.add_argument("--json", action="store_true", help="Output pure JSON")

    p_tp = sub.add_parser("trade-propr", help="Place a safety-checked Propr trade with mandatory SL & TP")
    p_tp.add_argument("--asset", required=True)
    p_tp.add_argument("--side", required=True, choices=["LONG", "SHORT", "long", "short"])
    p_tp.add_argument("--notional", type=float, default=310.0)
    p_tp.add_argument("--sl", type=float, required=True)
    p_tp.add_argument("--tp", type=float, required=True)
    p_tp.add_argument("--dry-run", action="store_true")

    p_tk = sub.add_parser("trade-kucoin", help="Place a safety-checked KuCoin postOnly Maker order + MP stop")
    p_tk.add_argument("--symbol", required=True)
    p_tk.add_argument("--side", required=True, choices=["LONG", "SHORT", "long", "short"])
    p_tk.add_argument("--lots", type=int, required=True)
    p_tk.add_argument("--price", type=float, required=True)
    p_tk.add_argument("--sl", type=float, required=True)
    p_tk.add_argument("--tp", type=float, default=None)
    p_tk.add_argument("--dry-run", action="store_true")

    p_cl = sub.add_parser("close", help="Close an open position and cancel resting orders")
    p_cl.add_argument("--venue", required=True, choices=["propr", "kucoin", "PROPR", "KUCOIN"])
    p_cl.add_argument("--symbol", required=True)
    p_cl.add_argument("--dry-run", action="store_true")

    p_msg = sub.add_parser("message", help="Send or read verified messages on the Sonia <-> Grok Bridge")
    p_msg.add_argument("--from", dest="sender", default="sonia")
    p_msg.add_argument("--to", dest="recipient", default="grok")
    p_msg.add_argument("--subject", default="")
    p_msg.add_argument("--text", default="")

    args = parser.parse_args()
    cmd = args.cmd or "status"

    if cmd == "status":
        st = get_full_status()
        if getattr(args, "json", False):
            print(json.dumps(st, indent=2))
        else:
            print_human_status(st)
    elif cmd == "ratchet":
        res = run_ratchet(execute=not getattr(args, "dry_run", False))
        print(json.dumps(res, indent=2))
    elif cmd == "auto-trade":
        res = run_auto_trade(execute=not getattr(args, "dry_run", False))
        print(json.dumps(res, indent=2))
    elif cmd == "trade-propr":
        res = place_propr_trade_safe(
            asset=args.asset,
            side=args.side,
            notional_usd=args.notional,
            sl_price=args.sl,
            tp_price=args.tp,
            execute=not args.dry_run,
        )
        print(json.dumps(res, indent=2))
    elif cmd == "trade-kucoin":
        res = place_kucoin_trade_safe(
            symbol=args.symbol,
            side=args.side,
            lots=args.lots,
            limit_price=args.price,
            mp_stop_price=args.sl,
            tp_price=args.tp,
            execute=not args.dry_run,
        )
        print(json.dumps(res, indent=2))
    elif cmd == "close":
        res = close_position_safe(venue=args.venue, symbol=args.symbol, execute=not args.dry_run)
        print(json.dumps(res, indent=2))
    elif cmd == "message":
        import sonia_grok_bridge
        if args.text:
            res = sonia_grok_bridge.post_message(sender=args.sender, recipient=args.recipient, subject=args.subject, text=args.text)
        else:
            res = sonia_grok_bridge.read_messages()
        print(json.dumps(res, indent=2))


if __name__ == "__main__":
    main()
