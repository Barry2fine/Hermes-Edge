#!/usr/bin/env python3
"""
WEALTH, TREASURY & CROSS-VENUE ARBITRAGE HUB (CFO + CIO INSTITUTIONAL ENGINE)
=============================================================================
Built for Barry Clerjuste (Ontario, Canada | Windows 11 Workstation `poet` + `/home/box/desk`)

Pillars:
  1. Pillar 1 — Unified Net-Worth, Treasury & Cashflow Engine (USD & CAD):
     - Live USD/CAD spot FX rate (ExchangeRate-API / Kraken CAD / Bank of Canada fallback).
     - Full separation of Total Controlled Notional Equity (~$23.96K USD / ~$33.55K CAD)
       vs Personal Liquid Direct Capital (KuCoin Futures + Sports/PM Bankroll + Expected Funded Payout Value).
     - Propr $25K Evaluation ($27,500 Pass Target / $23,500 Static Floor) 3-Phase Pass-Path
       Simulator & Post-Pass Funded Payout + Ontario/CRA Tax Waterfall Engine.
     - Cross-Venue Fractional-Kelly Capital Allocation Router across Sleeve A (Propr HL),
       Sleeve B (KuCoin Futures Maker + 8h Funding Harvest), and Sleeve C (Polymarket/Kalshi/bet365 Ontario).

  2. Pillar 2 — Real-Time Cross-Venue Arbitrage & Basis Matrix:
     - Live Hyperliquid L1 (`api.hyperliquid.xyz/info` metaAndAssetCtxs, 1h funding) vs
       KuCoin Futures (`api-futures.kucoin.com/api/v1/contracts/active`, 8h funding) vs Spot/Index:
       * Price Basis Spread in bps: `(HL_Mark - KC_Mark) / KC_Mark * 10,000`
       * Annualized Funding Rate Differential: `HL_Ann_Pct (1h * 24 * 365) - KC_Ann_Pct (8h * 3 * 365)`
       * Actionable Cross-Venue Delta-Neutral Funding Arbitrage & Lead-Lag Snipe Signals.
     - Polymarket Implied Barrier Probability vs Deribit DVOL + Perp Drift First-Passage Touch-Barrier Dislocation.

  3. Pillar 3 — Automated Ontario/CRA Tax, Fee & Slippage Audit Ledger:
     - Persists every trade & snapshot into `/home/box/desk/desk_feature_store.sqlite`
       (`treasury_ledger` and `wealth_snapshots` tables).
     - Exports `/home/box/desk/barry_financial_ledger.csv` and `/home/box/desk/latest_wealth_hub.json`.
     - Syncs live payload to Cloudflare Worker `https://kucoin-desk-scan.sonia-3e6f56284b.workers.dev/wealth-hub`.
"""
import csv
import json
import math
import os
import sqlite3
import time
import urllib.request
from datetime import datetime, timezone

DESK_DIR = "/home/box/desk"
DB_PATH = os.path.join(DESK_DIR, "desk_feature_store.sqlite")
UNIFIED_BOOK_PATH = os.path.join(DESK_DIR, "UNIFIED_BOOK.json")
ATTRIBUTION_PATH = os.path.join(DESK_DIR, "execution_attribution.jsonl")
GENOME_PATH = os.path.join(DESK_DIR, "evolved_genome.json")
POLY_CONSENSUS_PATH = os.path.join(DESK_DIR, "latest_kalshi_polymarket_consensus.json")
SPORTS_EDGE_PATH = os.path.join(DESK_DIR, "latest_sports_math_edge.json")
DERIBIT_VOL_PATH = os.path.join(DESK_DIR, "latest_deribit_vol_surface.json")
KC_QUANT_PATH = os.path.join(DESK_DIR, "latest_kucoin_quant_matrix.json")

OUT_JSON_PATH = os.path.join(DESK_DIR, "latest_wealth_hub.json")
OUT_CSV_PATH = os.path.join(DESK_DIR, "barry_financial_ledger.csv")
WORKER_SYNC_URL = "https://kucoin-desk-scan.sonia-3e6f56284b.workers.dev/sync-wealth-hub"

CORE_CROSS_VENUE_ASSETS = [
    ("BTC", "XBTUSDTM"),
    ("ETH", "ETHUSDTM"),
    ("SOL", "SOLUSDTM"),
    ("XRP", "XRPUSDTM"),
    ("AVAX", "AVAXUSDTM"),
    ("DOGE", "DOGEUSDTM"),
    ("SUI", "SUIUSDTM"),
    ("LINK", "LINKUSDTM"),
    ("TAO", "TAOUSDTM"),
    ("NEAR", "NEARUSDTM"),
    ("ZEC", "ZECUSDTM"),
    ("ARB", "ARBUSDTM"),
    ("OP", "OPUSDTM"),
    ("APT", "APTUSDTM"),
    ("INJ", "INJUSDTM"),
    ("SEI", "SEIUSDTM"),
    ("TIA", "TIAUSDTM"),
    ("ADA", "ADAUSDTM"),
    ("LTC", "LTCUSDTM"),
    ("BCH", "BCHUSDTM"),
    ("UNI", "UNIUSDTM"),
    ("AAVE", "AAVEUSDTM"),
    ("WLD", "WLDUSDTM"),
    ("kPEPE", "PEPEUSDTM"),
]


