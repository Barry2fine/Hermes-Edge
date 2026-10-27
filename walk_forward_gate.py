#!/usr/bin/env python3
"""
Pre-Trade Walk-Forward & Fee-Drag Gate
File: /home/box/desk/walk_forward_gate.py

Tests any candidate symbol, side, and strategy playbook over historical 4h bars
using a strict chronological walk-forward split (In-Sample + Out-of-Sample),
deducting real round-trip fees and slippage:
  - KuCoin Futures Maker-Maker RT: 4 bps (2 bps entry + 2 bps TP)
  - KuCoin Futures Maker-Stop RT: 10 bps (2 bps entry + 6 bps Taker stop + 2 bps stop slip)
  - Propr IOC-Stop RT: 14 bps (4.5 bps entry + 4.5 bps exit + 5 bps slip)

Promotion Gate Requirements:
  1. Fee Drag per Trade <= 0.12R
  2. Net Expectancy E[R] after fees >= +0.10R
  3. Profit Factor >= 1.15
  4. Max Drawdown <= 4.0R
"""

import json
import math
import sys
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

KC_SPOT_URL = "https://api.kucoin.com"

CANDIDATE_UNIVERSE = [
    ("XMRUSDTM", "XMR-USDT"),
    ("SEIUSDTM", "SEI-USDT"),
    ("ETHUSDTM", "ETH-USDT"),
    ("SOLUSDTM", "SOL-USDT"),
    ("XBTUSDTM", "BTC-USDT"),
    ("ZECUSDTM", "ZEC-USDT"),
    ("SUIUSDTM", "SUI-USDT"),
    ("DOGEUSDTM", "DOGE-USDT"),
    ("XRPUSDTM", "XRP-USDT"),
    ("PEPEUSDTM", "PEPE-USDT"),
    ("TAOUSDTM", "TAO-USDT"),
    ("AAVEUSDTM", "AAVE-USDT"),
    ("LINKUSDTM", "LINK-USDT"),
    ("AVAXUSDTM", "AVAX-USDT"),
    ("NEARUSDTM", "NEAR-USDT"),
]


def fetch_4h_bars(spot_symbol: str, lookback_bars: int = 420) -> List[Dict[str, float]]:
    now_s = int(time.time())
    start_s = now_s - lookback_bars * 14400
    url = f"{KC_SPOT_URL}/api/v1/market/candles?type=4hour&symbol={spot_symbol}&startAt={start_s}&endAt={now_s}"
    req = urllib.request.Request(url, headers={"Accept": "application/json", "User-Agent": "WalkForwardGate/1.0"})
    with urllib.request.urlopen(req, timeout=10) as r:
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
    a = 2.0 / (span + 1.0)
    out: List[float] = []
    for i, v in enumerate(vals):
        out.append(v if i == 0 else v * a + out[-1] * (1.0 - a))
    return out


def atr_series(bars: List[Dict[str, float]], period: int = 14) -> List[float]:
    trs: List[float] = [0.0]
    for i in range(1, len(bars)):
        h, l, pc = bars[i]["h"], bars[i]["l"], bars[i - 1]["c"]
        trs.append(max(h - l, abs(h - pc), abs(l - pc)))
    out: List[float] = []
    for i in range(len(bars)):
        if i < period:
            out.append(sum(trs[1 : i + 1]) / max(1, i))
        else:
            out.append(sum(trs[i - period + 1 : i + 1]) / period)
    return out


