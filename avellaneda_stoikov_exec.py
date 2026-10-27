#!/usr/bin/env python3
r"""
Avellaneda-Stoikov Sub-Tick Maker Queue Placement & Microprice / OBI Toxic-Flow Guard
File: /home/box/desk/avellaneda_stoikov_exec.py

Implements continuous-time market-making & optimal limit-order placement math
(Avellaneda & Stoikov 2008, Stoikov 2018 Microprice, Cont-Kukanov-Stoikov 2014 OFI)
on live KuCoin Futures Level-2 (top-20) order books:

  1. Volume-Weighted Microprice (Stoikov 2018):
       I = V_{bid,1} / (V_{bid,1} + V_{ask,1})
       P_{micro} = I * P_{ask,1} + (1 - I) * P_{bid,1}

  2. Multi-Level Exponential Order-Book Imbalance (EOBI_20 & OBI_10):
       EOBI = \frac{\sum_{i=1}^{20} e^{-\alpha (i-1)} V_{bid,i} - \sum_{i=1}^{20} e^{-\alpha (i-1)} V_{ask,i}}
                   {\sum_{i=1}^{20} e^{-\alpha (i-1)} V_{bid,i} + \sum_{i=1}^{20} e^{-\alpha (i-1)} V_{ask,i}}

  3. Empirical L2 Order-Book Intensity Decay (\kappa) & Avellaneda-Stoikov Optimal Spread:
       r(s, q, \tau) = P_{micro} - q \cdot \gamma \cdot \sigma^2 \cdot \tau
       \delta^*(\tau) = \frac{1}{\gamma} \ln(1 + \frac{\gamma}{\kappa}) + \frac{1}{2} \gamma \sigma^2 \tau

  4. Sub-Tick Liquidity Wall Front-Running (Passive Maker Queue Priority):
       Locates the largest resting L2 liquidity wall on our side of the book and pegs
       the `postOnly` limit order exactly 1 tick in front of the wall while strictly
       respecting the BBO (`<= best_ask - tick` for LONG, `>= best_bid + tick` for SHORT).

  5. Toxic-Flow Adverse Selection Guard:
       Evaluates resting `postOnly` entry orders and open positions against L2 EOBI_20,
       Microprice drift, and liquidity-wall pull/spoof risk.
"""

import json
import math
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

KC_FUT_URL = "https://api-futures.kucoin.com"

UNIVERSE = [
    "XBTUSDTM", "ETHUSDTM", "SOLUSDTM", "XRPUSDTM", "ZECUSDTM",
    "XMRUSDTM", "DOGEUSDTM", "SUIUSDTM", "LINKUSDTM", "AVAXUSDTM",
    "TAOUSDTM", "AAVEUSDTM", "NEARUSDTM", "SEIUSDTM", "PEPEUSDTM",
]


def http_get_json(url: str, timeout: float = 6.0) -> Any:
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "AvellanedaStoikov/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def round_to_tick(px: float, tick: float) -> float:
    if tick <= 0:
        return round(px, 6)
    decimals = max(0, -int(round(math.log10(tick)))) if tick < 1 else 0
    return round(round(px / tick) * tick, decimals)


