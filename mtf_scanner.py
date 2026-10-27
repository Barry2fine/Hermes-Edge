#!/usr/bin/env python3
"""
Institutional Multi-Timeframe (MTF) Hierarchy & 8h Funding Clock Scanner
File: /home/box/desk/mtf_scanner.py

Aligns 5 timescales before firing or sizing any trade across KuCoin Futures & Propr:
  1. HTF Structure (1W & 1D):
     - Previous Week High/Low (PWH/PWL), Previous Day High/Low (PDH/PDL)
     - Daily Dealing Range Equilibrium (0.0 = PDL, 0.50 = EQ, 1.0 = PDH -> Discount < 0.50 vs Premium > 0.50)
     - 1D ADX(14) Trend Strength vs Chop Filter (ADX < 18 = CHOP/RANGE; ADX >= 22 = TREND EXPANSION)
  2. MTF Regime & Volatility Squeeze (4h & 1h):
     - 4h EMA20/100 structural trend + distance from 4h EMA20 in ATRs
     - 1h Bollinger Band (20, 2.0) vs Keltner Channel (20, 1.5 ATR) Volatility Squeeze
     - 1h RSI(14) momentum state
  3. LTF Microstructure Trigger (15m & 5m):
     - 15m Session VWAP & 15m 2.0-ATR Chandelier trailing bands
     - 15m Liquidity Sweep of recent 12-bar swing high/low + 5m Change in State of Delivery (CISD body close)
     - Exact Micro-Inflection Maker Limit Price (combining 4h EMA20, 1h EMA20, and 15m VWAP/ATR)
  4. Event / Funding Clock (8h Settlement Window: 00:00, 08:00, 16:00 UTC):
     - Minutes to/since 8h funding settlement + funding rate bps + basis bps
  5. Composite MTF Confluence Score (0-100) & Dynamic Volatility-Targeted Risk Sizing:
     - Scales risk between $1.50 (0.75% of $200 book) and $3.00 (1.50% of $200 book) based on MTF score + Walk-Forward E[R].
"""

import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

KC_SPOT_URL = "https://api.kucoin.com"
KC_FUT_URL = "https://api-futures.kucoin.com"

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


def http_get_json(url: str, timeout: float = 4.5) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "MTFScanner/2.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def fetch_klines(spot_sym: str, ktype: str, bars_count: int) -> List[Dict[str, float]]:
    sec_map = {
        "5min": 300,
        "15min": 900,
        "1hour": 3600,
        "4hour": 14400,
        "1day": 86400,
        "1week": 604800,
    }
    step = sec_map[ktype]
    now_s = int(time.time())
    start_s = now_s - bars_count * step
    url = f"{KC_SPOT_URL}/api/v1/market/candles?type={ktype}&symbol={spot_sym}&startAt={start_s}&endAt={now_s}"
    raw = (http_get_json(url) or {}).get("data") or []
    out = []
    for row in reversed(raw):
        out.append({
            "t": int(row[0]),
            "o": float(row[1]),
            "c": float(row[2]),
            "h": float(row[3]),
            "l": float(row[4]),
            "v": float(row[5]),
            "qv": float(row[6]),
        })
    return out


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


def compute_adx14(bars: List[Dict[str, float]], period: int = 14) -> Tuple[float, float, float]:
    """Returns (adx, plus_di, minus_di) on 1D or 4h bars."""
    if len(bars) < period * 2:
        return 20.0, 20.0, 20.0
    trs, plus_dm, minus_dm = [], [], []
    for i in range(1, len(bars)):
        up = bars[i]["h"] - bars[i - 1]["h"]
        dn = bars[i - 1]["l"] - bars[i]["l"]
        pdm = up if (up > dn and up > 0) else 0.0
        mdm = dn if (dn > up and dn > 0) else 0.0
        tr = max(bars[i]["h"] - bars[i]["l"], abs(bars[i]["h"] - bars[i - 1]["c"]), abs(bars[i]["l"] - bars[i - 1]["c"]))
        trs.append(tr)
        plus_dm.append(pdm)
        minus_dm.append(mdm)

    atr_s = ema_series(trs, period)
    pdm_s = ema_series(plus_dm, period)
    mdm_s = ema_series(minus_dm, period)
    dxs = []
    pdi_last, mdi_last = 20.0, 20.0
    for a, p, m in zip(atr_s, pdm_s, mdm_s):
        if a <= 0:
            dxs.append(0.0)
            continue
        pdi = 100.0 * p / a
        mdi = 100.0 * m / a
        pdi_last, mdi_last = pdi, mdi
        denom = pdi + mdi
        dxs.append(100.0 * abs(pdi - mdi) / denom if denom > 0 else 0.0)
    adx_s = ema_series(dxs, period)
    return round(adx_s[-1], 2), round(pdi_last, 2), round(mdi_last, 2)


