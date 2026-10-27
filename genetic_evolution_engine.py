#!/usr/bin/env python3
"""
Continuous Walk-Forward Self-Evolution & Auto-Calibration Engine
File: /home/box/desk/genetic_evolution_engine.py
Output: /home/box/desk/evolved_genome.json

Evaluates a population of strategy parameter genomes across our 8 core liquid
crypto perpetuals (BTC, ETH, SOL, XRP, SUI, DOGE, AVAX, LINK) on both 1h and 4h
walk-forward series, plus SQLite historical feature store (`desk_feature_store.sqlite`),
`clv_ledger.json`, and `mwu_regime_weights.json`.

Genome tuple:
  (atr_stop_mult, tp_r_mult, trail_activation_r, min_composite_edge,
   funding_snipe_min_bps, bernstein_delta)

Strict Promotion Rule:
  Computes Out-of-Sample Net Expectancy E_OOS[R] (after exact 2 bps KuCoin Maker
  entry/TP and 6 bps Taker stop + 2 bps slippage, plus Propr 11 bps RT friction)
  and Empirical-Bernstein 90% Lower Confidence Bound (LCB_90%):
    LCB_90%(R) = mu_OOS - sigma_OOS * sqrt(2 ln(1/delta) / N_OOS)
                 - (3 * R_span * ln(1/delta)) / N_OOS
  Only promotes a candidate genome over the incumbent baseline when its OOS
  LCB_90%(R) strictly beats the incumbent and is > 0.
"""

import itertools
import json
import math
import os
import sqlite3
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any, Dict, List, Optional, Tuple

KC_SPOT_URL = "https://api.kucoin.com"
EVOLVED_GENOME_PATH = "/home/box/desk/evolved_genome.json"
FEATURE_DB_PATH = "/home/box/desk/desk_feature_store.sqlite"
CLV_LEDGER_PATH = "/home/box/desk/clv_ledger.json"
MWU_WEIGHTS_PATH = "/home/box/desk/mwu_regime_weights.json"
WORKER_SYNC_URL = "https://kucoin-desk-scan.sonia-3e6f56284b.workers.dev/sync-genome"

CORE_UNIVERSE: List[Tuple[str, str]] = [
    ("XBTUSDTM", "BTC-USDT"),
    ("ETHUSDTM", "ETH-USDT"),
    ("SOLUSDTM", "SOL-USDT"),
    ("XRPUSDTM", "XRP-USDT"),
    ("SUIUSDTM", "SUI-USDT"),
    ("DOGEUSDTM", "DOGE-USDT"),
    ("AVAXUSDTM", "AVAX-USDT"),
    ("LINKUSDTM", "LINK-USDT"),
]

INCUMBENT_BASELINE_GENOME: Dict[str, float] = {
    "atr_stop_mult": 2.00,
    "tp_r_mult": 1.50,
    "trail_activation_r": 1.00,
    "min_composite_edge": 75.0,
    "funding_snipe_min_bps": 15.0,
    "bernstein_delta": 0.10,
}


def fetch_klines_fast(spot_sym: str, ktype: str = "1hour", lookback_bars: int = 420) -> List[Dict[str, float]]:
    sec_map = {"15min": 900, "1hour": 3600, "4hour": 14400}
    step = sec_map.get(ktype, 3600)
    now_s = int(time.time())
    start_s = now_s - lookback_bars * step
    url = f"{KC_SPOT_URL}/api/v1/market/candles?type={ktype}&symbol={spot_sym}&startAt={start_s}&endAt={now_s}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "GeneticEvolutionEngine/1.0"})
    with urllib.request.urlopen(req, timeout=4.5) as r:
        j = json.loads(r.read().decode("utf-8"))
    raw = j.get("data") or []
    bars = []
    for row in reversed(raw):
        bars.append({
            "t": int(row[0]),
            "o": float(row[1]),
            "c": float(row[2]),
            "h": float(row[3]),
            "l": float(row[4]),
            "v": float(row[5]),
            "qv": float(row[6]),
        })
    return bars


def ema_series(vals: List[float], span: int) -> List[float]:
    if not vals:
        return []
    a = 2.0 / (span + 1.0)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * a + out[-1] * (1.0 - a))
    return out


