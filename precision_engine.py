#!/usr/bin/env python3
"""
Institutional Precision & Proficiency Engine v3.5 (Parallelized + Self-Evolving)
File: /home/box/desk/precision_engine.py

Integrates all quantitative & execution engines across KuCoin Futures & Propr in <5s:
  1. 5-Timescale MTF Hierarchy Scanner (`mtf_scanner.py`)
  2. OpenAI `/math` PDE Navier-Stokes Vorticity, Martingale Cone & Ramsey Clique (`quant_math_frontiers.py`)
  3. Cross-Exchange 3-Venue Liquidation Cascade & OI Delta Sniper (`liquidation_oi_sniper.py`)
  4. Avellaneda-Stoikov Sub-Tick Maker Queue Placement & Microprice/EOBI Guard (`avellaneda_stoikov_exec.py`)
  5. 8h Funding Settlement-Clock Harvester & Carry Analyzer (`funding_clock_harvester.py`)
  6. Polymarket Whale-Flow & Smart-Money CLOB Sweep Tracker (`polymarket_whale_tracker.py`)
  7. Online Multiplicative-Weights (MWU / Hedge) Bayesian Regime Auto-Updater (`bayesian_regime_mwu.py`)
  8. Walk-Forward Genetic Evolution Genome (`genetic_evolution_engine.py` -> `evolved_genome.json`)
  9. Automated Chandelier Stop Ratchet, Missing Maker-TP Stager & Toxic-Flow Order Re-Pricer
"""

import json
import math
import os
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Dict, List, Optional

sys.path.insert(0, "/home/box/desk/kucoin")
sys.path.insert(0, "/home/box/desk")
from kucoin_client import KuCoinClient
import mtf_scanner
import unified_ledger
import quant_math_frontiers
import liquidation_oi_sniper
import avellaneda_stoikov_exec
import funding_clock_harvester
import polymarket_whale_tracker
import bayesian_regime_mwu
import deribit_options_edge
import kalshi_polymarket_consensus
import feature_store
import kucoin_multiservice_alpha
import genetic_evolution_engine


def load_cached_or_run(
    cache_path: str,
    runner_fn: Callable[[], Dict[str, Any]],
    max_age_sec: float = 120.0,
) -> Dict[str, Any]:
    """
    Returns cached JSON if fresh within `max_age_sec`; otherwise executes `runner_fn()`,
    falling back gracefully to cached JSON if an external API times out.
    """
    cached_data: Optional[Dict[str, Any]] = None
    if os.path.exists(cache_path):
        try:
            age = time.time() - os.path.getmtime(cache_path)
            with open(cache_path, "r", encoding="utf-8") as f:
                cached_data = json.load(f)
            if age <= max_age_sec and cached_data:
                return cached_data
        except Exception:
            pass
    try:
        return runner_fn()
    except Exception:
        return cached_data or {}


def get_active_evolved_genome(max_age_sec: float = 600.0) -> Dict[str, Any]:
    return load_cached_or_run(
        "/home/box/desk/evolved_genome.json",
        lambda: genetic_evolution_engine.run_genetic_evolution(sync_worker=False),
        max_age_sec=max_age_sec,
    )