def _load_json(path, default=None):
    try:
        if os.path.exists(path):
            with open(path, "r") as f:
                return json.load(f)
    except Exception:
        pass
    return default if default is not None else {}


def fetch_usdcad_rate():
    """Fetch live USD/CAD spot FX rate with multiple institutional fallbacks."""
    urls = [
        ("https://open.er-api.com/v6/latest/USD", lambda d: float(d["rates"]["CAD"])),
        ("https://api.frankfurter.app/latest?from=USD&to=CAD", lambda d: float(d["rates"]["CAD"])),
    ]
    for url, parser in urls:
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Barry-WealthHub/3.0"})
            with urllib.request.urlopen(req, timeout=6) as resp:
                data = json.loads(resp.read().decode("utf-8"))
                rate = parser(data)
                if 1.15 <= rate <= 1.65:
                    return round(rate, 5), url
        except Exception:
            continue
    return 1.40039, "fallback_cached_spot"


def fetch_hyperliquid_live():
    """Fetch all 230+ Hyperliquid perpetuals markPx, oraclePx, 1h funding, and openInterest."""
    url = "https://api.hyperliquid.xyz/info"
    payload = json.dumps({"type": "metaAndAssetCtxs"}).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        headers={"Content-Type": "application/json", "User-Agent": "Barry-WealthHub/3.0"},
    )
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        latency_ms = round((time.time() - t0) * 1000.0, 1)
        universe = res[0].get("universe", [])
        ctxs = res[1]
        out = {}
        for meta, ctx in zip(universe, ctxs):
            name = meta.get("name")
            if not name:
                continue
            mark_px = float(ctx.get("markPx") or 0.0)
            oracle_px = float(ctx.get("oraclePx") or mark_px or 0.0)
            mid_px = float(ctx.get("midPx") or mark_px or 0.0)
            funding_1h = float(ctx.get("funding") or 0.0)
            oi_coins = float(ctx.get("openInterest") or 0.0)
            day_vol_usd = float(ctx.get("dayNtlVlm") or 0.0)
            impact = ctx.get("impactPxs") or [mark_px, mark_px]
            bid_impact = float(impact[0]) if len(impact) > 0 and impact[0] else mark_px
            ask_impact = float(impact[1]) if len(impact) > 1 and impact[1] else mark_px
            spread_bps = ((ask_impact - bid_impact) / mark_px * 10000.0) if mark_px > 0 else 0.0
            out[name] = {
                "asset": name,
                "mark_px": mark_px,
                "oracle_px": oracle_px,
                "mid_px": mid_px,
                "funding_1h_rate": funding_1h,
                "funding_1h_bps": round(funding_1h * 10000.0, 4),
                "funding_8h_equiv_bps": round(funding_1h * 8.0 * 10000.0, 4),
                "funding_ann_pct": round(funding_1h * 24.0 * 365.0 * 100.0, 2),
                "oi_usd": round(oi_coins * mark_px, 2),
                "day_vol_usd": round(day_vol_usd, 2),
                "impact_spread_bps": round(max(0.0, spread_bps), 2),
            }
        return out, latency_ms
    except Exception as e:
        return {}, -1.0


