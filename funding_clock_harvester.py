#!/usr/bin/env python3
r"""
8-Hour Funding Settlement-Clock Harvester & Cross-Venue Carry Analyzer
File: /home/box/desk/funding_clock_harvester.py

KuCoin Futures and Binance USD-M Futures settle funding every 8 hours at:
  00:00 UTC (20:00 EDT), 08:00 UTC (04:00 EDT), and 16:00 UTC (12:00 EDT).
Hyperliquid settles 1/8th of the 8h rate every 1 hour on the hour.

This engine:
  1. Tracks exact minutes until the next 8h settlement (`\Delta t_{settle}`) and minutes since the last settlement.
  2. Computes current 8h funding (bps), predicted next 8h funding (bps), annualized carry APR (%),
     and 3-venue funding spread (`KuCoin vs Binance vs Hyperliquid`).
  3. Audits our live KuCoin Futures positions (`ZECUSDTM SHORT`, `XRPUSDTM SHORT`, etc.) to verify
     exact dollar and bps carry earned or paid at the upcoming 20:00 EDT settlement.
  4. Flags high-EV settlement clock setups:
     - `PRE_SETTLEMENT_RECEIVER_WINDOW` (15-90m before settlement when |f| >= 2.5 bps)
     - `POST_SETTLEMENT_UNWIND_SNAPBACK` (0-30m after settlement when crowded side dumps)
     - `DIRECTIONAL_CARRY_TAILWIND` (MTF directional trade also collects positive funding carry)
"""

import json
import sys
import time
from typing import Any, Dict, List

sys.path.insert(0, "/home/box/desk/kucoin")
sys.path.insert(0, "/home/box/desk")
from kucoin_client import KuCoinClient
import liquidation_oi_sniper


def compute_settlement_clock() -> Dict[str, Any]:
    now = int(time.time())
    sec_8h = 8 * 3600
    rem_8h = now % sec_8h
    mins_since_8h = round(rem_8h / 60.0, 1)
    mins_until_8h = round((sec_8h - rem_8h) / 60.0, 1)
    next_8h_ts = now + (sec_8h - rem_8h)

    rem_1h = now % 3600
    mins_until_1h_hl = round((3600 - rem_1h) / 60.0, 1)

    if mins_until_8h <= 30.0:
        window_state = "IMMINENT_PRE_SETTLEMENT_SQUEEZE_WINDOW"
    elif mins_until_8h <= 90.0:
        window_state = "PRE_SETTLEMENT_RECEIVER_ACCUMULATION"
    elif mins_since_8h <= 30.0:
        window_state = "POST_SETTLEMENT_UNWIND_SNAPBACK_WINDOW"
    else:
        window_state = "MID_CYCLE_CARRY_ACCUMULATION"

    return {
        "utc_now": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "edt_now": time.strftime("%Y-%m-%d %H:%M:%S EDT", time.localtime(now)),
        "next_8h_settlement_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(next_8h_ts)),
        "next_8h_settlement_edt": time.strftime("%Y-%m-%d %H:%M:%S EDT", time.localtime(next_8h_ts)),
        "mins_since_last_8h_settlement": mins_since_8h,
        "mins_until_next_8h_settlement": mins_until_8h,
        "mins_until_next_1h_hl_settlement": mins_until_1h_hl,
        "window_state": window_state,
    }


