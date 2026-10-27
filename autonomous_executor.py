PROPR_DISABLED_BY_OWNER = True
#!/usr/bin/env python3
"""
Closed-Loop Autonomous Execution, Ratchet & Dual-Book Dispatcher v2.0
File: /home/box/desk/autonomous_executor.py

Runs every 3 minutes inside `autonomous_daemon.py`:
  1. KuCoin Futures (UID 249983249, Max 3 Slots):
     - Orphan Stop Cleanup: Cancels any orphan reduceOnly stop orders for symbols with 0 open position and 0 working entry order.
     - Position Defense & Volatility Ratchet: Verifies every open KuCoin position has both a server-side `MP` stop and a resting `postOnly` Maker TP (`evolved tp_r_mult`). Ratchets stop at `+0.75R` (`+0.05R` breakeven) and `+1.20R` (`+0.60R` / Chandelier).
     - 6-Axiom Lean Gate + Evolved Genome Auto-Dispatcher: Stages `postOnly` Maker entries at the Avellaneda-Stoikov reservation price $r(s,q,\tau)$ with immediate server-side `MP` stop protection.
  2. Propr Classic 1-Step $25K Account (urn:prp-account:jcBy6Vk3KDNd, Max 2 Slots):
     - Live Position Defense & Stop Ratchet: Verifies every open Propr position has a resting `stop_market` (`reduceOnly=True`) order; ratchets stop to fee-covered breakeven (`+0.08R`) at `+0.75R` and trails to `+0.60R` at `+1.20R`.
     - Autonomous Propr Dispatcher: When Propr has open slots (`< 2`) and floor room allows (`open_risk + new_risk <= room - $40`), dispatches:
       (a) Top Evolved Genome + 6-Axiom directional setup passing `check_cross_book_gate("PROPR", ...)` (sized at `<= $320` notional, `2x` leverage, `>= 1.5%` structural stop, immediate `stop_market` SL + `take_profit_market` TP at `+1.85R`), and
       (b) Approved `XS_MOM` / `READY_TREND` Pattern-Lab tickets (`XMR LONG` #1 14d momentum `+18.65%`, `$30` risk cap, resting `stop_market` SL + `take_profit_market` TP).
  3. Imminent `<45m` Extreme Funding Sniper Audit.
"""

import datetime
import json
import logging
import math

logger = logging.getLogger("desk_engine")
import os
import sys
import time
import urllib.request
from typing import Any, Dict, List, Optional

sys.path.insert(0, "/home/box/desk/kucoin")
sys.path.insert(0, "/home/box/desk")
sys.path.insert(0, "/workspace/grok-propr-bot")
sys.path.insert(0, "/workspace/propr-hedge-fund/bin")

from kucoin_client import KuCoinClient
from propr_sdk import ProprClient
try:
    import profit_gates as PG
except ImportError:
    PG = None
import unified_ledger

PRECISION_REPORT_PATH = "/home/box/desk/kucoin/latest_precision_report.json"
EVOLVED_GENOME_PATH = "/home/box/desk/evolved_genome.json"
MULTISERVICE_PATH = "/home/box/desk/latest_kucoin_multiservice.json"
PROPR_STATE_PATH = "/home/box/desk/latest_propr_state.json"
OUTPUT_PATH = "/home/box/desk/latest_autonomous_execution.json"
KUCOIN_WORKER_URL = "https://kucoin-desk-scan.sonia-3e6f56284b.workers.dev"
PROPR_ACCOUNT_ID = "urn:prp-account:jcBy6Vk3KDNd"

MAX_KUCOIN_SLOTS = 2
MAX_PROPR_SLOTS = 2
PROPR_MIN_EQUITY_FOR_AUTO = 23560.0

HL_SZ_DECIMALS = {
    "BTC": 5, "ETH": 4, "SOL": 2, "XRP": 0, "DOGE": 0,
    "AVAX": 2, "LINK": 1, "SUI": 1, "TAO": 3, "XMR": 3,
    "NEAR": 1, "AAVE": 2, "UNI": 1, "SEI": 0, "PEPE": 0,
}
HL_PX_DECIMALS = {
    "BTC": 1, "ETH": 2, "SOL": 2, "XRP": 4, "DOGE": 5,
    "AVAX": 3, "LINK": 3, "SUI": 4, "TAO": 2, "XMR": 2,
    "NEAR": 3, "AAVE": 2, "UNI": 3, "SEI": 4,
}


POET_TELEMETRY_PATH = "/home/box/desk/latest_poet_max_telemetry.json"


def load_poet_microstructure_telemetry() -> Dict[str, Any]:
    """Pull live L2 OBI, VPIN toxicity, and HL->KC Micro-Price Lead-Lag from `poet` node."""
    try:
        req = urllib.request.Request(
            f"{KUCOIN_WORKER_URL}/poet-node",
            headers={"Accept": "application/json", "User-Agent": "AutonomousExecutor/4.0"},
        )
        with urllib.request.urlopen(req, timeout=3.5) as r:
            remote_doc = json.loads(r.read().decode("utf-8"))
        if isinstance(remote_doc, dict) and remote_doc.get("all_24_pair_microstructure"):
            with open(POET_TELEMETRY_PATH, "w", encoding="utf-8") as f:
                json.dump(remote_doc, f, indent=2)
            return remote_doc
    except Exception:
        logger.error("OPERATION_FAILED")
    return load_json(POET_TELEMETRY_PATH)


