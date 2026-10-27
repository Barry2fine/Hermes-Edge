#!/usr/bin/env python3
"""
KuCoin Multi-Service Alpha & Cross-Surface Arbitrage Engine v1.1
File: /home/box/desk/kucoin_multiservice_alpha.py

Unlocks 4 distinct non-directional & lead-lag profit surfaces across KuCoin's API & On-Chain DEXs:
  1. Full 706-Contract Extreme Funding-Rate & Settlement Countdown Harvester
     - Scans all 706 active KuCoin Futures contracts + 969 Spot tickers + 65 Cross-Margin borrowable assets
     - Filters liquid contracts (24h turnover > $250,000) and ranks by interval funding (bps) & annualized APR (%)
     - Flags imminent settlements (< 60 mins) where |funding| > 15 bps (7.5x our 2 bps Maker fee)
     - Evaluates Delta-Neutral Cash-and-Carry (SHORT Perp + LONG Spot when funding > +3 bps) and
       Reverse Cash-and-Carry (LONG Perp + SHORT Cross-Margin when funding < -10 bps & margin-borrowable,
       or LONG Perp postOnly Maker receiver when momentum confirms).
  2. On-Chain DEX (DexScreener API) -> KuCoin Perpetual Futures Lead-Lag Sniper
     - Queries canonical on-chain pools concurrently (Solana/Ethereum/Base/Arbitrum/Sui/Aptos) for 13 major perps:
       SOL, PEPE, WIF, BONK, ONDO, TAO, PENDLE, VIRTUAL, AAVE, UNI, SUI, APT, ARB
     - Filters for actively traded pools (h1_txns > 0) and computes DEX-to-KuCoin Perp Basis Dislocation (bps),
       5m/1h DEX Buy/Sell Transaction Imbalance, 5m DEX Volume, and On-Chain Buy-Flow Impulse Score.
     - Flags > 15 bps on-chain lead-lag dislocations before KuCoin perp market makers adjust.
  3. Triangular Spot & Spot-vs-Perp Basis Arbitrage Scanner
     - Scans ALT-USDT vs (ALT-BTC * BTC-USDT), (ALT-USDC * USDC-USDT), (ALT-ETH * ETH-USDT)
       and Spot ALT-USDT vs Futures ALTUSDTM Mark/Last Price across liquid pairs.
     - Computes gross & net basis (bps) after VIP 0 Maker fees (2 bps Futures Maker, 8 bps Spot w/ 20% coupon).
  4. KuCoin Earn (Demand Savings) & Crypto Lending Pro Yield Optimizer
     - Queries /api/v1/earn/saving/products and /api/v3/project/list for USDT, USDC, SOL, ETH, ADA, etc.
     - Tracks instant-redemption (0-day) demand savings APRs vs P2P Lending Pro rates and current idle balances.
"""

from concurrent.futures import ThreadPoolExecutor
import json
import math
import os
import sys
import time
import urllib.parse
import urllib.request
from typing import Any, Dict, List, Optional, Tuple

sys.path.insert(0, "/home/box/desk/kucoin")
sys.path.insert(0, "/home/box/desk")
from kucoin_client import KuCoinClient, SPOT_BASE_URL, FUTURES_BASE_URL

OUTPUT_PATH = "/home/box/desk/latest_kucoin_multiservice.json"

# Canonical On-Chain Token Addresses for DexScreener Lead-Lag Sniper (All 13 Tokens)
DEX_TOKENS: List[Dict[str, Any]] = [
    {
        "base": "SOL",
        "fut_symbol": "SOLUSDTM",
        "spot_symbol": "SOL-USDT",
        "chain": "solana",
        "address": "So11111111111111111111111111111111111111112",
        "perp_scale": 1.0,
    },
    {
        "base": "PEPE",
        "fut_symbol": "PEPEUSDTM",
        "spot_symbol": "PEPE-USDT",
        "chain": "ethereum",
        "address": "0x6982508145454Ce325dDbE47a25d4ec3d2311933",
        "perp_scale": 1.0,
    },
    {
        "base": "WIF",
        "fut_symbol": "WIFUSDTM",
        "spot_symbol": "WIF-USDT",
        "chain": "solana",
        "address": "EKpQGSJtjMFqKZ9KQanSqYXRcF8fBopzLHYxdM65zcjm",
        "perp_scale": 1.0,
    },
    {
        "base": "BONK",
        "fut_symbol": "1000BONKUSDTM",
        "spot_symbol": "BONK-USDT",
        "chain": "solana",
        "address": "DezXAZ8z7PnrnRJjz3wXBoRgixCa6xjnB7YaB1pPB263",
        "perp_scale": 1000.0,  # 1000BONKUSDTM is priced per 1,000 BONK
    },
    {
        "base": "ONDO",
        "fut_symbol": "ONDOUSDTM",
        "spot_symbol": "ONDO-USDT",
        "chain": "ethereum",
        "address": "0xfAbA6f8e4a5E8Ab82F62fe7C39859FA577269BE3",
        "perp_scale": 1.0,
    },
    {
        "base": "TAO",
        "fut_symbol": "TAOUSDTM",
        "spot_symbol": "TAO-USDT",
        "chain": "ethereum",
        "address": "0x77E06c9eCCf2E797fd462A92B6D7642EF85b0A44",
        "perp_scale": 1.0,
    },
    {
        "base": "PENDLE",
        "fut_symbol": "PENDLEUSDTM",
        "spot_symbol": "PENDLE-USDT",
        "chain": "arbitrum",
        "address": "0x0c880f6761F1af8d9Aa9C466984b80DAb9a8c9e8",
        "alt_address": "0x808507121B80c02388fAd14726482e061B8da827",
        "perp_scale": 1.0,
    },
    {
        "base": "VIRTUAL",
        "fut_symbol": "VIRTUALUSDTM",
        "spot_symbol": "VIRTUAL-USDT",
        "chain": "base",
        "address": "0x0b3e328455c4059EEb9e3f84b5543F74E24e7E1b",
        "perp_scale": 1.0,
    },
    {
        "base": "AAVE",
        "fut_symbol": "AAVEUSDTM",
        "spot_symbol": "AAVE-USDT",
        "chain": "ethereum",
        "address": "0x7Fc66500c84A76Ad7e9c93437bFc5Ac33E2DDaE9",
        "perp_scale": 1.0,
    },
    {
        "base": "UNI",
        "fut_symbol": "UNIUSDTM",
        "spot_symbol": "UNI-USDT",
        "chain": "ethereum",
        "address": "0x1f9840a85d5aF5bf1D1762F925BDADdC4201F984",
        "perp_scale": 1.0,
    },
    {
        "base": "SUI",
        "fut_symbol": "SUIUSDTM",
        "spot_symbol": "SUI-USDT",
        "chain": "sui",
        "address": "0x2::sui::SUI",
        "perp_scale": 1.0,
    },
    {
        "base": "APT",
        "fut_symbol": "APTUSDTM",
        "spot_symbol": "APT-USDT",
        "chain": "aptos",
        "address": "0x1::aptos_coin::AptosCoin",
        "perp_scale": 1.0,
    },
    {
        "base": "ARB",
        "fut_symbol": "ARBUSDTM",
        "spot_symbol": "ARB-USDT",
        "chain": "arbitrum",
        "address": "0x912CE59144191C1204E64559FE8253a0e49E6548",
        "perp_scale": 1.0,
    },
]

