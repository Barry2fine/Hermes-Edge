#!/usr/bin/env python3
"""
Candlestick Geometry Mathematical Matrix & k-NN Empirical Transition Engine
File: /home/box/desk/candle_geometry_matrix.py

Represents every bar t across 1D, 4h, 1h, and 15m as an exact 8-dimensional
normalized feature vector g_t in R^8:
  0. b_t = (C_t - O_t) / max(H_t - L_t, eps)           in [-1, 1]  (Signed Body-to-Range ratio)
  1. u_t = (H_t - max(O_t, C_t)) / max(H_t - L_t, eps) in [0, 1]   (Upper-Wick Rejection ratio)
  2. l_t = (min(O_t, C_t) - L_t) / max(H_t - L_t, eps) in [0, 1]   (Lower-Wick Absorption ratio)
  3. c_t = (C_t - L_t) / max(H_t - L_t, eps)           in [0, 1]   (Close Location Value / CLV)
  4. r_t = (H_t - L_t) / ATR_{14,t}                    in [0, inf) (Volatility-Normalized Range Expansion)
  5. v_t = V_t / SMA_{20}(V)_t                         in [0, inf) (Relative Volume Surge multiplier)
  6. e_t = (|C_t - O_t| / ATR_{14,t}) / max(v_t, 0.1)  in [0, inf) (Wyckoff Effort-vs-Result Efficiency)
  7. s_t = (u_t - l_t) / max(u_t + l_t, eps)           in [-1, 1]  (Wick Asymmetry / Tail Pressure Index)

Stacks a 3-bar State-Transition Matrix M_t = [g_{t-2}, g_{t-1}, g_t] in R^{3x8} (24-D flattened vector)
and performs:
  1. Exact Algebraic Signature Classification:
     - BULL_ABSORPTION_RECLAIM / BEAR_REJECTION_SWEEP (Wyckoff spring / upthrust: high wick + high vol + low e_t + CLV flip)
     - BULL_FVG_DISPLACEMENT / BEAR_FVG_DISPLACEMENT (3-bar Fair Value Gap + marubozu impulse |b_{t-1}| > 0.65, r_{t-1} > 1.2)
     - INSIDE_BAR_COIL (H_t <= H_{t-1} and L_t >= L_{t-1} with r_t < 0.70)
  2. Empirical Historical Cosine-Similarity k-NN Lookup (k=25 over rolling 420 bars):
     - Computes cosine similarity between live M_t (24-D) and every historical 3-bar window M_k in the last 420 bars.
     - Extracts the empirical forward 3-bar directional win rate (P_bull, P_bear) and ATR-normalized expected excursion E[R_geom].
"""

import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

KC_SPOT_URL = "https://api.kucoin.com"

UNIVERSE = [
    ("XMRUSDTM", "XMR-USDT"),
    ("ZECUSDTM", "ZEC-USDT"),
    ("DOGEUSDTM", "DOGE-USDT"),
    ("SOLUSDTM", "SOL-USDT"),
    ("ETHUSDTM", "ETH-USDT"),
    ("XBTUSDTM", "BTC-USDT"),
    ("XRPUSDTM", "XRP-USDT"),
    ("LINKUSDTM", "LINK-USDT"),
    ("TAOUSDTM", "TAO-USDT"),
    ("SUIUSDTM", "SUI-USDT"),
    ("SEIUSDTM", "SEI-USDT"),
    ("AVAXUSDTM", "AVAX-USDT"),
    ("PEPEUSDTM", "PEPE-USDT"),
    ("AAVEUSDTM", "AAVE-USDT"),
    ("NEARUSDTM", "NEAR-USDT"),
]


def fetch_klines(spot_sym: str, ktype: str, bars_count: int = 420) -> List[Dict[str, float]]:
    sec_map = {"15min": 900, "1hour": 3600, "4hour": 14400, "1day": 86400}
    step = sec_map[ktype]
    now_s = int(time.time())
    start_s = now_s - bars_count * step
    url = f"{KC_SPOT_URL}/api/v1/market/candles?type={ktype}&symbol={spot_sym}&startAt={start_s}&endAt={now_s}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "CandleGeometryMatrix/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
        raw = (json.loads(r.read().decode("utf-8")) or {}).get("data") or []
    return [
        {
            "t": int(row[0]),
            "o": float(row[1]),
            "c": float(row[2]),
            "h": float(row[3]),
            "l": float(row[4]),
            "v": float(row[5]),
        }
        for row in reversed(raw)
    ]


