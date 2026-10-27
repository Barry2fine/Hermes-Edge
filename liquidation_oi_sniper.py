#!/usr/bin/env python3
r"""
Cross-Exchange Liquidation Cascade, Open-Interest (OI) Delta & Basis Dislocation Sniper
File: /home/box/desk/liquidation_oi_sniper.py

Pulls real-time Open Interest (OI), 5m/15m/30m OI Delta, Taker Buy/Sell CVD Ratio,
Funding Rates, and Mark/Index/Oracle Prices across 3 venues simultaneously:
  1. Hyperliquid Perps (`api.hyperliquid.xyz/info` -> `metaAndAssetCtxs`)
  2. Binance USD-M Futures (`fapi.binance.com` -> `premiumIndex`, `openInterestHist`, `takerlongshortRatio`, `klines`)
  3. KuCoin Futures (`api-futures.kucoin.com/api/v1/contracts/active`)

Mathematical Formulations:
  1. Forced Liquidation Cascade / OI Wipeout Detector:
     - Tracks 5m & 15m Open Interest fractional change:
         \Delta OI_{15m} = (OI_t - OI_{t-15m}) / OI_{t-15m}
     - Tracks 15m ATR-normalized price displacement and wick rejection:
         z_{disp} = |C_t - O_{t-15m}| / ATR_{15m}
     - Long Liquidation Flush (Bullish Exhaustion Reversal):
         Price drops sharply (ret_15m < -0.60 * ATR_15m or lower wick > 45% of range)
         WHILE Open Interest contracts (\Delta OI_{15m} < -0.50% or \Delta OI_{30m} < -1.0%)
         AND Taker Buy/Sell Ratio rebounds (> 1.05) or basis snaps into deep discount.
     - Short Squeeze Flush (Bearish Exhaustion Reversal):
         Price spikes sharply (ret_15m > +0.60 * ATR_15m or upper wick > 45% of range)
         WHILE Open Interest contracts (\Delta OI_{15m} < -0.50% or \Delta OI_{30m} < -1.0%)
         AND Taker Buy/Sell Ratio fades (< 0.95).
     - Fresh Leverage Expansion (Breakout Continuation):
         \Delta OI_{15m} > +0.80% with directional Taker CVD confirmation (Taker Ratio > 1.15 for LONG, < 0.87 for SHORT).

  2. Cross-Venue Mark-Price & Basis Dislocation (KuCoin vs Hyperliquid & Binance):
     - Computes fair consensus perpetual price:
         P_{fair} = 0.55 * P_{Binance} + 0.45 * P_{Hyperliquid}
     - Dislocation in bps:
         \Delta_{KC\_lag\_bps} = ((P_{KuCoin} - P_{fair}) / P_{fair}) * 10000
     - Flags when KuCoin perp lags the sharp Hyperliquid/Binance move by >= 8 bps.
"""

import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

KC_FUT_URL = "https://api-futures.kucoin.com"
BN_FUT_URL = "https://fapi.binance.com"
HL_INFO_URL = "https://api.hyperliquid.xyz/info"