# Fee structure Constants (in bps)
FUTURES_MAKER_FEE_BPS = 2.0      # 0.02% VIP 0 Futures Maker
FUTURES_TAKER_FEE_BPS = 6.0      # 0.06% VIP 0 Futures Taker
SPOT_MAKER_COUPON_BPS = 8.0      # 0.10% base * 0.80 (20% active fee deduction coupon) = 8.0 bps


def _http_get_json(url: str, timeout: int = 10) -> Dict[str, Any]:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "KuCoinMultiServiceAlpha/1.1",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _normalize_base(base_curr: str) -> str:
    if base_curr == "XBT":
        return "BTC"
    if base_curr.startswith("1000000"):
        return base_curr[7:]
    if base_curr.startswith("1000"):
        return base_curr[4:]
    return base_curr


# =============================================================================
# 1. FULL 706-CONTRACT EXTREME FUNDING-RATE & SETTLEMENT COUNTDOWN HARVESTER
# =============================================================================

def scan_funding_and_cash_carry(
    contracts: List[Dict[str, Any]],
    spot_map: Dict[str, Dict[str, Any]],
    margin_borrowable: set,
    min_turnover_usd: float = 250000.0,
) -> Dict[str, Any]:
    liquid_contracts = []
    all_extreme_unfiltered = []
    imminent_settlements_lt60m = []
    cash_and_carry_LongSpot_ShortPerp = []
    reverse_carry_LongPerp_ShortMargin = []

    for c in contracts:
        sym = c.get("symbol") or ""
        if not sym.endswith("USDTM"):
            continue
        turnover = float(c.get("turnoverOf24h") or 0.0)
        vol24h = float(c.get("volumeOf24h") or 0.0)
        raw_rate = c.get("fundingFeeRate")
        if raw_rate is None:
            continue
        fund_rate = float(raw_rate)
        fund_bps = round(fund_rate * 10000.0, 2)
        pred_rate = float(c.get("predictedFundingFeeRate") or fund_rate)
        pred_bps = round(pred_rate * 10000.0, 2)

        gran_ms = float(c.get("fundingRateGranularity") or 28800000.0)
        interval_hours = max(1.0, round(gran_ms / 3600000.0, 1))
        intervals_per_day = 24.0 / interval_hours
        apr_pct = round((fund_rate * 100.0) * intervals_per_day * 365.25, 2)

        next_ms = float(c.get("nextFundingRateTime") or 0.0)
        mins_to_settle = round(next_ms / 60000.0, 1)

        base_raw = str(c.get("baseCurrency") or sym[:-5])
        base_norm = _normalize_base(base_raw)
        spot_sym = f"{base_norm}-USDT"
        spot_t = spot_map.get(spot_sym)

        mark_px = float(c.get("markPrice") or 0.0)
        index_px = float(c.get("indexPrice") or mark_px)
        last_px = float(c.get("lastTradePrice") or mark_px)
        price_chg_pct = round(float(c.get("priceChgPct") or 0.0) * 100.0, 2)

        scale = 1000.0 if base_raw.startswith("1000") and not base_raw.startswith("1000000") else (
            1000000.0 if base_raw.startswith("1000000") else 1.0
        )
        perp_unit_mark = mark_px / scale if scale > 0 else mark_px
        perp_unit_index = index_px / scale if scale > 0 else index_px

        raw_spot_last = float(spot_t.get("last") or 0.0) if spot_t else None
        # Verify spot ticker is the same underlying token (within 20% of index price)
        has_spot = bool(
            raw_spot_last
            and raw_spot_last > 0
            and perp_unit_index > 0
            and abs(raw_spot_last - perp_unit_index) / perp_unit_index < 0.20
        )
        spot_last = raw_spot_last if has_spot else None
        is_margin_borrowable = has_spot and (base_norm in margin_borrowable)

        spot_perp_basis_bps = (
            round(((perp_unit_mark - spot_last) / spot_last) * 10000.0, 2)
            if (spot_last and spot_last > 0)
            else round(((mark_px - index_px) / index_px) * 10000.0, 2) if index_px > 0 else 0.0
        )

        # Round-trip delta-neutral entry Maker cost = 2 bps (Perp) + 8 bps (Spot) = 10 bps
        dn_one_way_fee_bps = FUTURES_MAKER_FEE_BPS + SPOT_MAKER_COUPON_BPS
        perp_only_rt_maker_bps = 2.0 * FUTURES_MAKER_FEE_BPS  # 4 bps round-trip Perp Maker

        strategy_type = "NEUTRAL_CARRY"
        execution_playbook = "WAIT_FOR_EXTREME"
        net_first_interval_edge_bps = round(abs(fund_bps) - perp_only_rt_maker_bps, 2)
        breakeven_intervals_dn = (
            round(dn_one_way_fee_bps / abs(fund_bps), 2) if abs(fund_bps) > 0.1 else 999.0
        )

        if fund_bps >= 3.0:
            if has_spot:
                strategy_type = "DELTA_NEUTRAL_CASH_AND_CARRY"
                execution_playbook = f"SHORT {sym} Perp (2bps Maker) + LONG {spot_sym} Spot (8bps Coupon) -> Collect +{fund_bps:.2f} bps/{int(interval_hours)}h ({apr_pct:+.1f}% APR)"
            else:
                strategy_type = "PERP_SHORT_POSITIVE_CARRY"
                execution_playbook = f"SHORT {sym} Perp postOnly Maker -> Collect +{fund_bps:.2f} bps/{int(interval_hours)}h ({apr_pct:+.1f}% APR)"
        elif fund_bps <= -10.0:
            if is_margin_borrowable and has_spot:
                strategy_type = "DELTA_NEUTRAL_REVERSE_CASH_AND_CARRY"
                execution_playbook = f"LONG {sym} Perp (2bps Maker) + BORROW & SHORT {spot_sym} Cross-Margin -> Collect {abs(fund_bps):.2f} bps/{int(interval_hours)}h ({abs(apr_pct):.1f}% APR)"
            else:
                strategy_type = "DIRECTIONAL_LONG_MAKER_RECEIVER"
                execution_playbook = f"LONG {sym} Perp postOnly Maker (15m OBI/Momentum Receiver) -> Collect +{abs(fund_bps):.2f} bps/{int(interval_hours)}h ({abs(apr_pct):.1f}% APR)"

        imminent_flag = mins_to_settle <= 60.0 and abs(fund_bps) >= 15.0

        row = {
            "symbol": sym,
            "base_currency": base_norm,
            "spot_symbol": spot_sym if has_spot else None,
            "has_spot_pair": has_spot,
            "is_margin_borrowable": is_margin_borrowable,
            "mark_price": mark_px,
            "last_price": last_px,
            "spot_price": spot_last,
            "price_chg_24h_pct": price_chg_pct,
            "turnover_24h_usd": round(turnover, 2),
            "volume_24h": round(vol24h, 2),
            "funding_interval_hours": interval_hours,
            "funding_rate_bps": fund_bps,
            "abs_funding_bps": round(abs(fund_bps), 2),
            "predicted_funding_bps": pred_bps,
            "annualized_apr_pct": apr_pct,
            "abs_annualized_apr_pct": round(abs(apr_pct), 2),
            "mins_until_settlement": mins_to_settle,
            "imminent_lt60m_gt15bps": imminent_flag,
            "spot_to_perp_basis_bps": spot_perp_basis_bps,
            "net_1st_interval_after_perp_maker_bps": net_first_interval_edge_bps,
            "dn_breakeven_intervals": breakeven_intervals_dn,
            "strategy_type": strategy_type,
            "execution_playbook": execution_playbook,
        }

        if abs(fund_bps) >= 30.0 and turnover >= 25000.0:
            all_extreme_unfiltered.append(row)

        if turnover >= min_turnover_usd:
            liquid_contracts.append(row)
            if imminent_flag:
                imminent_settlements_lt60m.append(row)
            if fund_bps >= 3.0:
                cash_and_carry_LongSpot_ShortPerp.append(row)
            if fund_bps <= -10.0:
                reverse_carry_LongPerp_ShortMargin.append(row)

    # Also evaluate all Cross-Margin borrowable negative-funding contracts (even if -2 to -10 bps)
    margin_borrowable_short_plays = [
        r for r in liquid_contracts if r["is_margin_borrowable"] and r["funding_rate_bps"] <= -2.0
    ]
    margin_borrowable_short_plays.sort(key=lambda x: x["funding_rate_bps"])

    # Sort rankings
    liquid_by_abs_funding = sorted(liquid_contracts, key=lambda x: x["abs_funding_bps"], reverse=True)
    all_extreme_unfiltered.sort(key=lambda x: x["abs_funding_bps"], reverse=True)
    imminent_settlements_lt60m.sort(key=lambda x: x["abs_funding_bps"], reverse=True)
    cash_and_carry_LongSpot_ShortPerp.sort(key=lambda x: x["funding_rate_bps"], reverse=True)
    reverse_carry_LongPerp_ShortMargin.sort(key=lambda x: x["funding_rate_bps"])

    return {
        "summary": {
            "total_active_futures_contracts": len(contracts),
            "liquid_contracts_gt_250k_usd": len(liquid_contracts),
            "imminent_lt60m_gt15bps_count": len(imminent_settlements_lt60m),
            "positive_carry_gt3bps_count": len(cash_and_carry_LongSpot_ShortPerp),
            "negative_carry_lt_minus10bps_count": len(reverse_carry_LongPerp_ShortMargin),
            "margin_borrowable_negative_carry_count": len(margin_borrowable_short_plays),
        },
        "imminent_settlements_lt60m_gt15bps": imminent_settlements_lt60m[:15],
        "top_liquid_extreme_funding": liquid_by_abs_funding[:20],
        "delta_neutral_cash_and_carry_candidates": cash_and_carry_LongSpot_ShortPerp[:15],
        "reverse_cash_and_carry_candidates": reverse_carry_LongPerp_ShortMargin[:15],
        "margin_borrowable_reverse_carry": margin_borrowable_short_plays[:10],
        "all_universe_extreme_gt30bps": all_extreme_unfiltered[:15],
    }