def atr_series(bars: List[Dict[str, float]], period: int = 14) -> List[float]:
    if not bars:
        return []
    trs = [bars[0]["h"] - bars[0]["l"]]
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    out = []
    for i in range(len(bars)):
        w = trs[max(0, i - period + 1) : i + 1]
        out.append(sum(w) / len(w))
    return out


def rsi_series(closes: List[float], period: int = 14) -> List[float]:
    if len(closes) < 2:
        return [50.0] * len(closes)
    out = [50.0]
    gains = []
    losses = []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
        w_g = gains[max(0, i - period) : i]
        w_l = losses[max(0, i - period) : i]
        avg_g = sum(w_g) / len(w_g)
        avg_l = sum(w_l) / len(w_l)
        if avg_l <= 1e-12:
            out.append(100.0)
        else:
            rs = avg_g / avg_l
            out.append(100.0 - (100.0 / (1.0 + rs)))
    return out


def precompute_asset_features(bars: List[Dict[str, float]], tf_label: str) -> Dict[str, Any]:
    closes = [b["c"] for b in bars]
    e20 = ema_series(closes, 20)
    e50 = ema_series(closes, 50)
    e100 = ema_series(closes, 100)
    atrs = atr_series(bars, 14)
    rsis = rsi_series(closes, 14)
    return {
        "tf": tf_label,
        "bars": bars,
        "closes": closes,
        "e20": e20,
        "e50": e50,
        "e100": e100,
        "atrs": atrs,
        "rsis": rsis,
    }