def run_funding_clock_harvester() -> Dict[str, Any]:
    t0 = time.time()
    clock = compute_settlement_clock()

    # Load or run 3-venue OI & funding data
    try:
        with open("/home/box/desk/kucoin/latest_liquidation_oi_sniper.json") as f:
            sniper_data = json.load(f)
        if time.time() - time.mktime(time.strptime(sniper_data["as_of"], "%Y-%m-%dT%H:%M:%SZ")) > 300:
            sniper_data = liquidation_oi_sniper.run_liquidation_oi_sniper()
    except Exception:
        sniper_data = liquidation_oi_sniper.run_liquidation_oi_sniper()

    # Load MTF scan for directional alignment
    mtf_map: Dict[str, Any] = {}
    try:
        with open("/home/box/desk/kucoin/latest_mtf_scan.json") as f:
            for r in json.load(f).get("results") or []:
                mtf_map[r["fut_symbol"]] = r
    except Exception:
        pass

    # Audit live KuCoin positions funding carry
    position_carry_audit: List[Dict[str, Any]] = []
    try:
        kc = KuCoinClient()
        contracts = {
            c["symbol"]: c
            for c in (kc.request("https://api-futures.kucoin.com", "GET", "/api/v1/contracts/active", auth=False).get("data") or [])
        }
        positions = [p for p in kc.get_futures_positions() if abs(float(p.get("currentQty") or 0)) > 0]
        for p in positions:
            sym = p["symbol"]
            qty = int(p.get("currentQty") or 0)
            side = "LONG" if qty > 0 else "SHORT"
            c_spec = contracts.get(sym) or {}
            mark = float(c_spec.get("markPrice") or p.get("markPrice") or 0.0)
            mult = abs(float(c_spec.get("multiplier") or 0.01))
            notional_usd = round(abs(qty) * mult * mark, 2)
            fund_8h_rate = float(c_spec.get("fundingFeeRate") or 0.0)
            pred_8h_rate = float(c_spec.get("predictedFundingFeeRate") or 0.0)
            fund_8h_bps = round(fund_8h_rate * 10000.0, 2)
            pred_8h_bps = round(pred_8h_rate * 10000.0, 2)

            # Positive funding rate: Longs pay Shorts. Negative funding rate: Shorts pay Longs.
            signed_carry_bps = fund_8h_bps if side == "SHORT" else -fund_8h_bps
            next_payout_usd = round(notional_usd * (signed_carry_bps / 10000.0), 4)
            ann_carry_apr_pct = round((signed_carry_bps / 100.0) * 3.0 * 365.25, 2)

            position_carry_audit.append({
                "symbol": sym,
                "side": side,
                "qty_lots": abs(qty),
                "notional_usd": notional_usd,
                "kucoin_funding_8h_bps": fund_8h_bps,
                "predicted_next_8h_bps": pred_8h_bps,
                "net_position_carry_8h_bps": signed_carry_bps,
                "estimated_next_settlement_usd": next_payout_usd,
                "annualized_carry_apr_pct": ann_carry_apr_pct,
                "carry_verdict": "EARNING_FUNDING_TAILWIND" if signed_carry_bps > 0 else ("NEUTRAL_ZERO_CARRY" if signed_carry_bps == 0 else "PAYING_FUNDING_HEADWIND"),
            })
    except Exception:
        pass

    # Scan all 27 perps for funding clock & cross-venue carry opportunities
    opportunities: List[Dict[str, Any]] = []
    for r in sniper_data.get("results") or []:
        sym = r["fut_symbol"]
        bf = r["basis_and_funding"]
        kc_f = float(bf.get("kucoin_funding_8h_bps") or 0.0)
        bn_f = float(bf.get("binance_funding_8h_bps") or 0.0)
        hl_f = float(bf.get("hyperliquid_funding_8h_bps") or 0.0)
        kc_basis = float(bf.get("kucoin_basis_bps") or 0.0)

        mtf = mtf_map.get(sym) or {}
        t_side = mtf.get("target_side") or ("SHORT" if kc_f > 0 else "LONG")
        mtf_score = int(mtf.get("mtf_confluence_score") or 50)

        # Does target_side collect positive funding on KuCoin?
        directional_carry_bps = kc_f if t_side == "SHORT" else -kc_f
        ann_apr_pct = round((directional_carry_bps / 100.0) * 3.0 * 365.25, 2)
        hl_kc_spread_bps = round(hl_f - kc_f, 2)

        strategy_tag = "STANDARD_CARRY"
        if abs(kc_f) >= 2.5 and clock["mins_until_next_8h_settlement"] <= 90:
            strategy_tag = "PRE_SETTLEMENT_RECEIVER_HARVEST"
        elif abs(kc_f) >= 2.5 and clock["mins_since_last_8h_settlement"] <= 30:
            strategy_tag = "POST_SETTLEMENT_UNWIND_SNAPBACK"
        elif directional_carry_bps >= 1.0 and mtf_score >= 70:
            strategy_tag = "HIGH_CONVICTION_DIRECTIONAL_CARRY_TAILWIND"
        elif abs(hl_kc_spread_bps) >= 3.0:
            strategy_tag = "CROSS_VENUE_HL_VS_KC_FUNDING_DIVERGENCE"

        opportunities.append({
            "fut_symbol": sym,
            "target_side": t_side,
            "mtf_score": mtf_score,
            "kucoin_funding_8h_bps": kc_f,
            "binance_funding_8h_bps": bn_f,
            "hyperliquid_funding_8h_bps": hl_f,
            "hl_minus_kc_funding_bps": hl_kc_spread_bps,
            "kucoin_basis_bps": kc_basis,
            "directional_carry_8h_bps": round(directional_carry_bps, 2),
            "annualized_directional_apr_pct": ann_apr_pct,
            "strategy_tag": strategy_tag,
        })

    opportunities.sort(key=lambda x: (x["directional_carry_8h_bps"], x["mtf_score"]), reverse=True)

    out = {
        "as_of": clock["utc_now"],
        "edt_now": clock["edt_now"],
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "settlement_clock": clock,
        "live_position_carry_audit": position_carry_audit,
        "top_carry_tailwinds": [o for o in opportunities if o["directional_carry_8h_bps"] > 0][:12],
        "all_pairs_funding": opportunities,
    }
    with open("/home/box/desk/kucoin/latest_funding_harvester.json", "w") as f:
        json.dump(out, f, indent=2)
    return out