def compute_geometry_vectors(bars: List[Dict[str, float]]) -> Tuple[List[List[float]], List[float]]:
    """
    Computes the 8-dimensional feature vector g_t for every bar t in `bars`,
    plus the ATR14 series.
    """
    n = len(bars)
    if n == 0:
        return [], []

    trs: List[float] = [max(1e-12, bars[0]["h"] - bars[0]["l"])]
    for i in range(1, n):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc), 1e-12))

    atrs: List[float] = []
    vol_sma: List[float] = []
    for i in range(n):
        w_tr = trs[max(0, i - 13) : i + 1]
        atrs.append(sum(w_tr) / len(w_tr))
        w_v = [b["v"] for b in bars[max(0, i - 19) : i + 1]]
        vol_sma.append(max(1e-12, sum(w_v) / len(w_v)))

    vecs: List[List[float]] = []
    for i, b in enumerate(bars):
        o, h, l, c, v = b["o"], b["h"], b["l"], b["c"], b["v"]
        rng = max(h - l, 1e-12)
        atr = max(atrs[i], 1e-12)
        vsma = vol_sma[i]

        b_t = (c - o) / rng
        u_t = (h - max(o, c)) / rng
        l_t = (min(o, c) - l) / rng
        c_t = (c - l) / rng
        r_t = (h - l) / atr
        v_t = min(5.0, v / vsma)  # cap extreme outliers at 5.0x for cosine stability
        e_t = min(5.0, (abs(c - o) / atr) / max(v_t, 0.15))
        s_t = (u_t - l_t) / max(u_t + l_t, 1e-9)

        vecs.append([
            round(b_t, 4),
            round(u_t, 4),
            round(l_t, 4),
            round(c_t, 4),
            round(r_t, 4),
            round(v_t, 4),
            round(e_t, 4),
            round(s_t, 4),
        ])
    return vecs, atrs


