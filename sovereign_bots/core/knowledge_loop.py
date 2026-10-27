"""Continuous Self-Improving Knowledge Loop for Per-Coin & Per-Category Bots.

Maintains a durable, deterministic Bayesian / Multiplicative-Weights-Update (MWU)
knowledge store across:
  1. 16 Per-Coin Specialist Bots (KuCoin Spot, Margin, Coin-M, USDT-M + Hyperliquid Perps & HIP-2 Spot)
  2. 10 Per-Category Polymarket Specialist Bots (Crypto, Politics, Macro/Fed, Geopolitics,
     Sports, AI/Tech, Earnings/IPO, Regulatory/Legal, Weather/Science, Culture/Entertainment)

Every scan, fill, or resolved contract updates rolling Brier calibration, Closing Line
Value (CLV), realized R-expectancy, and optimal venue routing weights.
"""

from __future__ import annotations

import json
import math
import os
import time
from typing import Any, Dict, List, Optional

DEFAULT_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "learning",
    "knowledge_loop_state.json",
)

COIN_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "BTC":  {"cluster": "btc_eth", "min_vol_pct": 0.8, "max_vol_pct": 4.8, "max_spread_bps": 4.5, "funding_crowd_bps": 12.0, "preferred_venues": ["kucoin_usdtm_perp", "hyperliquid_propr_perp", "kucoin_coinm_inverse"]},
    "ETH":  {"cluster": "btc_eth", "min_vol_pct": 1.0, "max_vol_pct": 5.5, "max_spread_bps": 5.0, "funding_crowd_bps": 12.0, "preferred_venues": ["kucoin_usdtm_perp", "hyperliquid_propr_perp", "kucoin_coinm_inverse"]},
    "SOL":  {"cluster": "sol_l1",  "min_vol_pct": 1.4, "max_vol_pct": 7.5, "max_spread_bps": 6.5, "funding_crowd_bps": 15.0, "preferred_venues": ["kucoin_spot_oco", "kucoin_usdtm_perp", "kucoin_coinm_inverse", "hyperliquid_propr_perp"]},
    "HYPE": {"cluster": "hl_eco",  "min_vol_pct": 2.0, "max_vol_pct": 9.5, "max_spread_bps": 8.0, "funding_crowd_bps": 20.0, "preferred_venues": ["hyperliquid_propr_perp", "hyperliquid_hip2_spot", "kucoin_usdtm_perp"]},
    "XMR":  {"cluster": "privacy", "min_vol_pct": 1.2, "max_vol_pct": 7.0, "max_spread_bps": 8.5, "funding_crowd_bps": 18.0, "preferred_venues": ["kucoin_usdtm_perp", "hyperliquid_propr_perp", "hyperliquid_funding_carry"]},
    "ZEC":  {"cluster": "privacy", "min_vol_pct": 1.8, "max_vol_pct": 9.0, "max_spread_bps": 9.0, "funding_crowd_bps": 18.0, "preferred_venues": ["hyperliquid_propr_perp", "kucoin_usdtm_perp"]},
    "XRP":  {"cluster": "payments","min_vol_pct": 1.2, "max_vol_pct": 6.8, "max_spread_bps": 6.0, "funding_crowd_bps": 14.0, "preferred_venues": ["kucoin_usdtm_perp", "kucoin_coinm_inverse", "hyperliquid_propr_perp", "kucoin_spot_oco"]},
    "DOGE": {"cluster": "memes",   "min_vol_pct": 1.5, "max_vol_pct": 8.0, "max_spread_bps": 7.0, "funding_crowd_bps": 16.0, "preferred_venues": ["kucoin_usdtm_perp", "hyperliquid_propr_perp", "kucoin_spot_oco"]},
    "SUI":  {"cluster": "sol_l1",  "min_vol_pct": 1.8, "max_vol_pct": 8.5, "max_spread_bps": 7.5, "funding_crowd_bps": 16.0, "preferred_venues": ["kucoin_usdtm_perp", "hyperliquid_propr_perp", "kucoin_spot_oco"]},
    "NEAR": {"cluster": "ai_l1",   "min_vol_pct": 1.6, "max_vol_pct": 8.2, "max_spread_bps": 7.5, "funding_crowd_bps": 16.0, "preferred_venues": ["hyperliquid_propr_perp", "kucoin_usdtm_perp"]},
    "AVAX": {"cluster": "alt_l1",  "min_vol_pct": 1.4, "max_vol_pct": 7.2, "max_spread_bps": 7.0, "funding_crowd_bps": 15.0, "preferred_venues": ["kucoin_usdtm_perp", "hyperliquid_propr_perp", "kucoin_spot_oco"]},
    "LINK": {"cluster": "defi",    "min_vol_pct": 1.3, "max_vol_pct": 7.0, "max_spread_bps": 6.5, "funding_crowd_bps": 15.0, "preferred_venues": ["kucoin_usdtm_perp", "hyperliquid_propr_perp", "kucoin_spot_oco"]},
    "BNB":  {"cluster": "exch_l1", "min_vol_pct": 0.8, "max_vol_pct": 4.5, "max_spread_bps": 5.5, "funding_crowd_bps": 12.0, "preferred_venues": ["kucoin_usdtm_perp", "hyperliquid_propr_perp"]},
    "AAVE": {"cluster": "defi",    "min_vol_pct": 1.4, "max_vol_pct": 7.5, "max_spread_bps": 7.5, "funding_crowd_bps": 15.0, "preferred_venues": ["kucoin_usdtm_perp", "hyperliquid_propr_perp", "kucoin_margin_cross"]},
    "ENA":  {"cluster": "defi",    "min_vol_pct": 2.0, "max_vol_pct": 9.5, "max_spread_bps": 8.5, "funding_crowd_bps": 20.0, "preferred_venues": ["hyperliquid_propr_perp", "kucoin_usdtm_perp", "kucoin_funding_carry"]},
    "PEPE": {"cluster": "memes",   "min_vol_pct": 2.2, "max_vol_pct": 10.5,"max_spread_bps": 9.5, "funding_crowd_bps": 22.0, "preferred_venues": ["hyperliquid_propr_perp", "kucoin_usdtm_perp"]},
}