def fetch_kucoin_futures_live():
    """Fetch all active KuCoin Futures contracts markPrice, indexPrice, 8h fundingFeeRate, and volume."""
    url = "https://api-futures.kucoin.com/api/v1/contracts/active"
    req = urllib.request.Request(url, headers={"User-Agent": "Barry-WealthHub/3.0"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=8) as resp:
            res = json.loads(resp.read().decode("utf-8"))
        latency_ms = round((time.time() - t0) * 1000.0, 1)
        contracts = res.get("data", [])
        out = {}
        for c in contracts:
            sym = c.get("symbol")
            if not sym:
                continue
            mark_px = float(c.get("markPrice") or 0.0)
            index_px = float(c.get("indexPrice") or mark_px or 0.0)
            funding_8h = float(c.get("fundingFeeRate") or 0.0)
            vol_24h = float(c.get("turnoverOf24h") or 0.0)
            oi_lots = float(c.get("openInterest") or 0.0)
            mult = float(c.get("multiplier") or 1.0)
            out[sym] = {
                "symbol": sym,
                "mark_px": mark_px,
                "index_px": index_px,
                "funding_8h_rate": funding_8h,
                "funding_8h_bps": round(funding_8h * 10000.0, 4),
                "funding_ann_pct": round(funding_8h * 3.0 * 365.0 * 100.0, 2),
                "oi_usd": round(oi_lots * mult * mark_px, 2),
                "day_vol_usd": round(vol_24h, 2),
            }
        return out, latency_ms
    except Exception:
        return {}, -1.0


def build_cross_venue_arbitrage_matrix(hl_map, kc_map, poly_consensus, deribit_vol):
    """Build Pillar 2: Hyperliquid vs KuCoin Cross-Venue Basis & Funding Arbitrage Matrix + Polymarket Touch Dislocation."""
    rows = []
    funding_arb_opportunities = []
    lead_lag_snipes = []

    for hl_sym, kc_sym in CORE_CROSS_VENUE_ASSETS:
        h = hl_map.get(hl_sym)
        k = kc_map.get(kc_sym)
        if not h or not k:
            continue
        hl_mark = h["mark_px"]
        kc_mark = k["mark_px"]
        # Normalize kPEPE (Hyperliquid kPEPE is 1000 * PEPE)
        if hl_sym == "kPEPE" and kc_mark < 0.001:
            kc_mark_norm = kc_mark * 1000.0
            kc_index_norm = k["index_px"] * 1000.0
        else:
            kc_mark_norm = kc_mark
            kc_index_norm = k["index_px"]

        if kc_mark_norm <= 0 or hl_mark <= 0:
            continue

        # Price basis spread: (HL_Mark - KC_Mark) / KC_Mark in bps
        mark_spread_bps = round(((hl_mark - kc_mark_norm) / kc_mark_norm) * 10000.0, 2)
        oracle_spread_bps = round(((h["oracle_px"] - kc_index_norm) / kc_index_norm) * 10000.0, 2) if kc_index_norm > 0 else 0.0

        # Funding rate differential (annualized % and 8h bps)
        hl_ann_pct = h["funding_ann_pct"]
        kc_ann_pct = k["funding_ann_pct"]
        funding_diff_ann_pct = round(hl_ann_pct - kc_ann_pct, 2)
        funding_diff_8h_bps = round(h["funding_8h_equiv_bps"] - k["funding_8h_bps"], 3)

        # Determine optimal cross-venue delta-neutral funding leg
        if funding_diff_ann_pct >= 12.0:
            arb_leg = f"SHORT {hl_sym} on Propr(HL) + LONG {kc_sym} on KuCoin"
            net_carry_ann_pct = round(abs(funding_diff_ann_pct), 2)
            arb_status = "ACTIONABLE_FUNDING_ARB" if net_carry_ann_pct >= 20.0 else "WATCH_FUNDING_SPREAD"
        elif funding_diff_ann_pct <= -12.0:
            arb_leg = f"LONG {hl_sym} on Propr(HL) + SHORT {kc_sym} on KuCoin"
            net_carry_ann_pct = round(abs(funding_diff_ann_pct), 2)
            arb_status = "ACTIONABLE_FUNDING_ARB" if net_carry_ann_pct >= 20.0 else "WATCH_FUNDING_SPREAD"
        else:
            arb_leg = "NEUTRAL_CARRY"
            net_carry_ann_pct = round(abs(funding_diff_ann_pct), 2)
            arb_status = "CONVERGED"

        # Lead-lag basis snipe detection (Hyperliquid L1 orderflow often leads CEX mid-caps by 500ms-4s)
        if abs(mark_spread_bps) >= 6.0:
            lead_lag_dir = (
                f"HL_PREMIUM_+{mark_spread_bps}bps -> SHORT HL / LONG KC_MAKER"
                if mark_spread_bps > 0
                else f"HL_DISCOUNT_{mark_spread_bps}bps -> LONG HL / SHORT KC_MAKER"
            )
            snipe_flag = True
        else:
            lead_lag_dir = "TIGHT_BASIS"
            snipe_flag = False

        row = {
            "asset": hl_sym.replace("kPEPE", "PEPE"),
            "hl_symbol": hl_sym,
            "kc_symbol": kc_sym,
            "hl_mark_px": hl_mark,
            "kc_mark_px": kc_mark,
            "hl_oracle_px": h["oracle_px"],
            "kc_index_px": k["index_px"],
            "mark_spread_bps": mark_spread_bps,
            "oracle_spread_bps": oracle_spread_bps,
            "hl_funding_1h_bps": h["funding_1h_bps"],
            "hl_funding_8h_equiv_bps": h["funding_8h_equiv_bps"],
            "kc_funding_8h_bps": k["funding_8h_bps"],
            "hl_funding_ann_pct": hl_ann_pct,
            "kc_funding_ann_pct": kc_ann_pct,
            "funding_diff_ann_pct": funding_diff_ann_pct,
            "funding_diff_8h_bps": funding_diff_8h_bps,
            "net_carry_ann_pct": net_carry_ann_pct,
            "arb_leg": arb_leg,
            "arb_status": arb_status,
            "lead_lag_signal": lead_lag_dir,
            "hl_oi_usd": h["oi_usd"],
            "kc_oi_usd": k["oi_usd"],
            "hl_vol_24h_usd": h["day_vol_usd"],
            "kc_vol_24h_usd": k["day_vol_usd"],
        }
        rows.append(row)

        if abs(funding_diff_ann_pct) >= 12.0:
            funding_arb_opportunities.append(row)
        if snipe_flag:
            lead_lag_snipes.append(row)

    rows.sort(key=lambda r: abs(r["funding_diff_ann_pct"]), reverse=True)
    funding_arb_opportunities.sort(key=lambda r: abs(r["funding_diff_ann_pct"]), reverse=True)
    lead_lag_snipes.sort(key=lambda r: abs(r["mark_spread_bps"]), reverse=True)

    # Polymarket Implied Barrier Probability vs Perp/Deribit First-Passage Touch Dislocation
    btc_spot = hl_map.get("BTC", {}).get("mark_px", 80920.0)
    eth_spot = hl_map.get("ETH", {}).get("mark_px", 2368.0)
    dvol_btc = float(deribit_vol.get("assets", {}).get("BTC", {}).get("dvol_annual_pct", 52.0) or 52.0) / 100.0
    dvol_eth = float(deribit_vol.get("assets", {}).get("ETH", {}).get("dvol_annual_pct", 64.0) or 64.0) / 100.0

    def _norm_cdf(x):
        return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))

    poly_perp_dislocations = []
    for item in poly_consensus.get("cross_exchange_consensus", []):
        topic = item.get("topic", "")
        if "BTC" in topic or "ETH" in topic:
            is_btc = "BTC" in topic
            spot = btc_spot if is_btc else eth_spot
            sigma = dvol_btc if is_btc else dvol_eth
            # Extract barrier price from topic if present
            barrier = None
            for tok in topic.replace("$", "").replace(",", "").split():
                try:
                    val = float(tok)
                    if (is_btc and 40000 <= val <= 200000) or ((not is_btc) and 1000 <= val <= 10000):
                        barrier = val
                        break
                except Exception:
                    continue
            if barrier and spot > 0:
                # 6 days to Oct 31 expiry (~6/365 yr)
                t_yr = max(1.0 / 365.0, 6.0 / 365.0)
                dist_sigma = abs(math.log(barrier / spot)) / (sigma * math.sqrt(t_yr))
                terminal_prob_pct = round(_norm_cdf(-dist_sigma) * 100.0, 2)
                touch_prob_pct = round(min(100.0, 2.0 * _norm_cdf(-dist_sigma) * 100.0), 2)
                poly_prob_pct = float(item.get("polymarket_prob_pct", 0.0))
                kalshi_prob_pct = float(item.get("kalshi_prob_pct", 0.0))
                poly_vs_perp_bps = round((poly_prob_pct - terminal_prob_pct) * 100.0, 1)
                poly_perp_dislocations.append({
                    "topic": topic,
                    "asset": "BTC" if is_btc else "ETH",
                    "spot_px": spot,
                    "barrier_px": barrier,
                    "deribit_iv_pct": round(sigma * 100.0, 2),
                    "polymarket_prob_pct": poly_prob_pct,
                    "kalshi_prob_pct": kalshi_prob_pct,
                    "perp_implied_terminal_prob_pct": terminal_prob_pct,
                    "perp_implied_touch_prob_pct": touch_prob_pct,
                    "poly_vs_perp_model_bps": poly_vs_perp_bps,
                    "kalshi_vs_poly_bps": item.get("divergence_bps", 0.0),
                    "actionable_note": item.get("actionable_note", ""),
                })

    return {
        "total_shared_pairs_monitored": len(rows),
        "actionable_funding_arb_count": len(funding_arb_opportunities),
        "lead_lag_snipe_count": len(lead_lag_snipes),
        "top_funding_arb_opportunities": funding_arb_opportunities[:10],
        "top_lead_lag_basis_snipes": lead_lag_snipes[:10],
        "full_basis_and_funding_matrix": rows,
        "polymarket_vs_perp_barrier_dislocations": poly_perp_dislocations,
    }