# =============================================================================
# 2. ON-CHAIN DEX (DEXSCREENER) -> KUCOIN PERP LEAD-LAG SNIPER
# =============================================================================

def _fetch_token_pools(t: Dict[str, Any]) -> Tuple[str, List[Dict[str, Any]]]:
    addrs = [t["address"]]
    if t.get("alt_address"):
        addrs.append(t["alt_address"])
    encoded = ",".join(urllib.parse.quote(a) for a in addrs)
    url = f"https://api.dexscreener.com/latest/dex/tokens/{encoded}"
    try:
        data = _http_get_json(url, timeout=8)
        pairs = data.get("pairs") or []
        t_addrs = {a.lower() for a in addrs}
        matched = []
        for p in pairs:
            bt_addr = str((p.get("baseToken") or {}).get("address") or "").lower()
            bt_sym = str((p.get("baseToken") or {}).get("symbol") or "").upper()
            if bt_addr in t_addrs or bt_sym in (t["base"], f"W{t['base']}"):
                matched.append(p)
        return t["base"], matched
    except Exception:
        return t["base"], []


def scan_dex_to_kucoin_lead_lag(
    contract_map: Dict[str, Dict[str, Any]],
    spot_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    pools_by_base: Dict[str, List[Dict[str, Any]]] = {}
    with ThreadPoolExecutor(max_workers=8) as ex:
        for base, plist in ex.map(_fetch_token_pools, DEX_TOKENS):
            pools_by_base[base] = plist

    results = []
    active_dislocations_gt15bps = []

    for t in DEX_TOKENS:
        base = t["base"]
        fut_sym = t["fut_symbol"]
        spot_sym = t["spot_symbol"]
        scale = float(t["perp_scale"])

        c = contract_map.get(fut_sym)
        if not c:
            continue
        perp_mark_raw = float(c.get("markPrice") or 0.0)
        perp_last_raw = float(c.get("lastTradePrice") or perp_mark_raw)

        # Unit price of 1 token on KuCoin Perp
        perp_mark_unit = perp_mark_raw / scale
        perp_last_unit = perp_last_raw / scale

        spot_t = spot_map.get(spot_sym)
        spot_last = float(spot_t.get("last") or 0.0) if spot_t else None

        cand_pools = pools_by_base.get(base) or []
        valid_pools = [
            p for p in cand_pools
            if float((p.get("liquidity") or {}).get("usd") or 0.0) >= 35000.0
            and str(p.get("chainId") or "").lower() not in ("robinhood", "pulsechain")
            and float(p.get("priceUsd") or 0.0) > 0
            and abs(float(p["priceUsd"]) - perp_mark_unit) / max(perp_mark_unit, 1e-12) < 0.12
        ]
        if not valid_pools:
            continue

        # Prioritize pools that actually had trades in the last 1h so stale pools don't distort priceUsd
        active_h1_pools = [
            p for p in valid_pools
            if (
                int(((p.get("txns") or {}).get("h1") or {}).get("buys") or 0)
                + int(((p.get("txns") or {}).get("h1") or {}).get("sells") or 0)
            ) > 0
        ]
        pool_set = active_h1_pools if active_h1_pools else valid_pools

        # Sort by 1h volume + 24h volume + liquidity
        pool_set.sort(
            key=lambda p: (
                float((p.get("volume") or {}).get("h1") or 0.0) * 5.0
                + float((p.get("volume") or {}).get("h24") or 0.0) * 0.5
                + float((p.get("liquidity") or {}).get("usd") or 0.0) * 0.2
            ),
            reverse=True,
        )
        top_pool = pool_set[0]
        top3 = pool_set[:3]

        # Activity-weighted DEX VWAP across top 3 active pools
        weights = [
            max(1000.0, float((p.get("volume") or {}).get("h1") or 0.0) * 10.0 + float((p.get("liquidity") or {}).get("usd") or 0.0) * 0.2)
            for p in top3
        ]
        tot_w = sum(weights)
        dex_vwap_usd = sum(float(p["priceUsd"]) * w for p, w in zip(top3, weights)) / tot_w
        dex_primary_usd = float(top_pool["priceUsd"])

        # Aggregate 5m and 1h transactions & volume across top 3 pools
        m5_buys = sum(int(((p.get("txns") or {}).get("m5") or {}).get("buys") or 0) for p in top3)
        m5_sells = sum(int(((p.get("txns") or {}).get("m5") or {}).get("sells") or 0) for p in top3)
        h1_buys = sum(int(((p.get("txns") or {}).get("h1") or {}).get("buys") or 0) for p in top3)
        h1_sells = sum(int(((p.get("txns") or {}).get("h1") or {}).get("sells") or 0) for p in top3)

        vol_m5_usd = round(sum(float((p.get("volume") or {}).get("m5") or 0.0) for p in top3), 2)
        vol_h1_usd = round(sum(float((p.get("volume") or {}).get("h1") or 0.0) for p in top3), 2)
        vol_h24_usd = round(sum(float((p.get("volume") or {}).get("h24") or 0.0) for p in top3), 2)
        liq_top3_usd = round(sum(float((p.get("liquidity") or {}).get("usd") or 0.0) for p in top3), 2)

        m5_tot = m5_buys + m5_sells
        h1_tot = h1_buys + h1_sells
        m5_imbalance = round((m5_buys - m5_sells) / m5_tot, 4) if m5_tot > 0 else 0.0
        h1_imbalance = round((h1_buys - h1_sells) / h1_tot, 4) if h1_tot > 0 else 0.0

        chg_m5_pct = float((top_pool.get("priceChange") or {}).get("m5") or 0.0)
        chg_h1_pct = float((top_pool.get("priceChange") or {}).get("h1") or 0.0)

        # Basis Dislocation (in bps): positive means On-Chain DEX trades ABOVE KuCoin Perp
        basis_vs_mark_bps = round(((dex_primary_usd - perp_mark_unit) / perp_mark_unit) * 10000.0, 2)
        basis_vs_last_bps = round(((dex_primary_usd - perp_last_unit) / perp_last_unit) * 10000.0, 2)
        vwap_vs_mark_bps = round(((dex_vwap_usd - perp_mark_unit) / perp_mark_unit) * 10000.0, 2)

        # On-Chain Buy-Flow Impulse Score [-100, +100]
        raw_impulse = (
            m5_imbalance * 40.0
            + h1_imbalance * 20.0
            + max(-1.0, min(1.0, chg_m5_pct / 1.5)) * 20.0
            + max(-1.0, min(1.0, basis_vs_last_bps / 40.0)) * 20.0
        )
        impulse_score = round(max(-100.0, min(100.0, raw_impulse)), 2)

        lead_lag_flag = abs(basis_vs_last_bps) >= 15.0 or abs(vwap_vs_mark_bps) >= 15.0
        if basis_vs_last_bps >= 15.0 and impulse_score >= 5.0:
            signal = "ONCHAIN_DEX_LEADING_BULL_BREAKOUT_LONG_PERP"
        elif basis_vs_last_bps <= -15.0 and impulse_score <= -5.0:
            signal = "ONCHAIN_DEX_LEADING_BEAR_BREAKDOWN_SHORT_PERP"
        elif abs(basis_vs_last_bps) >= 15.0:
            signal = "DEX_PERP_BASIS_DISLOCATION_WATCH"
        else:
            signal = "PARITY_SYNCED"

        item = {
            "base": base,
            "fut_symbol": fut_sym,
            "spot_symbol": spot_sym,
            "dex_chain": top_pool.get("chainId"),
            "dex_venue": top_pool.get("dexId"),
            "dex_primary_price_usd": dex_primary_usd,
            "dex_vwap_top3_usd": round(dex_vwap_usd, 8 if dex_vwap_usd < 0.01 else 5),
            "kucoin_perp_mark_unit": perp_mark_unit,
            "kucoin_perp_last_unit": perp_last_unit,
            "kucoin_perp_raw_mark": perp_mark_raw,
            "kucoin_spot_last": spot_last,
            "dex_vs_perp_last_bps": basis_vs_last_bps,
            "dex_vs_perp_mark_bps": basis_vs_mark_bps,
            "dex_vwap_vs_mark_bps": vwap_vs_mark_bps,
            "txns_5m_buys": m5_buys,
            "txns_5m_sells": m5_sells,
            "txns_5m_imbalance": m5_imbalance,
            "txns_1h_buys": h1_buys,
            "txns_1h_sells": h1_sells,
            "txns_1h_imbalance": h1_imbalance,
            "dex_vol_5m_usd": vol_m5_usd,
            "dex_vol_1h_usd": vol_h1_usd,
            "dex_vol_24h_usd": vol_h24_usd,
            "dex_liquidity_usd": liq_top3_usd,
            "dex_price_chg_5m_pct": chg_m5_pct,
            "dex_price_chg_1h_pct": chg_h1_pct,
            "onchain_impulse_score": impulse_score,
            "dislocation_gt_15bps": lead_lag_flag,
            "lead_lag_signal": signal,
        }
        results.append(item)
        if lead_lag_flag:
            active_dislocations_gt15bps.append(item)

    results.sort(key=lambda x: abs(x["dex_vs_perp_last_bps"]), reverse=True)
    active_dislocations_gt15bps.sort(key=lambda x: abs(x["dex_vs_perp_last_bps"]), reverse=True)

    return {
        "summary": {
            "tokens_monitored": len(results),
            "dislocations_gt_15bps_count": len(active_dislocations_gt15bps),
            "top_bull_impulse": max(results, key=lambda x: x["onchain_impulse_score"])["base"] if results else None,
            "top_bear_impulse": min(results, key=lambda x: x["onchain_impulse_score"])["base"] if results else None,
        },
        "active_dislocations_gt15bps": active_dislocations_gt15bps,
        "all_monitored_tokens": results,
    }


# =============================================================================
# 3. TRIANGULAR SPOT & SPOT-VS-PERP BASIS ARBITRAGE SCANNER
# =============================================================================

def scan_triangular_and_spot_perp_basis(
    contracts: List[Dict[str, Any]],
    spot_map: Dict[str, Dict[str, Any]],
) -> Dict[str, Any]:
    btc_usdt = spot_map.get("BTC-USDT") or {}
    eth_usdt = spot_map.get("ETH-USDT") or {}
    usdc_usdt = spot_map.get("USDC-USDT") or {}

    bridges = {
        "BTC": {
            "symbol": "BTC-USDT",
            "last": float(btc_usdt.get("last") or 0.0),
            "bid": float(btc_usdt.get("buy") or 0.0),
            "ask": float(btc_usdt.get("sell") or 0.0),
        },
        "ETH": {
            "symbol": "ETH-USDT",
            "last": float(eth_usdt.get("last") or 0.0),
            "bid": float(eth_usdt.get("buy") or 0.0),
            "ask": float(eth_usdt.get("sell") or 0.0),
        },
        "USDC": {
            "symbol": "USDC-USDT",
            "last": float(usdc_usdt.get("last") or 1.0),
            "bid": float(usdc_usdt.get("buy") or 0.9999),
            "ask": float(usdc_usdt.get("sell") or 1.0001),
        },
    }

    perp_by_base: Dict[str, Dict[str, Any]] = {}
    for c in contracts:
        sym = c.get("symbol") or ""
        if not sym.endswith("USDTM"):
            continue
        base_raw = str(c.get("baseCurrency") or sym[:-5])
        base_norm = _normalize_base(base_raw)
        scale = 1000.0 if base_raw.startswith("1000") and not base_raw.startswith("1000000") else (
            1000000.0 if base_raw.startswith("1000000") else 1.0
        )
        if base_norm not in perp_by_base or float(c.get("turnoverOf24h") or 0) > float(perp_by_base[base_norm].get("turnoverOf24h") or 0):
            c_copy = dict(c)
            c_copy["_unit_scale"] = scale
            perp_by_base[base_norm] = c_copy

    triangular_loops = []
    spot_perp_basis_list = []

    for sym, t_usdt in spot_map.items():
        if not sym.endswith("-USDT"):
            continue
        alt = sym[:-5]
        if alt in ("BTC", "ETH", "USDC", "USDT", "DAI", "FDUSD", "TUSD"):
            continue
        vol_usdt = float(t_usdt.get("volValue") or 0.0)
        if vol_usdt < 50000.0:
            continue

        p_usdt_last = float(t_usdt.get("last") or 0.0)
        p_usdt_bid = float(t_usdt.get("buy") or 0.0)
        p_usdt_ask = float(t_usdt.get("sell") or 0.0)
        if p_usdt_last <= 0 or p_usdt_bid <= 0 or p_usdt_ask <= 0:
            continue

        spread_usdt_bps = round(((p_usdt_ask - p_usdt_bid) / p_usdt_last) * 10000.0, 2)

        c_perp = perp_by_base.get(alt)
        perp_unit_mark = None
        perp_unit_index = None
        if c_perp and float(c_perp.get("markPrice") or 0) > 0:
            sc = float(c_perp["_unit_scale"])
            cand_mark = float(c_perp["markPrice"]) / sc
            cand_idx = float(c_perp.get("indexPrice") or c_perp["markPrice"]) / sc
            # Verify same underlying token (within 20% of spot price)
            if abs(cand_idx - p_usdt_last) / p_usdt_last < 0.20:
                perp_unit_mark = cand_mark
                perp_unit_index = cand_idx

        # 3A. Triangular Loops against BTC, USDC, ETH
        for quote_ccy, br in bridges.items():
            if br["last"] <= 0:
                continue
            cross_sym = f"{alt}-{quote_ccy}"
            t_cross = spot_map.get(cross_sym)
            if not t_cross:
                continue
            cross_vol_quote = float(t_cross.get("volValue") or 0.0)
            cross_vol_usd = cross_vol_quote * br["last"]
            if cross_vol_usd < 15000.0:
                continue

            c_last = float(t_cross.get("last") or 0.0)
            c_bid = float(t_cross.get("buy") or 0.0)
            c_ask = float(t_cross.get("sell") or 0.0)
            if c_last <= 0 or c_bid <= 0 or c_ask <= 0:
                continue

            synthetic_usdt_last = c_last * br["last"]
            mid_parity_dev_bps = round(((synthetic_usdt_last - p_usdt_last) / p_usdt_last) * 10000.0, 2)

            cross_vs_perp_bps = (
                round(((synthetic_usdt_last - perp_unit_mark) / perp_unit_mark) * 10000.0, 2)
                if perp_unit_mark
                else None
            )

            hybrid_maker_fee_bps = SPOT_MAKER_COUPON_BPS + 2.0 * FUTURES_MAKER_FEE_BPS  # 12 bps
            spot_3leg_fee_bps = 3.0 * SPOT_MAKER_COUPON_BPS  # 24 bps

            best_gross_bps = max(abs(mid_parity_dev_bps), abs(cross_vs_perp_bps or 0.0))
            net_after_hybrid_maker_bps = round(best_gross_bps - hybrid_maker_fee_bps, 2)
            net_after_3leg_spot_bps = round(abs(mid_parity_dev_bps) - spot_3leg_fee_bps, 2)

            if mid_parity_dev_bps > 0:
                direction = f"BUY {sym} (${p_usdt_last:.6g}) -> SELL {cross_sym} ({c_last:.6g}) -> SELL {br['symbol']}"
            else:
                direction = f"BUY {cross_sym} ({c_last:.6g}) -> SELL {sym} (${p_usdt_last:.6g})"

            triangular_loops.append({
                "alt": alt,
                "usdt_pair": sym,
                "cross_pair": cross_sym,
                "bridge_pair": br["symbol"],
                "usdt_last": p_usdt_last,
                "synthetic_usdt_via_cross": round(synthetic_usdt_last, 8),
                "perp_unit_mark": perp_unit_mark,
                "cross_24h_vol_usd": round(cross_vol_usd, 2),
                "usdt_24h_vol_usd": round(vol_usdt, 2),
                "usdt_bid_ask_spread_bps": spread_usdt_bps,
                "mid_parity_deviation_bps": mid_parity_dev_bps,
                "abs_parity_deviation_bps": round(abs(mid_parity_dev_bps), 2),
                "cross_spot_vs_perp_bps": cross_vs_perp_bps,
                "net_after_hybrid_perp_maker_12bps": net_after_hybrid_maker_bps,
                "net_after_3leg_spot_maker_24bps": net_after_3leg_spot_bps,
                "arb_direction": direction,
            })

        # 3B. Spot-vs-Perp Basis Scanner
        if c_perp and perp_unit_mark and float(c_perp.get("turnoverOf24h") or 0) >= 250000.0 and vol_usdt >= 150000.0:
            scale = float(c_perp["_unit_scale"])
            perp_last = float(c_perp.get("lastTradePrice") or c_perp["markPrice"]) / scale
            if perp_last > 0:
                basis_mark_bps = round(((perp_unit_mark - p_usdt_last) / p_usdt_last) * 10000.0, 2)
                basis_last_bps = round(((perp_last - p_usdt_last) / p_usdt_last) * 10000.0, 2)
                fund_bps = round(float(c_perp.get("fundingFeeRate") or 0.0) * 10000.0, 2)
                dn_entry_fee_bps = SPOT_MAKER_COUPON_BPS + FUTURES_MAKER_FEE_BPS  # 10 bps
                net_basis_convergence_bps = round(abs(basis_last_bps) - dn_entry_fee_bps, 2)

                spot_perp_basis_list.append({
                    "alt": alt,
                    "spot_symbol": sym,
                    "fut_symbol": c_perp["symbol"],
                    "spot_last": p_usdt_last,
                    "perp_unit_mark": perp_unit_mark,
                    "perp_unit_last": perp_last,
                    "perp_vs_spot_mark_bps": basis_mark_bps,
                    "perp_vs_spot_last_bps": basis_last_bps,
                    "abs_basis_last_bps": round(abs(basis_last_bps), 2),
                    "funding_rate_bps": fund_bps,
                    "spot_spread_bps": spread_usdt_bps,
                    "net_after_10bps_maker_entry": net_basis_convergence_bps,
                    "spot_24h_vol_usd": round(vol_usdt, 2),
                    "perp_24h_turnover_usd": round(float(c_perp.get("turnoverOf24h") or 0.0), 2),
                    "play": (
                        "SHORT_PERP_LONG_SPOT_PREMIUM_CONVERGENCE"
                        if basis_last_bps >= 10.0
                        else (
                            "LONG_PERP_SHORT_MARGIN_DISCOUNT_CONVERGENCE"
                            if basis_last_bps <= -10.0
                            else "TIGHT_PARITY"
                        )
                    ),
                })

    triangular_loops.sort(key=lambda x: max(x["abs_parity_deviation_bps"], abs(x["cross_spot_vs_perp_bps"] or 0.0)), reverse=True)
    spot_perp_basis_list.sort(key=lambda x: x["abs_basis_last_bps"], reverse=True)

    return {
        "summary": {
            "triangular_pairs_evaluated": len(triangular_loops),
            "triangular_positive_net_hybrid_12bps_count": sum(
                1 for r in triangular_loops if r["net_after_hybrid_perp_maker_12bps"] > 0
            ),
            "spot_perp_pairs_evaluated": len(spot_perp_basis_list),
            "spot_perp_basis_gt_15bps_count": sum(
                1 for r in spot_perp_basis_list if r["abs_basis_last_bps"] >= 15.0
            ),
            "bridge_prices": bridges,
        },
        "top_triangular_parity_deviations": triangular_loops[:15],
        "top_spot_vs_perp_basis_dislocations": spot_perp_basis_list[:15],
    }


# =============================================================================
# 4. KUCOIN EARN (DEMAND SAVINGS) & LENDING PRO YIELD OPTIMIZER
# =============================================================================

def scan_earn_and_lending_yields(kc: KuCoinClient) -> Dict[str, Any]:
    earn_res = kc.request(SPOT_BASE_URL, "GET", "/api/v1/earn/saving/products", auth=True)
    earn_list = earn_res.get("data") or []

    lend_res = kc.request(SPOT_BASE_URL, "GET", "/api/v3/project/list", auth=True)
    lend_list = lend_res.get("data") or []

    hold_res = kc.request(SPOT_BASE_URL, "GET", "/api/v1/earn/hold-assets", auth=True)
    hold_items = (hold_res.get("data") or {}).get("items") if isinstance(hold_res.get("data"), dict) else (hold_res.get("data") or [])

    spot_accts = kc.get_nonzero_spot_accounts()

    earn_by_ccy: Dict[str, Dict[str, Any]] = {}
    for e in earn_list:
        ccy = e.get("currency")
        if not ccy or e.get("status") != "ONGOING":
            continue
        rate_pct = round(float(e.get("returnRate") or 0.0) * 100.0, 4)
        if ccy not in earn_by_ccy or rate_pct > earn_by_ccy[ccy]["demand_apr_pct"]:
            earn_by_ccy[ccy] = {
                "product_id": e.get("id"),
                "currency": ccy,
                "category": e.get("category"),
                "demand_apr_pct": rate_pct,
                "redeem_period_days": int(e.get("redeemPeriod") or 0),
                "min_subscribe": float(e.get("userLowerLimit") or 0.0),
                "max_user_limit": float(e.get("userUpperLimit") or 0.0),
                "income_release": e.get("incomeReleaseType"),
            }

    lend_by_ccy: Dict[str, Dict[str, Any]] = {}
    for l in lend_list:
        ccy = l.get("currency")
        if not ccy:
            continue
        mkt_apr_pct = round(float(l.get("marketInterestRate") or 0.0) * 100.0, 4)
        lend_by_ccy[ccy] = {
            "currency": ccy,
            "lending_market_apr_pct": mkt_apr_pct,
            "min_lend_apr_pct": round(float(l.get("minInterestRate") or 0.0) * 100.0, 2),
            "max_lend_apr_pct": round(float(l.get("maxInterestRate") or 0.0) * 100.0, 2),
            "min_purchase_size": float(l.get("minPurchaseSize") or 0.0),
            "purchase_enabled": bool(l.get("purchaseEnable")),
            "redeem_enabled": bool(l.get("redeemEnable")),
        }

    focus_currencies = ["USDT", "USDC", "SOL", "ETH", "ADA", "BTC", "DOT", "NEAR", "AVAX", "SUI", "APT", "XRP", "TRX", "ATOM", "KCS"]
    focus_table = []
    for ccy in focus_currencies:
        e_info = earn_by_ccy.get(ccy) or {}
        l_info = lend_by_ccy.get(ccy) or {}
        demand_apr = e_info.get("demand_apr_pct")
        lend_apr = l_info.get("lending_market_apr_pct")
        best_apr = max(demand_apr or 0.0, lend_apr or 0.0)
        best_venue = (
            "EARN_DEMAND_0DAY"
            if (demand_apr or 0.0) >= (lend_apr or 0.0) and demand_apr is not None
            else ("LENDING_PRO_HOURLY" if lend_apr is not None else "NONE")
        )
        focus_table.append({
            "currency": ccy,
            "earn_product_id": e_info.get("product_id"),
            "earn_demand_apr_pct": demand_apr,
            "earn_redeem_period_days": e_info.get("redeem_period_days"),
            "earn_min_amount": e_info.get("min_subscribe"),
            "lending_pro_market_apr_pct": lend_apr,
            "lending_pro_min_size": l_info.get("min_purchase_size"),
            "best_passive_apr_pct": round(best_apr, 4),
            "optimal_idle_venue": best_venue,
        })

    all_earn_sorted = sorted(earn_by_ccy.values(), key=lambda x: x["demand_apr_pct"], reverse=True)
    all_lend_sorted = sorted(lend_by_ccy.values(), key=lambda x: x["lending_market_apr_pct"], reverse=True)

    return {
        "summary": {
            "ongoing_earn_demand_products": len(earn_by_ccy),
            "lending_pro_currencies": len(lend_by_ccy),
            "active_earn_holdings_count": len(hold_items) if isinstance(hold_items, list) else 0,
            "nonzero_spot_accounts": spot_accts,
            "usdt_instant_demand_apr_pct": (earn_by_ccy.get("USDT") or {}).get("demand_apr_pct"),
            "usdt_earn_product_id": (earn_by_ccy.get("USDT") or {}).get("product_id"),
        },
        "core_currencies_yield_matrix": focus_table,
        "top_earn_demand_aprs": all_earn_sorted[:12],
        "top_lending_pro_aprs": all_lend_sorted[:12],
    }


# =============================================================================
# MAIN ORCHESTRATOR
# =============================================================================

def run_kucoin_multiservice_alpha() -> Dict[str, Any]:
    t0 = time.time()
    kc = KuCoinClient()

    c_res = kc.request(FUTURES_BASE_URL, "GET", "/api/v1/contracts/active", auth=False)
    contracts = c_res.get("data") or []
    contract_map = {c["symbol"]: c for c in contracts if c.get("symbol")}

    t_res = kc.request(SPOT_BASE_URL, "GET", "/api/v1/market/allTickers", auth=False)
    tickers = (t_res.get("data") or {}).get("ticker") or []
    spot_map = {t["symbol"]: t for t in tickers if t.get("symbol")}

    m_res = kc.request(SPOT_BASE_URL, "GET", "/api/v1/margin/config", auth=True)
    margin_data = m_res.get("data") or {}
    margin_borrowable = set(margin_data.get("currencyList") or [])

    funding_carry = scan_funding_and_cash_carry(contracts, spot_map, margin_borrowable, min_turnover_usd=250000.0)
    dex_lead_lag = scan_dex_to_kucoin_lead_lag(contract_map, spot_map)
    triangular_basis = scan_triangular_and_spot_perp_basis(contracts, spot_map)
    earn_lending = scan_earn_and_lending_yields(kc)

    now = int(time.time())
    payload = {
        "ok": True,
        "engine": "kucoin_multiservice_alpha_v1.1",
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime(now)),
        "edt_now": time.strftime("%Y-%m-%d %H:%M:%S EDT", time.localtime(now)),
        "elapsed_ms": round((time.time() - t0) * 1000.0, 1),
        "capabilities_verified": {
            "futures_contracts_active": len(contracts),
            "spot_pairs_active": len(tickers),
            "cross_margin_borrowable_assets": len(margin_borrowable),
            "cross_margin_max_leverage": margin_data.get("maxLeverage"),
            "earn_demand_products": earn_lending["summary"]["ongoing_earn_demand_products"],
            "lending_pro_currencies": earn_lending["summary"]["lending_pro_currencies"],
            "onchain_dex_tokens_monitored": dex_lead_lag["summary"]["tokens_monitored"],
        },
        "funding_and_cash_carry": funding_carry,
        "onchain_dex_lead_lag": dex_lead_lag,
        "triangular_and_spot_perp_basis": triangular_basis,
        "earn_and_lending_optimizer": earn_lending,
    }

    with open(OUTPUT_PATH, "w") as f:
        json.dump(payload, f, indent=2)

    try:
        req = urllib.request.Request(
            "https://kucoin-desk-scan.sonia-3e6f56284b.workers.dev/sync-multiservice",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "KuCoinMultiServiceAlpha/1.1"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=10).read()
    except Exception:
        pass

    return payload