POLY_CATEGORY_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "crypto":        {"min_edge": 0.035, "base_rate_prior": 0.46, "brier_ema": 0.165, "clv_ema_cents": 1.8, "resolved_n": 24},
    "politics":      {"min_edge": 0.040, "base_rate_prior": 0.48, "brier_ema": 0.178, "clv_ema_cents": 1.5, "resolved_n": 30},
    "macro":         {"min_edge": 0.030, "base_rate_prior": 0.50, "brier_ema": 0.152, "clv_ema_cents": 2.1, "resolved_n": 28},
    "geopolitics":   {"min_edge": 0.045, "base_rate_prior": 0.28, "brier_ema": 0.170, "clv_ema_cents": 2.4, "resolved_n": 22},
    "sports":        {"min_edge": 0.030, "base_rate_prior": 0.50, "brier_ema": 0.160, "clv_ema_cents": 1.9, "resolved_n": 35},
    "ai_tech":       {"min_edge": 0.040, "base_rate_prior": 0.38, "brier_ema": 0.168, "clv_ema_cents": 2.0, "resolved_n": 18},
    "earnings_ipo":  {"min_edge": 0.038, "base_rate_prior": 0.54, "brier_ema": 0.162, "clv_ema_cents": 1.7, "resolved_n": 20},
    "regulatory":    {"min_edge": 0.042, "base_rate_prior": 0.34, "brier_ema": 0.172, "clv_ema_cents": 2.2, "resolved_n": 16},
    "weather":       {"min_edge": 0.035, "base_rate_prior": 0.42, "brier_ema": 0.158, "clv_ema_cents": 2.3, "resolved_n": 19},
    "entertainment": {"min_edge": 0.040, "base_rate_prior": 0.45, "brier_ema": 0.175, "clv_ema_cents": 1.6, "resolved_n": 15},
}


def default_state() -> Dict[str, Any]:
    coins = {}
    for sym, cfg in COIN_DEFAULTS.items():
        coins[sym] = {
            **cfg,
            "trades_n": 12,
            "win_rate": 0.58,
            "ema_realized_r": 0.42,
            "ema_clv_bps": 6.5,
            "brier_ema": 0.175,
            "mwu_weight": 1.0,
        }
    return {
        "version": "2.0",
        "updated_at": "2026-10-27T21:58:00Z",
        "coins": coins,
        "polymarket_categories": {k: dict(v, mwu_weight=1.0) for k, v in POLY_CATEGORY_DEFAULTS.items()},
    }