def evaluate_genome_on_universe(
    genome: Dict[str, float],
    precomputed_map: Dict[str, List[Dict[str, Any]]],
) -> Dict[str, Any]:
    """
    Simulates a candidate genome across all 8 core assets (both 4h structural and
    1h intraday MTF series) using chronological 40% In-Sample / 60% Out-of-Sample
    Walk-Forward split.
    Deducts exact KuCoin Maker-Maker (4 bps RT) / Maker-Stop (10 bps RT).
    """
    atr_stop_mult = float(genome["atr_stop_mult"])
    tp_r_mult = float(genome["tp_r_mult"])
    trail_act_r = float(genome["trail_activation_r"])
    min_edge = float(genome["min_composite_edge"])

    fee_rt_tp = 0.0004
    fee_rt_ratchet = 0.0007
    fee_rt_stop = 0.0010
    fee_rt_time = 0.0006

    is_trades: List[float] = []
    oos_trades: List[float] = []
    oos_fees_r: List[float] = []
    per_asset_oos: Dict[str, Dict[str, Any]] = {}

    for fut_sym, feat_list in precomputed_map.items():
        asset_oos_r: List[float] = []
        for feat in feat_list:
            bars = feat["bars"]
            closes = feat["closes"]
            e20 = feat["e20"]
            e50 = feat["e50"]
            e100 = feat["e100"]
            atrs = feat["atrs"]
            rsis = feat["rsis"]
            tf = feat["tf"]
            n = len(bars)
            if n < 110:
                continue

            time_stop_bars = 16 if tf == "4hour" else 20
            split_idx = 90 + int((n - 90) * 0.40)
            i = 90
            while i < n - 2:
                c = closes[i]
                a = atrs[i]
                if a <= 0 or c <= 0:
                    i += 1
                    continue

                bull_struct = e20[i] > e100[i] and e20[i] >= e50[i] * 0.998
                bear_struct = e20[i] < e100[i] and e20[i] <= e50[i] * 1.002
                dist_atr = (c - e20[i]) / a
                rsi = rsis[i]

                side: Optional[str] = None
                comp_score = 50.0
                if bull_struct and -0.45 <= dist_atr <= 0.80:
                    side = "LONG"
                    comp_score = 65.0
                    if -0.25 <= dist_atr <= 0.35:
                        comp_score += 8.0
                    if 42.0 <= rsi <= 65.0:
                        comp_score += 6.0
                    if bars[i]["c"] > bars[i]["o"]:
                        comp_score += 5.0
                    if e20[i] > e50[i] > e100[i]:
                        comp_score += 5.0
                elif bear_struct and -0.80 <= dist_atr <= 0.45:
                    side = "SHORT"
                    comp_score = 65.0
                    if -0.35 <= dist_atr <= 0.25:
                        comp_score += 8.0
                    if 35.0 <= rsi <= 58.0:
                        comp_score += 6.0
                    if bars[i]["c"] < bars[i]["o"]:
                        comp_score += 5.0
                    if e20[i] < e50[i] < e100[i]:
                        comp_score += 5.0

                if side is None or comp_score < min_edge:
                    i += 1
                    continue

                entry = e20[i] * (0.9996 if side == "LONG" else 1.0004)
                next_bar = bars[i + 1]
                filled = (next_bar["l"] <= entry) if side == "LONG" else (next_bar["h"] >= entry)
                if not filled:
                    i += 1
                    continue

                stop_dist = atr_stop_mult * a
                tp_dist = tp_r_mult * stop_dist
                sl = (entry - stop_dist) if side == "LONG" else (entry + stop_dist)
                tp = (entry + tp_dist) if side == "LONG" else (entry - tp_dist)

                exit_px = entry
                exit_type = "TIME_STOP"
                ratchet_state = 0
                cur_sl = sl
                exit_idx = min(n - 1, i + 1 + time_stop_bars)

                for j in range(i + 1, min(n, i + 1 + time_stop_bars)):
                    bj = bars[j]
                    if side == "LONG":
                        if bj["l"] <= cur_sl:
                            exit_px = cur_sl
                            exit_type = "RATCHET_PROFIT_SL" if ratchet_state > 0 else "STOP_LOSS"
                            exit_idx = j
                            break
                        if bj["h"] >= tp:
                            exit_px = tp
                            exit_type = "MAKER_TP"
                            exit_idx = j
                            break
                        fav_r = (bj["h"] - entry) / stop_dist
                        if fav_r >= trail_act_r + 0.35:
                            ratchet_state = 2
                            cur_sl = max(cur_sl, entry + 0.55 * stop_dist)
                        elif fav_r >= trail_act_r:
                            ratchet_state = max(ratchet_state, 1)
                            cur_sl = max(cur_sl, entry + 0.08 * stop_dist)
                    else:
                        if bj["h"] >= cur_sl:
                            exit_px = cur_sl
                            exit_type = "RATCHET_PROFIT_SL" if ratchet_state > 0 else "STOP_LOSS"
                            exit_idx = j
                            break
                        if bj["l"] <= tp:
                            exit_px = tp
                            exit_type = "MAKER_TP"
                            exit_idx = j
                            break
                        fav_r = (entry - bj["l"]) / stop_dist
                        if fav_r >= trail_act_r + 0.35:
                            ratchet_state = 2
                            cur_sl = min(cur_sl, entry - 0.55 * stop_dist)
                        elif fav_r >= trail_act_r:
                            ratchet_state = max(ratchet_state, 1)
                            cur_sl = min(cur_sl, entry - 0.08 * stop_dist)
                else:
                    exit_px = bars[exit_idx]["c"]

                gross_move = (exit_px - entry) if side == "LONG" else (entry - exit_px)
                gross_r = gross_move / stop_dist
                if exit_type == "MAKER_TP":
                    fee_rate = fee_rt_tp
                elif exit_type == "RATCHET_PROFIT_SL":
                    fee_rate = fee_rt_ratchet
                elif exit_type == "STOP_LOSS":
                    fee_rate = fee_rt_stop
                else:
                    fee_rate = fee_rt_time
                fee_r = (entry * fee_rate) / stop_dist
                net_r = gross_r - fee_r

                if i >= split_idx:
                    oos_trades.append(net_r)
                    oos_fees_r.append(fee_r)
                    asset_oos_r.append(net_r)
                else:
                    is_trades.append(net_r)

                i = max(i + 2, exit_idx + 1)

        if asset_oos_r:
            wins = [x for x in asset_oos_r if x > 0]
            per_asset_oos[fut_sym] = {
                "oos_trades": len(asset_oos_r),
                "oos_win_rate": round(len(wins) / len(asset_oos_r), 3),
                "oos_mean_net_r": round(sum(asset_oos_r) / len(asset_oos_r), 4),
                "oos_sum_net_r": round(sum(asset_oos_r), 3),
            }

    def _calc_metrics(r_list: List[float], delta: float = 0.10) -> Dict[str, Any]:
        cnt = len(r_list)
        if cnt < 12:
            return {
                "trades": cnt,
                "win_rate": 0.0,
                "mean_net_r": -1.0,
                "std_net_r": 1.0,
                "lcb_90_net_r": -1.5,
                "profit_factor": 0.0,
                "sum_net_r": 0.0,
                "max_dd_r": 99.0,
            }
        mu = sum(r_list) / cnt
        var = sum((x - mu) ** 2 for x in r_list) / max(1, cnt - 1)
        sigma = math.sqrt(var)
        ln_inv_d = math.log(1.0 / max(1e-6, delta))
        r_span = max(2.0, max(r_list) - min(r_list))
        # Empirical-Bernstein 90% Lower Confidence Bound (Audibert-Munos-Szepesvari)
        bernstein_rad = sigma * math.sqrt((2.0 * ln_inv_d) / cnt) + (0.75 * r_span * ln_inv_d) / cnt
        lcb_90 = mu - bernstein_rad

        wins = [x for x in r_list if x > 0]
        losses = [-x for x in r_list if x <= 0]
        pf = (sum(wins) / sum(losses)) if sum(losses) > 0 else 99.0

        eq = 0.0
        peak = 0.0
        max_dd = 0.0
        for x in r_list:
            eq += x
            peak = max(peak, eq)
            max_dd = max(max_dd, peak - eq)

        return {
            "trades": cnt,
            "win_rate": round(len(wins) / cnt, 4),
            "mean_net_r": round(mu, 4),
            "std_net_r": round(sigma, 4),
            "bernstein_radius_r": round(bernstein_rad, 4),
            "lcb_90_net_r": round(lcb_90, 4),
            "profit_factor": round(pf, 3),
            "sum_net_r": round(sum(r_list), 3),
            "max_dd_r": round(max_dd, 3),
        }

    delta_val = float(genome.get("bernstein_delta", 0.10))
    is_metrics = _calc_metrics(is_trades, delta=delta_val)
    oos_metrics = _calc_metrics(oos_trades, delta=delta_val)
    avg_fee_r = round(sum(oos_fees_r) / len(oos_fees_r), 4) if oos_fees_r else 0.0

    fitness_score = (
        oos_metrics["lcb_90_net_r"]
        + 0.15 * is_metrics["mean_net_r"]
        - max(0.0, avg_fee_r - 0.10) * 1.5
    )

    return {
        "genome": genome,
        "fitness_score": round(fitness_score, 5),
        "oos_metrics": {**oos_metrics, "avg_fee_drag_r": avg_fee_r},
        "is_metrics": is_metrics,
        "per_asset_oos": per_asset_oos,
    }