def simulate_pullback_strategy(
    bars: List[Dict[str, float]],
    target_side: Optional[str] = None,
    venue: str = "KUCOIN_FUTURES",
    sl_atr_mult: float = 2.0,
    tp_atr_mult: float = 3.0,
    time_stop_bars: int = 12,
) -> Dict[str, Any]:
    """
    Walk-forward simulation of 4h EMA20/100 Trend + Pullback Maker Entry with:
      - Maker Limit Entry at EMA20 +/- 0.15*ATR14 (filled if next bar range crosses entry)
      - Structural MP Stop at 2.0*ATR14
      - Stay-Green Profit Lock: once unrealized peak >= 1.0R, ratchet SL to +0.35R net
      - Maker TP at 3.0*ATR14 (1.5R) or Time-Stop after 12 bars (48h)
    """
    if len(bars) < 120:
        return {"ok": False, "reason": "insufficient_bars"}

    closes = [b["c"] for b in bars]
    e20 = ema_series(closes, 20)
    e100 = ema_series(closes, 100)
    atrs = atr_series(bars, 14)

    # Fee schedule:
    # KuCoin Futures: Maker entry (2bp) + Maker TP (2bp) = 4bp RT; Maker entry (2bp) + Taker Stop (6bp) + 2bp slip = 10bp RT
    if venue == "KUCOIN_FUTURES":
        fee_rt_tp = 0.0004
        fee_rt_stop = 0.0010
        fee_rt_time = 0.0006
    else:
        fee_rt_tp = 0.0011
        fee_rt_stop = 0.0014
        fee_rt_time = 0.0011

    trades: List[Dict[str, Any]] = []
    i = 100
    n = len(bars)
    split_idx = 100 + int((n - 100) * 0.45)  # First 45% In-Sample, Last 55% Out-of-Sample (including recent regime)

    while i < n - 2:
        c = closes[i]
        a = atrs[i]
        if a <= 0 or c <= 0:
            i += 1
            continue

        bull = e20[i] > e100[i] and c >= e20[i] * 0.995
        bear = e20[i] < e100[i] and c <= e20[i] * 1.005
        dist_atr = (c - e20[i]) / a

        side: Optional[str] = None
        if bull and -0.35 <= dist_atr <= 0.90:
            side = "LONG"
        elif bear and -0.90 <= dist_atr <= 0.35:
            side = "SHORT"

        if side is None or (target_side and side != target_side.upper()):
            i += 1
            continue

        # Maker entry resting at pullback level
        entry = e20[i]
        next_bar = bars[i + 1]
        # Check if Maker limit order fills on bar i+1
        filled = (next_bar["l"] <= entry) if side == "LONG" else (next_bar["h"] >= entry)
        if not filled:
            i += 1
            continue

        stop_dist = sl_atr_mult * a
        tp_dist = tp_atr_mult * a
        sl = entry - stop_dist if side == "LONG" else entry + stop_dist
        tp = entry + tp_dist if side == "LONG" else entry - tp_dist

        # Simulate forward from bar i+1 up to time_stop_bars
        exit_px = entry
        exit_type = "TIME_STOP"
        armed_lock = False
        locked_sl = sl
        exit_bar_idx = min(n - 1, i + 1 + time_stop_bars)

        for j in range(i + 1, min(n, i + 1 + time_stop_bars)):
            bj = bars[j]
            if side == "LONG":
                if bj["l"] <= locked_sl:
                    exit_px = locked_sl
                    exit_type = "PROFIT_LOCK_SL" if armed_lock else "STOP_LOSS"
                    exit_bar_idx = j
                    break
                if bj["h"] >= tp:
                    exit_px = tp
                    exit_type = "TAKE_PROFIT"
                    exit_bar_idx = j
                    break
                if bj["h"] >= entry + 1.05 * stop_dist:
                    armed_lock = True
                    locked_sl = max(locked_sl, entry + 0.35 * stop_dist)
            else:
                if bj["h"] >= locked_sl:
                    exit_px = locked_sl
                    exit_type = "PROFIT_LOCK_SL" if armed_lock else "STOP_LOSS"
                    exit_bar_idx = j
                    break
                if bj["l"] <= tp:
                    exit_px = tp
                    exit_type = "TAKE_PROFIT"
                    exit_bar_idx = j
                    break
                if bj["l"] <= entry - 1.05 * stop_dist:
                    armed_lock = True
                    locked_sl = min(locked_sl, entry - 0.35 * stop_dist)
        else:
            exit_px = bars[exit_bar_idx]["c"]

        gross_move = (exit_px - entry) if side == "LONG" else (entry - exit_px)
        gross_r = gross_move / stop_dist

        fee_rate = fee_rt_tp if exit_type == "TAKE_PROFIT" else (fee_rt_stop if "SL" in exit_type or "STOP" in exit_type else fee_rt_time)
        fee_r = (entry * fee_rate) / stop_dist
        net_r = gross_r - fee_r

        trades.append({
            "bar_idx": i,
            "sample": "OOS" if i >= split_idx else "IS",
            "side": side,
            "entry": round(entry, 6),
            "exit": round(exit_px, 6),
            "exit_type": exit_type,
            "gross_r": round(gross_r, 4),
            "fee_r": round(fee_r, 4),
            "net_r": round(net_r, 4),
        })
        i = max(i + 2, exit_bar_idx + 1)

    def _summarize(subset: List[Dict[str, Any]]) -> Dict[str, Any]:
        if not subset:
            return {
                "trades": 0,
                "win_rate": 0.0,
                "gross_expectancy_r": 0.0,
                "avg_fee_drag_r": 0.0,
                "net_expectancy_r": 0.0,
                "sum_net_r": 0.0,
                "profit_factor": 0.0,
                "max_dd_r": 0.0,
            }
        cnt = len(subset)
        wins = [x["net_r"] for x in subset if x["net_r"] > 0]
        losses = [-x["net_r"] for x in subset if x["net_r"] <= 0]
        gross_e = sum(x["gross_r"] for x in subset) / cnt
        fee_e = sum(x["fee_r"] for x in subset) / cnt
        net_e = sum(x["net_r"] for x in subset) / cnt
        pf = (sum(wins) / sum(losses)) if sum(losses) > 0 else (99.0 if sum(wins) > 0 else 0.0)

        eq_curve = 0.0
        peak = 0.0
        max_dd = 0.0
        for x in subset:
            eq_curve += x["net_r"]
            peak = max(peak, eq_curve)
            max_dd = max(max_dd, peak - eq_curve)

        return {
            "trades": cnt,
            "win_rate": round(len(wins) / cnt, 3),
            "gross_expectancy_r": round(gross_e, 3),
            "avg_fee_drag_r": round(fee_e, 4),
            "net_expectancy_r": round(net_e, 3),
            "sum_net_r": round(sum(x["net_r"] for x in subset), 2),
            "profit_factor": round(pf, 2),
            "max_dd_r": round(max_dd, 2),
        }

    all_stats = _summarize(trades)
    oos_trades = [t for t in trades if t["sample"] == "OOS"]
    oos_stats = _summarize(oos_trades)

    # Current live bar regime & fee-drag check
    cur_c = closes[-1]
    cur_a = atrs[-1]
    cur_bull = e20[-1] > e100[-1] and cur_c > e20[-1]
    cur_bear = e20[-1] < e100[-1] and cur_c < e20[-1]
    cur_regime = "BULL" if cur_bull else ("BEAR" if cur_bear else "NEUTRAL")
    cur_dist_atr = (cur_c - e20[-1]) / cur_a if cur_a > 0 else 0.0
    cur_stop_dist = sl_atr_mult * cur_a
    live_fee_drag_r = (cur_c * fee_rt_tp) / cur_stop_dist if cur_stop_dist > 0 else 1.0

    # Walk-forward promotion gate
    eval_stats = oos_stats if oos_stats["trades"] >= 4 else all_stats
    gate_reasons = []
    if eval_stats["trades"] < 3:
        gate_reasons.append(f"LOW_SAMPLE({eval_stats['trades']}<3)")
    if eval_stats["net_expectancy_r"] < 0.10:
        gate_reasons.append(f"LOW_NET_EXPECTANCY({eval_stats['net_expectancy_r']:+.3f}R<+0.10R)")
    if eval_stats["profit_factor"] < 1.15:
        gate_reasons.append(f"LOW_PF({eval_stats['profit_factor']}<1.15)")
    if live_fee_drag_r > 0.12:
        gate_reasons.append(f"HIGH_FEE_DRAG({live_fee_drag_r:.3f}R>0.12R)")
    if eval_stats["max_dd_r"] > 4.0:
        gate_reasons.append(f"HIGH_DD({eval_stats['max_dd_r']}R>4.0R)")

    return {
        "ok": True,
        "live_regime": cur_regime,
        "live_close": cur_c,
        "live_ema20": round(e20[-1], 6),
        "live_ema100": round(e100[-1], 6),
        "live_atr14": round(cur_a, 6),
        "live_dist_ema20_atr": round(cur_dist_atr, 2),
        "live_maker_fee_drag_r": round(live_fee_drag_r, 4),
        "full_sample": all_stats,
        "out_of_sample": oos_stats,
        "gate_passed": len(gate_reasons) == 0,
        "gate_reasons": gate_reasons,
    }