def classify_3bar_matrix(bars: List[Dict[str, float]], vecs: List[List[float]], idx: int) -> Dict[str, Any]:
    """Classifies the 3-bar geometry matrix M_t = [g_{idx-2}, g_{idx-1}, g_idx]."""
    g2, g1, g0 = vecs[idx - 2], vecs[idx - 1], vecs[idx]
    b2, b1, b0 = bars[idx - 2], bars[idx - 1], bars[idx]

    # Unpack g0 (current bar) and g1 (prior bar)
    bt0, ut0, lt0, ct0, rt0, vt0, et0, st0 = g0
    bt1, ut1, lt1, ct1, rt1, vt1, et1, st1 = g1

    signatures = []
    geom_bias_pts = 0.0  # Positive = Bullish geometry, Negative = Bearish geometry

    # 1. Lower-Wick Absorption / Spring (Bullish)
    if (lt0 >= 0.48 and ct0 >= 0.62) or (lt1 >= 0.52 and bt0 > 0.35 and ct0 >= 0.68):
        signatures.append("BULL_WICK_ABSORPTION")
        geom_bias_pts += 22.0 * (1.25 if max(vt0, vt1) >= 1.15 else 1.0)

    # 2. Upper-Wick Rejection / Upthrust (Bearish)
    if (ut0 >= 0.48 and ct0 <= 0.38) or (ut1 >= 0.52 and bt0 < -0.35 and ct0 <= 0.32):
        signatures.append("BEAR_WICK_REJECTION")
        geom_bias_pts -= 22.0 * (1.25 if max(vt0, vt1) >= 1.15 else 1.0)

    # 3. 3-Bar Fair Value Gap (FVG) & Body Displacement
    bull_fvg = (b0["l"] > b2["h"]) and (bt1 > 0.55 and rt1 > 1.10)
    bear_fvg = (b0["h"] < b2["l"]) and (bt1 < -0.55 and rt1 > 1.10)
    if bull_fvg:
        signatures.append("BULL_FVG_DISPLACEMENT")
        geom_bias_pts += 25.0
    if bear_fvg:
        signatures.append("BEAR_FVG_DISPLACEMENT")
        geom_bias_pts -= 25.0

    # 4. Marubozu / High-Efficiency Displacement on Current Bar
    if bt0 >= 0.68 and rt0 >= 1.05 and ct0 >= 0.80:
        signatures.append("BULL_MARUBOZU_DRIVE")
        geom_bias_pts += 18.0
    elif bt0 <= -0.68 and rt0 >= 1.05 and ct0 <= 0.20:
        signatures.append("BEAR_MARUBOZU_DRIVE")
        geom_bias_pts -= 18.0

    # 5. Wyckoff Effort-vs-Result Climax / Exhaustion
    if vt0 >= 1.50 and abs(bt0) <= 0.22 and et0 <= 0.30:
        if st0 > 0.25:
            signatures.append("WYCKOFF_BUYING_EXHAUSTION")
            geom_bias_pts -= 15.0
        elif st0 < -0.25:
            signatures.append("WYCKOFF_SELLING_ABSORPTION")
            geom_bias_pts += 15.0
        else:
            signatures.append("HIGH_VOL_DOJI_NODE")

    # 6. Inside-Bar Coiled Compression
    if b0["h"] <= b1["h"] and b0["l"] >= b1["l"] and rt0 <= 0.75:
        signatures.append("INSIDE_BAR_COIL")

    # Continuous signed pressure from 3-bar CLV, Body, and Wick Asymmetry
    continuous_score = (
        12.0 * (0.5 * bt0 + 0.3 * bt1 + 0.2 * g2[0])
        + 10.0 * ((ct0 - 0.5) * 1.4 + (ct1 - 0.5) * 0.6)
        - 10.0 * (0.6 * st0 + 0.4 * st1)
    )
    total_signed_score = max(-100.0, min(100.0, round(geom_bias_pts + continuous_score, 1)))

    if not signatures:
        if total_signed_score >= 12.0:
            signatures.append("BULL_CLV_CONTINUATION")
        elif total_signed_score <= -12.0:
            signatures.append("BEAR_CLV_CONTINUATION")
        else:
            signatures.append("BALANCED_ROTATION")

    return {
        "matrix_3x8": [g2, g1, g0],
        "current_vector_8d": {
            "b_body_ratio": bt0,
            "u_upper_wick": ut0,
            "l_lower_wick": lt0,
            "c_clv": ct0,
            "r_range_atr": rt0,
            "v_rel_vol": vt0,
            "e_wyckoff_eff": et0,
            "s_wick_asym": st0,
        },
        "signatures": signatures,
        "signed_geometry_score": total_signed_score,
    }


