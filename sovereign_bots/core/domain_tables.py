"""
Institutional Multi-Domain Tables & Deterministic Truth Tables (`domain_tables.py`).

Implements the `typesafe-ai` System One architecture across all three desks:
  1. Sports Betting (`bet365 Ontario` / Sharp De-Vig / SGP+ / Live Cash-Out Truth Table)
  2. Polymarket & Kalshi (`10` Canonical Categories / `518` Tags / Oracle & Freshness Truth Table)
  3. Crypto Trading (`KuCoin` Futures+Spot & `Propr` Hyperliquid / Flow & Regime Truth Table)
  4. Cross-Domain Spillover Matrix (routes catalysts from one market into lagging markets)
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple

# ---------------------------------------------------------------------------
# 1. SPORTS BETTING DOMAIN TABLE (bet365 Ontario / Sharp De-Vig / SGP+)
# ---------------------------------------------------------------------------
SPORTS_DOMAIN_TABLE: Dict[str, Dict[str, Any]] = {
    "nba_player_assists_rebounds_alt": {
        "sport": "NBA",
        "market_family": "player_prop_floor",
        "min_ev_pct": 2.5,
        "max_vig_pct": 6.5,
        "variance_tier": "LOW",
        "sgp_anchor_eligible": True,
        "bonus_bet_preferred": True,
        "blowout_sensitivity": 0.45,
        "notes": "High-floor stat categories (e.g. Tatum 5+ AST, KAT 10+ REB, Cunningham 7+ AST) with stable role usage.",
    },
    "nba_player_points_milestone": {
        "sport": "NBA",
        "market_family": "player_prop_scoring",
        "min_ev_pct": 3.5,
        "max_vig_pct": 6.5,
        "variance_tier": "MEDIUM_HIGH",
        "sgp_anchor_eligible": True,
        "bonus_bet_preferred": True,
        "blowout_sensitivity": 0.80,
        "early_cashout_q1_pace_ratio": 0.50,
        "notes": "High-ceiling scoring legs (e.g. Luka 30+ PTS). Ideal for Q1 pace burst + early cashout if blowout/sub risk rises.",
    },
    "nba_ml_early_payout": {
        "sport": "NBA",
        "market_family": "moneyline_early_payout",
        "min_ev_pct": 2.0,
        "max_vig_pct": 4.8,
        "variance_tier": "MEDIUM",
        "sgp_anchor_eligible": True,
        "bonus_bet_preferred": False,
        "early_payout_trigger": "20_point_lead_any_quarter",
        "notes": "bet365 Early Payout adds ~1.8% to 2.6% hidden EV when backing high-pace favorites or high-variance 3PT teams.",
    },
    "nhl_ml_early_payout": {
        "sport": "NHL",
        "market_family": "moneyline_early_payout",
        "min_ev_pct": 3.2,
        "max_vig_pct": 4.8,
        "variance_tier": "HIGH",
        "sgp_anchor_eligible": False,
        "bonus_bet_preferred": False,
        "early_payout_trigger": "3_goal_lead_any_period",
        "require_confirmed_starter_goalie": True,
        "notes": "3-goal Early Payout rescues overtime/shootout losses (like CLB vs PHI), but raw NHL ML parlays carry high OT/SO variance.",
    },
    "nhl_puckline_plus_1_5": {
        "sport": "NHL",
        "market_family": "spread_cushion",
        "min_ev_pct": 2.2,
        "max_vig_pct": 5.0,
        "variance_tier": "LOW",
        "sgp_anchor_eligible": True,
        "bonus_bet_preferred": False,
        "require_confirmed_starter_goalie": True,
        "notes": "Dog +1.5 puckline survives 1-goal regulation and all OT/SO losses; superior parlay anchor vs raw dog ML.",
    },
    "bonus_bet_sgp_plus_conversion": {
        "sport": "MULTI",
        "market_family": "bonus_bet_snr",
        "target_combined_decimal_min": 4.00,
        "target_combined_decimal_max": 7.50,
        "optimal_leg_count": (4, 5),
        "stake_returned_on_win": False,
        "notes": "Bonus Bets do not return stake; +300 to +650 SGP+ built from low-variance alt-rebounds/assists maximizes conversion SNR.",
    },
}

# Star absence usage/rebound/assist redistribution coefficients
STAR_ABSENCE_SPILLOVER_TABLE: Dict[str, Dict[str, Any]] = {
    "primary_ballhandler_out": {
        "secondary_guard_ast_boost_pct": 24.0,
        "secondary_wing_pts_boost_pct": 16.5,
        "team_pace_delta": -1.2,
        "book_adjustment_lag_minutes": 90,
        "target_props": ["secondary_guard_assists_over", "secondary_wing_points_over", "big_rebounds_over"],
    },
    "starting_center_out": {
        "power_forward_reb_boost_pct": 28.0,
        "opponent_paint_pts_boost_pct": 14.0,
        "team_rebound_rate_delta": -3.8,
        "book_adjustment_lag_minutes": 120,
        "target_props": ["pf_rebounds_over", "opponent_center_rebounds_over", "opponent_center_points_over"],
    },
    "elite_edge_rusher_return": {
        "opponent_qb_sack_over_boost_pct": 22.0,
        "team_defensive_epa_boost": 0.08,
        "book_adjustment_lag_minutes": 360,
        "target_props": ["team_spread_favorite", "opponent_team_total_under", "qb_sacks_taken_over"],
    },
}


# ---------------------------------------------------------------------------
# 2. POLYMARKET & KALSHI EXPANDED DOMAIN TABLE
# ---------------------------------------------------------------------------
POLYMARKET_DOMAIN_TABLE: Dict[str, Dict[str, Any]] = {
    "crypto": {
        "min_edge_bps": 35.0,
        "max_spread_bps": 60.0,
        "min_depth_usd": 10000.0,
        "oracle_dispute_discount": 0.02,
        "kalshi_cross_arb": True,
        "five_min_barrier_enabled": True,
        "primary_driver": "spot_perp_basis_and_deribit_iv",
    },
    "politics": {
        "min_edge_bps": 40.0,
        "max_spread_bps": 50.0,
        "min_depth_usd": 25000.0,
        "oracle_dispute_discount": 0.03,
        "kalshi_cross_arb": True,
        "five_min_barrier_enabled": False,
        "primary_driver": "exit_polls_and_state_ballot_returns",
    },
    "macro": {
        "min_edge_bps": 30.0,
        "max_spread_bps": 45.0,
        "min_depth_usd": 20000.0,
        "oracle_dispute_discount": 0.01,
        "kalshi_cross_arb": True,
        "five_min_barrier_enabled": False,
        "primary_driver": "cme_fedwatch_and_bls_prints",
    },
    "geopolitics": {
        "min_edge_bps": 45.0,
        "max_spread_bps": 70.0,
        "min_depth_usd": 15000.0,
        "oracle_dispute_discount": 0.08,
        "kalshi_cross_arb": True,
        "five_min_barrier_enabled": False,
        "primary_driver": "official_state_wires_and_parliament_votes",
    },
    "sports": {
        "min_edge_bps": 30.0,
        "max_spread_bps": 55.0,
        "min_depth_usd": 12000.0,
        "oracle_dispute_discount": 0.01,
        "kalshi_cross_arb": True,
        "five_min_barrier_enabled": False,
        "primary_driver": "pinnacle_devig_and_espn_injury_wire",
    },
    "ai_tech": {
        "min_edge_bps": 40.0,
        "max_spread_bps": 65.0,
        "min_depth_usd": 8000.0,
        "oracle_dispute_discount": 0.05,
        "kalshi_cross_arb": True,
        "five_min_barrier_enabled": False,
        "primary_driver": "official_repo_releases_and_hardware_deals",
    },
}


# ---------------------------------------------------------------------------
# 3. CRYPTO TRADING EXPANDED DOMAIN TABLE (KuCoin & Propr)
# ---------------------------------------------------------------------------
CRYPTO_REGIME_DOMAIN_TABLE: Dict[str, Dict[str, Any]] = {
    "btc_eth_macro_core": {
        "assets": ["BTC", "ETH"],
        "etf_inflow_threshold_musd": 100.0,
        "cex_whale_inflow_veto_musd": 200.0,
        "max_spread_bps": 5.0,
        "fomc_pre_window_hours": 18.0,
        "stop_loss_atr_mult": 1.35,
        "take_profit_r_mult": 2.20,
    },
    "high_beta_l1_momentum": {
        "assets": ["SOL", "SUI", "AVAX", "NEAR", "HYPE"],
        "btc_trend_confirmation_required": True,
        "max_spread_bps": 7.5,
        "funding_crowding_cap_bps": 16.0,
        "stop_loss_atr_mult": 1.50,
        "take_profit_r_mult": 2.50,
    },
    "liquid_memes_and_retail_flow": {
        "assets": ["DOGE", "PEPE"],
        "btc_trend_confirmation_required": True,
        "max_spread_bps": 8.5,
        "funding_crowding_cap_bps": 18.0,
        "stop_loss_atr_mult": 1.60,
        "take_profit_r_mult": 2.40,
    },
}


# ---------------------------------------------------------------------------
# 4. CROSS-DOMAIN SPILLOVER MATRIX
# ---------------------------------------------------------------------------
CROSS_DOMAIN_SPILLOVER_TABLE: List[Dict[str, Any]] = [
    {
        "trigger_domain": "x_sports_injury_wire",
        "primary_target": "bet365_ontario_player_props",
        "secondary_target": "polymarket_nba_nhl_contracts",
        "lag_window_minutes": 45,
        "rule": "When a starter/star is ruled out on X (confirmed Noul >= 0.75), target secondary teammates' Rebound/Assist overs on bet365 and check Polymarket/Kalshi game winner CLOB before books finish repricing.",
    },
    {
        "trigger_domain": "x_whale_cex_inflow_or_etf_print",
        "primary_target": "kucoin_usdtm_perps",
        "secondary_target": "polymarket_5m_crypto_barriers",
        "lag_window_minutes": 15,
        "rule": "When CEX institutional inflow >= $200M or ETF net flow diverges (e.g. BTC +$110M vs ETH -$88M), favor relative-strength pair or veto unhedged longs into FOMC.",
    },
    {
        "trigger_domain": "x_geopolitical_or_exit_poll_shock",
        "primary_target": "polymarket_kalshi_cross_arb",
        "secondary_target": "crypto_btc_gold_macro",
        "lag_window_minutes": 20,
        "rule": "When official exit polls or state addresses break on X, compare Polymarket vs Kalshi implied probabilities for >300 bps cross-exchange divergence.",
    },
]


# ---------------------------------------------------------------------------
# 5. DETERMINISTIC TRUTH TABLES (Judgments from Jev -> Decisions in Code)
# ---------------------------------------------------------------------------
def evaluate_live_cashout_truth_table(
    *,
    total_legs: int,
    settled_legs_won: int,
    active_leg_progress_ratio: float,
    active_game_period: int,
    score_margin_abs: int,
    star_sub_or_injury_noul: float = 0.20,
    cashout_to_fair_ev_ratio: float = 0.85,
    is_bonus_bet: bool = True,
) -> Dict[str, Any]:
    """
    Deterministic Live Cash-Out Truth Table inspired by Barry's Oct 27 5-leg SGP+ win
    (4 legs settled green + Luka Doncic at 16/30 pts in Q1 -> early cashout for +$103.79).

    Inputs combine observable game state + TypeSafe Jev `star_sub_or_injury_noul`:
      - If all other legs are already won (`settled_legs_won >= total_legs - 1`)
      - And the final leg has surged early (`active_leg_progress_ratio >= 0.50` in Q1/Q2)
      - Or blowout/bench/injury tail risk is elevated (`score_margin_abs >= 16` or `star_sub_or_injury_noul >= 0.45`)
      - And the sportsbook cashout offer captures `>= 75%` of fair remaining equity (`>= 70%` on a Bonus Bet since stake isn't returned):
      -> Action is `LOCK_EARLY_CASHOUT`.
    """
    all_prior_legs_green = settled_legs_won >= max(1, total_legs - 1)
    early_pace_surge = active_leg_progress_ratio >= 0.50 and active_game_period <= 2
    blowout_or_sub_risk = score_margin_abs >= 16 or star_sub_or_injury_noul >= 0.45
    min_fair_capture = 0.70 if is_bonus_bet else 0.78
    offer_is_fair = cashout_to_fair_ev_ratio >= min_fair_capture

    if all_prior_legs_green and offer_is_fair and (early_pace_surge or blowout_or_sub_risk):
        return {
            "action": "LOCK_EARLY_CASHOUT",
            "reason": (
                f"Prior {settled_legs_won}/{total_legs} legs green, active leg at "
                f"{active_leg_progress_ratio*100:.0f}% target (period {active_game_period}), "
                f"sub/blowout risk={star_sub_or_injury_noul:.2f}, offer captures {cashout_to_fair_ev_ratio*100:.0f}% fair equity."
            ),
        }
    if star_sub_or_injury_noul >= 0.70 and cashout_to_fair_ev_ratio >= 0.55:
        return {
            "action": "EMERGENCY_CASHOUT_PROTECTION",
            "reason": f"High injury/substitution Noul ({star_sub_or_injury_noul:.2f} >= 0.70); salvaging equity.",
        }
    return {
        "action": "HOLD_TICKET_TO_TARGET",
        "reason": "Remaining EV exceeds cash-out discount and blowout/injury Noul is below threshold.",
    }


def evaluate_multi_domain_signal_truth_table(
    *,
    concrete_noul: float,
    impact_noul: float,
    already_priced_in_noul: float,
    ambiguity_or_rumor_noul: float,
    primary_desk: str,
    directional_lean: str,
) -> Dict[str, Any]:
    """
    Deterministic 4-Noul Truth Table mapping TypeSafe Jev-1.14 probabilities (`0.70` / `0.30` thresholds)
    into concrete desk execution actions and cross-domain spillover routes.
    """
    # Hard veto rows first
    if ambiguity_or_rumor_noul >= 0.70 or concrete_noul < 0.30:
        return {
            "tier": "VETO_UNVERIFIED_OR_RUMOR",
            "action": "ABSTAIN",
            "recommended_stake_mult": 0.0,
            "spillover_targets": [],
        }
    if already_priced_in_noul >= 0.75:
        return {
            "tier": "ABSTAIN_ALREADY_PRICED_IN",
            "action": "PASS_LATE_CHASE",
            "recommended_stake_mult": 0.0,
            "spillover_targets": [],
        }

    # Compute net unpriced catalyst score
    freshness = max(0.0, 1.0 - already_priced_in_noul)
    clarity = max(0.0, 1.0 - 0.5 * ambiguity_or_rumor_noul)
    net_edge_score = round(concrete_noul * 0.35 + impact_noul * 0.40 + freshness * 0.25, 3) * clarity
    net_edge_score = round(net_edge_score, 3)

    spillovers: List[str] = []
    if primary_desk == "sports_betting":
        spillovers = ["bet365_ontario_player_props", "polymarket_sports_clob"]
    elif primary_desk == "crypto_perp_spot":
        spillovers = ["kucoin_usdtm_perps", "polymarket_5m_crypto_barriers"]
    else:
        spillovers = ["polymarket_gamma_clob", "kalshi_cross_arb"]

    if (concrete_noul >= 0.70 or (impact_noul >= 0.75 and ambiguity_or_rumor_noul <= 0.25)) and impact_noul >= 0.65 and already_priced_in_noul <= 0.45:
        return {
            "tier": "TIER_1_SNIPE_ALPHA",
            "action": f"EXECUTE_{primary_desk.upper()}_{directional_lean.upper()}",
            "net_edge_score": net_edge_score,
            "recommended_stake_mult": 1.0,
            "spillover_targets": spillovers,
        }
    if concrete_noul >= 0.55 and impact_noul >= 0.45 and already_priced_in_noul <= 0.65:
        return {
            "tier": "TIER_2_WATCH_CATALYST",
            "action": f"QUEUE_LIMIT_OR_PROP_CHECK_{primary_desk.upper()}",
            "net_edge_score": net_edge_score,
            "recommended_stake_mult": 0.5,
            "spillover_targets": spillovers,
        }
    return {
        "tier": "NOISE_BELOW_HURDLE",
        "action": "ABSTAIN",
        "net_edge_score": net_edge_score,
        "recommended_stake_mult": 0.0,
        "spillover_targets": [],
    }