if __name__ == "__main__":
    out = run_kucoin_multiservice_alpha()
    print(f"=== KuCoin Multi-Service Alpha Engine v1.1 ({out['elapsed_ms']} ms) ===")
    caps = out["capabilities_verified"]
    print(
        f"Verified Surfaces: {caps['futures_contracts_active']} Futures Contracts | "
        f"{caps['spot_pairs_active']} Spot Tickers | {caps['cross_margin_borrowable_assets']} Margin Borrowable | "
        f"{caps['earn_demand_products']} Earn Products | {caps['lending_pro_currencies']} Lending Currencies | "
        f"{caps['onchain_dex_tokens_monitored']} On-Chain DEX Tokens"
    )

    fc = out["funding_and_cash_carry"]
    print(
        f"\n1. Full-Universe Funding & Cash-and-Carry Harvester "
        f"(Liquid >$250k: {fc['summary']['liquid_contracts_gt_250k_usd']} | "
        f"Imminent <60m & |f|>15bps: {fc['summary']['imminent_lt60m_gt15bps_count']}):"
    )
    for r in fc["top_liquid_extreme_funding"][:12]:
        imm = " [IMMINENT <60m!]" if r["imminent_lt60m_gt15bps"] else ""
        print(
            f"   - {r['symbol']:<14} fund={r['funding_rate_bps']:+7.2f} bps/{int(r['funding_interval_hours'])}h "
            f"({r['annualized_apr_pct']:+8.1f}% APR) | settle={r['mins_until_settlement']:>5.1f}m{imm} | "
            f"vol24h=${r['turnover_24h_usd']:>11,.0f} | Spot={str(r['has_spot_pair']):<5} MarginBorrow={str(r['is_margin_borrowable']):<5} | "
            f"Basis={r['spot_to_perp_basis_bps']:+6.1f}bps | {r['strategy_type']}"
        )

    dl = out["onchain_dex_lead_lag"]
    print(
        f"\n2. On-Chain DEX (DexScreener) -> KuCoin Perp Lead-Lag Sniper "
        f"(Monitored: {dl['summary']['tokens_monitored']} | Dislocations >15bps: {dl['summary']['dislocations_gt_15bps_count']}):"
    )
    for r in dl["all_monitored_tokens"]:
        flag = " *** >15bps LEAD ***" if r["dislocation_gt_15bps"] else ""
        print(
            f"   - {r['base']:<7} ({r['dex_chain']}/{r['dex_venue']}) DEX=${r['dex_primary_price_usd']:<10.6g} vs "
            f"KC_Perp=${r['kucoin_perp_last_unit']:<10.6g} | Basis={r['dex_vs_perp_last_bps']:+6.2f} bps (VWAP={r['dex_vwap_vs_mark_bps']:+6.2f} bps) | "
            f"5mTxns={r['txns_5m_buys']}B/{r['txns_5m_sells']}S (Imb={r['txns_5m_imbalance']:+.2f}) | "
            f"5mVol=${r['dex_vol_5m_usd']:>8,.0f} | Impulse={r['onchain_impulse_score']:+6.2f} | {r['lead_lag_signal']}{flag}"
        )

    tb = out["triangular_and_spot_perp_basis"]
    print("\n3A. Top Triangular Spot & Cross-Quote Perp Parity Deviations:")
    for r in tb["top_triangular_parity_deviations"][:8]:
        print(
            f"   - {r['alt']:<8} {r['usdt_pair']:<12} vs {r['cross_pair']:<12} ({r['bridge_pair']}) | "
            f"MidDev={r['mid_parity_deviation_bps']:+6.2f} bps | CrossVsPerp={str(r['cross_spot_vs_perp_bps']):>6} bps | "
            f"NetHybrid(12bps)={r['net_after_hybrid_perp_maker_12bps']:+6.2f} bps | Vol=${r['cross_24h_vol_usd']:>9,.0f}"
        )
    print("\n3B. Top Spot-vs-Perp Basis Dislocations (Liquid >$250k):")
    for r in tb["top_spot_vs_perp_basis_dislocations"][:8]:
        print(
            f"   - {r['alt']:<8} Spot=${r['spot_last']:<10.6g} vs Perp=${r['perp_unit_last']:<10.6g} | "
            f"Basis={r['perp_vs_spot_last_bps']:+6.2f} bps (Net10bps={r['net_after_10bps_maker_entry']:+6.2f} bps) | "
            f"Fund={r['funding_rate_bps']:+6.2f} bps | {r['play']}"
        )

    el = out["earn_and_lending_optimizer"]
    print("\n4. KuCoin Earn (0-Day Demand) & Lending Pro Yield Optimizer:")
    for r in el["core_currencies_yield_matrix"]:
        print(
            f"   - {r['currency']:<6} Earn_0d_APR={str(r['earn_demand_apr_pct']):>7}% (ID={str(r['earn_product_id']):<6} min={r['earn_min_amount']}) | "
            f"LendingPro_APR={str(r['lending_pro_market_apr_pct']):>7}% | Best={r['best_passive_apr_pct']:>6.2f}% ({r['optimal_idle_venue']})"
        )