def analyze_l2_avellaneda(
    symbol: str,
    contract_spec: Dict[str, Any],
    target_side: str = "SHORT",
    structural_anchor_px: Optional[float] = None,
    inventory_q: float = 0.0,
    atr_15m_pct: float = 0.45,
) -> Dict[str, Any]:
    tick = float(contract_spec.get("tickSize") or 0.01)
    mult = abs(float(contract_spec.get("multiplier") or 0.01))
    mark = float(contract_spec.get("markPrice") or 0.0)
    decimals = max(0, -int(round(math.log10(tick)))) if 0 < tick < 1 else 0

    l2_raw = (http_get_json(f"{KC_FUT_URL}/api/v1/level2/depth20?symbol={symbol}") or {}).get("data") or {}
    bids = [[float(b[0]), float(b[1])] for b in (l2_raw.get("bids") or []) if len(b) >= 2]
    asks = [[float(a[0]), float(a[1])] for a in (l2_raw.get("asks") or []) if len(a) >= 2]

    if not bids or not asks:
        return {"symbol": symbol, "error": "empty_l2_book"}

    best_bid, v_bid1 = bids[0][0], bids[0][1]
    best_ask, v_ask1 = asks[0][0], asks[0][1]
    mid = (best_bid + best_ask) / 2.0
    spread_px = max(tick, best_ask - best_bid)
    spread_bps = (spread_px / mid) * 10000.0 if mid > 0 else 0.0

    # 1. Stoikov Volume-Weighted Microprice
    top_vol = max(1e-9, v_bid1 + v_ask1)
    imbalance_l1 = v_bid1 / top_vol
    microprice = imbalance_l1 * best_ask + (1.0 - imbalance_l1) * best_bid
    micro_drift_bps = ((microprice - mid) / mid) * 10000.0 if mid > 0 else 0.0

    # 2. Multi-Level Order Book Imbalance (OBI_10 and Exponentially-Weighted EOBI_20)
    bid_vol_10 = sum(b[1] for b in bids[:10])
    ask_vol_10 = sum(a[1] for a in asks[:10])
    obi_10 = (bid_vol_10 - ask_vol_10) / max(1e-9, bid_vol_10 + ask_vol_10)

    alpha_decay = 0.18
    ebid = sum(math.exp(-alpha_decay * i) * b[1] for i, b in enumerate(bids[:20]))
    eask = sum(math.exp(-alpha_decay * i) * a[1] for i, a in enumerate(asks[:20]))
    eobi_20 = (ebid - eask) / max(1e-9, ebid + eask)

    # 3. Empirical Book Liquidity Decay Parameter \kappa (in bps^-1)
    # Measure depth accumulation across the 20 levels
    bid_span_bps = max(0.5, ((mid - bids[-1][0]) / mid) * 10000.0)
    ask_span_bps = max(0.5, ((asks[-1][0] - mid) / mid) * 10000.0)
    avg_span_bps = (bid_span_bps + ask_span_bps) / 2.0
    top3_vol = sum(b[1] for b in bids[:3]) + sum(a[1] for a in asks[:3])
    top20_vol = sum(b[1] for b in bids[:20]) + sum(a[1] for a in asks[:20])
    kappa = max(0.05, min(2.50, math.log(max(1.1, top20_vol / max(1.0, top3_vol))) / max(1.0, avg_span_bps * 0.5)))

    # 4. Avellaneda-Stoikov Reservation Price r(s, q, \tau) & Optimal Half-Spread \delta^*
    # Use risk aversion \gamma = 0.20, horizon \tau = 0.25 (15m bar fraction of 1h), volatility \sigma in bps
    gamma = 0.20
    sigma_bps = max(8.0, atr_15m_pct * 100.0)  # 15m volatility in bps
    tau = 0.25
    # Inventory-adjusted reservation shift in bps
    inv_shift_bps = inventory_q * gamma * ((sigma_bps / 10.0) ** 2) * tau
    # Microprice + EOBI drift adjustment in bps
    obi_shift_bps = eobi_20 * max(1.5, spread_bps * 0.8)
    reservation_shift_bps = micro_drift_bps + obi_shift_bps - inv_shift_bps
    reservation_px = mid * (1.0 + reservation_shift_bps / 10000.0)

    # Optimal half-spread in bps: (1/\gamma)*ln(1 + \gamma/\kappa) + 0.5 * \gamma * (\sigma/10)^2 * \tau
    opt_half_spread_bps = (1.0 / gamma) * math.log(1.0 + gamma / kappa) + 0.5 * gamma * ((sigma_bps / 10.0) ** 2) * tau
    opt_half_spread_bps = max(spread_bps * 0.5, min(45.0, opt_half_spread_bps))

    as_bid_px = round_to_tick(min(best_ask - tick, reservation_px * (1.0 - opt_half_spread_bps / 10000.0)), tick)
    as_ask_px = round_to_tick(max(best_bid + tick, reservation_px * (1.0 + opt_half_spread_bps / 10000.0)), tick)

    # 5. Locate Largest L2 Liquidity Wall & Compute Sub-Tick Front-Run Maker Price
    bid_wall = max(bids, key=lambda x: x[1])
    ask_wall = max(asks, key=lambda x: x[1])
    bid_wall_px, bid_wall_lots = bid_wall[0], bid_wall[1]
    ask_wall_px, ask_wall_lots = ask_wall[0], ask_wall[1]

    # Front-run the wall by 1 tick (unless the wall is already at best_bid/best_ask and spread is 1 tick)
    l2_frontrun_bid = round_to_tick(min(best_ask - tick, bid_wall_px + tick if bid_wall_px < best_ask - tick else best_bid), tick)
    l2_frontrun_ask = round_to_tick(max(best_bid + tick, ask_wall_px - tick if ask_wall_px > best_bid + tick else best_ask), tick)

    # Blend with structural anchor if provided:
    # If structural anchor is inside the visible L2 book (within 25 bps of mid), snap to 1 tick in front of the L2 wall!
    # If structural anchor is a deeper pullback level, adjust the structural anchor by the Avellaneda-Stoikov reservation offset + 1 tick inside round numbers.
    anchor = structural_anchor_px if (structural_anchor_px and structural_anchor_px > 0) else (as_bid_px if target_side == "LONG" else as_ask_px)
    dist_anchor_bps = abs(anchor - mid) / mid * 10000.0 if mid > 0 else 0.0

    if target_side == "LONG":
        if dist_anchor_bps <= max(25.0, bid_span_bps * 1.2):
            optimal_maker_px = min(best_ask - tick, max(as_bid_px, l2_frontrun_bid))
            placement_mode = "L2_WALL_SUBTICK_FRONTRUN_BID"
        else:
            # Structural pullback bid: apply Avellaneda-Stoikov microprice/OBI tilt + 1 tick above round level
            tilt_factor = 1.0 + max(-0.0015, min(0.0015, (eobi_20 * 0.0006)))
            optimal_maker_px = round_to_tick(min(best_ask - tick, anchor * tilt_factor + tick), tick)
            placement_mode = "STRUCTURAL_PULLBACK_AS_TILTED_BID"
    else:
        if dist_anchor_bps <= max(25.0, ask_span_bps * 1.2):
            optimal_maker_px = max(best_bid + tick, min(as_ask_px, l2_frontrun_ask))
            placement_mode = "L2_WALL_SUBTICK_FRONTRUN_ASK"
        else:
            tilt_factor = 1.0 + max(-0.0015, min(0.0015, (eobi_20 * 0.0006)))
            optimal_maker_px = round_to_tick(max(best_bid + tick, anchor * tilt_factor - tick), tick)
            placement_mode = "STRUCTURAL_PULLBACK_AS_TILTED_ASK"

    # 6. Toxic-Flow / Adverse-Selection Score for target_side
    # Positive toxicity means L2 order book is aggressively pushing AGAINST target_side
    opposing_obi = (-eobi_20) if target_side == "LONG" else eobi_20
    opposing_micro = (-micro_drift_bps) if target_side == "LONG" else micro_drift_bps
    toxicity_score = round(max(0.0, min(100.0, (opposing_obi + 0.35) * 55.0 + max(0.0, opposing_micro) * 8.0)), 1)
    if toxicity_score >= 68.0:
        flow_safety = "TOXIC_ADVERSE_FLOW_WARNING"
    elif toxicity_score >= 45.0:
        flow_safety = "MODERATE_OPPOSING_PRESSURE"
    else:
        flow_safety = "FAVORABLE_MAKER_FLOW"

    return {
        "symbol": symbol,
        "target_side": target_side,
        "bbo": {
            "best_bid": best_bid,
            "best_ask": best_ask,
            "mid_price": round(mid, decimals + 1),
            "mark_price": mark,
            "spread_px": round(spread_px, decimals + 1),
            "spread_bps": round(spread_bps, 2),
            "tick_size": tick,
        },
        "microprice_and_obi": {
            "microprice": round(microprice, decimals + 2),
            "micro_drift_bps": round(micro_drift_bps, 2),
            "l1_bid_share": round(imbalance_l1, 3),
            "obi_top10": round(obi_10, 3),
            "eobi_top20": round(eobi_20, 3),
            "top20_bid_usd": round(sum(b[0] * b[1] * mult for b in bids), 2),
            "top20_ask_usd": round(sum(a[0] * a[1] * mult for a in asks), 2),
        },
        "avellaneda_stoikov": {
            "kappa_decay": round(kappa, 3),
            "gamma_risk_aversion": gamma,
            "sigma_15m_bps": round(sigma_bps, 2),
            "reservation_price": round(reservation_px, decimals + 1),
            "reservation_shift_bps": round(reservation_shift_bps, 2),
            "optimal_half_spread_bps": round(opt_half_spread_bps, 2),
            "as_optimal_bid": as_bid_px,
            "as_optimal_ask": as_ask_px,
        },
        "liquidity_walls": {
            "bid_wall_px": bid_wall_px,
            "bid_wall_usd": round(bid_wall_px * bid_wall_lots * mult, 2),
            "ask_wall_px": ask_wall_px,
            "ask_wall_usd": round(ask_wall_px * ask_wall_lots * mult, 2),
            "l2_frontrun_bid": l2_frontrun_bid,
            "l2_frontrun_ask": l2_frontrun_ask,
        },
        "execution_recommendation": {
            "structural_anchor_px": round(anchor, decimals),
            "optimal_post_only_px": round(optimal_maker_px, decimals),
            "placement_mode": placement_mode,
            "toxicity_score": toxicity_score,
            "flow_safety": flow_safety,
        },
    }