def compute_rsi14(closes: List[float], period: int = 14) -> float:
    if len(closes) <= period:
        return 50.0
    gains, losses = [], []
    for i in range(1, len(closes)):
        d = closes[i] - closes[i - 1]
        gains.append(max(0.0, d))
        losses.append(max(0.0, -d))
    avg_g = sum(gains[-period:]) / period
    avg_l = sum(losses[-period:]) / period
    if avg_l == 0:
        return 100.0
    rs = avg_g / avg_l
    return round(100.0 - (100.0 / (1.0 + rs)), 2)


def compute_1h_squeeze(bars_1h: List[Dict[str, float]]) -> Dict[str, Any]:
    """Detects Bollinger Band (20, 2.0) inside Keltner Channel (20, 1.5 ATR) volatility squeeze."""
    if len(bars_1h) < 22:
        return {"squeeze_on": False, "bb_width_pct": 0.0, "squeeze_state": "NORMAL"}
    closes = [b["c"] for b in bars_1h[-20:]]
    mean = sum(closes) / 20.0
    var = sum((c - mean) ** 2 for c in closes) / 20.0
    std = math.sqrt(var)
    bb_upper = mean + 2.0 * std
    bb_lower = mean - 2.0 * std

    atrs = atr_series(bars_1h, 14)
    atr_now = atrs[-1]
    kc_upper = mean + 1.5 * atr_now
    kc_lower = mean - 1.5 * atr_now

    squeeze_on = (bb_upper <= kc_upper) and (bb_lower >= kc_lower)
    bb_width_pct = ((bb_upper - bb_lower) / mean) * 100.0 if mean > 0 else 0.0
    ratio = (bb_upper - bb_lower) / max(1e-9, (kc_upper - kc_lower))
    state = "COILED_SQUEEZE" if squeeze_on else ("TIGHT_COMPRESSION" if ratio < 1.15 else "EXPANDING")
    return {
        "squeeze_on": squeeze_on,
        "bb_kc_ratio": round(ratio, 3),
        "bb_width_pct": round(bb_width_pct, 2),
        "squeeze_state": state,
    }