def evaluate_and_ratchet_positions(
    kc: KuCoinClient,
    mtf_map: Dict[str, Any],
    math_map: Dict[str, Any],
    as_map: Dict[str, Any],
    evolved_params: Dict[str, float],
    contracts: Dict[str, Any],
    execute: bool = False,
) -> List[Dict[str, Any]]:
    execute = False  # HARD-DISABLED BY CHIEF OF STAFF
    positions = [p for p in kc.get_futures_positions() if abs(float(p.get("currentQty") or 0)) > 0]
    stops = kc.get_futures_stop_orders()
    active_orders = kc.get_futures_active_orders()

    tp_r_mult = float(evolved_params.get("tp_r_mult") or 1.50)
    trail_act_r = float(evolved_params.get("trail_activation_r") or 0.75)

    actions = []
    for p in positions:
        sym = p["symbol"]
        qty = int(p.get("currentQty") or 0)
        abs_qty = abs(qty)
        side = "LONG" if qty > 0 else "SHORT"
        entry = float(p.get("avgEntryPrice") or 0)
        c_spec = contracts.get(sym) or {}
        mark = float(c_spec.get("markPrice") or p.get("markPrice") or entry)
        mult = abs(float(c_spec.get("multiplier") or 0.01))
        tick = float(c_spec.get("tickSize") or 0.01)
        decimals = max(0, -int(round(math.log10(tick)))) if 0 < tick < 1 else 0

        expected_close_side = "sell" if side == "LONG" else "buy"
        matching_stops = [
            s for s in stops
            if s.get("symbol") == sym and str(s.get("side") or "").lower() == expected_close_side
        ]
        cur_stop = matching_stops[0] if matching_stops else None
        cur_sl_px = float(cur_stop.get("stopPrice") or 0) if cur_stop else 0.0

        matching_tps = [
            o for o in active_orders
            if o.get("symbol") == sym
            and str(o.get("side") or "").lower() == expected_close_side
            and (o.get("reduceOnly") or o.get("reduce_only"))
        ]
        cur_tp = matching_tps[0] if matching_tps else None
        cur_tp_px = float(cur_tp.get("price") or 0) if cur_tp else None

        init_stop_dist = abs(entry - cur_sl_px) if cur_sl_px > 0 else (entry * 0.035)
        fav_move = (mark - entry) if side == "LONG" else (entry - mark)
        cur_r = fav_move / init_stop_dist if init_stop_dist > 0 else 0.0

        mtf_item = mtf_map.get(sym) or {}
        ltf = mtf_item.get("ltf_15m_5m") or {}
        chandelier_sl = ltf.get("chandelier_long_sl_15m" if side == "LONG" else "chandelier_short_sl_15m")

        math_item = math_map.get(sym) or {}
        cone = math_item.get("martingale_concentration_cone") or {}
        cone_status = cone.get("cone_status", "INSIDE_POSITIVE_DRIFT_CONE")
        early_cut_r = float(cone.get("optimal_early_cut_r") or -0.48)

        as_item = as_map.get(sym) or {}
        flow_safety = (as_item.get("execution_recommendation") or {}).get("flow_safety", "FAVORABLE_MAKER_FLOW")
        eobi_20 = float((as_item.get("microprice_and_obi") or {}).get("eobi_top20") or 0.0)

        new_sl_px: Optional[float] = None
        ratchet_reason = "HOLD_INITIAL_MP_STOP"

        if cur_r >= 1.20:
            lock_060 = (entry + 0.60 * init_stop_dist) if side == "LONG" else (entry - 0.60 * init_stop_dist)
            if chandelier_sl:
                cand = max(lock_060, float(chandelier_sl)) if side == "LONG" else min(lock_060, float(chandelier_sl))
            else:
                cand = lock_060
            if (side == "LONG" and cand > cur_sl_px + tick) or (side == "SHORT" and cand < cur_sl_px - tick):
                new_sl_px = round(cand, decimals)
                ratchet_reason = f"CHANDELIER_TRAIL_0.60R_AT_{cur_r:+.2f}R"
        elif cur_r >= trail_act_r:
            cand = (entry + 0.05 * init_stop_dist) if side == "LONG" else (entry - 0.05 * init_stop_dist)
            if (side == "LONG" and cand > cur_sl_px + tick) or (side == "SHORT" and cand < cur_sl_px - tick):
                new_sl_px = round(cand, decimals)
                ratchet_reason = f"BREAKEVEN_PLUS_FEES_LOCK_AT_{cur_r:+.2f}R"
        elif cur_r <= early_cut_r and cone_status == "MARTINGALE_DRIFT_VIOLATION" and flow_safety == "TOXIC_ADVERSE_FLOW_WARNING":
            cand = (entry - 0.70 * init_stop_dist) if side == "LONG" else (entry + 0.70 * init_stop_dist)
            if (side == "LONG" and cand < mark - 2 * tick and cand > cur_sl_px + tick) or (
                side == "SHORT" and cand > mark + 2 * tick and cand < cur_sl_px - tick
            ):
                new_sl_px = round(cand, decimals)
                ratchet_reason = f"MARTINGALE_CONE_TIGHTEN_AT_{cur_r:+.2f}R"

        executed_order_id = None
        if new_sl_px is not None and execute:
            if cur_stop:
                kc.cancel_futures_order(cur_stop["id"])
            stop_dir = "down" if side == "LONG" else "up"
            r_sl = kc.place_futures_stop_order(
                symbol=sym,
                side=expected_close_side,
                size=abs_qty,
                stop_price=f"{new_sl_px:.{decimals}f}",
                stop_dir=stop_dir,
                order_type="market",
                stop_price_type="MP",
                reduce_only=True,
                leverage=str(p.get("leverage") or "3"),
                margin_mode="CROSS",
            )
            executed_order_id = (r_sl.get("data") or {}).get("orderId")

        staged_tp_order_id = None
        if cur_tp is None and init_stop_dist > 0 and execute:
            target_tp = (entry + tp_r_mult * init_stop_dist) if side == "LONG" else (entry - tp_r_mult * init_stop_dist)
            target_tp_round = round(round(target_tp / tick) * tick, decimals)
            if (side == "LONG" and target_tp_round > mark + tick) or (side == "SHORT" and target_tp_round < mark - tick):
                r_tp = kc.place_futures_order(
                    symbol=sym,
                    side=expected_close_side,
                    size=abs_qty,
                    order_type="limit",
                    price=f"{target_tp_round:.{decimals}f}",
                    leverage=str(p.get("leverage") or "3"),
                    post_only=True,
                    reduce_only=True,
                    margin_mode="CROSS",
                )
                staged_tp_order_id = (r_tp.get("data") or {}).get("orderId")
                if staged_tp_order_id:
                    cur_tp_px = target_tp_round
                    ratchet_reason = f"{ratchet_reason}+STAGED_{tp_r_mult}R_MAKER_TP({target_tp_round})"

        actions.append({
            "symbol": sym,
            "side": side,
            "qty_lots": abs_qty,
            "notional_usd": round(abs_qty * mult * mark, 2),
            "entry_price": entry,
            "mark_price": mark,
            "unrealised_pnl_usd": round(float(p.get("unrealisedPnl") or 0.0), 4),
            "current_r": round(cur_r, 3),
            "current_mp_stop": cur_sl_px,
            "current_maker_tp": cur_tp_px,
            "chandelier_15m_stop": chandelier_sl,
            "martingale_cone_status": cone_status,
            "martingale_early_cut_r": early_cut_r,
            "l2_eobi_20": eobi_20,
            "l2_flow_safety": flow_safety,
            "ratchet_thresholds": {
                "r_075_breakeven_px": round((entry + 0.05 * init_stop_dist) if side == "LONG" else (entry - 0.05 * init_stop_dist), decimals),
                "r_120_trail_px": round((entry + 0.60 * init_stop_dist) if side == "LONG" else (entry - 0.60 * init_stop_dist), decimals),
                "r_125_chandelier_px": chandelier_sl,
            },
            "action": ratchet_reason,
            "new_mp_stop": new_sl_px,
            "new_stop_order_id": executed_order_id,
            "staged_tp_order_id": staged_tp_order_id,
        })
    return actions