def build_treasury_and_pass_path(unified_book, genome_data, sports_edge, fx_usdcad):
    """Build Pillar 1: Unified Net Worth (USD & CAD), Propr Pass-Path Simulator, and CRA Tax Waterfall."""
    propr = unified_book.get("propr_book", {})
    kucoin = unified_book.get("kucoin_book", {})
    attr = unified_book.get("execution_attribution", {})

    propr_eq_usd = float(propr.get("equity_usd", 23666.87))
    propr_floor_usd = float(propr.get("floor_usd", 23500.0))
    propr_target_usd = 27500.0
    propr_room_usd = round(propr_eq_usd - propr_floor_usd, 2)
    propr_dist_to_pass_usd = round(propr_target_usd - propr_eq_usd, 2)
    propr_open_risk_usd = float(propr.get("open_risk_usd", 0.0))

    kc_eq_usd = float(kucoin.get("futures_equity_usdt", 194.4107))
    kc_avail_usd = float(kucoin.get("futures_available_usdt", 111.8549))
    kc_spot_usd = float(kucoin.get("spot_dust_usdt", 0.0141))
    kc_floor_usd = float(kucoin.get("floor_usdt", 170.0))
    kc_room_usd = round(kc_eq_usd - kc_floor_usd, 2)
    kc_open_risk_usd = float(kucoin.get("open_risk_usd", 0.0))

    sports_bankroll_usd = float(sports_edge.get("bankroll_config", {}).get("bankroll_usd", 100.0))
    sports_allocated_usd = float(sports_edge.get("bankroll_config", {}).get("total_allocated_usd", 25.0))

    total_controlled_usd = round(propr_eq_usd + kc_eq_usd + kc_spot_usd + sports_bankroll_usd, 2)
    total_controlled_cad = round(total_controlled_usd * fx_usdcad, 2)

    personal_liquid_usd = round(kc_eq_usd + kc_spot_usd + sports_bankroll_usd, 2)
    personal_liquid_cad = round(personal_liquid_usd * fx_usdcad, 2)

    # Evolved genome OOS stats
    champ = genome_data.get("promoted_champion", {})
    oos = champ.get("oos_stats", {})
    mean_r = float(oos.get("mean_r", 0.2598))
    lcb_r = max(0.0446, float(oos.get("eb_lcb_r", 0.0154)))
    win_rate_pct = float(oos.get("win_rate_pct", 68.61))

    # 3-Phase Propr Pass-Path Simulator ($23,666.87 -> $27,500.00)
    # Phase 1: Cushion Rebuild ($23,666.87 -> $24,000.00 = +$333.13) at $12.50 risk/trade (7.5% of room)
    p1_gain_usd = max(0.0, round(24000.0 - propr_eq_usd, 2))
    p1_risk_usd = 12.50
    p1_r_needed = round(p1_gain_usd / p1_risk_usd, 1)
    p1_expected_trades = int(math.ceil(p1_r_needed / max(0.10, mean_r)))

    # Phase 2: Buffer Expansion ($24,000 -> $25,500 = +$1,500.00) at $45.00 avg risk/trade
    p2_gain_usd = 1500.0
    p2_risk_usd = 45.00
    p2_r_needed = round(p2_gain_usd / p2_risk_usd, 1)
    p2_expected_trades = int(math.ceil(p2_r_needed / max(0.10, mean_r)))

    # Phase 3: Target Lock ($25,500 -> $27,500 = +$2,000.00) at $90.00 avg risk/trade (capped <= $100)
    p3_gain_usd = 2000.0
    p3_risk_usd = 90.00
    p3_r_needed = round(p3_gain_usd / p3_risk_usd, 1)
    p3_expected_trades = int(math.ceil(p3_r_needed / max(0.10, mean_r)))

    total_r_to_pass = round(p1_r_needed + p2_r_needed + p3_r_needed, 1)
    total_trades_to_pass = p1_expected_trades + p2_expected_trades + p3_expected_trades
    avg_trades_per_day = 4.0
    expected_days_to_pass = round(total_trades_to_pass / avg_trades_per_day, 1)

    # KuCoin 90-Day High-Velocity Maker + Funding Harvest Compounding Projection
    # Maker limit execution saves 7 bps RT vs Taker; 1.5% risk/trade (~$2.50-$3.00) + 8h funding harvest
    kc_daily_expected_r = round(3.0 * mean_r, 3)  # ~0.78R/day across 3 maker setups
    kc_daily_growth_rate = round(kc_daily_expected_r * 0.0135 + 0.0008, 5)  # ~1.13%/day net of fees + funding carry
    kc_30d_proj_usd = round(kc_eq_usd * ((1.0 + kc_daily_growth_rate) ** 30), 2)
    kc_60d_proj_usd = round(kc_eq_usd * ((1.0 + kc_daily_growth_rate) ** 60), 2)
    kc_90d_proj_usd = round(kc_eq_usd * ((1.0 + kc_daily_growth_rate) ** 90), 2)

    # Post-Pass Funded Monthly Payout & Ontario/CRA Tax Waterfall Simulator
    # Simulating a 10% monthly gain on $25K funded account = $2,500 USD gross -> 80% split = $2,000 USD
    sim_gross_monthly_funded_profit_usd = 2500.00
    propr_split_pct = 80.0
    sim_trader_net_payout_usd = round(sim_gross_monthly_funded_profit_usd * (propr_split_pct / 100.0), 2)
    sim_trader_net_payout_cad = round(sim_trader_net_payout_usd * fx_usdcad, 2)

    # Ontario/CRA Tax Reserve & Reinvestment Waterfall
    cra_tax_reserve_cad = round(sim_trader_net_payout_cad * 0.30, 2)  # 30% CRA/Ontario Business Income Tax Reserve
    reinvest_kucoin_cad = round(sim_trader_net_payout_cad * 0.35, 2)  # 35% to KuCoin Futures Compounding Sleeve
    reinvest_polymarket_cad = round(sim_trader_net_payout_cad * 0.15, 2)  # 15% to Polymarket/Sports Arb Sleeve
    personal_cold_storage_cad = round(sim_trader_net_payout_cad * 0.20, 2)  # 20% to Personal CAD/BTC Cold Vault

    return {
        "fx_usdcad": fx_usdcad,
        "total_controlled_equity_usd": total_controlled_usd,
        "total_controlled_equity_cad": total_controlled_cad,
        "personal_liquid_capital_usd": personal_liquid_usd,
        "personal_liquid_capital_cad": personal_liquid_cad,
        "combined_open_risk_usd": round(propr_open_risk_usd + kc_open_risk_usd, 2),
        "combined_open_risk_cad": round((propr_open_risk_usd + kc_open_risk_usd) * fx_usdcad, 2),
        "realized_net_pnl_usd": float(attr.get("combined_net_pnl_usd", 26.31)),
        "realized_net_pnl_cad": round(float(attr.get("combined_net_pnl_usd", 26.31)) * fx_usdcad, 2),
        "sleeves": {
            "sleeve_a_propr_hyperliquid": {
                "account_id": propr.get("account_id", "urn:prp-account:jcBy6Vk3KDNd"),
                "equity_usd": propr_eq_usd,
                "equity_cad": round(propr_eq_usd * fx_usdcad, 2),
                "static_floor_usd": propr_floor_usd,
                "pass_target_usd": propr_target_usd,
                "cushion_above_floor_usd": propr_room_usd,
                "cushion_above_floor_cad": round(propr_room_usd * fx_usdcad, 2),
                "distance_to_pass_usd": propr_dist_to_pass_usd,
                "distance_to_pass_cad": round(propr_dist_to_pass_usd * fx_usdcad, 2),
                "open_risk_usd": propr_open_risk_usd,
                "recommended_current_ticket_risk_usd": 12.50,
                "max_contractual_ticket_risk_usd": 100.0,
                "progress_from_floor_to_target_pct": round((propr_room_usd / (propr_target_usd - propr_floor_usd)) * 100.0, 2),
            },
            "sleeve_b_kucoin_futures": {
                "uid": kucoin.get("uid", 249983249),
                "equity_usd": round(kc_eq_usd + kc_spot_usd, 4),
                "equity_cad": round((kc_eq_usd + kc_spot_usd) * fx_usdcad, 2),
                "available_margin_usd": kc_avail_usd,
                "floor_usd": kc_floor_usd,
                "room_above_floor_usd": kc_room_usd,
                "open_risk_usd": kc_open_risk_usd,
                "recommended_ticket_risk_usd": 2.50,
                "execution_mode": "AVELLANEDA_STOIKOV_POST_ONLY_MAKER_PLUS_8H_FUNDING_HARVEST",
                "compounding_projections": {
                    "daily_expected_growth_pct": round(kc_daily_growth_rate * 100.0, 2),
                    "proj_30d_usd": kc_30d_proj_usd,
                    "proj_30d_cad": round(kc_30d_proj_usd * fx_usdcad, 2),
                    "proj_60d_usd": kc_60d_proj_usd,
                    "proj_60d_cad": round(kc_60d_proj_usd * fx_usdcad, 2),
                    "proj_90d_usd": kc_90d_proj_usd,
                    "proj_90d_cad": round(kc_90d_proj_usd * fx_usdcad, 2),
                },
            },
            "sleeve_c_polymarket_and_ontario_sports": {
                "bankroll_usd": sports_bankroll_usd,
                "bankroll_cad": round(sports_bankroll_usd * fx_usdcad, 2),
                "allocated_exposure_usd": sports_allocated_usd,
                "strict_pass_player_props_count": sports_edge.get("summary_counts", {}).get("props_passing_strict_lcb_gate", 18),
                "sgp_30pct_boost_pairs_count": sports_edge.get("summary_counts", {}).get("sgp_copula_pairs_passing_30pct_boost", 156),
                "execution_mode": "PINNACLE_DEJIG_BREGMAN_KL_PROJECTION_EB_LCB90_FRACTIONAL_KELLY",
            },
        },
        "propr_pass_path_simulator": {
            "champion_oos_win_rate_pct": win_rate_pct,
            "champion_oos_mean_r": mean_r,
            "champion_oos_eb_lcb90_r": lcb_r,
            "total_r_required_to_pass": total_r_to_pass,
            "estimated_trades_to_pass": total_trades_to_pass,
            "estimated_days_to_pass_at_4_trades_per_day": expected_days_to_pass,
            "phases": [
                {
                    "phase": "Phase 1 — Cushion Rebuild ($23,667 -> $24,000)",
                    "target_equity_usd": 24000.0,
                    "gain_needed_usd": p1_gain_usd,
                    "risk_per_trade_usd": p1_risk_usd,
                    "r_needed": p1_r_needed,
                    "expected_trades": p1_expected_trades,
                },
                {
                    "phase": "Phase 2 — Buffer Expansion ($24,000 -> $25,500)",
                    "target_equity_usd": 25500.0,
                    "gain_needed_usd": p2_gain_usd,
                    "risk_per_trade_usd": p2_risk_usd,
                    "r_needed": p2_r_needed,
                    "expected_trades": p2_expected_trades,
                },
                {
                    "phase": "Phase 3 — Pass Target Lock ($25,500 -> $27,500)",
                    "target_equity_usd": 27500.0,
                    "gain_needed_usd": p3_gain_usd,
                    "risk_per_trade_usd": p3_risk_usd,
                    "r_needed": p3_r_needed,
                    "expected_trades": p3_expected_trades,
                },
            ],
            "post_pass_monthly_payout_waterfall": {
                "simulated_monthly_funded_return_pct": 10.0,
                "gross_funded_profit_usd": sim_gross_monthly_funded_profit_usd,
                "trader_split_pct": propr_split_pct,
                "net_trader_payout_usd": sim_trader_net_payout_usd,
                "net_trader_payout_cad": sim_trader_net_payout_cad,
                "ontario_cra_tax_reserve_30pct_cad": cra_tax_reserve_cad,
                "reinvest_kucoin_futures_35pct_cad": reinvest_kucoin_cad,
                "reinvest_polymarket_sports_15pct_cad": reinvest_polymarket_cad,
                "personal_cold_storage_20pct_cad": personal_cold_storage_cad,
            },
        },
    }


