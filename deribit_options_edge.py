#!/usr/bin/env python3
r"""
Deribit Options Implied Volatility (DVOL), Put/Call Skew & Gamma/Max-Pain Scraper
File: /home/box/desk/deribit_options_edge.py

Queries Deribit's public zero-auth v2 API for BTC and ETH options:
  1. `public/get_book_summary_by_currency?currency={BTC|ETH}&kind=option`
  2. `public/get_index_price?index_name={btc_usd|eth_usd}`
  3. `public/get_historical_volatility?currency={BTC|ETH}`
  4. `public/get_volatility_index_data?currency={BTC|ETH}` (30d DVOL Index)

Computes:
  - Total Open Interest (contracts & USD notional) and 24h Volume by Strike
  - Exact Options Max-Pain Magnet Strike (strike minimizing aggregate intrinsic option payout)
  - Largest Gamma Pin / Dealer Wall Strikes (highest total & near-spot OI strikes)
  - Put/Call Open Interest Ratio & 24h Volume Put/Call Ratio
  - OTM 25-Delta-Equivalent Put vs Call Implied Volatility Skew:
      \text{Skew}_{25\Delta} = \overline{\text{IV}}_{\text{OTM Puts (5-15\%)}} - \overline{\text{IV}}_{\text{OTM Calls (5-15\%)}}
    Classifies Options Skew Regime:
      - `BEARISH_PUT_SKEW`  (Put IV > Call IV + 2.0 vol pts or P/C OI > 0.85 with put skew)
      - `BULLISH_CALL_SKEW` (Call IV > Put IV + 2.0 vol pts and P/C OI < 0.70)
      - `NEUTRAL_BALANCED_SKEW`
  - Implied vs. Realized Volatility Spread (`IV - RV` = DVOL - 30d Historical Volatility)
"""

import collections
import datetime
import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

DERIBIT_BASE = "https://www.deribit.com/api/v2/public"
OUTPUT_JSON = "/home/box/desk/kucoin/latest_deribit_options.json"