def audit_working_maker_orders(
    kc: KuCoinClient,
    mtf_map: Dict[str, Any],
    as_map: Dict[str, Any],
    mwu_map: Dict[str, Any],
    execute: bool = False,
) -> List[Dict[str, Any]]:
    execute = False  # HARD-DISABLED BY CHIEF OF STAFF
    active_orders = kc.get_futures_active_orders()
    stop_orders = kc.get_futures_stop_orders()
    audits = []

    for o in active_orders:
        if o.get("reduceOnly") or o.get("reduce_only"):
            continue
        sym = o["symbol"]
        oid = o["id"]
        side = "LONG" if str(o.get("side") or "").upper() == "BUY" else "SHORT"
        limit_px = float(o.get("price") or 0)
        size = int(o.get("size") or 0)

        mtf_item = mtf_map.get(sym) or {}
        score = int(mtf_item.get("mtf_confluence_score") or 0)
        mwu_item = mwu_map.get(sym) or {}
        mwu_score = float(mwu_item.get("mwu_posterior_score") or score)
        as_item = as_map.get(sym) or {}
        ex_rec = as_item.get("execution_recommendation") or {}
        opt_as_px = float(ex_rec.get("optimal_post_only_px") or limit_px)
        toxicity = float(ex_rec.get("toxicity_score") or 0.0)
        flow_safety = ex_rec.get("flow_safety", "FAVORABLE_MAKER_FLOW")
        mark = float(mtf_item.get("mark_price") or limit_px)

        drift_pct = abs(limit_px - opt_as_px) / opt_as_px * 100.0 if opt_as_px > 0 else 0.0
        dist_to_fill_pct = abs(mark - limit_px) / mark * 100.0 if mark > 0 else 0.0
        status = "OPTIMAL_QUEUE_PRIORITY_HOLD"
        new_order_id = None

        if score < 55 or mwu_score < 56.0 or (dist_to_fill_pct <= 0.20 and flow_safety == "TOXIC_ADVERSE_FLOW_WARNING"):
            status = f"CANCELLED_ADVERSE_OR_TOXIC(MTF={score},MWU={mwu_score:.1f},TOX={toxicity:.1f})"
            if execute:
                kc.cancel_futures_order(oid)
                for s in stop_orders:
                    if s.get("symbol") == sym:
                        kc.cancel_futures_order(s["id"])
        elif drift_pct >= 0.25 and ((side == "LONG" and opt_as_px < mark) or (side == "SHORT" and opt_as_px > mark)):
            status = f"AVELLANEDA_REPEGGED({opt_as_px})"
            if execute:
                kc.cancel_futures_order(oid)
                r_new = kc.place_futures_order(
                    symbol=sym,
                    side="buy" if side == "LONG" else "sell",
                    size=size,
                    order_type="limit",
                    price=str(opt_as_px),
                    leverage="2",
                    post_only=True,
                    reduce_only=False,
                    margin_mode="CROSS",
                )
                new_order_id = (r_new.get("data") or {}).get("orderId")

        audits.append({
            "order_id": oid,
            "symbol": sym,
            "side": side,
            "size_lots": size,
            "limit_price": limit_px,
            "avellaneda_optimal_px": opt_as_px,
            "drift_pct": round(drift_pct, 4),
            "mark_price": mark,
            "dist_to_fill_pct": round(dist_to_fill_pct, 2),
            "mtf_confluence_score": score,
            "mwu_posterior_score": mwu_score,
            "l2_toxicity_score": toxicity,
            "l2_flow_safety": flow_safety,
            "decision": status,
            "new_order_id": new_order_id,
        })
    return audits