def load_json(path: str) -> Dict[str, Any]:
    if not os.path.exists(path):
        return {}
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {}


def cleanup_orphan_stops(
    kc: KuCoinClient,
    open_positions: List[Dict[str, Any]],
    active_orders: List[Dict[str, Any]],
    stop_orders: List[Dict[str, Any]],
    execute: bool = False,
) -> List[Dict[str, Any]]:
    execute = False  # HARD-DISABLED BY CHIEF OF STAFF
    pos_syms = {p["symbol"] for p in open_positions if abs(float(p.get("currentQty") or 0)) > 0}
    working_entry_syms = {
        o["symbol"] for o in active_orders if not (o.get("reduceOnly") or o.get("reduce_only"))
    }
    protected_syms = pos_syms | working_entry_syms
    cleaned = []
    for s in stop_orders:
        sym = s.get("symbol")
        if sym and sym not in protected_syms:
            oid = s.get("id")
            if execute and oid:
                try:
                    kc.cancel_futures_order(oid)
                except Exception:
                    logger.error("OPERATION_FAILED")
            cleaned.append({
                "symbol": sym,
                "cancelled_stop_order_id": oid,
                "stop_price": s.get("stopPrice"),
                "reason": "ORPHAN_STOP_NO_POSITION_OR_WORKING_ENTRY",
            })
    return cleaned


def evaluate_funding_snipe_candidates(
    ms_data: Dict[str, Any],
    min_funding_bps: float,
) -> List[Dict[str, Any]]:
    candidates = []
    rows = ((ms_data.get("futures_706_funding_and_basis") or {}).get("top_extreme_negative_funding") or []) + (
        (ms_data.get("futures_706_funding_and_basis") or {}).get("top_extreme_positive_funding") or []
    )
    for r in rows:
        f_bps = float(r.get("funding_bps") or 0.0)
        abs_bps = abs(f_bps)
        mins_left = float(r.get("mins_to_next_funding") or 999.0)
        vol_24h = float(r.get("turnover_24h_usd") or 0.0)
        basis_bps = float(r.get("basis_bps") or 0.0) if r.get("basis_bps") is not None else 0.0

        receiver_side = "LONG" if f_bps < 0 else "SHORT"
        basis_supportive = (receiver_side == "LONG" and basis_bps <= -15.0) or (
            receiver_side == "SHORT" and basis_bps >= 10.0
        )

        status = "MONITOR_SETTLEMENT_CLOCK"
        if abs_bps >= min_funding_bps and mins_left <= 45.0 and vol_24h >= 2_500_000 and basis_supportive:
            status = "IMMINENT_FUNDING_SNIPE_ELIGIBLE"
        elif mins_left > 45.0:
            status = f"WAIT_CLOCK({mins_left:.0f}m_to_settlement>45m)"
        elif abs_bps < min_funding_bps:
            status = f"BELOW_MIN_FUNDING({abs_bps:.1f}<{min_funding_bps:.1f}bps)"
        elif not basis_supportive:
            status = f"BASIS_NOT_ALIGNED({basis_bps:+.1f}bps)"

        candidates.append({
            "symbol": r.get("fut_symbol"),
            "receiver_side": receiver_side,
            "funding_bps": f_bps,
            "granularity_hours": r.get("granularity_hours"),
            "mins_to_next_funding": mins_left,
            "turnover_24h_usd": vol_24h,
            "spot_perp_basis_bps": basis_bps,
            "net_1_interval_bps": r.get("net_1_interval_after_rt_maker_bps"),
            "status": status,
        })
    return candidates[:8]


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