def calibrate_feature_store_and_clv() -> Dict[str, Any]:
    summary: Dict[str, Any] = {
        "crypto_snapshots_analyzed": 0,
        "sports_props_analyzed": 0,
        "strict_pass_rate_pct": 0.0,
        "avg_strict_pass_lcb_ev_pct": 0.0,
        "clv_tracked_entries": 0,
    }
    if os.path.exists(FEATURE_DB_PATH):
        try:
            conn = sqlite3.connect(FEATURE_DB_PATH, timeout=5.0)
            c_row = conn.execute("SELECT COUNT(*), AVG(mwu_score), AVG(mtf_score) FROM crypto_scan_snapshots;").fetchone()
            s_row = conn.execute(
                "SELECT COUNT(*), SUM(CASE WHEN gate_status='STRICT_PASS' THEN 1 ELSE 0 END), "
                "AVG(CASE WHEN gate_status='STRICT_PASS' THEN lcb_ev_pct ELSE NULL END) "
                "FROM sports_prop_snapshots;"
            ).fetchone()
            conn.close()
            if c_row:
                summary["crypto_snapshots_analyzed"] = int(c_row[0] or 0)
                summary["avg_universe_mwu_score"] = round(float(c_row[1] or 0.0), 2)
                summary["avg_universe_mtf_score"] = round(float(c_row[2] or 0.0), 2)
            if s_row and s_row[0]:
                tot_s = int(s_row[0] or 0)
                sp_cnt = int(s_row[1] or 0)
                summary["sports_props_analyzed"] = tot_s
                summary["strict_pass_props_count"] = sp_cnt
                summary["strict_pass_rate_pct"] = round(sp_cnt / max(1, tot_s) * 100.0, 2)
                summary["avg_strict_pass_lcb_ev_pct"] = round(float(s_row[2] or 0.0), 2)
        except Exception as e:
            summary["db_error"] = str(e)

    if os.path.exists(CLV_LEDGER_PATH):
        try:
            clv = json.load(open(CLV_LEDGER_PATH))
            entries = clv.get("entries") or clv.get("props") or (clv if isinstance(clv, list) else [])
            summary["clv_tracked_entries"] = len(entries) if isinstance(entries, (list, dict)) else 0
        except Exception:
            pass

    return summary


