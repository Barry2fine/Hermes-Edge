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
import threading
from contextlib import contextmanager
from .state_io import atomic_json, state_lock
from typing import Any, Dict, List, Optional

DEFAULT_STORE_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "learning",
    "knowledge_loop_state.json",
)
_transactions = threading.local()

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
        "sports_markets": {k: dict(v) for k, v in SPORTS_MARKET_DEFAULTS.items()},
    }


def load_knowledge_state(path: Optional[str] = None) -> Dict[str, Any]:
    p = path or DEFAULT_STORE_PATH
    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f, parse_constant=lambda value: (_ for _ in ()).throw(ValueError("nonfinite state")))
    except FileNotFoundError:
        return default_state()
    if not isinstance(data, dict) or any(not isinstance(data.get(key), dict)
                                        for key in ("coins", "polymarket_categories")):
        raise ValueError("invalid knowledge state schema; restore a verified backup")
    return data


def save_knowledge_state(state: Dict[str, Any], path: Optional[str] = None) -> str:
    p = path or DEFAULT_STORE_PATH
    with state_lock(p):
        saved = dict(state, updated_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()))
        atomic_json(p, saved)
    return p


@contextmanager
def knowledge_transaction(path=None):
    """Commit one snapshot; reject nesting that would overwrite an inner update."""
    p = path or DEFAULT_STORE_PATH
    # PID separates a forked child's transaction ownership from its parent's.
    key = (os.getpid(), os.path.normcase(os.path.realpath(p)))
    active = getattr(_transactions, "active", set())
    _transactions.active = active
    if key in active:
        raise RuntimeError("nested knowledge transaction for the same state is unsupported")
    with state_lock(p):
        active.add(key)
        try:
            state = load_knowledge_state(p)
            yield state
            if os.getpid() != key[0]:
                raise RuntimeError("cannot commit an inherited knowledge transaction after fork")
            save_knowledge_state(state, p)
        finally:
            active.remove(key)


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
    with knowledge_transaction(path) as state:
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
    with knowledge_transaction(path) as state:
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

        return entry


SPORTS_MARKET_DEFAULTS: Dict[str, Dict[str, Any]] = {
    "nba_player_assists_rebounds_alt": {"min_ev_pct": 2.5, "win_rate": 0.72, "brier_ema": 0.145, "clv_ema_bps": 18.0, "settled_n": 18, "mwu_weight": 1.15},
    "nba_player_points_milestone":     {"min_ev_pct": 3.5, "win_rate": 0.61, "brier_ema": 0.168, "clv_ema_bps": 14.0, "settled_n": 14, "mwu_weight": 1.05},
    "nba_ml_early_payout":             {"min_ev_pct": 2.0, "win_rate": 0.64, "brier_ema": 0.162, "clv_ema_bps": 11.0, "settled_n": 20, "mwu_weight": 1.02},
    "nhl_ml_early_payout":             {"min_ev_pct": 3.2, "win_rate": 0.56, "brier_ema": 0.184, "clv_ema_bps": 9.5,  "settled_n": 16, "mwu_weight": 0.94},
    "nhl_puckline_plus_1_5":           {"min_ev_pct": 2.2, "win_rate": 0.71, "brier_ema": 0.151, "clv_ema_bps": 15.5, "settled_n": 19, "mwu_weight": 1.12},
    "bonus_bet_sgp_plus_conversion":   {"min_ev_pct": 4.0, "win_rate": 0.45, "brier_ema": 0.158, "clv_ema_bps": 24.0, "settled_n": 8,  "mwu_weight": 1.22},
}


def record_sports_outcome(
    market_family: str,
    p_model: float,
    outcome: int,
    realized_roi_mult: float = 0.0,
    clv_bps: float = 0.0,
    early_cashout_used: bool = False,
    path: Optional[str] = None,
) -> Dict[str, Any]:
    """Update per-market-family sports betting calibration, CLV, and MWU weight."""
    with knowledge_transaction(path) as state:
        return apply_sports_outcome(state, market_family, p_model, outcome,
                                    realized_roi_mult, clv_bps, early_cashout_used)


def apply_sports_outcome(state, market_family, p_model, outcome,
                         realized_roi_mult=0.0, clv_bps=0.0, early_cashout_used=False):
    """Apply one validated observation inside the caller's state transaction."""
    if isinstance(p_model, bool) or not math.isfinite(float(p_model)) or not 0 <= float(p_model) <= 1:
        raise ValueError("invalid model probability")
    if outcome not in (0, 1) or not isinstance(outcome, (int, bool)):
        raise ValueError("outcome must be binary")
    if any(isinstance(v, bool) or not math.isfinite(float(v)) for v in (realized_roi_mult, clv_bps)):
        raise ValueError("invalid realized evidence")
    sports_map = state.setdefault(
        "sports_markets",
        {k: dict(v) for k, v in SPORTS_MARKET_DEFAULTS.items()},
    )
    key = str(market_family or "nba_player_assists_rebounds_alt").lower().strip()
    entry = sports_map.setdefault(
        key,
        {"min_ev_pct": 3.0, "win_rate": 0.55, "brier_ema": 0.175, "clv_ema_bps": 10.0, "settled_n": 0, "mwu_weight": 1.0},
    )
    alpha = 0.15
    y = 1.0 if int(outcome) == 1 else 0.0
    brier = (float(p_model) - y) ** 2
    entry["settled_n"] = int(entry.get("settled_n", 0)) + 1
    entry["win_rate"] = round((1.0 - alpha) * float(entry.get("win_rate", 0.55)) + alpha * y, 4)
    entry["brier_ema"] = round((1.0 - alpha) * float(entry.get("brier_ema", 0.175)) + alpha * brier, 4)
    entry["clv_ema_bps"] = round((1.0 - alpha) * float(entry.get("clv_ema_bps", 0.0)) + alpha * float(clv_bps), 4)
    if early_cashout_used:
        entry["early_cashout_saves"] = int(entry.get("early_cashout_saves", 0)) + 1

    w = float(entry.get("mwu_weight", 1.0)) * math.exp(0.08 * max(-1.5, min(2.0, float(realized_roi_mult))))
    entry["mwu_weight"] = round(max(0.50, min(2.00, w)), 4)
    return entry