def manage_and_execute_propr(
    active_genome: Dict[str, Any],
    sizing_table: List[Dict[str, Any]],
    active_kucoin_assets: set,
    execute: bool = False,
) -> Dict[str, Any]:
    execute = False  # HARD-DISABLED BY CHIEF OF STAFF
    """
    Audits, ratchets, and dispatches live trades on Propr (urn:prp-account:jcBy6Vk3KDNd).
    """
    if PROPR_DISABLED_BY_OWNER:
        return {
            "account_id": PROPR_ACCOUNT_ID,
            "disabled_by_owner": True,
            "note": "Propr execution & polling disabled on Grok Bot per owner instruction (delegated to Hermes/SovereignAI).",
            "equity_usd": 0.0,
            "floor_buffer_usd": 0.0,
            "open_risk_usd": 0.0,
            "open_positions_count": 0,
            "max_slots": MAX_PROPR_SLOTS,
            "ratchet_actions": [],
            "newly_executed_trades": [],
        }
    client = ProprClient()
    client.setup(PROPR_ACCOUNT_ID)

    bk = PG.live_book(PROPR_ACCOUNT_ID, sync_worker=True)
    propr_eq = float(bk["equity"])
    propr_room = float(bk["room"])
    open_risk = float(bk["open"]["total"])
    open_legs = list(bk["open"]["legs"])

    raw_positions = client.get_open_positions()
    raw_orders = (client.get_orders(status="pending", limit=100) or []) + (client.get_orders(status="open", limit=50) or [])

    trail_act_r = float(active_genome.get("trail_activation_r") or 0.75)
    tp_r_mult = float(active_genome.get("tp_r_mult") or 1.85)
    min_edge = float(active_genome.get("min_composite_edge") or 78.0)

    ratchet_actions = []
    # 1. Audit & ratchet open Propr positions
    for pos in raw_positions:
        asset = str(pos.get("asset") or pos.get("base") or "").upper()
        pos_id = pos.get("positionId")
        pos_side = str(pos.get("positionSide") or "").lower()  # "long" or "short"
        qty_str = str(pos.get("quantity") or "0")
        qty = abs(float(qty_str))
        if qty <= 0:
            continue
        entry_px = float(pos.get("entryPrice") or 0)
        mark_px = float(pos.get("markPrice") or entry_px)

        # Close order alignment on Propr: long -> sell/short; short -> buy/long
        close_side = "sell" if pos_side == "long" else "buy"
        close_pos_side = "short" if pos_side == "long" else "long"

        pos_stops = [
            o for o in raw_orders
            if o.get("positionId") == pos_id and o.get("type") in ("stop_market", "stop_limit")
        ]
        pos_tps = [
            o for o in raw_orders
            if o.get("positionId") == pos_id and o.get("type") in ("take_profit_market", "take_profit_limit", "limit")
        ]

        cur_stop = pos_stops[0] if pos_stops else None
        cur_sl_px = float(cur_stop.get("triggerPrice") or 0) if cur_stop else 0.0

        # Emergency attach stop if naked
        if cur_stop is None and execute and entry_px > 0:
            emerg_sl = entry_px * 0.975 if pos_side == "long" else entry_px * 1.025
            sl_str = _fmt_hl_px(asset, emerg_sl)
            r_sl = client.create_order(
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
            cur_sl_px = float(sl_str)
            ratchet_actions.append({
                "asset": asset,
                "action": "ATTACHED_EMERGENCY_STOP",
                "stop_price": cur_sl_px,
                "order": r_sl,
            })

        # Check ratchet if in profit
        init_stop_dist = abs(entry_px - cur_sl_px) if cur_sl_px > 0 else (entry_px * 0.02)
        fav_move = (mark_px - entry_px) if pos_side == "long" else (entry_px - mark_px)
        cur_r = fav_move / init_stop_dist if init_stop_dist > 0 else 0.0

        new_sl_px = None
        reason = "HOLD_STOP"
        if cur_r >= 1.20:
            cand = (entry_px + 0.60 * init_stop_dist) if pos_side == "long" else (entry_px - 0.60 * init_stop_dist)
            if (pos_side == "long" and cand > cur_sl_px * 1.001) or (pos_side == "short" and cand < cur_sl_px * 0.999):
                new_sl_px = cand
                reason = f"PROPR_TRAIL_0.60R_AT_{cur_r:+.2f}R"
        elif cur_r >= trail_act_r:
            cand = (entry_px + 0.08 * init_stop_dist) if pos_side == "long" else (entry_px - 0.08 * init_stop_dist)
            if (pos_side == "long" and cand > cur_sl_px * 1.001) or (pos_side == "short" and cand < cur_sl_px * 0.999):
                new_sl_px = cand
                reason = f"PROPR_BREAKEVEN_LOCK_AT_{cur_r:+.2f}R"

        if new_sl_px is not None and execute:
            sl_str = _fmt_hl_px(asset, new_sl_px)
            r_new = client.create_order(
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
            if cur_stop and cur_stop.get("orderId"):
                try:
                    client.cancel_order(cur_stop["orderId"])
                except Exception:
                    logger.error("OPERATION_FAILED")
            ratchet_actions.append({
                "asset": asset,
                "action": reason,
                "old_sl": cur_sl_px,
                "new_sl": float(sl_str),
                "order": r_new,
            })

    # 2. Dispatch new Propr trades when slots (< MAX_PROPR_SLOTS) and room allow
    active_propr_assets = {str(p.get("asset") or p.get("base") or "").upper() for p in raw_positions}
    used_propr_slots = len(active_propr_assets)
    open_propr_slots = max(0, MAX_PROPR_SLOTS - used_propr_slots)
    newly_executed_propr = []

    if execute and open_propr_slots > 0 and propr_eq >= PROPR_MIN_EQUITY_FOR_AUTO and propr_room >= 60.0:
        # Refresh unified book before checking gates
        unified_ledger.build_unified_book()

        # Candidate Stream A: Top Evolved-Genome + 6-Axiom directional setup not already active on KuCoin/Propr
        for cand in sizing_table:
            if open_propr_slots <= 0:
                break
            sym = cand["symbol"]
            asset = unified_ledger.normalize_asset(sym)
            side = cand["side"].upper()
            mwu_score = float(cand.get("mwu_posterior_score") or 0.0)
            axioms_ok = bool(cand.get("lean_6_axioms_ok", False))
            oi_regime = str(cand.get("oi_regime") or "")
            cvd_opposing = (side == "SHORT" and "BUY_DOMINANCE" in oi_regime) or (
                side == "LONG" and "SELL_DOMINANCE" in oi_regime
            )
            if asset in active_propr_assets or asset in active_kucoin_assets:
                continue
            if not axioms_ok or mwu_score < min_edge or cvd_opposing:
                continue
            if asset not in HL_SZ_DECIMALS:
                continue

            entry_ref = float(cand["avellaneda_maker_entry_px"])
            sl_ref = float(cand["mp_stop_px"])
            raw_stop_pct = abs(entry_ref - sl_ref) / entry_ref
            # Ensure Propr stop distance is at least 1.65% so fee drag (<7% of 1R) & 1h ATR gates pass cleanly
            stop_pct = max(raw_stop_pct, 0.0185)
            sl_px = entry_ref * (1.0 - stop_pct) if side == "LONG" else entry_ref * (1.0 + stop_pct)
            tp_px = entry_ref * (1.0 + tp_r_mult * stop_pct) if side == "LONG" else entry_ref * (1.0 - tp_r_mult * stop_pct)

            # Challenge-safe sizing: target <= $315 notional (~$6.20 1R risk)
            target_notional = 310.0
            raw_qty = target_notional / entry_ref
            qty_str = _fmt_hl_qty(asset, raw_qty)
            qty_val = float(qty_str)
            if qty_val * entry_ref < 25.0:
                continue
            actual_notional = round(qty_val * entry_ref, 2)
            risk_usd = round(qty_val * abs(entry_ref - sl_px) + actual_notional * 0.0011, 2)

            gate_check = unified_ledger.check_cross_book_gate("PROPR", asset, side, risk_usd)
            if not gate_check["ok"]:
                continue

            # Set safe 2x leverage on Propr — abort entry if leverage config fails
            try:
                client.set_leverage(asset, 2, margin_mode="isolated")
            except Exception:
                logger.error("LEVERAGE_CONFIGURATION_FAILED:%s", asset)
                continue

            order_side = "buy" if side == "LONG" else "sell"
            pos_side = "long" if side == "LONG" else "short"
            close_side = "sell" if side == "LONG" else "buy"
            close_pos_side = "short" if side == "LONG" else "long"

            r_entry = client.create_order(
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
            fill_px = float(entry_ord.get("averageFillPrice") or entry_ref)

            # Re-anchor SL & TP around actual IOC fill price
            sl_final = _fmt_hl_px(asset, fill_px * (1.0 - stop_pct) if side == "LONG" else fill_px * (1.0 + stop_pct))
            tp_final = _fmt_hl_px(asset, fill_px * (1.0 + tp_r_mult * stop_pct) if side == "LONG" else fill_px * (1.0 - tp_r_mult * stop_pct))

            if not pos_id:
                open_p = client.get_open_positions(base=asset)
                if open_p:
                    pos_id = open_p[0].get("positionId")

            sl_ord_id = None
            tp_ord_id = None
            if pos_id:
                r_sl = client.create_order(
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
                sl_ord_id = (r_sl[0] if isinstance(r_sl, list) and r_sl else {}).get("orderId")

                r_tp = client.create_order(
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
                tp_ord_id = (r_tp[0] if isinstance(r_tp, list) and r_tp else {}).get("orderId")

                protected = bool(pos_id and sl_ord_id and tp_ord_id)
                if not sl_ord_id:
                    logger.error("PROPR_STOP_MISSING_EMERGENCY_FLATTEN:%s", asset)
                    try:
                        client.close_position(base=asset)
                    except Exception:
                        logger.error("PROPR_EMERGENCY_FLATTEN_FAILED:%s", asset)
                newly_executed_propr.append({
                    "ok": protected,
                    "requires_operator_review": not protected,
                    "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                    "book": "PROPR",
                    "strategy": "EVOLVED_GENOME_6AXIOM_IOC",
                    "asset": asset,
                    "side": side,
                    "quantity": float(qty_str),
                    "fill_price": fill_px,
                    "notional_usd": round(float(qty_str) * fill_px, 2),
                    "sl_price": float(sl_final),
                    "tp_price": float(tp_final),
                    "risk_1r_usd": risk_usd,
                    "position_id": pos_id,
                    "entry_order_id": entry_ord.get("orderId"),
                    "sl_order_id": sl_ord_id,
                    "tp_order_id": tp_ord_id,
                    "mwu_posterior_score": mwu_score,
                })
                active_propr_assets.add(asset)
                open_propr_slots -= 1
                unified_ledger.build_unified_book()

        # Candidate Stream B: Approved Pattern-Lab XS_MOM Top-1 Relative Strength Leader (XMR LONG)
        if open_propr_slots > 0 and "XMR" not in active_propr_assets:
            gate_xmr = unified_ledger.check_cross_book_gate("PROPR", "XMR", "LONG", 18.50)
            if gate_xmr["ok"]:
                try:
                    client.set_leverage("XMR", 2, margin_mode="isolated")
                except Exception:
                    logger.error("OPERATION_FAILED")
                # Size $310 notional (0.50 XMR) with a 6.0% structural swing stop (~$18.60 1R risk) and +1.85R TP (+11.1%)
                qty_str = "0.5"
                r_entry = client.create_order(
                    side="buy",
                    position_side="long",
                    order_type="market",
                    asset="XMR",
                    base="XMR",
                    quote="USDC",
                    quantity=qty_str,
                    time_in_force="IOC",
                    reduce_only=False,
                )
                entry_ord = r_entry[0] if isinstance(r_entry, list) and r_entry else {}
                pos_id = entry_ord.get("positionId")
                fill_px = float(entry_ord.get("averageFillPrice") or 617.30)
                if not pos_id:
                    open_p = client.get_open_positions(base="XMR")
                    if open_p:
                        pos_id = open_p[0].get("positionId")
                if pos_id:
                    sl_final = _fmt_hl_px("XMR", fill_px * 0.94)
                    tp_final = _fmt_hl_px("XMR", fill_px * (1.0 + 0.06 * tp_r_mult))
                    r_sl = client.create_order(
                        side="sell",
                        position_side="short",
                        order_type="stop_market",
                        asset="XMR",
                        base="XMR",
                        quote="USDC",
                        quantity=qty_str,
                        trigger_price=sl_final,
                        reduce_only=True,
                        position_id=pos_id,
                    )
                    sl_ord_id = (r_sl[0] if isinstance(r_sl, list) and r_sl else {}).get("orderId")
                    r_tp = client.create_order(
                        side="sell",
                        position_side="short",
                        order_type="take_profit_market",
                        asset="XMR",
                        base="XMR",
                        quote="USDC",
                        quantity=qty_str,
                        trigger_price=tp_final,
                        reduce_only=True,
                        position_id=pos_id,
                    )
                    tp_ord_id = (r_tp[0] if isinstance(r_tp, list) and r_tp else {}).get("orderId")
                    risk_usd = round(0.5 * (fill_px - float(sl_final)) + 0.5 * fill_px * 0.0011, 2)
                    newly_executed_propr.append({
                        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "book": "PROPR",
                        "strategy": "XS_MOM_14D_TOP1_LEADER",
                        "asset": "XMR",
                        "side": "LONG",
                        "quantity": 0.5,
                        "fill_price": fill_px,
                        "notional_usd": round(0.5 * fill_px, 2),
                        "sl_price": float(sl_final),
                        "tp_price": float(tp_final),
                        "risk_1r_usd": risk_usd,
                        "position_id": pos_id,
                        "entry_order_id": entry_ord.get("orderId"),
                        "sl_order_id": sl_ord_id,
                        "tp_order_id": tp_ord_id,
                        "mom14d": "+18.65%",
                    })
                    active_propr_assets.add("XMR")
                    open_propr_slots -= 1

    # Sync updated Propr book back to propr-desk-scan Worker
    bk_after = PG.live_book(PROPR_ACCOUNT_ID, sync_worker=True)
    return {
        "account_id": PROPR_ACCOUNT_ID,
        "equity_usd": bk_after["equity"],
        "static_floor_usd": 23500.0,
        "floor_buffer_usd": bk_after["room"],
        "open_risk_usd": bk_after["open"]["total"],
        "open_positions_count": len(bk_after["open"]["legs"]),
        "open_legs": bk_after["open"]["legs"],
        "naked_positions": bk_after["open"]["naked"],
        "used_slots": len(bk_after["open"]["legs"]),
        "max_slots": MAX_PROPR_SLOTS,
        "ratchet_actions": ratchet_actions,
        "newly_executed_trades": newly_executed_propr,
        "decision": "ACTIVE_EXECUTION_ENABLED" if execute else "READ_ONLY_AUDIT_MODE",
    }


def run_autonomous_executor(execute: bool = False, sync_worker: bool = True) -> Dict[str, Any]:
    execute = False  # HARD-DISABLED BY CHIEF OF STAFF
    t0 = time.time()
    kc = KuCoinClient()

    prec = load_json(PRECISION_REPORT_PATH)
    genome_doc = load_json(EVOLVED_GENOME_PATH) or (prec.get("evolved_genome") or {})
    active_genome = genome_doc.get("active_genome") or {
        "atr_stop_mult": 1.65,
        "tp_r_mult": 1.85,
        "trail_activation_r": 0.75,
        "min_composite_edge": 78.0,
        "funding_snipe_min_bps": 16.5,
        "bernstein_delta": 0.08,
    }
    ms_data = load_json(MULTISERVICE_PATH) or (prec.get("multiservice_alpha") or {})

    # 1. Fetch live KuCoin account & order state
    acct = kc.get_futures_overview("USDT")
    kc_equity = round(float(acct.get("accountEquity") or 0.0), 2)
    kc_avail = round(float(acct.get("availableBalance") or 0.0), 2)

    raw_positions = kc.get_futures_positions()
    open_positions = [p for p in raw_positions if abs(float(p.get("currentQty") or 0)) > 0]
    active_orders = kc.get_futures_active_orders()
    stop_orders = kc.get_futures_stop_orders()

    # 2. Clean up any orphan stop orders left over from closed positions
    orphan_stops_cleaned = cleanup_orphan_stops(
        kc, open_positions, active_orders, stop_orders, execute=execute
    )
    if orphan_stops_cleaned:
        stop_orders = kc.get_futures_stop_orders()

    prev_exec = load_json(OUTPUT_PATH)
    recent_actions = list(prev_exec.get("recent_autonomous_actions") or [])

    working_entry_orders = [
        o for o in active_orders if not (o.get("reduceOnly") or o.get("reduce_only"))
    ]
    working_tp_orders = [
        o for o in active_orders if (o.get("reduceOnly") or o.get("reduce_only"))
    ]

    active_slot_symbols = {p["symbol"] for p in open_positions} | {
        o["symbol"] for o in working_entry_orders
    }
    used_kucoin_slots = len(active_slot_symbols)
    open_kucoin_slots = max(0, MAX_KUCOIN_SLOTS - used_kucoin_slots)

    # 3. Position Defense & Volatility Ratchet Log (from precision_engine + live verification)
    ratchet_audit = prec.get("chandelier_ratchet_audit") or []
    working_order_audit = prec.get("working_maker_order_audit") or []

    # 4. Evaluate 6-Axiom Lean Gate + Evolved Genome Candidates for KuCoin Auto-Dispatch
    min_edge = float(active_genome.get("min_composite_edge") or 78.0)
    tp_r_mult = float(active_genome.get("tp_r_mult") or 1.85)
    min_fund_bps = float(active_genome.get("funding_snipe_min_bps") or 16.5)

    contracts_resp = kc.request("https://api-futures.kucoin.com", "GET", "/api/v1/contracts/active", auth=False)
    contracts = {c["symbol"]: c for c in (contracts_resp.get("data") or [])}

    poet_tele = load_poet_microstructure_telemetry()
    poet_micro_map = {
        m.get("kc_symbol"): m for m in (poet_tele.get("all_24_pair_microstructure") or []) if m.get("kc_symbol")
    }
    poet_exec_ov = poet_tele.get("execution_overrides") or {}
    poet_vpin_veto_map = {
        v.get("kc_symbol"): v for v in (poet_exec_ov.get("vpin_toxicity_veto_symbols") or []) if v.get("kc_symbol")
    }
    poet_boost_map = {
        b.get("kc_symbol"): b for b in (poet_exec_ov.get("hl_lead_lag_boost_symbols") or []) if b.get("kc_symbol")
    }

    sizing_table = prec.get("a_grade_sizing_table") or []
    candidate_decisions: List[Dict[str, Any]] = []
    staged_entries: List[Dict[str, Any]] = []

    for cand in sizing_table:
        sym = cand["symbol"]
        side = cand["side"]
        mwu_score = float(cand.get("mwu_posterior_score") or 0.0)
        mtf_score = int(cand.get("mtf_score") or 0)
        axioms_ok = bool(cand.get("lean_6_axioms_ok", False))
        fee_drag = float(cand.get("fee_drag_pct_of_r") or 99.0)
        gate_ok = bool(cand.get("cross_book_gate_ok", False))
        gate_reasons = cand.get("cross_book_gate_reasons") or []
        flow_safety = str(cand.get("l2_flow_safety") or "FAVORABLE_MAKER_FLOW")
        oi_regime = str(cand.get("oi_regime") or "NEUTRAL_OI_DRIFT")

        cvd_opposing = (side == "SHORT" and "BUY_DOMINANCE" in oi_regime) or (
            side == "LONG" and "SELL_DOMINANCE" in oi_regime
        )

        veto_reasons = []
        if sym in active_slot_symbols:
            veto_reasons.append(f"ALREADY_ACTIVE_SLOT({sym})")
        if not axioms_ok:
            veto_reasons.append("LEAN_6_AXIOMS_FAIL")
        if mwu_score < min_edge:
            veto_reasons.append(f"MWU_BELOW_EVOLVED_MIN({mwu_score:.2f}<{min_edge:.1f})")
        if fee_drag >= 15.0:
            veto_reasons.append(f"FEE_DRAG_TOO_HIGH({fee_drag:.2f}%>=15%)")
        if not gate_ok:
            veto_reasons.append(f"CROSS_BOOK_GATE({','.join(gate_reasons)})")
        if flow_safety == "TOXIC_ADVERSE_FLOW_WARNING":
            veto_reasons.append("TOXIC_L2_FLOW_VETO")
        if cvd_opposing:
            veto_reasons.append(f"OPPOSING_TAKER_CVD({oi_regime})")

        # Apply poet v4.0 Real-Time L2 VPIN Toxicity Veto & HL->KC Micro-Price Lead-Lag Boost
        poet_m = poet_micro_map.get(sym) or {}
        poet_vpin = float(poet_m.get("combined_vpin") or 0.0)
        poet_lead_bps = float(poet_m.get("micro_lead_lag_bps") or 0.0)
        poet_vpin_veto = poet_vpin_veto_map.get(sym)
        if poet_vpin_veto and str(poet_vpin_veto.get("veto_passive_maker_side") or "").upper() == side.upper():
            veto_reasons.append(f"POET_VPIN_TOXICITY_VETO(VPIN={poet_vpin:.3f})")
        poet_boost = poet_boost_map.get(sym)
        if poet_boost and str(poet_boost.get("direction") or "").upper() == side.upper():
            mwu_score = round(mwu_score + float(poet_boost.get("boost_mwu_pts") or 0.0), 2)

        decision = "VETO_REASON"
        staged_order_info: Optional[Dict[str, Any]] = None

        if not veto_reasons:
            if open_kucoin_slots > 0 and execute:
                c_spec = contracts.get(sym) or {}
                tick = float(c_spec.get("tickSize") or 0.0001)
                decimals = max(0, -int(round(math.log10(tick)))) if 0 < tick < 1 else 0
                mark_px = float(c_spec.get("markPrice") or cand["avellaneda_maker_entry_px"])
                entry_px = float(cand["avellaneda_maker_entry_px"])
                sl_px = float(cand["mp_stop_px"])
                tp_px = float(cand["maker_tp_px"])
                lots = int(cand["lots"])

                if side == "SHORT" and entry_px <= mark_px:
                    entry_px = round(mark_px + 2 * tick, decimals)
                elif side == "LONG" and entry_px >= mark_px:
                    entry_px = round(mark_px - 2 * tick, decimals)

                order_side = "buy" if side == "LONG" else "sell"
                stop_side = "sell" if side == "LONG" else "buy"
                stop_dir = "down" if side == "LONG" else "up"

                r_entry = kc.place_futures_order(
                    symbol=sym,
                    side=order_side,
                    size=lots,
                    order_type="limit",
                    price=f"{entry_px:.{decimals}f}",
                    leverage="3",
                    post_only=True,
                    reduce_only=False,
                    margin_mode="CROSS",
                )
                entry_oid = (r_entry.get("data") or {}).get("orderId")

                stop_oid = None
                if entry_oid:
                    r_stop = kc.place_futures_stop_order(
                        symbol=sym,
                        side=stop_side,
                        size=lots,
                        stop_price=f"{sl_px:.{decimals}f}",
                        stop_dir=stop_dir,
                        order_type="market",
                        stop_price_type="MP",
                        reduce_only=True,
                        leverage="3",
                        margin_mode="CROSS",
                    )
                    stop_oid = (r_stop.get("data") or {}).get("orderId")
                    if not stop_oid:
                        logger.error("KUCOIN_STOP_MISSING_CANCELLING_ENTRY:%s", sym)
                        try:
                            kc.cancel_futures_order(entry_oid)
                        except Exception:
                            logger.error("KUCOIN_EMERGENCY_CANCEL_FAILED:%s", sym)
                        decision = "ABORTED_MISSING_MP_STOP"
                    else:
                        open_kucoin_slots -= 1
                        used_kucoin_slots += 1
                        active_slot_symbols.add(sym)
                        decision = "STAGE_MAKER_ENTRY"
                    staged_order_info = {
                        "ts": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                        "book": "KUCOIN",
                        "action": "STAGE_MAKER_ENTRY",
                        "symbol": sym,
                        "side": side,
                        "lots": lots,
                        "notional_usd": cand["notional_usd"],
                        "risk_1r_usd": cand["actual_risk_usd"],
                        "entry_limit_px": entry_px,
                        "mp_stop_px": sl_px,
                        "target_maker_tp_px": tp_px,
                        "tp_r_mult": tp_r_mult,
                        "entry_order_id": entry_oid,
                        "mp_stop_order_id": stop_oid,
                        "mwu_posterior_score": mwu_score,
                        "oi_regime": oi_regime,
                    }
                    staged_entries.append(staged_order_info)
                else:
                    decision = f"EXCHANGE_REJECT({r_entry.get('msg', 'unknown')})"
            elif open_kucoin_slots > 0:
                decision = "ELIGIBLE_READY_6AXIOM_PASS"
            else:
                decision = "ELIGIBLE_STANDBY_MAX_SLOTS_FULL"
                veto_reasons.append(f"MAX_KUCOIN_SLOTS_ACTIVE({used_kucoin_slots}/{MAX_KUCOIN_SLOTS})")

        candidate_decisions.append({
            "symbol": sym,
            "side": side,
            "mwu_posterior_score": mwu_score,
            "mtf_score": mtf_score,
            "lean_6_axioms_ok": axioms_ok,
            "evolved_min_edge_pass": mwu_score >= min_edge,
            "fee_drag_pct_of_r": fee_drag,
            "l2_flow_safety": flow_safety,
            "oi_regime": oi_regime,
            "avellaneda_maker_entry_px": cand["avellaneda_maker_entry_px"],
            "mp_stop_px": cand["mp_stop_px"],
            "maker_tp_px": cand["maker_tp_px"],
            "lots": cand["lots"],
            "notional_usd": cand["notional_usd"],
            "actual_risk_usd": cand["actual_risk_usd"],
            "decision": decision,
            "veto_reasons": veto_reasons,
            "staged_order": staged_order_info,
        })

    # 5. Execute & Manage Propr Prop Account Live
    active_kc_assets = {unified_ledger.normalize_asset(s) for s in active_slot_symbols}
    propr_gate_status = manage_and_execute_propr(
        active_genome=active_genome,
        sizing_table=sizing_table,
        active_kucoin_assets=active_kc_assets,
        execute=execute,
    )

    # 6. Evaluate Imminent <45m Extreme Funding Sniper Setups
    funding_snipe_audit = evaluate_funding_snipe_candidates(ms_data, min_fund_bps)

    now_utc = datetime.datetime.now(datetime.timezone.utc)
    edt_tz = datetime.timezone(datetime.timedelta(hours=-4))
    now_edt = now_utc.astimezone(edt_tz)

    new_propr_actions = propr_gate_status.get("newly_executed_trades") or []

    report = {
        "ok": True,
        "as_of": now_utc.isoformat(),
        "edt_now": now_edt.strftime("%Y-%m-%d %H:%M:%S EDT"),
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "evolved_genome_applied": active_genome,
        "kucoin_execution_state": {
            "account_equity_usdt": kc_equity,
            "available_balance_usdt": kc_avail,
            "open_positions_count": len(open_positions),
            "working_entry_orders_count": len(working_entry_orders) + len(staged_entries),
            "working_tp_orders_count": len(working_tp_orders),
            "used_slots": used_kucoin_slots,
            "max_slots": MAX_KUCOIN_SLOTS,
            "orphan_stops_cleaned": orphan_stops_cleaned,
            "ratchet_and_defense_audit": ratchet_audit,
            "working_maker_order_audit": working_order_audit,
            "newly_staged_maker_entries": staged_entries,
            "active_working_entries": [
                {
                    "id": o.get("id"),
                    "symbol": o.get("symbol"),
                    "side": "LONG" if str(o.get("side") or "").lower() == "buy" else "SHORT",
                    "size_lots": int(o.get("size") or 0),
                    "price": float(o.get("price") or 0.0),
                    "post_only": bool(o.get("postOnly")),
                }
                for o in working_entry_orders
            ],
        },
        "recent_autonomous_actions": (new_propr_actions + staged_entries + recent_actions)[:12],
        "propr_execution_state": propr_gate_status,
        "poet_microstructure_integration": {
            "node_ip": poet_tele.get("residential_ip"),
            "engine_version": poet_tele.get("engine_version"),
            "lakehouse_stats": poet_tele.get("lakehouse_stats"),
            "monte_carlo_10k": poet_tele.get("monte_carlo_10k"),
            "vpin_toxicity_vetoes_active": list(poet_vpin_veto_map.values()),
            "hl_lead_lag_boosts_active": list(poet_boost_map.values()),
        },
        "candidate_gate_decisions": candidate_decisions,
        "funding_sniper_45m_audit": funding_snipe_audit,
        "kucoin_postmortem_learning": _load_json("/home/box/desk/latest_kucoin_postmortem.json").get("postmortem_summary", {}),
    }

    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=2)

    if sync_worker:
        try:
            payload = json.dumps(report).encode("utf-8")
            req = urllib.request.Request(
                f"{KUCOIN_WORKER_URL}/sync-auto-exec",
                data=payload,
                headers={"Content-Type": "application/json", "User-Agent": "AutonomousExecutor/2.0"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=6).read()
        except Exception:
            logger.error("OPERATION_FAILED")

    return report


if __name__ == "__main__":
    rep = run_autonomous_executor(execute=False, sync_worker=True)
    kc_st = rep["kucoin_execution_state"]
    pr_st = rep["propr_execution_state"]
    print(f"=== Closed-Loop Autonomous Executor v2.0 ({rep['elapsed_ms']} ms) ===")
    print(
        f"KuCoin Equity: ${kc_st['account_equity_usdt']} | Open Positions: {kc_st['open_positions_count']} | "
        f"Working Entry Orders: {kc_st['working_entry_orders_count']} | Active Slots: {kc_st['used_slots']}/{kc_st['max_slots']}"
    )
    print(
        f"Propr Equity:  ${pr_st['equity_usd']} | Room: ${pr_st['floor_buffer_usd']} | Open Risk: ${pr_st['open_risk_usd']} | "
        f"Open Positions: {pr_st['open_positions_count']}/{pr_st['max_slots']}"
    )
    if kc_st["newly_staged_maker_entries"]:
        print("Newly Staged KuCoin Maker Entries:")
        for s in kc_st["newly_staged_maker_entries"]:
            print(
                f"  -> [KUCOIN] STAGED {s['symbol']} {s['side']} {s['lots']} lots (${s['notional_usd']} notional, 1R=${s['risk_1r_usd']}) "
                f"@ postOnly {s['entry_limit_px']} | MP_Stop={s['mp_stop_px']} | Target_TP={s['target_maker_tp_px']} ({s['tp_r_mult']}R)"
            )
    if pr_st.get("newly_executed_trades"):
        print("Newly Executed Propr Live Trades:")
        for p in pr_st["newly_executed_trades"]:
            print(
                f"  -> [PROPR] FILLED {p['asset']} {p['side']} qty={p['quantity']} (${p['notional_usd']} notional, 1R=${p['risk_1r_usd']}) "
                f"@ {p['fill_price']} | SL={p['sl_price']} | TP={p['tp_price']} [PosID={p['position_id']}]"
            )