def compute_dynamic_sizing_table(
    mtf_results: List[Dict[str, Any]],
    mwu_map: Dict[str, Any],
    as_map: Dict[str, Any],
    oi_map: Dict[str, Any],
    contracts: Dict[str, Any],
    evolved_params: Dict[str, float],
    kucoin_equity: float = 196.76,
) -> List[Dict[str, Any]]:
    """
    Computes Fractional-Kelly + Volatility-Targeted sizing using MWU Posterior Score,
    Evolved Genome Parameters, and Avellaneda-Stoikov Sub-Tick Maker Limit Pricing.
    """
    tp_r_mult = float(evolved_params.get("tp_r_mult") or 1.50)
    min_edge = float(evolved_params.get("min_composite_edge") or 75.0)

    table = []
    for r in mtf_results:
        sym = r["fut_symbol"]
        mwu_item = mwu_map.get(sym) or {}
        mwu_score = float(mwu_item.get("mwu_posterior_score") or r["mtf_confluence_score"])
        if r["mtf_confluence_score"] < 68 and mwu_score < (min_edge - 8.0):
            continue

        c_spec = contracts.get(sym) or {}
        mult = abs(float(c_spec.get("multiplier") or 0.01))
        tick = float(c_spec.get("tickSize") or 0.01)
        decimals = max(0, -int(round(math.log10(tick)))) if 0 < tick < 1 else 0

        as_item = as_map.get(sym) or {}
        ex_rec = as_item.get("execution_recommendation") or {}
        raw_entry = float(r["precision_levels"]["maker_entry_px"] or r.get("mark_price") or 0.0)
        raw_sl = float(r["precision_levels"]["mp_stop_px"] or 0.0)
        raw_stop_pct = abs(raw_entry - raw_sl) / raw_entry if raw_entry > 0 and raw_sl > 0 else 0.0185
        stop_pct = min(max(raw_stop_pct, 0.0185), 0.035)  # Enforce 1.85% - 3.50% structural stop distance (matching Propr)

        entry = float(ex_rec.get("optimal_post_only_px") or raw_entry)
        if entry <= 0 or mult <= 0:
            continue
        stop_dist = max(tick * 4, entry * stop_pct)
        sl = (entry - stop_dist) if r["target_side"] == "LONG" else (entry + stop_dist)
        tp = (entry + tp_r_mult * stop_dist) if r["target_side"] == "LONG" else (entry - tp_r_mult * stop_dist)

        # Scale risk budget by MWU Posterior Score (0.80% to 1.15% of equity, max $120 notional per leg)
        risk_pct = 0.008 + max(0.0, (mwu_score - 65.0) / 30.0) * 0.0035
        target_risk_usd = min(2.25, round(kucoin_equity * risk_pct, 2))
        risk_per_lot = stop_dist * mult
        max_lots_notional = max(1, int(120.0 / max(1e-9, entry * mult)))
        lots = min(max_lots_notional, max(1, int(round(target_risk_usd / risk_per_lot))))
        actual_risk_usd = round(lots * risk_per_lot, 2)
        notional_usd = round(lots * mult * entry, 2)

        # Round-trip Maker fee drag as % of 1R
        rt_maker_fee_usd = notional_usd * 0.0004
        fee_drag_pct_of_r = round((rt_maker_fee_usd / max(0.01, actual_risk_usd)) * 100.0, 2)

        gate_res = unified_ledger.check_cross_book_gate("KUCOIN", sym, r["target_side"], actual_risk_usd)
        oi_item = oi_map.get(sym) or {}

        table.append({
            "symbol": sym,
            "side": r["target_side"],
            "mtf_score": r["mtf_confluence_score"],
            "mwu_posterior_score": mwu_score,
            "evolved_min_edge_pass": mwu_score >= min_edge,
            "posterior_win_prob": mwu_item.get("posterior_win_prob"),
            "lean_6_axioms_ok": bool(mwu_item.get("lean_6_axioms_ok", False)),
            "tier": mwu_item.get("mwu_verdict") or r["setup_tier"],
            "wf_net_er": r["walk_forward"]["net_expectancy_r"],
            "avellaneda_maker_entry_px": round(entry, decimals),
            "placement_mode": ex_rec.get("placement_mode", "STRUCTURAL"),
            "mp_stop_px": round(sl, decimals),
            "maker_tp_px": round(tp, decimals),
            "tp_r_mult": tp_r_mult,
            "lots": lots,
            "notional_usd": notional_usd,
            "actual_risk_usd": actual_risk_usd,
            "fee_drag_pct_of_r": fee_drag_pct_of_r,
            "oi_regime": oi_item.get("oi_regime", "NEUTRAL"),
            "l2_flow_safety": ex_rec.get("flow_safety", "FAVORABLE_MAKER_FLOW"),
            "cross_book_gate_ok": gate_res["ok"],
            "cross_book_gate_reasons": gate_res["reasons"],
        })
    table.sort(key=lambda x: x["mwu_posterior_score"], reverse=True)
    return table