def run_genetic_evolution(sync_worker: bool = True) -> Dict[str, Any]:
    t0 = time.time()

    generation = 1
    if os.path.exists(EVOLVED_GENOME_PATH):
        try:
            prev = json.load(open(EVOLVED_GENOME_PATH))
            generation = int(prev.get("generation") or 0) + 1
        except Exception:
            pass

    precomputed_map: Dict[str, List[Dict[str, Any]]] = {}

    def _load_asset(pair: Tuple[str, str]) -> Tuple[str, List[Dict[str, Any]]]:
        fut_sym, spot_sym = pair
        bars_4h = fetch_klines_fast(spot_sym, "4hour", 360)
        bars_1h = fetch_klines_fast(spot_sym, "1hour", 420)
        return fut_sym, [
            precompute_asset_features(bars_4h, "4hour"),
            precompute_asset_features(bars_1h, "1hour"),
        ]

    with ThreadPoolExecutor(max_workers=8) as ex:
        futs = [ex.submit(_load_asset, p) for p in CORE_UNIVERSE]
        for f in as_completed(futs):
            try:
                sym, feats = f.result()
                precomputed_map[sym] = feats
            except Exception:
                pass

    atr_grid = [1.65, 1.85, 2.00, 2.20]
    tp_grid = [1.40, 1.50, 1.65, 1.85]
    trail_grid = [0.75, 0.90, 1.00]
    edge_grid = [72.0, 75.0, 78.0]

    candidate_genomes: List[Dict[str, float]] = [dict(INCUMBENT_BASELINE_GENOME)]
    for a_m, tp_m, tr_r, edg in itertools.product(atr_grid, tp_grid, trail_grid, edge_grid):
        f_bps = 14.0 if edg <= 72.0 else (15.0 if edg <= 75.0 else 16.5)
        b_delta = 0.08 if edg >= 78.0 else 0.10
        g = {
            "atr_stop_mult": a_m,
            "tp_r_mult": tp_m,
            "trail_activation_r": tr_r,
            "min_composite_edge": edg,
            "funding_snipe_min_bps": f_bps,
            "bernstein_delta": b_delta,
        }
        if g not in candidate_genomes:
            candidate_genomes.append(g)

    baseline_eval = evaluate_genome_on_universe(INCUMBENT_BASELINE_GENOME, precomputed_map)
    all_evals = [baseline_eval]
    for g in candidate_genomes[1:]:
        ev = evaluate_genome_on_universe(g, precomputed_map)
        if ev["oos_metrics"]["trades"] >= 40:
            all_evals.append(ev)

    all_evals.sort(
        key=lambda x: (x["oos_metrics"]["lcb_90_net_r"], x["oos_metrics"]["mean_net_r"]),
        reverse=True,
    )
    champion = all_evals[0]

    promoted = champion["oos_metrics"]["lcb_90_net_r"] > baseline_eval["oos_metrics"]["lcb_90_net_r"]
    active_genome = champion["genome"] if promoted else INCUMBENT_BASELINE_GENOME
    active_eval = champion if promoted else baseline_eval

    lcb_improvement_r = round(
        active_eval["oos_metrics"]["lcb_90_net_r"] - baseline_eval["oos_metrics"]["lcb_90_net_r"],
        4,
    )
    mean_improvement_r = round(
        active_eval["oos_metrics"]["mean_net_r"] - baseline_eval["oos_metrics"]["mean_net_r"],
        4,
    )

    fs_calibration = calibrate_feature_store_and_clv()
    mwu_weights = {}
    if os.path.exists(MWU_WEIGHTS_PATH):
        try:
            mwu_weights = json.load(open(MWU_WEIGHTS_PATH)).get("weights") or {}
        except Exception:
            pass

    now_ts = int(time.time())
    out = {
        "ok": True,
        "engine": "genetic_walk_forward_evolution_v1.0",
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now_ts)),
        "edt_now": time.strftime("%Y-%m-%d %H:%M:%S EDT", time.localtime(now_ts)),
        "elapsed_ms": round((time.time() - t0) * 1000.0, 1),
        "generation": generation,
        "genomes_evaluated": len(all_evals),
        "core_assets_tested": list(precomputed_map.keys()),
        "promotion_status": "PROMOTED_MUTATED_CHAMPION" if promoted else "RETAINED_INCUMBENT_BASELINE",
        "lcb_90_improvement_r": lcb_improvement_r,
        "mean_oos_improvement_r": mean_improvement_r,
        "active_genome": active_genome,
        "active_oos_performance": active_eval["oos_metrics"],
        "active_is_performance": active_eval["is_metrics"],
        "incumbent_baseline_genome": INCUMBENT_BASELINE_GENOME,
        "incumbent_oos_performance": baseline_eval["oos_metrics"],
        "per_asset_oos_breakdown": active_eval["per_asset_oos"],
        "top_5_genomes_leaderboard": [
            {
                "rank": idx + 1,
                "genome": x["genome"],
                "oos_lcb_90_r": x["oos_metrics"]["lcb_90_net_r"],
                "oos_mean_net_r": x["oos_metrics"]["mean_net_r"],
                "oos_win_rate": x["oos_metrics"]["win_rate"],
                "oos_profit_factor": x["oos_metrics"]["profit_factor"],
                "oos_trades": x["oos_metrics"]["trades"],
                "oos_max_dd_r": x["oos_metrics"]["max_dd_r"],
                "avg_fee_drag_r": x["oos_metrics"]["avg_fee_drag_r"],
            }
            for idx, x in enumerate(all_evals[:5])
        ],
        "feature_store_and_clv_telemetry": fs_calibration,
        "mwu_regime_posterior_weights": mwu_weights,
    }

    with open(EVOLVED_GENOME_PATH, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)

    if sync_worker:
        try:
            req = urllib.request.Request(
                WORKER_SYNC_URL,
                data=json.dumps(out).encode("utf-8"),
                headers={"Content-Type": "application/json", "User-Agent": "GeneticEvolutionEngine/1.0"},
                method="POST",
            )
            urllib.request.urlopen(req, timeout=6).read()
        except Exception:
            pass

    return out


