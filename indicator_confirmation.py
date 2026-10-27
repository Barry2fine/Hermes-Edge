#!/usr/bin/env python3
"""
Orthogonal 4-Domain Indicator Confirmation & Anti-Confirmation-Bias Falsification Engine
File: /home/box/desk/indicator_confirmation.py

Prevents collinear indicator stacking (false confirmation bias) by requiring
4 mathematically independent domains to confirm a trade thesis—and explicitly
checking for Falsification / Veto signals:

  Domain 1 — Trend & Directional Efficiency (1D & 4h):
    - Wilder ADX(14), +DI(14), -DI(14)
    - Kaufman Efficiency Ratio ER(10) = |C_t - C_{t-10}| / sum(|C_{t-i} - C_{t-i-1}|) in [0, 1]

  Domain 2 — Two-Speed Momentum: Impulse + Pullback Exhaustion (4h, 1h, 15m):
    - 4h & 1h MACD(12, 26, 9) line, signal, histogram, and histogram acceleration (dHist = H_t - H_{t-1})
    - 4h & 1h Wilder RSI(14) + Connors-style Short-Horizon RSI(2) Pullback Exhaustion trigger

  Domain 3 — Volume-Weighted Money Flow, CVD Divergence & VWAP Stretch (4h, 1h, 15m):
    - Money Flow Index MFI(14) (volume-weighted RSI)
    - Cumulative Intrabar Signed Volume Delta (CVD_10 = sum(V_i * (C_i - O_i)/max(H_i - L_i, eps)))
      & Price-vs-CVD Divergence detector
    - Rolling 8h Session VWAP Z-Score: Z_vwap = (C_t - VWAP) / sigma_vwap

  Domain 4 — Volatility Compression & Band Location (4h & 1h):
    - Bollinger Bands (20, 2.0) %B = (C - BB_lo)/(BB_hi - BB_lo) & Bandwidth %
    - Keltner Channel (20, 1.5 ATR) Squeeze ratio (BB_width / KC_width < 1.0 = Coiled Squeeze)
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


def fetch_klines(spot_sym: str, ktype: str, bars_count: int = 120) -> List[Dict[str, float]]:
    sec_map = {"15min": 900, "1hour": 3600, "4hour": 14400, "1day": 86400}
    step = sec_map[ktype]
    now_s = int(time.time())
    start_s = now_s - bars_count * step
    url = f"{KC_SPOT_URL}/api/v1/market/candles?type={ktype}&symbol={spot_sym}&startAt={start_s}&endAt={now_s}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "IndicatorConfirmation/1.0"})
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


def ema_series(vals: List[float], span: int) -> List[float]:
    if not vals:
        return []
    a = 2.0 / (span + 1.0)
    out = [vals[0]]
    for v in vals[1:]:
        out.append(v * a + out[-1] * (1.0 - a))
    return out


def wilder_rsi(closes: List[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    avg_g = sum(gains[:period]) / period
    avg_l = sum(losses[:period]) / period
    for g, l in zip(gains[period:], losses[period:]):
        avg_g = (avg_g * (period - 1) + g) / period
        avg_l = (avg_l * (period - 1) + l) / period
    if avg_l <= 1e-12:
        return 100.0
    rs = avg_g / avg_l
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def compute_macd(closes: List[float]) -> Dict[str, float]:
    if len(closes) < 35:
        return {"macd": 0.0, "signal": 0.0, "hist": 0.0, "hist_delta": 0.0, "hist_norm_bps": 0.0}
    e12 = ema_series(closes, 12)
    e26 = ema_series(closes, 26)
    macd_line = [a - b for a, b in zip(e12, e26)]
    sig_line = ema_series(macd_line, 9)
    hist = [m - s for m, s in zip(macd_line, sig_line)]
    px = max(closes[-1], 1e-9)
    return {
        "macd": round(macd_line[-1], 6),
        "signal": round(sig_line[-1], 6),
        "hist": round(hist[-1], 6),
        "hist_delta": round(hist[-1] - hist[-2], 6),
        "hist_norm_bps": round((hist[-1] / px) * 10000.0, 2),
        "hist_accelerating_up": hist[-1] > hist[-2],
    }


def compute_adx_di(bars: List[Dict[str, float]], period: int = 14) -> Dict[str, float]:
    if len(bars) < period * 2:
        return {"adx": 20.0, "plus_di": 20.0, "minus_di": 20.0}
    trs, pdm, mdm = [], [], []
    for i in range(1, len(bars)):
        up = bars[i]["h"] - bars[i - 1]["h"]
        dn = bars[i - 1]["l"] - bars[i]["l"]
        pdm.append(up if (up > dn and up > 0) else 0.0)
        mdm.append(dn if (dn > up and dn > 0) else 0.0)
        trs.append(max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - bars[i - 1]["c"]), abs(bars[i]["l"] - bars[i - 1]["c"])))
    atr_s = ema_series(trs, period)
    p_s = ema_series(pdm, period)
    m_s = ema_series(mdm, period)
    dxs = []
    pdi, mdi = 20.0, 20.0
    for a, p, m in zip(atr_s, p_s, m_s):
        if a <= 0:
            dxs.append(0.0)
            continue
        pdi = 100.0 * p / a
        mdi = 100.0 * m / a
        dxs.append(100.0 * abs(pdi - mdi) / max(1e-9, pdi + mdi))
    adx = ema_series(dxs, period)[-1]
    return {"adx": round(adx, 2), "plus_di": round(pdi, 2), "minus_di": round(mdi, 2)}


def kaufman_er(closes: List[float], period: int = 10) -> float:
    if len(closes) <= period:
        return 0.5
    net_move = abs(closes[-1] - closes[-1 - period])
    path_sum = sum(abs(closes[-i] - closes[-i - 1]) for i in range(1, period + 1))
    return round(net_move / max(1e-12, path_sum), 3)


def compute_mfi14(bars: List[Dict[str, float]], period: int = 14) -> float:
    if len(bars) <= period:
        return 50.0
    tps = [(b["h"] + b["l"] + b["c"]) / 3.0 for b in bars]
    pos_mf, neg_mf = 0.0, 0.0
    for i in range(len(bars) - period, len(bars)):
        rmf = tps[i] * bars[i]["v"]
        if tps[i] > tps[i - 1]:
            pos_mf += rmf
        elif tps[i] < tps[i - 1]:
            neg_mf += rmf
    if neg_mf <= 1e-12:
        return 100.0
    mfr = pos_mf / neg_mf
    return round(100.0 - (100.0 / (1.0 + mfr)), 2)


def compute_cvd_and_vwap_z(bars: List[Dict[str, float]], lookback: int = 20) -> Dict[str, Any]:
    if len(bars) < lookback:
        return {"vwap_z": 0.0, "cvd_norm": 0.0, "cvd_divergence": "NONE"}
    w = bars[-lookback:]
    tot_v = sum(b["v"] for b in w) or 1e-9
    tps = [(b["h"] + b["l"] + b["c"]) / 3.0 for b in w]
    vwap = sum(tp * b["v"] for tp, b in zip(tps, w)) / tot_v
    var = sum(b["v"] * ((tp - vwap) ** 2) for tp, b in zip(tps, w)) / tot_v
    std = math.sqrt(max(var, 1e-12))
    vwap_z = (w[-1]["c"] - vwap) / std

    # Signed intrabar delta = V_i * (C_i - O_i) / max(H_i - L_i, eps)
    deltas = [b["v"] * ((b["c"] - b["o"]) / max(b["h"] - b["l"], 1e-12)) for b in w]
    cvd_norm = sum(deltas) / tot_v  # in [-1, 1]

    # Compare first half vs second half of window for divergence
    half = lookback // 2
    px_chg = (w[-1]["c"] - w[half]["c"]) / max(w[half]["c"], 1e-9)
    cvd_2nd = sum(deltas[half:]) / max(sum(b["v"] for b in w[half:]), 1e-9)

    div = "NONE"
    if px_chg > 0.004 and cvd_2nd < -0.08:
        div = "BEARISH_CVD_DIVERGENCE"  # Price bouncing up on net selling delta (exhausted bounce)
    elif px_chg < -0.004 and cvd_2nd > 0.08:
        div = "BULLISH_CVD_ABSORPTION"  # Price dipping on net buying absorption

    return {
        "vwap": round(vwap, 6),
        "vwap_z": round(vwap_z, 2),
        "cvd_norm_20": round(cvd_norm, 3),
        "cvd_recent_10": round(cvd_2nd, 3),
        "cvd_divergence": div,
    }


def compute_bb_kc_domain(bars: List[Dict[str, float]]) -> Dict[str, Any]:
    if len(bars) < 22:
        return {"pct_b": 0.5, "bb_width_pct": 0.0, "bb_kc_ratio": 1.0, "squeeze_on": False}
    closes = [b["c"] for b in bars[-20:]]
    mean = sum(closes) / 20.0
    std = math.sqrt(sum((c - mean) ** 2 for c in closes) / 20.0)
    bb_hi = mean + 2.0 * std
    bb_lo = mean - 2.0 * std
    pct_b = (closes[-1] - bb_lo) / max(bb_hi - bb_lo, 1e-12)

    trs = [max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - bars[i - 1]["c"]), abs(bars[i]["l"] - bars[i - 1]["c"])) for i in range(len(bars) - 14, len(bars))]
    atr14 = sum(trs) / len(trs)
    kc_width = 3.0 * atr14
    bb_width = bb_hi - bb_lo
    ratio = bb_width / max(kc_width, 1e-12)
    return {
        "pct_b": round(pct_b, 3),
        "bb_width_pct": round((bb_width / max(mean, 1e-9)) * 100.0, 2),
        "bb_kc_ratio": round(ratio, 3),
        "squeeze_on": ratio <= 1.0,
    }


def analyze_pair_indicators(fut_sym: str, spot_sym: str) -> Dict[str, Any]:
    b_4h = fetch_klines(spot_sym, "4hour", 120)
    b_1h = fetch_klines(spot_sym, "1hour", 90)
    b_15m = fetch_klines(spot_sym, "15min", 60)

    c4 = [b["c"] for b in b_4h]
    c1 = [b["c"] for b in b_1h]
    c15 = [b["c"] for b in b_15m]

    # Domain 1: Trend & Directional Efficiency (4h ADX/DI + Kaufman ER-10)
    adx_4h = compute_adx_di(b_4h, 14)
    er_4h = kaufman_er(c4, 10)
    e20_4h = ema_series(c4, 20)[-1]
    e100_4h = ema_series(c4, 100)[-1]
    trend_dir = "BULL" if (e20_4h > e100_4h and adx_4h["plus_di"] >= adx_4h["minus_di"] * 0.9) else "BEAR"

    # Domain 2: Two-Speed Momentum (4h/1h MACD + RSI-14 + Short-Horizon RSI-2 Pullback Exhaustion)
    macd_4h = compute_macd(c4)
    macd_1h = compute_macd(c1)
    rsi14_4h = wilder_rsi(c4, 14)
    rsi14_1h = wilder_rsi(c1, 14)
    rsi2_1h = wilder_rsi(c1, 2)
    rsi2_15m = wilder_rsi(c15, 2)

    # Domain 3: Volume-Weighted Money Flow (MFI-14, CVD Divergence, VWAP Z-Score)
    mfi_4h = compute_mfi14(b_4h, 14)
    mfi_1h = compute_mfi14(b_1h, 14)
    flow_1h = compute_cvd_and_vwap_z(b_1h, 20)
    flow_15m = compute_cvd_and_vwap_z(b_15m, 32)

    # Domain 4: Volatility Compression & Band Location (4h & 1h BB %B + Keltner Squeeze)
    vol_4h = compute_bb_kc_domain(b_4h)
    vol_1h = compute_bb_kc_domain(b_1h)

    # Score the 4 Orthogonal Domains (-100 to +100, or Directional Confirmation 0-100 + Falsification Vetoes)
    target_side = "LONG" if trend_dir == "BULL" else "SHORT"
    domains_confirmed = 0
    domain_breakdown = {}
    falsification_flags = []

    # Evaluate Domain 1 (Trend Efficiency)
    d1_ok = (adx_4h["adx"] >= 18.0) and (
        (target_side == "LONG" and adx_4h["plus_di"] > adx_4h["minus_di"])
        or (target_side == "SHORT" and adx_4h["minus_di"] > adx_4h["plus_di"])
    )
    if d1_ok:
        domains_confirmed += 1
    domain_breakdown["D1_trend_efficiency"] = {
        "confirmed": d1_ok,
        "adx_4h": adx_4h["adx"],
        "plus_di_4h": adx_4h["plus_di"],
        "minus_di_4h": adx_4h["minus_di"],
        "kaufman_er10_4h": er_4h,
    }

    # Evaluate Domain 2 (Two-Speed Momentum: HTF trend + LTF pullback exhaustion)
    if target_side == "LONG":
        d2_ok = (rsi14_4h >= 48.0) and (rsi14_1h <= 68.0) and (rsi2_1h <= 65.0 or macd_1h["hist_accelerating_up"])
        if rsi14_1h >= 76.0:
            falsification_flags.append(f"OVERBOUGHT_1H_RSI14({rsi14_1h})")
    else:
        d2_ok = (rsi14_4h <= 52.0) and (rsi14_1h >= 30.0) and (rsi2_1h >= 35.0 or not macd_1h["hist_accelerating_up"])
        if rsi14_1h <= 24.0:
            falsification_flags.append(f"OVERSOLD_1H_RSI14({rsi14_1h})")
    if d2_ok:
        domains_confirmed += 1
    domain_breakdown["D2_two_speed_momentum"] = {
        "confirmed": d2_ok,
        "rsi14_4h": rsi14_4h,
        "rsi14_1h": rsi14_1h,
        "rsi2_1h": rsi2_1h,
        "rsi2_15m": rsi2_15m,
        "macd_4h_hist_bps": macd_4h["hist_norm_bps"],
        "macd_1h_hist_bps": macd_1h["hist_norm_bps"],
        "macd_1h_accel_up": macd_1h["hist_accelerating_up"],
    }

    # Evaluate Domain 3 (Money Flow, CVD & VWAP Z)
    if target_side == "LONG":
        d3_ok = (mfi_4h >= 42.0) and (flow_1h["cvd_recent_10"] >= -0.15) and (flow_15m["vwap_z"] <= 2.2)
        if flow_1h["cvd_divergence"] == "BEARISH_CVD_DIVERGENCE":
            falsification_flags.append("1H_BEARISH_CVD_DIVERGENCE")
    else:
        d3_ok = (mfi_4h <= 58.0) and (flow_1h["cvd_recent_10"] <= 0.18) and (flow_15m["vwap_z"] >= -2.2)
        if flow_1h["cvd_divergence"] == "BULLISH_CVD_ABSORPTION" and mfi_1h >= 60.0:
            falsification_flags.append("1H_BULLISH_CVD_ABSORPTION")
    if d3_ok:
        domains_confirmed += 1
    domain_breakdown["D3_volume_money_flow"] = {
        "confirmed": d3_ok,
        "mfi14_4h": mfi_4h,
        "mfi14_1h": mfi_1h,
        "cvd_1h_recent10": flow_1h["cvd_recent_10"],
        "cvd_1h_divergence": flow_1h["cvd_divergence"],
        "vwap_z_15m": flow_15m["vwap_z"],
    }

    # Evaluate Domain 4 (Volatility Band Location & Squeeze)
    if target_side == "LONG":
        d4_ok = (vol_1h["pct_b"] <= 0.88) or vol_1h["squeeze_on"]
        if vol_1h["pct_b"] > 0.98 and not vol_1h["squeeze_on"]:
            falsification_flags.append(f"CHASE_ABOVE_1H_UPPER_BB(%B={vol_1h['pct_b']})")
    else:
        d4_ok = (vol_1h["pct_b"] >= 0.12) or vol_1h["squeeze_on"]
        if vol_1h["pct_b"] < 0.02 and not vol_1h["squeeze_on"]:
            falsification_flags.append(f"CHASE_BELOW_1H_LOWER_BB(%B={vol_1h['pct_b']})")
    if d4_ok:
        domains_confirmed += 1
    domain_breakdown["D4_volatility_regime"] = {
        "confirmed": d4_ok,
        "bb_pct_b_4h": vol_4h["pct_b"],
        "bb_pct_b_1h": vol_1h["pct_b"],
        "bb_kc_ratio_1h": vol_1h["bb_kc_ratio"],
        "squeeze_1h": vol_1h["squeeze_on"],
    }

    confirmation_score = domains_confirmed * 25 - len(falsification_flags) * 20
    confirmation_score = max(0, min(100, confirmation_score))

    return {
        "fut_symbol": fut_sym,
        "spot_symbol": spot_sym,
        "target_side": target_side,
        "domains_confirmed": f"{domains_confirmed}/4",
        "domains_confirmed_int": domains_confirmed,
        "indicator_confirmation_score": confirmation_score,
        "falsification_flags": falsification_flags,
        "verdict": "CONFIRMED_4_OF_4" if (domains_confirmed == 4 and not falsification_flags) else (
            "CONFIRMED_3_OF_4" if (domains_confirmed >= 3 and not falsification_flags) else "FALSIFIED_OR_WEAK"
        ),
        "domains": domain_breakdown,
    }


def run_indicator_scan() -> Dict[str, Any]:
    t0 = time.time()
    results = []
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(analyze_pair_indicators, f_s, s_s) for f_s, s_s in UNIVERSE]
        for f in futs:
            try:
                results.append(f.result())
            except Exception:
                pass

    results.sort(key=lambda x: (x["indicator_confirmation_score"], x["domains"]["D1_trend_efficiency"]["adx_4h"]), reverse=True)
    out = {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "confirmed_count": sum(1 for r in results if r["domains_confirmed_int"] >= 3 and not r["falsification_flags"]),
        "results": results,
    }
    with open("/home/box/desk/kucoin/latest_indicator_confirmation.json", "w") as f:
        json.dump(out, f, indent=2)
    return out


if __name__ == "__main__":
    res = run_indicator_scan()
    print(f"=== Orthogonal 4-Domain Indicator Confirmation & Falsification Engine ({res['elapsed_ms']} ms | Confirmed: {res['confirmed_count']}/{len(res['results'])}) ===")
    print(f"{'SYMBOL':<10} {'SIDE':<6} {'DOMAINS':<8} {'SCORE':>5} {'4H_ADX/+DI/-DI':<18} {'ER10':>5} {'RSI14(4H/1H)':<13} {'RSI2(1H)':>8} {'MFI(4H/1H)':<11} {'15M_VWAP_Z':>10} {'1H_%B':>6} {'VERDICT':<20}")
    for r in res["results"]:
        d1 = r["domains"]["D1_trend_efficiency"]
        d2 = r["domains"]["D2_two_speed_momentum"]
        d3 = r["domains"]["D3_volume_money_flow"]
        d4 = r["domains"]["D4_volatility_regime"]
        adx_s = f"{d1['adx_4h']:.1f}/{d1['plus_di_4h']:.0f}/{d1['minus_di_4h']:.0f}"
        rsi_s = f"{d2['rsi14_4h']:.1f}/{d2['rsi14_1h']:.1f}"
        mfi_s = f"{d3['mfi14_4h']:.0f}/{d3['mfi14_1h']:.0f}"
        print(
            f"{r['fut_symbol']:<10} {r['target_side']:<6} {r['domains_confirmed']:<8} {r['indicator_confirmation_score']:>5} "
            f"{adx_s:<18} {d1['kaufman_er10_4h']:>5.2f} {rsi_s:<13} {d2['rsi2_1h']:>8.1f} "
            f"{mfi_s:<11} {d3['vwap_z_15m']:>+10.2f} {d4['bb_pct_b_1h']:>6.2f} {r['verdict']:<20}"
        )