def run_avellaneda_stoikov_scan(mtf_results: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    t0 = time.time()
    contracts_raw = (http_get_json(f"{KC_FUT_URL}/api/v1/contracts/active") or {}).get("data") or []
    contract_map = {c["symbol"]: c for c in contracts_raw if "symbol" in c}

    if mtf_results is None:
        try:
            with open("/home/box/desk/kucoin/latest_mtf_scan.json") as f:
                mtf_results = json.load(f).get("results") or []
        except Exception:
            mtf_results = []

    mtf_map = {r["fut_symbol"]: r for r in mtf_results if "fut_symbol" in r}
    syms = [r["fut_symbol"] for r in mtf_results] if mtf_results else UNIVERSE

    results: List[Dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=15) as ex:
        futs = []
        for sym in syms:
            c_spec = contract_map.get(sym) or {}
            m = mtf_map.get(sym) or {}
            t_side = m.get("target_side", "SHORT")
            anchor_px = (m.get("precision_levels") or {}).get("maker_entry_px")
            mark_px = float(c_spec.get("markPrice") or 1.0)
            atr_15m = float((m.get("ltf_15m_5m") or {}).get("atr_15m") or (mark_px * 0.0045))
            atr_15m_pct = (atr_15m / mark_px * 100.0) if mark_px > 0 else 0.45
            futs.append(ex.submit(analyze_l2_avellaneda, sym, c_spec, t_side, anchor_px, 0.0, atr_15m_pct))
        for f in futs:
            try:
                res = f.result()
                if "error" not in res:
                    results.append(res)
            except Exception:
                pass

    out = {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "edt_now": time.strftime("%Y-%m-%d %H:%M:%S EDT", time.localtime()),
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "results": results,
    }
    with open("/home/box/desk/kucoin/latest_avellaneda_stoikov.json", "w") as f:
        json.dump(out, f, indent=2)
    return out


if __name__ == "__main__":
    rep = run_avellaneda_stoikov_scan()
    print(f"=== Avellaneda-Stoikov Sub-Tick Maker Queue & Microprice Engine ({rep['elapsed_ms']} ms) ===")
    print(f"{'SYMBOL':<10} {'SIDE':<6} {'MID_PX':>10} {'MICRO_BPS':>9} {'EOBI_20':>8} {'OPT_HALF_SP':>11} {'WALL_USD':>11} {'OPT_MAKER_PX':>12} {'TOXIC':>6} {'SAFETY':<26}")
    for r in rep["results"]:
        b = r["bbo"]
        mo = r["microprice_and_obi"]
        av = r["avellaneda_stoikov"]
        lw = r["liquidity_walls"]
        ex = r["execution_recommendation"]
        w_usd = lw["bid_wall_usd"] if r["target_side"] == "LONG" else lw["ask_wall_usd"]
        print(
            f"{r['symbol']:<10} {r['target_side']:<6} {b['mid_price']:>10.4f} {mo['micro_drift_bps']:>+8.2f}bp "
            f"{mo['eobi_top20']:>+8.3f} {av['optimal_half_spread_bps']:>9.2f}bp ${w_usd:>10,.0f} "
            f"{ex['optimal_post_only_px']:>12.4f} {ex['toxicity_score']:>6.1f} {ex['flow_safety']:<26}"
        )