def knn_empirical_transition(
    bars: List[Dict[str, float]],
    vecs: List[List[float]],
    atrs: List[float],
    target_idx: int,
    k: int = 25,
    horizon: int = 3,
) -> Dict[str, Any]:
    """
    Finds the top-k most similar historical 3-bar geometry matrices M_i in R^{24}
    using weighted cosine similarity, and measures the empirical forward `horizon`-bar
    directional win rate and ATR-normalized expected return E[R_geom].
    """
    if target_idx < 35 or len(bars) < 60:
        return {"k": 0, "bull_win_rate": 0.5, "bear_win_rate": 0.5, "expected_signed_atr": 0.0, "mean_cosine_sim": 0.0}

    # Feature weights across the 3 bars (bar t-2 weight 0.6, bar t-1 weight 0.85, bar t weight 1.2)
    bar_w = [0.6, 0.85, 1.2]
    # Within each 8D vector: [b, u, l, c, r, v, e, s]
    feat_w = [1.3, 1.1, 1.1, 1.2, 0.8, 0.7, 0.7, 1.1]

    def _flatten_weighted(idx: int) -> List[float]:
        out = []
        for b_offset, bw in zip([-2, -1, 0], bar_w):
            v = vecs[idx + b_offset]
            # Center strictly positive features (u, l, c, r, v, e) around their typical neutral baseline
            centered = [
                v[0],              # b in [-1, 1]
                v[1] - 0.25,       # u centered
                v[2] - 0.25,       # l centered
                (v[3] - 0.5) * 2,  # c in [-1, 1]
                (v[4] - 1.0) * 0.5,# r centered at 1 ATR
                (v[5] - 1.0) * 0.4,# v centered at 1 SMA
                (v[6] - 0.5) * 0.5,# e centered
                v[7],              # s in [-1, 1]
            ]
            for val, fw in zip(centered, feat_w):
                out.append(val * bw * fw)
        return out

    target_v = _flatten_weighted(target_idx)
    norm_t = math.sqrt(sum(x * x for x in target_v)) or 1e-9

    candidates = []
    # Search historical bars from index 20 up to target_idx - horizon - 2
    for i in range(20, target_idx - horizon - 1):
        cand_v = _flatten_weighted(i)
        norm_c = math.sqrt(sum(x * x for x in cand_v)) or 1e-9
        cos_sim = sum(a * b for a, b in zip(target_v, cand_v)) / (norm_t * norm_c)
        if cos_sim <= 0.15:
            continue
        # Forward horizon-bar outcome normalized by ATR at bar i
        entry_c = bars[i]["c"]
        atr_i = max(atrs[i], 1e-9)
        fwd_c = bars[i + horizon]["c"]
        fwd_hi = max(bars[j]["h"] for j in range(i + 1, i + horizon + 1))
        fwd_lo = min(bars[j]["l"] for j in range(i + 1, i + horizon + 1))
        signed_atr_move = (fwd_c - entry_c) / atr_i
        mfe_long_atr = (fwd_hi - entry_c) / atr_i
        mfe_short_atr = (entry_c - fwd_lo) / atr_i
        candidates.append((cos_sim, signed_atr_move, mfe_long_atr, mfe_short_atr))

    candidates.sort(key=lambda x: x[0], reverse=True)
    top_k = candidates[:k]
    if not top_k:
        return {"k": 0, "bull_win_rate": 0.5, "bear_win_rate": 0.5, "expected_signed_atr": 0.0, "mean_cosine_sim": 0.0}

    w_sum = sum(x[0] for x in top_k)
    bull_wins = sum(x[0] for x in top_k if x[1] > 0)
    bear_wins = sum(x[0] for x in top_k if x[1] < 0)
    exp_signed_atr = sum(x[0] * x[1] for x in top_k) / w_sum
    exp_mfe_long = sum(x[0] * x[2] for x in top_k) / w_sum
    exp_mfe_short = sum(x[0] * x[3] for x in top_k) / w_sum
    mean_sim = w_sum / len(top_k)

    return {
        "k": len(top_k),
        "mean_cosine_sim": round(mean_sim, 3),
        "bull_win_rate": round(bull_wins / w_sum, 3),
        "bear_win_rate": round(bear_wins / w_sum, 3),
        "expected_signed_atr_3bar": round(exp_signed_atr, 3),
        "expected_mfe_long_atr": round(exp_mfe_long, 3),
        "expected_mfe_short_atr": round(exp_mfe_short, 3),
    }