if __name__ == "__main__":
    rep = run_funding_clock_harvester()
    c = rep["settlement_clock"]
    print(f"=== 8h Funding Settlement-Clock Harvester ({rep['elapsed_ms']} ms) ===")
    print(
        f"Clock: {c['edt_now']} | Next 8h Settlement: {c['next_8h_settlement_edt']} "
        f"(in {c['mins_until_next_8h_settlement']}m) | Next HL 1h: in {c['mins_until_next_1h_hl_settlement']}m | {c['window_state']}"
    )
    print("\n1. Live KuCoin Open Positions Carry Audit:")
    for p in rep["live_position_carry_audit"]:
        print(
            f"   - {p['symbol']} {p['side']} ({p['qty_lots']} lots, ${p['notional_usd']} notional): "
            f"KC_8h={p['kucoin_funding_8h_bps']:+.2f}bps -> Net Carry={p['net_position_carry_8h_bps']:+.2f}bps/8h "
            f"({p['annualized_carry_apr_pct']:+.2f}% APR, est ${p['estimated_next_settlement_usd']:+.4f}/8h) [{p['carry_verdict']}]"
        )
    print("\n2. Top Directional Carry Tailwinds & Cross-Venue Spreads:")
    print(f"{'SYMBOL':<10} {'SIDE':<6} {'MTF':>4} {'KC_8H':>8} {'BN_8H':>8} {'HL_8H':>8} {'CARRY_8H':>9} {'APR%':>8} {'BASIS':>8} {'TAG':<42}")
    for o in rep["all_pairs_funding"][:14]:
        print(
            f"{o['fut_symbol']:<10} {o['target_side']:<6} {o['mtf_score']:>4} "
            f"{o['kucoin_funding_8h_bps']:>+7.2f}bp {o['binance_funding_8h_bps']:>+7.2f}bp {o['hyperliquid_funding_8h_bps']:>+7.2f}bp "
            f"{o['directional_carry_8h_bps']:>+8.2f}bp {o['annualized_directional_apr_pct']:>+7.2f}% "
            f"{o['kucoin_basis_bps']:>+7.2f}bp {o['strategy_tag']:<42}"
        )