def evaluate_universe() -> Dict[str, Any]:
    t0 = time.time()
    results = []

    def _worker(pair: Tuple[str, str]) -> Optional[Dict[str, Any]]:
        fut_sym, spot_sym = pair
        try:
            bars = fetch_4h_bars(spot_sym, 420)
            # Test in alignment with live regime or overall
            sim = simulate_pullback_strategy(bars, target_side=None, venue="KUCOIN_FUTURES")
            if not sim.get("ok"):
                return None
            # Also test directional regime side specifically
            dir_side = "LONG" if sim["live_regime"] == "BULL" else ("SHORT" if sim["live_regime"] == "BEAR" else None)
            dir_sim = simulate_pullback_strategy(bars, target_side=dir_side, venue="KUCOIN_FUTURES") if dir_side else sim
            return {
                "fut_symbol": fut_sym,
                "spot_symbol": spot_sym,
                "live_regime": sim["live_regime"],
                "live_close": sim["live_close"],
                "live_ema20": sim["live_ema20"],
                "live_atr14": sim["live_atr14"],
                "dist_ema20_atr": sim["live_dist_ema20_atr"],
                "maker_fee_drag_r": sim["live_maker_fee_drag_r"],
                "overall_wf": sim["full_sample"],
                "oos_wf": sim["out_of_sample"],
                "regime_side_wf": dir_sim["full_sample"],
                "gate_passed": dir_sim["gate_passed"],
                "gate_reasons": dir_sim["gate_reasons"] if not dir_sim["gate_passed"] else [],
            }
        except Exception as e:
            return {"fut_symbol": fut_sym, "spot_symbol": spot_sym, "error": str(e)}

    with ThreadPoolExecutor(max_workers=6) as ex:
        for r in ex.map(_worker, CANDIDATE_UNIVERSE):
            if r and "error" not in r:
                results.append(r)

    results.sort(key=lambda x: x["regime_side_wf"]["net_expectancy_r"], reverse=True)
    out = {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "passed_count": sum(1 for r in results if r["gate_passed"]),
        "results": results,
    }
    with open("/home/box/desk/kucoin/latest_walk_forward.json", "w") as f:
        json.dump(out, f, indent=2)
    return out


if __name__ == "__main__":
    out = evaluate_universe()
    print(f"=== Walk-Forward & Fee-Drag Gate ({out['elapsed_ms']} ms | Passed: {out['passed_count']}/{len(out['results'])}) ===")
    print(f"{'SYMBOL':<10} {'REGIME':<8} {'DIST_ATR':>8} {'FEE_R':>7} {'TRADES':>6} {'WIN%':>6} {'NET_E[R]':>9} {'PF':>6} {'MAX_DD':>7} {'GATE':<8}")
    for r in out["results"]:
        st = r["regime_side_wf"]
        g_str = "PASS" if r["gate_passed"] else "VETO"
        print(
            f"{r['fut_symbol']:<10} {r['live_regime']:<8} {r['dist_ema20_atr']:>+8.2f} "
            f"{r['maker_fee_drag_r']:>7.4f} {st['trades']:>6} {st['win_rate']*100:>5.1f}% "
            f"{st['net_expectancy_r']:>+8.3f}R {st['profit_factor']:>6.2f} {st['max_dd_r']:>6.2f}R {g_str:<8}"
        )