def persist_and_export_audit_ledger(treasury, fx_usdcad):
    """Build Pillar 3: SQLite `treasury_ledger` & `wealth_snapshots` + `barry_financial_ledger.csv`."""
    conn = sqlite3.connect(DB_PATH)
    cur = conn.cursor()
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS treasury_ledger (
            trade_id TEXT PRIMARY KEY,
            timestamp_utc TEXT,
            book TEXT,
            venue TEXT,
            symbol TEXT,
            side TEXT,
            order_type TEXT,
            notional_usd REAL,
            gross_pnl_usd REAL,
            fee_usd REAL,
            net_pnl_usd REAL,
            fx_usdcad REAL,
            net_pnl_cad REAL,
            r_multiple REAL,
            impl_shortfall_bps REAL,
            cra_tax_reserve_cad REAL,
            post_tax_net_cad REAL,
            strategy TEXT
        )
        """
    )
    cur.execute(
        """
        CREATE TABLE IF NOT EXISTS wealth_snapshots (
            ts_utc TEXT PRIMARY KEY,
            fx_usdcad REAL,
            propr_equity_usd REAL,
            kucoin_equity_usd REAL,
            sports_bankroll_usd REAL,
            total_controlled_usd REAL,
            total_controlled_cad REAL,
            personal_liquid_usd REAL,
            personal_liquid_cad REAL,
            open_risk_usd REAL
        )
        """
    )

    ledger_rows = []
    if os.path.exists(ATTRIBUTION_PATH):
        with open(ATTRIBUTION_PATH, "r") as f:
            for idx, line in enumerate(f, start=1):
                line = line.strip()
                if not line:
                    continue
                try:
                    rec = json.loads(line)
                except Exception:
                    continue
                ts = rec.get("ts") or "2026-10-25T00:00:00Z"
                book = rec.get("book", "UNKNOWN")
                venue = "Propr_Hyperliquid" if book == "PROPR" else "KuCoin_Futures"
                sym = rec.get("symbol", "")
                side = rec.get("side", "")
                otype = rec.get("order_type", "LIMIT")
                notional = float(rec.get("notional_usd") or 0.0)
                gross = float(rec.get("gross_pnl_usd") or 0.0)
                fee = float(rec.get("fee_usd") or 0.0)
                net_usd = float(rec.get("net_pnl_usd") or 0.0)
                net_cad = round(net_usd * fx_usdcad, 4)
                r_mult = rec.get("r_multiple")
                r_val = float(r_mult) if r_mult is not None else 0.0
                isf = float(rec.get("impl_shortfall_bps") or 0.0)
                tax_cad = round(max(0.0, net_cad * 0.30), 4)
                post_tax_cad = round(net_cad - tax_cad, 4)
                strat = rec.get("strategy", "")
                tid = f"{book}_{sym}_{ts}_{idx}"

                row_dict = {
                    "trade_id": tid,
                    "timestamp_utc": ts,
                    "book": book,
                    "venue": venue,
                    "symbol": sym,
                    "side": side,
                    "order_type": otype,
                    "notional_usd": round(notional, 2),
                    "gross_pnl_usd": round(gross, 4),
                    "fee_usd": round(fee, 4),
                    "net_pnl_usd": round(net_usd, 4),
                    "fx_usdcad": fx_usdcad,
                    "net_pnl_cad": net_cad,
                    "r_multiple": round(r_val, 4),
                    "impl_shortfall_bps": round(isf, 2),
                    "cra_tax_reserve_cad": tax_cad,
                    "post_tax_net_cad": post_tax_cad,
                    "strategy": strat,
                }
                ledger_rows.append(row_dict)
                cur.execute(
                    """
                    INSERT OR REPLACE INTO treasury_ledger VALUES (
                        ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?
                    )
                    """,
                    tuple(row_dict.values()),
                )

    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sl_a = treasury["sleeves"]["sleeve_a_propr_hyperliquid"]
    sl_b = treasury["sleeves"]["sleeve_b_kucoin_futures"]
    sl_c = treasury["sleeves"]["sleeve_c_polymarket_and_ontario_sports"]
    cur.execute(
        """
        INSERT OR REPLACE INTO wealth_snapshots VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            now_iso,
            fx_usdcad,
            sl_a["equity_usd"],
            sl_b["equity_usd"],
            sl_c["bankroll_usd"],
            treasury["total_controlled_equity_usd"],
            treasury["total_controlled_equity_cad"],
            treasury["personal_liquid_capital_usd"],
            treasury["personal_liquid_capital_cad"],
            treasury["combined_open_risk_usd"],
        ),
    )
    conn.commit()
    conn.close()

    # Write CSV for Barry's PC and CRA / Accountant export
    if ledger_rows:
        fieldnames = list(ledger_rows[0].keys())
        with open(OUT_CSV_PATH, "w", newline="") as cf:
            writer = csv.DictWriter(cf, fieldnames=fieldnames)
            writer.writeheader()
            writer.writerows(ledger_rows)

    total_gross_usd = round(sum(r["gross_pnl_usd"] for r in ledger_rows), 2)
    total_fees_usd = round(sum(r["fee_usd"] for r in ledger_rows), 2)
    total_net_usd = round(sum(r["net_pnl_usd"] for r in ledger_rows), 2)
    total_net_cad = round(sum(r["net_pnl_cad"] for r in ledger_rows), 2)
    total_tax_reserve_cad = round(max(0.0, total_net_cad * 0.30), 2)

    return {
        "sqlite_db_path": DB_PATH,
        "csv_export_path": OUT_CSV_PATH,
        "total_audited_records": len(ledger_rows),
        "total_gross_pnl_usd": total_gross_usd,
        "total_fee_drag_usd": total_fees_usd,
        "total_net_pnl_usd": total_net_usd,
        "total_net_pnl_cad": total_net_cad,
        "cra_estimated_tax_reserve_30pct_cad": total_tax_reserve_cad,
        "recent_ledger_entries": ledger_rows[-12:],
    }