# Map (KuCoin Symbol, Binance Symbol, Hyperliquid Coin)
UNIVERSE_MAP: List[Tuple[str, str, str]] = [
    ("XBTUSDTM", "BTCUSDT", "BTC"),
    ("ETHUSDTM", "ETHUSDT", "ETH"),
    ("SOLUSDTM", "SOLUSDT", "SOL"),
    ("XRPUSDTM", "XRPUSDT", "XRP"),
    ("ZECUSDTM", "ZECUSDT", "ZEC"),
    ("XMRUSDTM", "XMRUSDT", "XMR"),
    ("DOGEUSDTM", "DOGEUSDT", "DOGE"),
    ("SUIUSDTM", "SUIUSDT", "SUI"),
    ("LINKUSDTM", "LINKUSDT", "LINK"),
    ("AVAXUSDTM", "AVAXUSDT", "AVAX"),
    ("TAOUSDTM", "TAOUSDT", "TAO"),
    ("AAVEUSDTM", "AAVEUSDT", "AAVE"),
    ("NEARUSDTM", "NEARUSDT", "NEAR"),
    ("SEIUSDTM", "SEIUSDT", "SEI"),
    ("PEPEUSDTM", "1000PEPEUSDT", "kPEPE"),
    ("WIFUSDTM", "WIFUSDT", "WIF"),
    ("ADAUSDTM", "ADAUSDT", "ADA"),
    ("LTCUSDTM", "LTCUSDT", "LTC"),
    ("BCHUSDTM", "BCHUSDT", "BCH"),
    ("UNIUSDTM", "UNIUSDT", "UNI"),
    ("WLDUSDTM", "WLDUSDT", "WLD"),
    ("ARBUSDTM", "ARBUSDT", "ARB"),
    ("OPUSDTM", "OPUSDT", "OP"),
    ("APTUSDTM", "APTUSDT", "APT"),
    ("INJUSDTM", "INJUSDT", "INJ"),
    ("TIAUSDTM", "TIAUSDT", "TIA"),
    ("DOTUSDTM", "DOTUSDT", "DOT"),
]