def analyze_pair_geometry(fut_sym: str, spot_sym: str) -> Dict[str, Any]:
    """
    Computes the multi-timeframe Candlestick Geometry Matrix & k-NN empirical probabilities
    across 4h, 1h, and 15m bars (analyzing the latest closed bar + live forming bar).
    """
    tf_weights = {"4hour": 0.45, "1hour": 0.35, "15min": 0.20}
    tf_results = {}
    composite_signed_score = 0.0
    composite_bull_prob = 0.0
    composite_bear_prob = 0.0

    for ktype, w in tf_weights.items():
        bars = fetch_klines(spot_sym, ktype, 400 if ktype != "15min" else 300)
        if len(bars) < 50:
            continue
        vecs, atrs = compute_geometry_vectors(bars)
        # Use idx = -2 (most recent completed candle) blended 65/35 with idx = -1 (live forming candle)
        closed_idx = len(bars) - 2
        live_idx = len(bars) - 1

        mat_closed = classify_3bar_matrix(bars, vecs, closed_idx)
        mat_live = classify_3bar_matrix(bars, vecs, live_idx)
        knn_closed = knn_empirical_transition(bars, vecs, atrs, closed_idx, k=25, horizon=3)
        knn_live = knn_empirical_transition(bars, vecs, atrs, live_idx, k=25, horizon=3)

        tf_score = round(0.65 * mat_closed["signed_geometry_score"] + 0.35 * mat_live["signed_geometry_score"], 1)
        tf_bull_p = round(0.65 * knn_closed["bull_win_rate"] + 0.35 * knn_live["bull_win_rate"], 3)
        tf_bear_p = round(0.65 * knn_closed["bear_win_rate"] + 0.35 * knn_live["bear_win_rate"], 3)
        tf_exp_atr = round(0.65 * knn_closed["expected_signed_atr_3bar"] + 0.35 * knn_live["expected_signed_atr_3bar"], 3)

        composite_signed_score += w * tf_score
        composite_bull_prob += w * tf_bull_p
        composite_bear_prob += w * tf_bear_p

        tf_results[ktype] = {
            "tf_signed_score": tf_score,
            "closed_bar_signatures": mat_closed["signatures"],
            "live_bar_signatures": mat_live["signatures"],
            "closed_vector_8d": mat_closed["current_vector_8d"],
            "live_vector_8d": mat_live["current_vector_8d"],
            "knn_bull_prob": tf_bull_p,
            "knn_bear_prob": tf_bear_p,
            "knn_expected_atr_3bar": tf_exp_atr,
            "knn_mean_cosine_sim": knn_closed["mean_cosine_sim"],
        }

    comp_score = round(composite_signed_score, 1)
    comp_bull = round(composite_bull_prob, 3)
    comp_bear = round(composite_bear_prob, 3)

    if comp_score >= 15.0 or comp_bull >= 0.56:
        verdict = "BULL_GEOMETRY_CONFLUENCE"
    elif comp_score <= -15.0 or comp_bear >= 0.56:
        verdict = "BEAR_GEOMETRY_CONFLUENCE"
    else:
        verdict = "NEUTRAL_GEOMETRY_BALANCE"

    return {
        "fut_symbol": fut_sym,
        "spot_symbol": spot_sym,
        "composite_geometry_score": comp_score,
        "empirical_knn_bull_prob": comp_bull,
        "empirical_knn_bear_prob": comp_bear,
        "geometry_verdict": verdict,
        "timeframes": tf_results,
    }


def run_geometry_scan() -> Dict[str, Any]:
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(analyze_pair_geometry, f_s, s_s) for f_s, s_s in UNIVERSE]
        for f in futs:
            try:
                results.append(f.result())
            except Exception:
                pass

    results.sort(key=lambda x: abs(x["composite_geometry_score"]), reverse=True)
    out = {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "universe_count": len(results),
        "results": results,
    }
    with open("/home/box/desk/kucoin/latest_candle_geometry.json", "w") as f:
        json.dump(out, f, indent=2)
    return out


if __name__ == "__main__":
    res = run_geometry_scan()
    print(f"=== Candlestick Geometry Mathematical Matrix & 24-D k-NN Engine ({res['elapsed_ms']} ms) ===")
    print(f"{'SYMBOL':<10} {'GEOM_SCORE':>10} {'kNN_BULL%':>10} {'kNN_BEAR%':>10} {'4H_SIGNATURES':<28} {'1H_SIGNATURES':<28} {'VERDICT':<25}")
    for r in res["results"]:
        tf4 = (r["timeframes"].get("4hour") or {}).get("closed_bar_signatures", [])
        tf1 = (r["timeframes"].get("1hour") or {}).get("closed_bar_signatures", [])
        s4 = ",".join(tf4)[:26]
        s1 = ",".join(tf1)[:26]
        print(
            f"{r['fut_symbol']:<10} {r['composite_geometry_score']:>+10.1f} "
            f"{r['empirical_knn_bull_prob']*100:>9.1f}% {r['empirical_knn_bear_prob']*100:>9.1f}% "
            f"{s4:<28} {s1:<28} {r['geometry_verdict']:<25}"
        )