def compute_ltf_microstructure(bars_15m: List[Dict[str, float]], bars_5m: List[Dict[str, float]]) -> Dict[str, Any]:
    """
    Computes:
      - 15m Session VWAP (rolling 32 bars = 8h funding session)
      - 15m 2.0-ATR Chandelier High/Low stops (for precision trailing ratchets)
      - 15m Liquidity Sweep + 5m CISD (Change in State of Delivery) micro trigger
    """
    if len(bars_15m) < 20 or len(bars_5m) < 15:
        return {}
    session_bars = bars_15m[-32:]
    tot_v = sum(b["v"] for b in session_bars)
    tot_pv = sum(((b["h"] + b["l"] + b["c"]) / 3.0) * b["v"] for b in session_bars)
    vwap_8h = tot_pv / tot_v if tot_v > 0 else bars_15m[-1]["c"]

    atr15 = atr_series(bars_15m, 14)[-1]
    hh_12 = max(b["h"] for b in bars_15m[-12:])
    ll_12 = min(b["l"] for b in bars_15m[-12:])
    # Chandelier stops:
    # For a SHORT position, trail above lowest_low_12 + 2.0 * ATR15
    # For a LONG position, trail below highest_high_12 - 2.0 * ATR15
    chandelier_long_sl = hh_12 - 2.0 * atr15
    chandelier_short_sl = ll_12 + 2.0 * atr15

    # Check if recent 3 15m bars swept prior 10-bar high/low and reclaimed (Sweep + Reclaim)
    prior_10_hi = max(b["h"] for b in bars_15m[-14:-3])
    prior_10_lo = min(b["l"] for b in bars_15m[-14:-3])
    recent_hi = max(b["h"] for b in bars_15m[-3:])
    recent_lo = min(b["l"] for b in bars_15m[-3:])
    cur_c = bars_15m[-1]["c"]

    bull_sweep_reclaim = (recent_lo <= prior_10_lo * 1.001) and (cur_c > prior_10_lo)
    bear_sweep_reclaim = (recent_hi >= prior_10_hi * 0.999) and (cur_c < prior_10_hi)

    # 5m CISD (Change in State of Delivery): 5m close vs 5m EMA9 & last 3-bar body structure
    c5 = [b["c"] for b in bars_5m]
    e9_5m = ema_series(c5, 9)[-1]
    cisd_bull = c5[-1] > e9_5m and bars_5m[-1]["c"] > bars_5m[-2]["h"]
    cisd_bear = c5[-1] < e9_5m and bars_5m[-1]["c"] < bars_5m[-2]["l"]

    if bull_sweep_reclaim or cisd_bull:
        ltf_trigger = "BULL_CISD_CONFIRMED" if (bull_sweep_reclaim and cisd_bull) else "BULL_MICRO_INFLECTION"
    elif bear_sweep_reclaim or cisd_bear:
        ltf_trigger = "BEAR_CISD_CONFIRMED" if (bear_sweep_reclaim and cisd_bear) else "BEAR_MICRO_INFLECTION"
    else:
        ltf_trigger = "ABOVE_VWAP_DRIFT" if cur_c >= vwap_8h else "BELOW_VWAP_DRIFT"

    return {
        "session_8h_vwap": round(vwap_8h, 6),
        "atr_15m": round(atr15, 6),
        "hh_3h": round(hh_12, 6),
        "ll_3h": round(ll_12, 6),
        "chandelier_long_sl_15m": round(chandelier_long_sl, 6),
        "chandelier_short_sl_15m": round(chandelier_short_sl, 6),
        "bull_sweep_reclaim_15m": bull_sweep_reclaim,
        "bear_sweep_reclaim_15m": bear_sweep_reclaim,
        "cisd_bull_5m": cisd_bull,
        "cisd_bear_5m": cisd_bear,
        "ltf_trigger": ltf_trigger,
    }


def compute_funding_clock() -> Dict[str, Any]:
    """Calculates time to/since 8h perpetual funding settlement (00:00, 08:00, 16:00 UTC)."""
    now = int(time.time())
    sec_in_8h = 8 * 3600
    rem_in_window = now % sec_in_8h
    mins_since = round(rem_in_window / 60.0, 1)
    mins_until = round((sec_in_8h - rem_in_window) / 60.0, 1)
    if mins_until <= 30:
        phase = "PRE_FUNDING_SQUEEZE_WINDOW"
    elif mins_since <= 30:
        phase = "POST_FUNDING_MEAN_REVERSION_WINDOW"
    else:
        phase = "MID_CYCLE_CONTINUATION"
    return {
        "utc_now": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "edt_now": time.strftime("%Y-%m-%d %H:%M:%S EDT", time.localtime(now)),
        "mins_since_last_settlement": mins_since,
        "mins_until_next_settlement": mins_until,
        "clock_phase": phase,
    }