def load_knowledge_state(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or DEFAULT_STORE_PATH
    if os.path.exists(p):
        try:
            with open(p, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict) and "coins" in data and "polymarket_categories" in data:
                return data
        except Exception:
            pass
    return default_state()


def save_knowledge_state(state: Dict[str, Any], path: Optional[str] = None) -> str:
    p = path or DEFAULT_STORE_PATH
    os.makedirs(os.path.dirname(p), exist_ok=True)
    state["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    with open(p, "w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
    return p


def record_coin_outcome(
    coin: str,
    venue: str,
    realized_r: float,
    clv_bps: float = 0.0,
    p_pred: float = 0.55,
    win: Optional[bool] = None,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """Update per-coin Bayesian / MWU knowledge state from a completed trade or shadow scan."""
    state = load_knowledge_state(path)
    c_key = str(coin or "").upper().replace("USDTM", "").replace("USDM", "").replace("-USDT", "")
    if c_key == "XBT":
        c_key = "BTC"
    entry = state["coins"].setdefault(c_key, dict(COIN_DEFAULTS.get("BTC", {}), trades_n=0, win_rate=0.5, ema_realized_r=0.0, ema_clv_bps=0.0, brier_ema=0.20, mwu_weight=1.0))
    alpha = 0.15
    is_win = (realized_r > 0) if win is None else bool(win)
    outcome = 1.0 if is_win else 0.0
    brier = (float(p_pred) - outcome) ** 2

    entry["trades_n"] = int(entry.get("trades_n", 0)) + 1
    entry["win_rate"] = round((1.0 - alpha) * float(entry.get("win_rate", 0.5)) + alpha * outcome, 4)
    entry["ema_realized_r"] = round((1.0 - alpha) * float(entry.get("ema_realized_r", 0.0)) + alpha * float(realized_r), 4)
    entry["ema_clv_bps"] = round((1.0 - alpha) * float(entry.get("ema_clv_bps", 0.0)) + alpha * float(clv_bps), 4)
    entry["brier_ema"] = round((1.0 - alpha) * float(entry.get("brier_ema", 0.20)) + alpha * brier, 4)

    # Multiplicative Weights Update (eta = 0.08)
    eta = 0.08
    w = float(entry.get("mwu_weight", 1.0)) * math.exp(eta * max(-1.5, min(1.5, float(realized_r))))
    entry["mwu_weight"] = round(max(0.40, min(2.00, w)), 4)
    if venue and is_win and realized_r >= 0.5:
        venues = list(entry.get("preferred_venues") or [])
        if venue in venues:
            venues.remove(venue)
        venues.insert(0, venue)
        entry["preferred_venues"] = venues[:4]

    save_knowledge_state(state, path)
    return entry


def record_polymarket_outcome(
    category: str,
    p_model: float,
    p_market: float,
    outcome: int,
    clv_cents: float = 0.0,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """Update per-category Polymarket calibration and MWU weight after contract resolution or CLV mark."""
    state = load_knowledge_state(path)
    cat = str(category or "other").lower().strip()
    entry = state["polymarket_categories"].setdefault(
        cat,
        {"min_edge": 0.040, "base_rate_prior": 0.45, "brier_ema": 0.180, "clv_ema_cents": 1.0, "resolved_n": 0, "mwu_weight": 1.0},
    )
    alpha = 0.15
    y = 1.0 if int(outcome) == 1 else 0.0
    brier_m = (float(p_model) - y) ** 2
    brier_mkt = (float(p_market) - y) ** 2
    skill = brier_mkt - brier_m  # positive means model beat market

    entry["resolved_n"] = int(entry.get("resolved_n", 0)) + 1
    entry["brier_ema"] = round((1.0 - alpha) * float(entry.get("brier_ema", 0.18)) + alpha * brier_m, 4)
    entry["clv_ema_cents"] = round((1.0 - alpha) * float(entry.get("clv_ema_cents", 0.0)) + alpha * float(clv_cents), 4)
    entry["base_rate_prior"] = round((1.0 - 0.05) * float(entry.get("base_rate_prior", 0.45)) + 0.05 * y, 4)

    w = float(entry.get("mwu_weight", 1.0)) * math.exp(0.25 * max(-0.5, min(0.5, skill)))
    entry["mwu_weight"] = round(max(0.50, min(1.80, w)), 4)
    # Tighten or relax min_edge hurdle inversely to skill
    if entry["brier_ema"] > 0.21:
        entry["min_edge"] = round(min(0.065, float(entry.get("min_edge", 0.04)) + 0.002), 4)
    elif entry["brier_ema"] < 0.165:
        entry["min_edge"] = round(max(0.025, float(entry.get("min_edge", 0.04)) - 0.001), 4)

    save_knowledge_state(state, path)
    return entry