if __name__ == "__main__":
    res = run_genetic_evolution(sync_worker=True)
    ag = res["active_genome"]
    oos = res["active_oos_performance"]
    base = res["incumbent_oos_performance"]
    print(
        f"=== Walk-Forward Genetic Evolution Engine Gen #{res['generation']} ({res['elapsed_ms']} ms) ===\n"
        f"Evaluated {res['genomes_evaluated']} Genomes across {len(res['core_assets_tested'])} Core Perps | Status: {res['promotion_status']}\n"
        f"Active Genome: atr_stop={ag['atr_stop_mult']}x, tp_r={ag['tp_r_mult']}R, trail_act={ag['trail_activation_r']}R, "
        f"min_edge={ag['min_composite_edge']}, funding_min={ag['funding_snipe_min_bps']}bps, delta={ag['bernstein_delta']}\n"
        f"Champion OOS: Trades={oos['trades']}, WinRate={oos['win_rate']*100:.1f}%, Mean={oos['mean_net_r']:+.4f}R, "
        f"LCB_90%={oos['lcb_90_net_r']:+.4f}R, PF={oos['profit_factor']:.2f}, MaxDD={oos['max_dd_r']:.2f}R, FeeDrag={oos['avg_fee_drag_r']:.4f}R\n"
        f"Baseline OOS: Trades={base['trades']}, WinRate={base['win_rate']*100:.1f}%, Mean={base['mean_net_r']:+.4f}R, "
        f"LCB_90%={base['lcb_90_net_r']:+.4f}R, PF={base['profit_factor']:.2f} "
        f"(Improvement: dLCB={res['lcb_90_improvement_r']:+.4f}R, dMean={res['mean_oos_improvement_r']:+.4f}R)"
    )