def http_json(url: str, data: Optional[bytes] = None, timeout: float = 7.0) -> Any:
    headers = {
        "Accept": "application/json",
        "Content-Type": "application/json",
        "User-Agent": "LiquidationOISniper/1.0",
    }
    req = urllib.request.Request(url, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def fetch_hyperliquid_map() -> Dict[str, Dict[str, float]]:
    try:
        res = http_json(HL_INFO_URL, data=b'{"type":"metaAndAssetCtxs"}', timeout=7.0)
        universe = res[0].get("universe") or []
        ctxs = res[1] or []
        out: Dict[str, Dict[str, float]] = {}
        for meta, ctx in zip(universe, ctxs):
            name = meta.get("name")
            if not name:
                continue
            mark = float(ctx.get("markPx") or 0.0)
            oracle = float(ctx.get("oraclePx") or mark)
            oi_coins = float(ctx.get("openInterest") or 0.0)
            funding_1h = float(ctx.get("funding") or 0.0)
            day_vol = float(ctx.get("dayNtlVlm") or 0.0)
            basis_bps = ((mark - oracle) / oracle * 10000.0) if oracle > 0 else 0.0
            out[name] = {
                "hl_mark": mark,
                "hl_oracle": oracle,
                "hl_basis_bps": round(basis_bps, 2),
                "hl_oi_usd": round(oi_coins * mark, 2),
                "hl_funding_8h_bps": round(funding_1h * 8.0 * 10000.0, 2),
                "hl_vol24h_usd": round(day_vol, 0),
            }
        return out
    except Exception:
        return {}


def fetch_binance_premium_map() -> Dict[str, Dict[str, float]]:
    try:
        rows = http_json(f"{BN_FUT_URL}/fapi/v1/premiumIndex", timeout=7.0)
        out: Dict[str, Dict[str, float]] = {}
        for r in rows:
            sym = r.get("symbol")
            if not sym:
                continue
            mark = float(r.get("markPrice") or 0.0)
            index_px = float(r.get("indexPrice") or mark)
            fund = float(r.get("lastFundingRate") or 0.0)
            basis_bps = ((mark - index_px) / index_px * 10000.0) if index_px > 0 else 0.0
            out[sym] = {
                "bn_mark": mark,
                "bn_index": index_px,
                "bn_basis_bps": round(basis_bps, 2),
                "bn_funding_8h_bps": round(fund * 10000.0, 2),
                "bn_next_funding_ms": int(r.get("nextFundingTime") or 0),
            }
        return out
    except Exception:
        return {}


def fetch_kucoin_contracts_map() -> Dict[str, Dict[str, Any]]:
    try:
        rows = (http_json(f"{KC_FUT_URL}/api/v1/contracts/active", timeout=4.5) or {}).get("data") or []
        return {r["symbol"]: r for r in rows if "symbol" in r}
    except Exception:
        return {}


def analyze_single_asset(
    kc_sym: str,
    bn_sym: str,
    hl_coin: str,
    kc_map: Dict[str, Dict[str, Any]],
    bn_map: Dict[str, Dict[str, float]],
    hl_map: Dict[str, Dict[str, float]],
) -> Dict[str, Any]:
    kc = kc_map.get(kc_sym) or {}
    bn = bn_map.get(bn_sym) or {}
    hl = hl_map.get(hl_coin) or {}

    kc_mark = float(kc.get("markPrice") or 0.0)
    kc_index = float(kc.get("indexPrice") or kc_mark)
    kc_basis_bps = round(((kc_mark - kc_index) / kc_index * 10000.0), 2) if kc_index > 0 else 0.0
    kc_funding_bps = round(float(kc.get("fundingFeeRate") or 0.0) * 10000.0, 2)
    kc_pred_funding_bps = round(float(kc.get("predictedFundingFeeRate") or 0.0) * 10000.0, 2)
    kc_mult = abs(float(kc.get("multiplier") or 0.01))
    kc_oi_lots = float(kc.get("openInterest") or 0.0)
    kc_oi_usd = round(kc_oi_lots * kc_mult * kc_mark, 2)

    # Normalize 1000PEPE / kPEPE price scale to match KuCoin PEPEUSDTM
    scale_factor = 1000.0 if bn_sym.startswith("1000") else 1.0
    bn_mark = (bn.get("bn_mark", 0.0) / scale_factor) if bn.get("bn_mark") else 0.0
    hl_mark = (hl.get("hl_mark", 0.0) / scale_factor) if hl.get("hl_mark") else 0.0

    # Fetch Binance 5m OI history (last 7 points = 30m), 5m Taker Buy/Sell ratio, and 5m klines
    oi_delta_5m_pct = 0.0
    oi_delta_15m_pct = 0.0
    oi_delta_30m_pct = 0.0
    bn_oi_usd = 0.0
    taker_buy_sell_ratio_5m = 1.0
    taker_buy_sell_ratio_15m = 1.0
    ret_15m_pct = 0.0
    atr_15m_pct = 0.5
    wick_Exhaustion_dir = "NONE"

    try:
        oi_hist = http_json(f"{BN_FUT_URL}/futures/data/openInterestHist?symbol={bn_sym}&period=5m&limit=7", timeout=4.5)
        if isinstance(oi_hist, list) and len(oi_hist) >= 2:
            oi_now = float(oi_hist[-1].get("sumOpenInterest") or 0.0)
            bn_oi_usd = round(float(oi_hist[-1].get("sumOpenInterestValue") or 0.0), 0)
            oi_5m = float(oi_hist[-2].get("sumOpenInterest") or oi_now)
            oi_15m = float(oi_hist[max(0, len(oi_hist) - 4)].get("sumOpenInterest") or oi_now)
            oi_30m = float(oi_hist[0].get("sumOpenInterest") or oi_now)
            if oi_5m > 0:
                oi_delta_5m_pct = round((oi_now - oi_5m) / oi_5m * 100.0, 3)
            if oi_15m > 0:
                oi_delta_15m_pct = round((oi_now - oi_15m) / oi_15m * 100.0, 3)
            if oi_30m > 0:
                oi_delta_30m_pct = round((oi_now - oi_30m) / oi_30m * 100.0, 3)
    except Exception:
        pass

    try:
        t_hist = http_json(f"{BN_FUT_URL}/futures/data/takerlongshortRatio?symbol={bn_sym}&period=5m&limit=4", timeout=4.5)
        if isinstance(t_hist, list) and len(t_hist) >= 1:
            taker_buy_sell_ratio_5m = round(float(t_hist[-1].get("buySellRatio") or 1.0), 3)
            buy_sum = sum(float(x.get("buyVol") or 0.0) for x in t_hist[-3:])
            sell_sum = sum(float(x.get("sellVol") or 0.0) for x in t_hist[-3:])
            if sell_sum > 0:
                taker_buy_sell_ratio_15m = round(buy_sum / sell_sum, 3)
    except Exception:
        pass

    try:
        kl = http_json(f"{BN_FUT_URL}/fapi/v1/klines?symbol={bn_sym}&interval=5m&limit=16", timeout=4.5)
        if isinstance(kl, list) and len(kl) >= 6:
            c_now = float(kl[-1][4])
            c_15m = float(kl[-4][1])  # open 3 bars ago (15m)
            h_15m = max(float(x[2]) for x in kl[-3:])
            l_15m = min(float(x[3]) for x in kl[-3:])
            rng_15m = max(h_15m - l_15m, c_now * 1e-6)
            ret_15m_pct = round((c_now - c_15m) / c_15m * 100.0, 3) if c_15m > 0 else 0.0
            # Compute 15m ATR equivalent from 3-bar groups
            trs = [float(x[2]) - float(x[3]) for x in kl[-12:]]
            atr_5m = sum(trs) / len(trs) if trs else c_now * 0.003
            atr_15m_pct = round((atr_5m * math.sqrt(3.0)) / c_now * 100.0, 3) if c_now > 0 else 0.5
            lower_wick_share = (min(c_now, c_15m) - l_15m) / rng_15m
            upper_wick_share = (h_15m - max(c_now, c_15m)) / rng_15m
            if lower_wick_share >= 0.45:
                wick_Exhaustion_dir = "BULL_LOWER_WICK_ABSORPTION"
            elif upper_wick_share >= 0.45:
                wick_Exhaustion_dir = "BEAR_UPPER_WICK_REJECTION"
    except Exception:
        pass

    # Compute Cross-Venue Fair Price & KuCoin Lag Dislocation (bps)
    ref_prices = []
    if bn_mark > 0:
        ref_prices.append((bn_mark, 0.55))
    if hl_mark > 0:
        ref_prices.append((hl_mark, 0.45))
    if ref_prices:
        w_sum = sum(w for _, w in ref_prices)
        fair_px = sum(p * w for p, w in ref_prices) / w_sum
        kc_vs_fair_bps = round(((kc_mark - fair_px) / fair_px) * 10000.0, 2) if fair_px > 0 else 0.0
    else:
        fair_px = kc_mark
        kc_vs_fair_bps = 0.0

    # Compute Liquidation Cascade / OI Delta Regime & Score (-100 Bearish to +100 Bullish)
    # 1. Forced Long Liquidation Exhaustion (OI drops while price dipped -> contrarian BULL bounce)
    # 2. Forced Short Squeeze Exhaustion (OI drops while price spiked -> contrarian BEAR fade)
    # 3. Fresh OI Expansion + Taker CVD Alignment (trend continuation)
    oi_regime = "NEUTRAL_OI_DRIFT"
    cascade_signal = "NEUTRAL"
    sniper_score = 0.0  # signed score: positive = bullish flow/squeeze, negative = bearish flow/flush

    eff_oi_delta = oi_delta_15m_pct if abs(oi_delta_15m_pct) >= abs(oi_delta_30m_pct) * 0.6 else oi_delta_30m_pct
    cvd_signed = math.log(max(0.2, min(5.0, taker_buy_sell_ratio_15m)))  # >0 buyer aggression, <0 seller aggression

    if eff_oi_delta <= -0.35 and ret_15m_pct < -0.15:
        if taker_buy_sell_ratio_5m >= 1.02 or wick_Exhaustion_dir == "BULL_LOWER_WICK_ABSORPTION":
            oi_regime = "LONG_LIQ_EXHAUSTION_REVERSAL"
            cascade_signal = "SNIPE_LONG_FLUSH_BOTTOM"
            sniper_score = round(min(100.0, abs(eff_oi_delta) * 35.0 + max(0.0, cvd_signed * 30.0) + 25.0), 1)
        else:
            oi_regime = "ACTIVE_LONG_LIQ_CASCADE"
            cascade_signal = "RIDE_SHORT_FLUSH_OR_WAIT"
            sniper_score = -round(min(100.0, abs(eff_oi_delta) * 35.0 + abs(min(0.0, cvd_signed)) * 30.0 + 20.0), 1)
    elif eff_oi_delta <= -0.35 and ret_15m_pct > 0.15:
        if taker_buy_sell_ratio_5m <= 0.98 or wick_Exhaustion_dir == "BEAR_UPPER_WICK_REJECTION":
            oi_regime = "SHORT_SQUEEZE_EXHAUSTION_REVERSAL"
            cascade_signal = "SNIPE_SHORT_SQUEEZE_TOP"
            sniper_score = -round(min(100.0, abs(eff_oi_delta) * 35.0 + abs(min(0.0, cvd_signed)) * 30.0 + 25.0), 1)
        else:
            oi_regime = "ACTIVE_SHORT_COVER_RALLY"
            cascade_signal = "RIDE_LONG_SQUEEZE"
            sniper_score = round(min(100.0, abs(eff_oi_delta) * 35.0 + max(0.0, cvd_signed) * 30.0 + 20.0), 1)
    elif eff_oi_delta >= 0.35:
        if taker_buy_sell_ratio_15m >= 1.08 and ret_15m_pct >= 0:
            oi_regime = "FRESH_OI_LONG_BUILDUP"
            cascade_signal = "BULL_OI_BREAKOUT_CONFIRM"
            sniper_score = round(min(100.0, eff_oi_delta * 30.0 + cvd_signed * 35.0 + 15.0), 1)
        elif taker_buy_sell_ratio_15m <= 0.92 and ret_15m_pct <= 0:
            oi_regime = "FRESH_OI_SHORT_BUILDUP"
            cascade_signal = "BEAR_OI_BREAKDOWN_CONFIRM"
            sniper_score = -round(min(100.0, eff_oi_delta * 30.0 + abs(cvd_signed) * 35.0 + 15.0), 1)
        else:
            oi_regime = "CONTESTED_OI_ABSORPTION"
            cascade_signal = "HIGH_OI_COMPRESSION"
            sniper_score = round(cvd_signed * 25.0, 1)
    else:
        sniper_score = round(cvd_signed * 22.0 + (ret_15m_pct / max(0.2, atr_15m_pct)) * 8.0, 1)
        if taker_buy_sell_ratio_15m >= 1.15:
            oi_regime = "TAKER_CVD_BUY_DOMINANCE"
            cascade_signal = "BULL_TAKER_SWEEP"
        elif taker_buy_sell_ratio_15m <= 0.86:
            oi_regime = "TAKER_CVD_SELL_DOMINANCE"
            cascade_signal = "BEAR_TAKER_SWEEP"

    return {
        "fut_symbol": kc_sym,
        "bn_symbol": bn_sym,
        "hl_coin": hl_coin,
        "prices": {
            "kucoin_mark": kc_mark,
            "binance_mark": round(bn_mark, 6) if bn_mark else None,
            "hyperliquid_mark": round(hl_mark, 6) if hl_mark else None,
            "consensus_fair_px": round(fair_px, 6),
            "kucoin_vs_fair_bps": kc_vs_fair_bps,
        },
        "basis_and_funding": {
            "kucoin_basis_bps": kc_basis_bps,
            "binance_basis_bps": bn.get("bn_basis_bps", 0.0),
            "hyperliquid_basis_bps": hl.get("hl_basis_bps", 0.0),
            "kucoin_funding_8h_bps": kc_funding_bps,
            "kucoin_pred_funding_8h_bps": kc_pred_funding_bps,
            "binance_funding_8h_bps": bn.get("bn_funding_8h_bps", 0.0),
            "hyperliquid_funding_8h_bps": hl.get("hl_funding_8h_bps", 0.0),
        },
        "open_interest": {
            "kucoin_oi_usd": kc_oi_usd,
            "binance_oi_usd": bn_oi_usd,
            "hyperliquid_oi_usd": hl.get("hl_oi_usd", 0.0),
            "total_3venue_oi_usd": round(kc_oi_usd + bn_oi_usd + hl.get("hl_oi_usd", 0.0), 0),
            "oi_delta_5m_pct": oi_delta_5m_pct,
            "oi_delta_15m_pct": oi_delta_15m_pct,
            "oi_delta_30m_pct": oi_delta_30m_pct,
        },
        "order_flow": {
            "taker_buy_sell_ratio_5m": taker_buy_sell_ratio_5m,
            "taker_buy_sell_ratio_15m": taker_buy_sell_ratio_15m,
            "ret_15m_pct": ret_15m_pct,
            "atr_15m_pct": atr_15m_pct,
            "wick_exhaustion": wick_Exhaustion_dir,
        },
        "oi_regime": oi_regime,
        "cascade_signal": cascade_signal,
        "signed_sniper_score": max(-100.0, min(100.0, sniper_score)),
    }


def run_liquidation_oi_sniper() -> Dict[str, Any]:
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_hl = ex.submit(fetch_hyperliquid_map)
        f_bn = ex.submit(fetch_binance_premium_map)
        f_kc = ex.submit(fetch_kucoin_contracts_map)
        hl_map = f_hl.result()
        bn_map = f_bn.result()
        kc_map = f_kc.result()

    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=15) as ex:
        futs = [
            ex.submit(analyze_single_asset, kc_sym, bn_sym, hl_coin, kc_map, bn_map, hl_map)
            for kc_sym, bn_sym, hl_coin in UNIVERSE_MAP
        ]
        for f in futs:
            try:
                results.append(f.result())
            except Exception:
                pass

    results.sort(key=lambda x: abs(x.get("signed_sniper_score", 0.0)), reverse=True)
    total_oi = sum(r["open_interest"]["total_3venue_oi_usd"] for r in results)
    active_cascades = [r for r in results if r["oi_regime"] != "NEUTRAL_OI_DRIFT"]
    dislocations = [r for r in results if abs(r["prices"]["kucoin_vs_fair_bps"]) >= 5.0]

    out = {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "edt_now": time.strftime("%Y-%m-%d %H:%M:%S EDT", time.localtime()),
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "summary": {
            "assets_scanned": len(results),
            "total_3venue_open_interest_usd": round(total_oi, 0),
            "active_oi_flow_signals_count": len(active_cascades),
            "cross_venue_dislocations_count": len(dislocations),
            "top_bull_flow": next((r["fut_symbol"] for r in results if r["signed_sniper_score"] > 0), None),
            "top_bear_flow": next((r["fut_symbol"] for r in results if r["signed_sniper_score"] < 0), None),
        },
        "active_signals": active_cascades[:12],
        "cross_venue_dislocations": dislocations,
        "results": results,
    }
    with open("/home/box/desk/kucoin/latest_liquidation_oi_sniper.json", "w") as f:
        json.dump(out, f, indent=2)
    return out