def run_precision_engine(execute: bool = False) -> Dict[str, Any]:
    t0 = time.time()
    kc = KuCoinClient()
    try:
        unified_ledger.build_unified_book()
    except Exception:
        pass

    # Phase 1: Parallel execution of MTF Scanner + Auxiliary Institutional Modules
    with ThreadPoolExecutor(max_workers=8) as ex:
        f_contracts = ex.submit(
            lambda: {
                c["symbol"]: c
                for c in (kc.request("https://api-futures.kucoin.com", "GET", "/api/v1/contracts/active", auth=False).get("data") or [])
            }
        )
        f_mtf = ex.submit(mtf_scanner.run_mtf_scan)
        f_oi = ex.submit(
            load_cached_or_run,
            "/home/box/desk/kucoin/latest_liquidation_oi_sniper.json",
            liquidation_oi_sniper.run_liquidation_oi_sniper,
            90.0,
        )
        f_fund = ex.submit(
            load_cached_or_run,
            "/home/box/desk/kucoin/latest_funding_harvester.json",
            funding_clock_harvester.run_funding_clock_harvester,
            90.0,
        )
        f_whales = ex.submit(
            load_cached_or_run,
            "/home/box/desk/kucoin/latest_polymarket_whales.json",
            polymarket_whale_tracker.run_polymarket_whale_tracker,
            120.0,
        )
        f_deribit = ex.submit(
            load_cached_or_run,
            "/home/box/desk/kucoin/latest_deribit_options.json",
            deribit_options_edge.run_deribit_options_edge,
            120.0,
        )
        f_kalshi = ex.submit(
            load_cached_or_run,
            "/home/box/desk/latest_kalshi_polymarket_consensus.json",
            kalshi_polymarket_consensus.run_kalshi_polymarket_consensus,
            120.0,
        )
        f_ms = ex.submit(
            load_cached_or_run,
            "/home/box/desk/latest_kucoin_multiservice.json",
            kucoin_multiservice_alpha.run_kucoin_multiservice_alpha,
            120.0,
        )
        f_genome = ex.submit(get_active_evolved_genome, 600.0)

        mtf_data = f_mtf.result()
        contracts = f_contracts.result()

        # Phase 2: As soon as MTF finishes, run OpenAI Math Frontiers & Avellaneda-Stoikov concurrently
        f_math = ex.submit(quant_math_frontiers.run_quant_math_frontiers, mtf_data["results"])
        f_as = ex.submit(avellaneda_stoikov_exec.run_avellaneda_stoikov_scan, mtf_data["results"])

        oi_sniper = f_oi.result()
        funding_harvester = f_fund.result()
        pm_whales = f_whales.result()
        deribit_options = f_deribit.result()
        kalshi_consensus = f_kalshi.result()
        multiservice_alpha = f_ms.result()
        evolved_genome_report = f_genome.result()
        math_frontiers = f_math.result()
        as_scan = f_as.result()

    # Phase 3: Update Online Multiplicative-Weights (MWU / Hedge) Bayesian Regime
    mwu_regime = bayesian_regime_mwu.run_bayesian_regime_mwu()

    mtf_map = {r["fut_symbol"]: r for r in mtf_data["results"]}
    math_map = math_frontiers.get("results") if isinstance(math_frontiers.get("results"), dict) else {}
    as_map = {r["symbol"]: r for r in (as_scan.get("results") or [])}
    oi_map = {r["fut_symbol"]: r for r in (oi_sniper.get("results") or [])}
    mwu_map = {r["fut_symbol"]: r for r in (mwu_regime.get("ranked_assets") or [])}
    evolved_params = evolved_genome_report.get("active_genome") or genetic_evolution_engine.INCUMBENT_BASELINE_GENOME

    # Phase 4: Audit & Ratchet Live Open Positions and Working Maker Orders
    ratchet_actions = evaluate_and_ratchet_positions(
        kc, mtf_map, math_map, as_map, evolved_params, contracts, execute=execute
    )
    order_audits = audit_working_maker_orders(kc, mtf_map, as_map, mwu_map, execute=execute)
    sizing_table = compute_dynamic_sizing_table(
        mtf_data["results"], mwu_map, as_map, oi_map, contracts, evolved_params
    )

    out = {
        "as_of": mtf_data["as_of"],
        "edt_now": mtf_data["edt_now"],
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "evolved_genome": evolved_genome_report,
        "funding_clock": funding_harvester.get("settlement_clock"),
        "live_position_carry_audit": funding_harvester.get("live_position_carry_audit"),
        "chandelier_ratchet_audit": ratchet_actions,
        "working_maker_order_audit": order_audits,
        "a_grade_sizing_table": sizing_table,
        "mwu_regime": mwu_regime,
        "liquidation_oi_sniper": {
            "summary": oi_sniper.get("summary"),
            "active_signals": oi_sniper.get("active_signals"),
            "cross_venue_dislocations": oi_sniper.get("cross_venue_dislocations"),
        },
        "avellaneda_stoikov": as_scan.get("results"),
        "funding_harvester_top": funding_harvester.get("top_carry_tailwinds"),
        "polymarket_whales": {
            "summary": pm_whales.get("summary"),
            "crypto_barrier_whales": pm_whales.get("crypto_barrier_whales"),
            "sports_smart_money_sweeps": (pm_whales.get("sports_smart_money_sweeps") or [])[:10],
            "top_mega_whale_prints": (pm_whales.get("top_mega_whale_prints") or [])[:12],
        },
        "openai_math_frontiers": math_frontiers,
        "deribit_options": deribit_options,
        "multiservice_alpha": multiservice_alpha,
        "kalshi_consensus_summary": {
            "summary": kalshi_consensus.get("summary") or kalshi_consensus.get("kalshi_v2_open_markets_snapshot"),
            "crypto_macro_consensus": ((kalshi_consensus.get("crypto_and_macro_consensus") or {}).get("cross_venue_consensus_pairs") or [])[:12],
            "sports_game_consensus": ((kalshi_consensus.get("sports_dual_venue_consensus") or {}).get("matched_games") or [])[:20],
        },
    }
    try:
        fs_stats = feature_store.log_crypto_scan_and_equity(
            mwu_ranked_assets=mwu_regime.get("ranked_assets") or [],
            mtf_map=mtf_map,
            oi_map=oi_map,
            deribit_summary=deribit_options.get("summary") or {},
            timestamp=out["as_of"],
        )
        out["feature_store_stats"] = fs_stats
    except Exception as e_fs:
        out["feature_store_stats"] = {"error": str(e_fs)}

    with open("/home/box/desk/kucoin/latest_precision_report.json", "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    try:
        payload = json.dumps({
            "as_of": out["as_of"],
            "edt_now": out["edt_now"],
            "elapsed_ms": out["elapsed_ms"],
            "evolved_genome": out["evolved_genome"],
            "funding_clock": out["funding_clock"],
            "live_position_carry_audit": out["live_position_carry_audit"],
            "chandelier_ratchet_audit": out["chandelier_ratchet_audit"],
            "working_maker_order_audit": out["working_maker_order_audit"],
            "a_grade_sizing_table": out["a_grade_sizing_table"],
            "mtf_hierarchy": mtf_data["results"],
            "mwu_regime": mwu_regime,
            "liquidation_oi_sniper": out["liquidation_oi_sniper"],
            "avellaneda_stoikov": out["avellaneda_stoikov"],
            "funding_harvester_top": out["funding_harvester_top"],
            "polymarket_whales": out["polymarket_whales"],
            "openai_math_frontiers": math_frontiers,
            "deribit_options": deribit_options,
            "multiservice_alpha": multiservice_alpha,
            "kalshi_consensus": out["kalshi_consensus_summary"],
            "feature_store_stats": out.get("feature_store_stats"),
        }).encode("utf-8")
        req = urllib.request.Request(
            "https://kucoin-desk-scan.sonia-3e6f56284b.workers.dev/sync-mtf",
            data=payload,
            headers={"Content-Type": "application/json", "User-Agent": "PrecisionEngine/3.5"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=6).read()
    except Exception:
        pass
    return out


if __name__ == "__main__":
    rep = run_precision_engine(execute=False)
    print(f"=== Institutional Precision & Proficiency Engine v3.5 ({rep['elapsed_ms']} ms) ===")
    ms = rep["mwu_regime"]["mwu_state"]
    w = ms["weights"]
    eg = (rep.get("evolved_genome") or {}).get("active_genome") or {}
    print(
        f"MWU Weights (Step {ms['update_steps']}): MTF={w['w_mtf']*100:.1f}%, IND={w['w_ind']*100:.1f}%, "
        f"MATH={w['w_math']*100:.1f}%, FLOW={w['w_flow']*100:.1f}%, GEOM={w['w_geom']*100:.1f}% | "
        f"Evolved Genome: Stop={eg.get('atr_stop_mult')}xATR, TP={eg.get('tp_r_mult')}R, Trail={eg.get('trail_activation_r')}R, MinEdge={eg.get('min_composite_edge')}"
    )
    print("\n1. Open Position Chandelier / Martingale Cone / Maker-TP Audit:")
    for a in rep["chandelier_ratchet_audit"]:
        print(
            f"   - {a['symbol']} {a['side']} ({a['qty_lots']} lots, ${a['notional_usd']} notional | entry={a['entry_price']} mark={a['mark_price']} | "
            f"uPnL=${a['unrealised_pnl_usd']:+.2f} [{a['current_r']:+.3f}R]): MP_SL={a['current_mp_stop']} Maker_TP={a['current_maker_tp']} | "
            f"Cone={a['martingale_cone_status']} | EOBI_20={a['l2_eobi_20']:+.3f} ({a['l2_flow_safety']}) -> {a['action']}"
        )
    print("\n2. Working Maker Order Queue-Priority & Adverse-Selection Audit:")
    if not rep["working_maker_order_audit"]:
        print("   - (No unfilled entry limit orders active)")
    for o in rep["working_maker_order_audit"]:
        print(
            f"   - {o['symbol']} {o['side']} ({o['size_lots']} lots @ {o['limit_price']} vs AS_Opt={o['avellaneda_optimal_px']} | "
            f"MWU={o['mwu_posterior_score']:.1f} | Tox={o['l2_toxicity_score']:.1f}): {o['decision']}"
        )
    print("\n3. MWU Bayesian + Avellaneda-Stoikov + Evolved Genome Dynamic Sizing Table:")
    for s in rep["a_grade_sizing_table"][:10]:
        g_str = "GATE_OK" if s["cross_book_gate_ok"] else f"VETO({','.join(s['cross_book_gate_reasons'])})"
        print(
            f"   - {s['symbol']:<10} {s['side']:<6} MWU={s['mwu_posterior_score']:>5.1f} (MTF={s['mtf_score']}) | "
            f"AS_Entry={s['avellaneda_maker_entry_px']} MP_SL={s['mp_stop_px']} TP={s['maker_tp_px']} ({s['tp_r_mult']}R) | "
            f"Lots={s['lots']} (${s['notional_usd']} notional, 1R=${s['actual_risk_usd']}, FeeDrag={s['fee_drag_pct_of_r']}%) | {s['oi_regime']} | {g_str}"
        )