def http_get_json(url: str, timeout: float = 10.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 DeribitOptionsEdge/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_instrument(inst_name: str) -> Optional[Tuple[str, str, float, str]]:
    """
    Parses Deribit option instrument name e.g. 'BTC-27NOV26-73000-P' -> ('BTC', '27NOV26', 73000.0, 'P')
    """
    parts = str(inst_name or "").split("-")
    if len(parts) != 4:
        return None
    asset, exp_str, strike_str, opt_type = parts
    try:
        strike = float(strike_str)
    except Exception:
        return None
    if opt_type not in ("C", "P"):
        return None
    return (asset, exp_str, strike, opt_type)


def parse_expiry_days(exp_str: str, now_utc: datetime.datetime) -> float:
    try:
        dt = datetime.datetime.strptime(exp_str, "%d%b%y").replace(
            hour=8, minute=0, second=0, tzinfo=datetime.timezone.utc
        )
        return max(0.05, (dt - now_utc).total_seconds() / 86400.0)
    except Exception:
        return 30.0


def analyze_currency_options(currency: str) -> Dict[str, Any]:
    idx_name = "btc_usd" if currency == "BTC" else "eth_usd"
    now_ms = int(time.time() * 1000)
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    with ThreadPoolExecutor(max_workers=4) as ex:
        f_book = ex.submit(http_get_json, f"{DERIBIT_BASE}/get_book_summary_by_currency?currency={currency}&kind=option")
        f_idx = ex.submit(http_get_json, f"{DERIBIT_BASE}/get_index_price?index_name={idx_name}")
        f_hv = ex.submit(http_get_json, f"{DERIBIT_BASE}/get_historical_volatility?currency={currency}")
        f_dvol = ex.submit(
            http_get_json,
            f"{DERIBIT_BASE}/get_volatility_index_data?currency={currency}&start_timestamp={now_ms - 86400000}&end_timestamp={now_ms + 3600000}&resolution=3600",
        )
        book_res = (f_book.result() or {}).get("result") or []
        idx_res = (f_idx.result() or {}).get("result") or {}
        hv_res = (f_hv.result() or {}).get("result") or []
        dvol_res = ((f_dvol.result() or {}).get("result") or {}).get("data") or []

    spot = float(idx_res.get("index_price") or 0.0)
    rv_annual = float(hv_res[-1][1]) if hv_res else 0.0
    dvol_current = float(dvol_res[-1][4]) if dvol_res else 0.0
    dvol_24h_ago = float(dvol_res[0][4]) if dvol_res else dvol_current
    dvol_24h_chg = dvol_current - dvol_24h_ago

    call_oi_By_strike: Dict[float, float] = collections.defaultdict(float)
    put_oi_by_strike: Dict[float, float] = collections.defaultdict(float)
    total_oi_by_strike: Dict[float, float] = collections.defaultdict(float)
    near_35d_oi_by_strike: Dict[float, float] = collections.defaultdict(float)

    total_call_oi = 0.0
    total_put_oi = 0.0
    total_call_vol = 0.0
    total_put_vol = 0.0

    otm_put_ivs: List[Tuple[float, float]] = []
    otm_call_ivs: List[Tuple[float, float]] = []
    atm_ivs: List[Tuple[float, float]] = []

    for row in book_res:
        parsed = parse_instrument(row.get("instrument_name", ""))
        if not parsed:
            continue
        _, exp_str, strike, opt_type = parsed
        oi = float(row.get("open_interest") or 0.0)
        vol = float(row.get("volume") or 0.0)
        mark_iv = float(row.get("mark_iv") or 0.0)
        days_to_exp = parse_expiry_days(exp_str, now_utc)

        if opt_type == "C":
            call_oi_By_strike[strike] += oi
            total_call_oi += oi
            total_call_vol += vol
        else:
            put_oi_by_strike[strike] += oi
            total_put_oi += oi
            total_put_vol += vol

        total_oi_by_strike[strike] += oi
        if days_to_exp <= 38.0:
            near_35d_oi_by_strike[strike] += oi

        # Collect IV skew across liquid 3d-45d options
        if spot > 0 and 2.0 <= days_to_exp <= 45.0 and oi >= 5.0 and 10.0 <= mark_iv <= 200.0:
            moneyness_pct = (strike - spot) / spot * 100.0
            weight = math.log(2.0 + oi)
            if abs(moneyness_pct) <= 3.0:
                atm_ivs.append((mark_iv, weight))
            elif opt_type == "P" and -14.0 <= moneyness_pct <= -3.0:
                otm_put_ivs.append((mark_iv, weight))
            elif opt_type == "C" and 3.0 <= moneyness_pct <= 14.0:
                otm_call_ivs.append((mark_iv, weight))

    def weighted_avg(pairs: List[Tuple[float, float]], default: float) -> float:
        if not pairs:
            return default
        w_sum = sum(w for _, w in pairs)
        return sum(v * w for v, w in pairs) / w_sum if w_sum > 0 else default

    atm_iv = weighted_avg(atm_ivs, dvol_current)
    otm_put_iv = weighted_avg(otm_put_ivs, atm_iv)
    otm_call_iv = weighted_avg(otm_call_ivs, atm_iv)
    put_call_iv_skew = otm_put_iv - otm_call_iv  # positive = puts more expensive than calls (bearish crash protection)

    # Compute exact Max-Pain Strike across all active strikes
    all_strikes = sorted(total_oi_by_strike.keys())
    max_pain_strike = spot
    if all_strikes:
        best_pain = None
        for s_settle in all_strikes:
            # Restrict max-pain candidate search to reasonable band around spot [0.55*spot, 1.45*spot]
            if spot > 0 and (s_settle < 0.55 * spot or s_settle > 1.45 * spot):
                continue
            call_payout = sum(max(0.0, s_settle - k) * oi_c for k, oi_c in call_oi_By_strike.items())
            put_payout = sum(max(0.0, k - s_settle) * oi_p for k, oi_p in put_oi_by_strike.items())
            tot_payout = call_payout + put_payout
            if best_pain is None or tot_payout < best_pain:
                best_pain = tot_payout
                max_pain_strike = s_settle

    # Find Largest Gamma Wall / Pin Strikes within +/- 18% of spot
    near_spot_strikes = [
        (k, near_35d_oi_by_strike.get(k, 0.0), total_oi_by_strike[k], call_oi_By_strike[k], put_oi_by_strike[k])
        for k in all_strikes
        if spot > 0 and abs(k - spot) / spot <= 0.18
    ]
    near_spot_strikes.sort(key=lambda x: (x[1], x[2]), reverse=True)
    gamma_magnet_strike = near_spot_strikes[0][0] if near_spot_strikes else max_pain_strike

    top_oi_strikes = [
        {
            "strike": k,
            "dist_from_spot_pct": round((k - spot) / spot * 100.0, 2) if spot > 0 else 0.0,
            "near_35d_oi_contracts": round(n_oi, 1),
            "total_oi_contracts": round(t_oi, 1),
            "total_oi_notional_usd": round(t_oi * spot, 0),
            "call_oi": round(c_oi, 1),
            "put_oi": round(p_oi, 1),
        }
        for k, n_oi, t_oi, c_oi, p_oi in near_spot_strikes[:6]
    ]

    pc_oi_ratio = total_put_oi / total_call_oi if total_call_oi > 0 else 1.0
    pc_vol_ratio = total_put_vol / total_call_vol if total_call_vol > 0 else 1.0
    iv_minus_rv = (dvol_current or atm_iv) - rv_annual

    if put_call_iv_skew >= 2.0 or (put_call_iv_skew >= 0.8 and pc_oi_ratio >= 0.75):
        skew_regime = "BEARISH_PUT_SKEW"
    elif put_call_iv_skew <= -2.0 or (put_call_iv_skew <= -0.8 and pc_oi_ratio <= 0.60):
        skew_regime = "BULLISH_CALL_SKEW"
    else:
        skew_regime = "NEUTRAL_BALANCED_SKEW"

    # Signed directional options impulse in [-100, +100]: positive = bullish, negative = bearish
    # Driven by call vs put skew, max-pain pull, and 24h volume P/C ratio
    max_pain_dist_pct = (max_pain_strike - spot) / spot * 100.0 if spot > 0 else 0.0
    gamma_dist_pct = (gamma_magnet_strike - spot) / spot * 100.0 if spot > 0 else 0.0
    signed_options_impulse = max(
        -100.0,
        min(
            100.0,
            -put_call_iv_skew * 7.5
            - (pc_vol_ratio - 0.75) * 28.0
            + max_pain_dist_pct * 1.8
            + gamma_dist_pct * 2.2,
        ),
    )

    return {
        "currency": currency,
        "index_price_usd": round(spot, 2),
        "options_contracts_scanned": len(book_res),
        "total_open_interest_contracts": round(total_call_oi + total_put_oi, 1),
        "total_open_interest_usd": round((total_call_oi + total_put_oi) * spot, 0),
        "call_oi_contracts": round(total_call_oi, 1),
        "put_oi_contracts": round(total_put_oi, 1),
        "put_call_oi_ratio": round(pc_oi_ratio, 4),
        "put_call_24h_vol_ratio": round(pc_vol_ratio, 4),
        "dvol_30d_implied_vol_pct": round(dvol_current, 2),
        "dvol_24h_change_pts": round(dvol_24h_chg, 2),
        "realized_30d_hv_pct": round(rv_annual, 2),
        "iv_minus_rv_spread_pts": round(iv_minus_rv, 2),
        "atm_iv_pct": round(atm_iv, 2),
        "otm_put_iv_pct": round(otm_put_iv, 2),
        "otm_call_iv_pct": round(otm_call_iv, 2),
        "put_minus_call_25d_skew_pts": round(put_call_iv_skew, 2),
        "skew_regime": skew_regime,
        "max_pain_strike": max_pain_strike,
        "max_pain_dist_pct": round(max_pain_dist_pct, 2),
        "gamma_magnet_strike": gamma_magnet_strike,
        "gamma_magnet_dist_pct": round(gamma_dist_pct, 2),
        "signed_options_impulse": round(signed_options_impulse, 2),
        "top_near_spot_gamma_walls": top_oi_strikes,
    }


def run_deribit_options_edge() -> Dict[str, Any]:
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=2) as ex:
        f_btc = ex.submit(analyze_currency_options, "BTC")
        f_eth = ex.submit(analyze_currency_options, "ETH")
        btc = f_btc.result()
        eth = f_eth.result()

    composite_impulse = round(0.65 * btc["signed_options_impulse"] + 0.35 * eth["signed_options_impulse"], 2)
    composite_pc_ratio = round(0.65 * btc["put_call_oi_ratio"] + 0.35 * eth["put_call_oi_ratio"], 4)
    if btc["skew_regime"] == "BEARISH_PUT_SKEW" or eth["skew_regime"] == "BEARISH_PUT_SKEW":
        macro_options_regime = "BEARISH_PUT_SKEW_PROTECTION"
    elif btc["skew_regime"] == "BULLISH_CALL_SKEW" and eth["skew_regime"] == "BULLISH_CALL_SKEW":
        macro_options_regime = "BULLISH_CALL_SKEW_CHASING"
    else:
        macro_options_regime = "NEUTRAL_GAMMA_PIN"

    out = {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "edt_now": time.strftime("%Y-%m-%d %H:%M:%S EDT", time.localtime()),
        "elapsed_ms": round((time.time() - t0) * 1000.0, 1),
        "summary": {
            "total_options_notional_usd": round(btc["total_open_interest_usd"] + eth["total_open_interest_usd"], 0),
            "macro_options_regime": macro_options_regime,
            "composite_put_call_oi_ratio": composite_pc_ratio,
            "composite_options_impulse": composite_impulse,
            "btc_dvol_pct": btc["dvol_30d_implied_vol_pct"],
            "btc_rv_pct": btc["realized_30d_hv_pct"],
            "btc_iv_rv_spread_pts": btc["iv_minus_rv_spread_pts"],
            "btc_max_pain": btc["max_pain_strike"],
            "btc_gamma_magnet": btc["gamma_magnet_strike"],
            "eth_dvol_pct": eth["dvol_30d_implied_vol_pct"],
            "eth_rv_pct": eth["realized_30d_hv_pct"],
            "eth_iv_rv_spread_pts": eth["iv_minus_rv_spread_pts"],
            "eth_max_pain": eth["max_pain_strike"],
            "eth_gamma_magnet": eth["gamma_magnet_strike"],
        },
        "BTC": btc,
        "ETH": eth,
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    return out


if __name__ == "__main__":
    res = run_deribit_options_edge()
    s = res["summary"]
    print(f"=== Deribit Options DVOL, Put/Call Skew & Gamma/Max-Pain Engine ({res['elapsed_ms']} ms) ===")
    print(
        f"Total Options OI: ${s['total_options_notional_usd']:,.0f} | Regime: {s['macro_options_regime']} | "
        f"Composite P/C OI: {s['composite_put_call_oi_ratio']:.3f} | Impulse: {s['composite_options_impulse']:+.2f}"
    )
    for cur in ("BTC", "ETH"):
        c = res[cur]
        print(
            f"\n[{cur}] Spot=${c['index_price_usd']:,.2f} | Contracts={c['options_contracts_scanned']} "
            f"(OI={c['total_open_interest_contracts']:,.0f} = ${c['total_open_interest_usd']:,.0f}) | "
            f"P/C OI={c['put_call_oi_ratio']:.3f} (24hVol P/C={c['put_call_24h_vol_ratio']:.3f})\n"
            f"  DVOL={c['dvol_30d_implied_vol_pct']:.2f}% vs 30d RV={c['realized_30d_hv_pct']:.2f}% "
            f"(IV-RV Spread={c['iv_minus_rv_spread_pts']:+.2f} pts) | "
            f"OTM Put IV={c['otm_put_iv_pct']:.2f}% vs Call IV={c['otm_call_iv_pct']:.2f}% "
            f"(25d Skew={c['put_minus_call_25d_skew_pts']:+.2f} pts -> {c['skew_regime']})\n"
            f"  Max-Pain Magnet=${c['max_pain_strike']:,.0f} ({c['max_pain_dist_pct']:+.2f}%) | "
            f"Nearest Gamma Wall=${c['gamma_magnet_strike']:,.0f} ({c['gamma_magnet_dist_pct']:+.2f}%)"
        )
        for w in c["top_near_spot_gamma_walls"][:4]:
            print(
                f"    * Strike ${w['strike']:,.0f} ({w['dist_from_spot_pct']:+.2f}%): "
                f"Near35d_OI={w['near_35d_oi_contracts']:,.0f} | Total_OI={w['total_oi_contracts']:,.0f} "
                f"(${w['total_oi_notional_usd']:,.0f} | Call={w['call_oi']:,.0f}, Put={w['put_oi']:,.0f})"
            )