def scan_single_pair(
    fut_sym: str,
    spot_sym: str,
    contract_map: Dict[str, Any],
    wf_map: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    try:
        with ThreadPoolExecutor(max_workers=6) as k_ex:
            f_1w = k_ex.submit(fetch_klines, spot_sym, "1week", 6)
            f_1d = k_ex.submit(fetch_klines, spot_sym, "1day", 45)
            f_4h = k_ex.submit(fetch_klines, spot_sym, "4hour", 140)
            f_1h = k_ex.submit(fetch_klines, spot_sym, "1hour", 60)
            f_15m = k_ex.submit(fetch_klines, spot_sym, "15min", 48)
            f_5m = k_ex.submit(fetch_klines, spot_sym, "5min", 36)
            b_1w = f_1w.result()
            b_1d = f_1d.result()
            b_4h = f_4h.result()
            b_1h = f_1h.result()
            b_15m = f_15m.result()
            b_5m = f_5m.result()

        if len(b_1d) < 20 or len(b_4h) < 100 or len(b_1h) < 25 or len(b_15m) < 20:
            return None

        c_spec = contract_map.get(fut_sym) or {}
        mark = float(c_spec.get("markPrice") or b_5m[-1]["c"])
        index_px = float(c_spec.get("indexPrice") or mark)
        funding_bps = round(float(c_spec.get("fundingFeeRate") or 0) * 10000.0, 2)
        basis_bps = round(((mark - index_px) / index_px) * 10000.0, 2) if index_px > 0 else 0.0
        tick = float(c_spec.get("tickSize") or 0.01)

        # 1. HTF (1W / 1D): PWH, PWL, PDH, PDL, Daily Equilibrium, 1D ADX(14)
        pwh = b_1w[-2]["h"] if len(b_1w) >= 2 else b_1w[-1]["h"]
        pwl = b_1w[-2]["l"] if len(b_1w) >= 2 else b_1w[-1]["l"]
        pdh = b_1d[-2]["h"]
        pdl = b_1d[-2]["l"]
        d_hi = max(pdh, b_1d[-1]["h"])
        d_lo = min(pdl, b_1d[-1]["l"])
        pd_eq = (d_hi + d_lo) / 2.0
        pd_zone_pct = round(((mark - d_lo) / max(1e-9, d_hi - d_lo)) * 100.0, 1)
        pd_zone = "DISCOUNT" if pd_zone_pct < 45.0 else ("PREMIUM" if pd_zone_pct > 55.0 else "EQUILIBRIUM")

        adx_1d, pdi_1d, mdi_1d = compute_adx14(b_1d, 14)
        htf_bias = "BULL" if pdi_1d > mdi_1d else "BEAR"
        adx_regime = "TREND_EXPANSION" if adx_1d >= 22.0 else ("MODERATE_TREND" if adx_1d >= 18.0 else "CHOP_RANGE")

        # 2. MTF (4h & 1h): 4h EMA20/100 + 1h Squeeze + 1h EMA20 + 1h RSI
        c4 = [b["c"] for b in b_4h]
        e20_4h = ema_series(c4, 20)[-1]
        e100_4h = ema_series(c4, 100)[-1]
        atr_4h = atr_series(b_4h, 14)[-1]
        dist_4h_atr = round((mark - e20_4h) / atr_4h, 2) if atr_4h > 0 else 0.0
        regime_4h = "BULL" if (e20_4h > e100_4h and mark >= e20_4h * 0.992) else ("BEAR" if (e20_4h < e100_4h and mark <= e20_4h * 1.008) else "NEUTRAL")

        c1 = [b["c"] for b in b_1h]
        e20_1h = ema_series(c1, 20)[-1]
        atr_1h = atr_series(b_1h, 14)[-1]
        dist_1h_atr = round((mark - e20_1h) / atr_1h, 2) if atr_1h > 0 else 0.0
        rsi_1h = compute_rsi14(c1, 14)
        sqz_1h = compute_1h_squeeze(b_1h)

        # 3. LTF (15m & 5m) Microstructure
        ltf = compute_ltf_microstructure(b_15m, b_5m)
        vwap_8h = ltf.get("session_8h_vwap", mark)

        # 4. Walk-Forward Lookup
        wf = wf_map.get(fut_sym) or {}
        wf_pass = bool(wf.get("gate_passed", False))
        wf_net_er = float((wf.get("regime_side_wf") or {}).get("net_expectancy_r") or 0.0)
        wf_pf = float((wf.get("regime_side_wf") or {}).get("profit_factor") or 0.0)

        # 5. Composite MTF Confluence Score (0-100) & Precision Maker Limit Calculation
        target_side = "LONG" if regime_4h == "BULL" else ("SHORT" if regime_4h == "BEAR" else ("LONG" if htf_bias == "BULL" else "SHORT"))
        score = 0
        reasons = []

        # (a) HTF + 4h Trend Alignment (up to 32 pts)
        expected_regime = "BULL" if target_side == "LONG" else "BEAR"
        if regime_4h == expected_regime:
            score += 20
            reasons.append(f"4H_{regime_4h}")
        if htf_bias == expected_regime:
            score += 12 if adx_1d >= 22.0 else 8
            reasons.append(f"1D_DI_{htf_bias}(ADX={adx_1d:.1f})")

        # (b) Walk-Forward OOS Expectancy Gate (up to 25 pts)
        if wf_pass:
            score += 15 + min(10, int(wf_net_er * 20))
            reasons.append(f"WF_PASS({wf_net_er:+.2f}R,PF={wf_pf:.2f})")

        # (c) Pullback Zone / PD Zone Alignment (up to 20 pts)
        if target_side == "LONG":
            if -0.35 <= dist_4h_atr <= 0.65:
                score += 15
                reasons.append(f"4H_EMA20_PULLBACK({dist_4h_atr:+.2f}ATR)")
            elif -0.60 <= dist_1h_atr <= 0.50:
                score += 10
                reasons.append(f"1H_EMA20_PULLBACK({dist_1h_atr:+.2f}ATR)")
            if pd_zone in ("DISCOUNT", "EQUILIBRIUM"):
                score += 5
        else:
            if -0.65 <= dist_4h_atr <= 0.35:
                score += 15
                reasons.append(f"4H_EMA20_PULLBACK({dist_4h_atr:+.2f}ATR)")
            elif -1.15 <= dist_4h_atr <= -0.65 and -0.80 <= dist_1h_atr <= 0.40:
                score += 14
                reasons.append(f"1H_EMA20_REJECTION({dist_1h_atr:+.2f}ATR)")
            elif -1.85 <= dist_4h_atr <= -1.15:
                score += 8
                reasons.append(f"TREND_CONTINUATION({dist_4h_atr:+.2f}ATR)")
            if pd_zone in ("PREMIUM", "EQUILIBRIUM") or mark < pdl:
                score += 5

        # (d) 1h Squeeze / Momentum & LTF Trigger (up to 15 pts)
        if sqz_1h["squeeze_state"] in ("COILED_SQUEEZE", "TIGHT_COMPRESSION"):
            score += 6
            reasons.append(f"1H_{sqz_1h['squeeze_state']}")
        if target_side == "LONG" and ("BULL" in ltf.get("ltf_trigger", "") or mark >= vwap_8h):
            score += 9
            reasons.append(ltf.get("ltf_trigger", ""))
        elif target_side == "SHORT" and ("BEAR" in ltf.get("ltf_trigger", "") or mark <= vwap_8h):
            score += 9
            reasons.append(ltf.get("ltf_trigger", ""))

        # (e) Funding Carry Alignment (up to 10 pts)
        if (target_side == "SHORT" and funding_bps > 0) or (target_side == "LONG" and funding_bps <= 1.0):
            score += 10
            reasons.append(f"FUNDING_OK({funding_bps:+.2f}bps)")

        score = min(100, score)

        # Precision Maker Entry & Structural MP Stop Calculation
        decimals = max(0, -int(round(math.log10(tick)))) if tick > 0 and tick < 1 else 0
        MIN_STRUCTURAL_STOP_PCT = 0.0185  # >= 1.85% structural floor (matches Propr stop room)
        if target_side == "LONG":
            # Blend 4h EMA20 and 15m support so we sit right at the structural pullback bid
            raw_entry = min(mark - tick, max(e20_4h, vwap_8h - 0.5 * ltf["atr_15m"])) if dist_4h_atr > 0.35 else min(mark - tick, e20_4h)
            stop_dist = max(1.65 * atr_4h, raw_entry * MIN_STRUCTURAL_STOP_PCT)
            raw_sl = raw_entry - stop_dist
            raw_tp = raw_entry + 1.85 * stop_dist
        else:
            # For SHORT: if close to 1h EMA20, offer at 1h EMA20 or top of 15m VWAP band; if ready now, offer at best ask
            raw_entry = max(mark + tick, min(e20_1h, vwap_8h + 0.35 * ltf["atr_15m"]))
            cand_sl = raw_entry + 1.65 * atr_4h if abs(raw_entry - e20_4h) < 0.8 * atr_4h else max(e20_4h + 0.15 * atr_4h, raw_entry + 1.65 * atr_4h)
            stop_dist = max(abs(cand_sl - raw_entry), raw_entry * MIN_STRUCTURAL_STOP_PCT)
            raw_sl = raw_entry + stop_dist
            raw_tp = raw_entry - 1.85 * stop_dist

        # Dynamic Volatility-Targeted Risk Sizing ($1.50 to $3.00 on $200 KuCoin book)
        if score >= 82 and wf_pass:
            target_risk_usd = 2.60
            tier = "A+_INSTITUTIONAL"
        elif score >= 70 and wf_pass:
            target_risk_usd = 2.10
            tier = "A_HIGH_CONVICTION"
        elif score >= 58:
            target_risk_usd = 1.60
            tier = "B_WATCHLIST"
        else:
            target_risk_usd = 0.0
            tier = "C_PASS"

        return {
            "fut_symbol": fut_sym,
            "spot_symbol": spot_sym,
            "mark_price": mark,
            "target_side": target_side,
            "mtf_confluence_score": score,
            "setup_tier": tier,
            "recommended_risk_usd": target_risk_usd,
            "htf_1w_1d": {
                "pwh": round(pwh, decimals),
                "pwl": round(pwl, decimals),
                "pdh": round(pdh, decimals),
                "pdl": round(pdl, decimals),
                "pd_eq": round(pd_eq, decimals),
                "pd_zone": pd_zone,
                "pd_zone_pct": pd_zone_pct,
                "adx_1d": adx_1d,
                "adx_regime": adx_regime,
                "di_bias_1d": htf_bias,
            },
            "mtf_4h_1h": {
                "regime_4h": regime_4h,
                "ema20_4h": round(e20_4h, decimals),
                "ema100_4h": round(e100_4h, decimals),
                "atr_4h": round(atr_4h, decimals),
                "dist_4h_atr": dist_4h_atr,
                "ema20_1h": round(e20_1h, decimals),
                "atr_1h": round(atr_1h, decimals),
                "dist_1h_atr": dist_1h_atr,
                "rsi_1h": rsi_1h,
                "squeeze_1h": sqz_1h,
            },
            "ltf_15m_5m": ltf,
            "funding_and_basis": {
                "funding_8h_bps": funding_bps,
                "basis_bps": basis_bps,
            },
            "walk_forward": {
                "gate_passed": wf_pass,
                "net_expectancy_r": wf_net_er,
                "profit_factor": wf_pf,
            },
            "precision_levels": {
                "maker_entry_px": round(raw_entry, decimals),
                "mp_stop_px": round(raw_sl, decimals),
                "maker_tp_px": round(raw_tp, decimals),
            },
            "confluence_factors": reasons,
        }
    except Exception as e:
        return {"fut_symbol": fut_sym, "error": str(e)}


def run_mtf_scan() -> Dict[str, Any]:
    t0 = time.time()
    contracts_raw = (http_get_json(f"{KC_FUT_URL}/api/v1/contracts/active") or {}).get("data") or []
    contract_map = {c["symbol"]: c for c in contracts_raw if "symbol" in c}

    wf_map = {}
    try:
        with open("/home/box/desk/kucoin/latest_walk_forward.json") as f:
            wf_data = json.load(f)
            wf_map = {r["fut_symbol"]: r for r in (wf_data.get("results") or [])}
    except Exception:
        pass
    geom_map = {}
    try:
        with open("/home/box/desk/kucoin/latest_candle_geometry.json") as f:
            g_data = json.load(f)
            geom_map = {r["fut_symbol"]: r for r in (g_data.get("results") or [])}
    except Exception:
        pass
    ind_map = {}
    try:
        with open("/home/box/desk/kucoin/latest_indicator_confirmation.json") as f:
            i_data = json.load(f)
            ind_map = {r["fut_symbol"]: r for r in (i_data.get("results") or [])}
    except Exception:
        pass

    results = []
    with ThreadPoolExecutor(max_workers=15) as ex:
        futs = [ex.submit(scan_single_pair, f_sym, s_sym, contract_map, wf_map) for f_sym, s_sym in UNIVERSE]
        for f in futs:
            r = f.result()
            if r and "error" not in r:
                sym = r["fut_symbol"]
                g = geom_map.get(sym) or {}
                ind = ind_map.get(sym) or {}
                r["candle_geometry"] = {
                    "composite_score": g.get("composite_geometry_score", 0.0),
                    "knn_bull_prob": g.get("empirical_knn_bull_prob", 0.5),
                    "knn_bear_prob": g.get("empirical_knn_bear_prob", 0.5),
                    "verdict": g.get("geometry_verdict", "NEUTRAL"),
                }
                r["indicator_confirmation"] = {
                    "domains_confirmed": ind.get("domains_confirmed", "0/4"),
                    "score": ind.get("indicator_confirmation_score", 0),
                    "verdict": ind.get("verdict", "UNCHECKED"),
                    "falsification_flags": ind.get("falsification_flags", []),
                    "domains": ind.get("domains", {}),
                }
                # Adjust MTF score with Geometry k-NN & 4-Domain Indicator Confirmation
                adj = 0
                if ind.get("indicator_confirmation_score", 0) == 100:
                    adj += 6
                    r["confluence_factors"].append("IND_4_OF_4_CONFIRMED")
                elif ind.get("indicator_confirmation_score", 0) <= 50:
                    adj -= 8
                    r["confluence_factors"].append(f"IND_WEAK({ind.get('domains_confirmed')})")
                if r["target_side"] == "SHORT" and g.get("empirical_knn_bear_prob", 0.5) >= 0.55:
                    adj += 5
                    r["confluence_factors"].append(f"GEOM_kNN_BEAR({g['empirical_knn_bear_prob']*100:.1f}%)")
                elif r["target_side"] == "LONG" and g.get("empirical_knn_bear_prob", 0.5) >= 0.57:
                    adj -= 6
                    r["confluence_factors"].append(f"GEOM_kNN_HEADWIND({g['empirical_knn_bear_prob']*100:.1f}%_BEAR)")
                r["mtf_confluence_score"] = max(0, min(100, r["mtf_confluence_score"] + adj))
                results.append(r)

    results.sort(key=lambda x: (x["mtf_confluence_score"], x["walk_forward"]["net_expectancy_r"]), reverse=True)
    clock = compute_funding_clock()
    out = {
        "as_of": clock["utc_now"],
        "edt_now": clock["edt_now"],
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "funding_clock": clock,
        "a_plus_count": sum(1 for r in results if r["mtf_confluence_score"] >= 80),
        "results": results,
    }
    with open("/home/box/desk/kucoin/latest_mtf_scan.json", "w") as f:
        json.dump(out, f, indent=2)
    return out


if __name__ == "__main__":
    res = run_mtf_scan()
    fc = res["funding_clock"]
    print(f"=== Institutional 5-Timescale MTF Hierarchy Scanner ({res['elapsed_ms']} ms) ===")
    print(f"Funding Clock: {fc['edt_now']} | {fc['clock_phase']} ({fc['mins_since_last_settlement']}m since / {fc['mins_until_next_settlement']}m until 8h settlement)")
    print(f"{'SYMBOL':<10} {'SIDE':<6} {'SCORE':>5} {'TIER':<17} {'1D_ADX':>6} {'PD_ZONE':<11} {'4H_DIST':>7} {'1H_DIST':>7} {'1H_SQZ':<17} {'LTF_15M_5M':<22} {'WF_E[R]':>8}")
    for r in res["results"]:
        h = r["htf_1w_1d"]
        m = r["mtf_4h_1h"]
        l = r["ltf_15m_5m"]
        w = r["walk_forward"]
        print(
            f"{r['fut_symbol']:<10} {r['target_side']:<6} {r['mtf_confluence_score']:>5} {r['setup_tier']:<17} "
            f"{h['adx_1d']:>6.1f} {h['pd_zone']:<11} {m['dist_4h_atr']:>+7.2f} {m['dist_1h_atr']:>+7.2f} "
            f"{m['squeeze_1h']['squeeze_state']:<17} {l.get('ltf_trigger',''):<22} {w['net_expectancy_r']:>+7.3f}R"
        )