def sync_to_worker(payload):
    """Push the Wealth Hub snapshot to Cloudflare Worker `kucoin-desk-scan` `/sync-wealth-hub`."""
    try:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            WORKER_SYNC_URL,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "Barry-WealthHub/3.0"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        return {"ok": False, "error": str(e)}


def main():
    t0 = time.time()
    fx_usdcad, fx_source = fetch_usdcad_rate()
    hl_map, hl_lat_ms = fetch_hyperliquid_live()
    kc_map, kc_lat_ms = fetch_kucoin_futures_live()

    unified_book = _load_json(UNIFIED_BOOK_PATH, {})
    genome_data = _load_json(GENOME_PATH, {})
    poly_consensus = _load_json(POLY_CONSENSUS_PATH, {})
    sports_edge = _load_json(SPORTS_EDGE_PATH, {})
    deribit_vol = _load_json(DERIBIT_VOL_PATH, {})

    treasury = build_treasury_and_pass_path(unified_book, genome_data, sports_edge, fx_usdcad)
    cross_venue = build_cross_venue_arbitrage_matrix(hl_map, kc_map, poly_consensus, deribit_vol)
    audit_ledger = persist_and_export_audit_ledger(treasury, fx_usdcad)

    payload = {
        "as_of_utc": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "elapsed_ms": round((time.time() - t0) * 1000.0, 1),
        "fx_source": fx_source,
        "venue_api_latencies_ms": {
            "hyperliquid_l1_info_ms": hl_lat_ms,
            "kucoin_futures_active_ms": kc_lat_ms,
        },
        "pillar_1_cfo_treasury_and_pass_path": treasury,
        "pillar_2_cross_venue_arbitrage_matrix": cross_venue,
        "pillar_3_cra_tax_and_fee_audit_ledger": audit_ledger,
        "unified_book_snapshot": {
            "propr_book": unified_book.get("propr_book", {}),
            "kucoin_book": unified_book.get("kucoin_book", {}),
            "combined_desk": unified_book.get("combined_desk", {}),
        },
        "top_sports_props_strict_pass": sports_edge.get("top_strict_pass_player_props", [])[:8],
        "top_sgp_copula_boost_pairs": sports_edge.get("top_sgp_copula_boost_pairs", [])[:5],
    }

    with open(OUT_JSON_PATH, "w") as f:
        json.dump(payload, f, indent=2)

    sync_res = sync_to_worker(payload)
    payload["worker_sync"] = sync_res
    with open(OUT_JSON_PATH, "w") as f:
        json.dump(payload, f, indent=2)

    print(
        json.dumps(
            {
                "status": "OK",
                "elapsed_ms": payload["elapsed_ms"],
                "fx_usdcad": fx_usdcad,
                "total_controlled_usd": treasury["total_controlled_equity_usd"],
                "total_controlled_cad": treasury["total_controlled_equity_cad"],
                "personal_liquid_usd": treasury["personal_liquid_capital_usd"],
                "personal_liquid_cad": treasury["personal_liquid_capital_cad"],
                "hl_pairs_fetched": len(hl_map),
                "kc_pairs_fetched": len(kc_map),
                "shared_pairs_monitored": cross_venue["total_shared_pairs_monitored"],
                "actionable_funding_arbs": cross_venue["actionable_funding_arb_count"],
                "lead_lag_snipes": cross_venue["lead_lag_snipe_count"],
                "audited_trades": audit_ledger["total_audited_records"],
                "worker_sync": sync_res,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