if __name__ == "__main__":
    rep = run_liquidation_oi_sniper()
    print(f"=== Cross-Exchange Liquidation & OI Delta Sniper ({rep['elapsed_ms']} ms) ===")
    s = rep["summary"]
    print(
        f"Scanned {s['assets_scanned']} perps across KuCoin + Binance + Hyperliquid | "
        f"Total 3-Venue OI=${s['total_3venue_open_interest_usd']:,.0f} | "
        f"Active OI/CVD Signals={s['active_oi_flow_signals_count']}"
    )
    print(f"{'SYMBOL':<10} {'KC_MARK':>10} {'KC_LAG_BPS':>10} {'3V_OI_USD':>14} {'dOI_15M%':>9} {'dOI_30M%':>9} {'TAKER_15M':>9} {'SCORE':>7} {'OI_REGIME':<32}")
    for r in rep["results"][:16]:
        p = r["prices"]
        oi = r["open_interest"]
        of = r["order_flow"]
        print(
            f"{r['fut_symbol']:<10} {p['kucoin_mark']:>10.4f} {p['kucoin_vs_fair_bps']:>+10.2f} "
            f"${oi['total_3venue_oi_usd']:>13,.0f} {oi['oi_delta_15m_pct']:>+8.3f}% {oi['oi_delta_30m_pct']:>+8.3f}% "
            f"{of['taker_buy_sell_ratio_15m']:>9.3f} {r['signed_sniper_score']:>+7.1f} {r['oi_regime']:<32}"
        )
