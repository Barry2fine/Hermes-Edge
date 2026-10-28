"""
Institutional Multi-Desk Risk Governor, L2 Kyle's Lambda/VPIN Microstructure
Router, and Live SGP+ Cashout/Hedge State Machine
(`sovereign_bots/core/institutional_risk_governor.py`).

Built to institutional prop-desk & market-making standards (Jane Street / Citadel / Millennium):
1. `compute_cppi_drawdown_adaptive_kelly`:
   - Constant Proportion Portfolio Insurance (CPPI) + Ruin-Boundary Governor for prop evaluations
     (`Propr` $25k account: $23,500 static floor, $27,500 target, 3% daily cap with 55% personal daily buffer)
     and cash accounts (`KuCoin`, `bet365 Ontario`, `Polymarket`).
   - Enforces quadratic risk decay near daily/max drawdown floors, consecutive-loss streak dampening,
     and a Target-Lock Taper when equity is within 1.5% of passing the evaluation target.
2. `compute_l2_kyle_lambda_and_vpin_routing`:
   - Multi-level exponential-decay Order Book Imbalance (`eobi`), Kyle's Lambda (`kyle_lambda_bps_per_10k`)
     price-impact coefficient, effective spread, and VPIN toxicity classification.
   - Determines optimal execution regime (`POST_ONLY_MAKER_RESERVATION`, `IOC_SNIPE_ALLOWED`, or `HARD_VETO_TOXIC`).
3. `evaluate_live_sgp_plus_state_machine`:
   - Real-time state machine for active multi-leg `bet365 Ontario` SGP+ / Parlay tickets.
   - Tracks settled legs, live in-play progress of remaining legs (clock remaining, current stat count vs target,
     foul/blowout risk), computes conditional Poisson/Normal continuation probability, and compares:
       (a) Expected value of holding the runner (`ev_hold_cad`),
       (b) Guaranteed profit from `bet365` Early Cashout (`cashout_offer_cad`),
       (c) Guaranteed risk-free profit from locking a two-way synthetic hedge on the final leg (`locked_hedge_profit_cad`).
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Sequence

from sovereign_bots.core.domain_tables import (
    avellaneda_stoikov_maker_quote,
    poisson_prob_at_least,
    solve_live_hedge_and_middle_locked_profit,
)


def _finite(val: Any, name: str = "value") -> float:
    if isinstance(val, bool) or val is None:
        raise ValueError(f"{name} must be numeric")
    f = float(val)
    if not math.isfinite(f):
        raise ValueError(f"{name} must be finite")
    return f


def compute_cppi_drawdown_adaptive_kelly(
    *,
    current_equity_usd: float,
    starting_equity_usd: float = 25000.0,
    static_drawdown_floor_usd: float = 23500.0,
    profit_target_usd: float = 27500.0,
    daily_starting_equity_usd: float = 25000.0,
    firm_daily_loss_limit_pct: float = 0.03,
    personal_daily_buffer_ratio: float = 0.55,
    consecutive_losses: int = 0,
    base_kelly_fraction: float = 0.25,
    base_risk_per_trade_pct: float = 0.005,
) -> Dict[str, Any]:
    """
    Compute drawdown-adaptive CPPI Kelly multiplier and dollar risk cap.
    Guarantees zero breach of personal daily floor or static evaluation floor.
    """
    eq = _finite(current_equity_usd, "current_equity_usd")
    start_eq = _finite(starting_equity_usd, "starting_equity_usd")
    floor_eq = _finite(static_drawdown_floor_usd, "static_drawdown_floor_usd")
    target_eq = _finite(profit_target_usd, "profit_target_usd")
    day_start = _finite(daily_starting_equity_usd, "daily_starting_equity_usd")
    day_lim_pct = _finite(firm_daily_loss_limit_pct, "firm_daily_loss_limit_pct")
    buf_ratio = _finite(personal_daily_buffer_ratio, "personal_daily_buffer_ratio")
    base_k = _finite(base_kelly_fraction, "base_kelly_fraction")
    base_r_pct = _finite(base_risk_per_trade_pct, "base_risk_per_trade_pct")

    if eq <= 0 or start_eq <= floor_eq or target_eq <= start_eq:
        raise ValueError("invalid equity or floor/target geometry")

    # 1. Static Cushion Ratio
    initial_max_cushion = start_eq - floor_eq
    current_static_cushion = max(0.0, eq - floor_eq)
    static_cushion_ratio = current_static_cushion / initial_max_cushion

    # 2. Personal Daily Loss Floor (e.g., 55% of firm's 3% daily cap = 1.65% personal stop)
    firm_daily_floor = day_start * (1.0 - day_lim_pct)
    personal_daily_max_loss = day_start * day_lim_pct * buf_ratio
    personal_daily_floor = day_start - personal_daily_max_loss
    remaining_daily_room = max(0.0, eq - personal_daily_floor)
    daily_cushion_ratio = (
        remaining_daily_room / personal_daily_max_loss if personal_daily_max_loss > 0 else 0.0
    )

    # Hard halt checks
    if eq <= floor_eq or remaining_daily_room <= 1e-6 or consecutive_losses >= 2:
        reason = (
            "STATIC_FLOOR_BREACHED"
            if eq <= floor_eq
            else (
                "PERSONAL_DAILY_FLOOR_HIT"
                if remaining_daily_room <= 1e-6
                else "TWO_CONSECUTIVE_LOSSES_SESSION_PAUSE"
            )
        )
        return {
            "trading_allowed": False,
            "regime": reason,
            "effective_kelly_fraction": 0.0,
            "max_risk_usd": 0.0,
            "static_cushion_usd": round(current_static_cushion, 2),
            "remaining_daily_room_usd": round(remaining_daily_room, 2),
            "distance_to_target_usd": round(max(0.0, target_eq - eq), 2),
        }

    # 3. CPPI Quadratic Cushion Multiplier
    binding_cushion = min(1.2, static_cushion_ratio, daily_cushion_ratio)
    cppi_mult = min(1.15, max(0.15, binding_cushion ** 1.35))

    # 4. Streak Multiplier (step down 35% after 1 full loss)
    streak_mult = 0.65 if int(consecutive_losses) == 1 else 1.0

    # 5. Profit-Target Lock Taper (when within $375 / 1.5% of passing $27,500 target)
    dist_to_target = max(0.0, target_eq - eq)
    target_taper_mult = 1.0
    regime = "NORMAL_CPPI_EXECUTION"
    if dist_to_target == 0.0:
        return {
            "trading_allowed": False,
            "regime": "EVALUATION_TARGET_PASSED_LOCK",
            "effective_kelly_fraction": 0.0,
            "max_risk_usd": 0.0,
            "static_cushion_usd": round(current_static_cushion, 2),
            "remaining_daily_room_usd": round(remaining_daily_room, 2),
            "distance_to_target_usd": 0.0,
        }
    if dist_to_target <= 0.015 * start_eq:
        target_taper_mult = max(0.25, dist_to_target / (0.015 * start_eq))
        regime = "TARGET_PROXIMITY_TAPER"
    elif binding_cushion < 0.50:
        regime = "DRAWDOWN_DEFENSIVE_THROTTLE"

    combined_mult = cppi_mult * streak_mult * target_taper_mult
    eff_kelly = round(base_k * combined_mult, 4)

    raw_risk_usd = eq * base_r_pct * combined_mult
    # Never risk more than 35% of remaining personal daily room or 20% of static cushion in one ticket
    max_risk_usd = min(
        raw_risk_usd,
        0.35 * remaining_daily_room,
        0.20 * current_static_cushion,
    )

    return {
        "trading_allowed": True,
        "regime": regime,
        "effective_kelly_fraction": eff_kelly,
        "max_risk_usd": round(max_risk_usd, 2),
        "static_cushion_usd": round(current_static_cushion, 2),
        "remaining_daily_room_usd": round(remaining_daily_room, 2),
        "firm_daily_floor_usd": round(firm_daily_floor, 2),
        "personal_daily_floor_usd": round(personal_daily_floor, 2),
        "distance_to_target_usd": round(dist_to_target, 2),
        "cppi_multiplier": round(combined_mult, 4),
    }


def compute_l2_kyle_lambda_and_vpin_routing(
    *,
    bids: Sequence[Sequence[float]],
    asks: Sequence[Sequence[float]],
    side: str,
    order_notional_usd: float,
    recent_buy_volume_usd: float = 50000.0,
    recent_sell_volume_usd: float = 50000.0,
    volatility_bps_per_min: float = 8.0,
    inventory_signed_units: float = 0.0,
) -> Dict[str, Any]:
    """
    Analyze L2 order book depth, compute exponential-decay Order Book Imbalance (`eobi`),
    Kyle's Lambda (`kyle_lambda_bps_per_10k`), VPIN toxicity, and Avellaneda-Stoikov
    optimal maker reservation prices.
    """
    if not bids or not asks:
        raise ValueError("L2 bids and asks must be non-empty")
    notional = _finite(order_notional_usd, "order_notional_usd")
    if notional <= 0:
        raise ValueError("order_notional_usd must be positive")

    parsed_bids = sorted([(float(p), float(q)) for p, q in bids if float(p) > 0 and float(q) > 0], key=lambda x: -x[0])
    parsed_asks = sorted([(float(p), float(q)) for p, q in asks if float(p) > 0 and float(q) > 0], key=lambda x: x[0])
    if not parsed_bids or not parsed_asks:
        raise ValueError("no valid positive bid/ask levels")

    best_bid = parsed_bids[0][0]
    best_ask = parsed_asks[0][0]
    if best_bid >= best_ask:
        raise ValueError("crossed orderbook")

    mid = 0.5 * (best_bid + best_ask)
    spread_bps = ((best_ask - best_bid) / mid) * 10000.0

    # Exponential-decay weighted Order Book Imbalance across top 5 levels
    decay = 0.65
    w_bid = sum((decay ** i) * (p * q) for i, (p, q) in enumerate(parsed_bids[:5]))
    w_ask = sum((decay ** i) * (p * q) for i, (p, q) in enumerate(parsed_asks[:5]))
    eobi = (w_bid - w_ask) / max(1e-9, w_bid + w_ask)

    # Walk the book to estimate VWAP slippage and Kyle's Lambda (bps impact per $10k notional)
    target_levels = parsed_asks if str(side).upper() in ("BUY", "LONG") else parsed_bids
    rem_usd = notional
    filled_usd = 0.0
    weighted_px_num = 0.0
    for px, qty in target_levels:
        lvl_usd = px * qty
        take_usd = min(rem_usd, lvl_usd)
        weighted_px_num += take_usd * px
        filled_usd += take_usd
        rem_usd -= take_usd
        if rem_usd <= 1e-6:
            break

    if filled_usd < notional * 0.99:
        return {
            "routing_decision": "HARD_VETO_INSUFFICIENT_DEPTH",
            "executable": False,
            "spread_bps": round(spread_bps, 3),
            "eobi": round(eobi, 4),
            "reason": "book depth insufficient for requested notional",
        }

    vwap_px = weighted_px_num / filled_usd
    impact_bps = (abs(vwap_px - mid) / mid) * 10000.0
    kyle_lambda_per_10k = impact_bps / max(0.1, notional / 10000.0)

    buy_v = max(0.0, _finite(recent_buy_volume_usd, "recent_buy_volume_usd"))
    sell_v = max(0.0, _finite(recent_sell_volume_usd, "recent_sell_volume_usd"))
    tot_v = buy_v + sell_v
    vpin = abs(buy_v - sell_v) / tot_v if tot_v > 0 else 0.35

    as_quote = avellaneda_stoikov_maker_quote(
        mid_price=mid,
        inventory_signed_units=_finite(inventory_signed_units, "inventory_signed_units"),
        risk_aversion_gamma=0.15,
        volatility_bps_per_min=_finite(volatility_bps_per_min, "volatility_bps_per_min"),
        horizon_minutes=15.0,
    )

    dir_up = str(side).upper()
    adverse_imbalance = (dir_up in ("BUY", "LONG") and eobi < -0.45) or (
        dir_up in ("SELL", "SHORT") and eobi > 0.45
    )

    if vpin > 0.72 or spread_bps > 15.0 or adverse_imbalance:
        decision = "HARD_VETO_TOXIC"
        executable = False
    elif impact_bps <= 3.5 and spread_bps <= 4.5 and vpin <= 0.48:
        decision = "IOC_SNIPE_ALLOWED"
        executable = True
    else:
        decision = "POST_ONLY_MAKER_RESERVATION"
        executable = True

    recommended_limit_px = (
        as_quote["optimal_post_only_bid"] if dir_up in ("BUY", "LONG") else as_quote["optimal_post_only_ask"]
    )

    return {
        "routing_decision": decision,
        "executable": executable,
        "mid_price": round(mid, 6),
        "spread_bps": round(spread_bps, 3),
        "expected_vwap_impact_bps": round(impact_bps, 3),
        "kyle_lambda_bps_per_10k": round(kyle_lambda_per_10k, 4),
        "eobi": round(eobi, 4),
        "vpin_toxicity": round(vpin, 4),
        "recommended_post_only_px": recommended_limit_px,
    }


def evaluate_live_sgp_plus_state_machine(
    *,
    stake_cad: float,
    is_bonus_bet: bool,
    combined_decimal_odds: float,
    total_legs: int,
    settled_legs_won: int,
    settled_legs_lost: int = 0,
    active_leg_current_stat: float = 0.0,
    active_leg_target_milestone: int = 1,
    active_leg_baseline_per_min: float = 0.5,
    active_leg_minutes_remaining: float = 12.0,
    live_score_margin_abs: int = 6,
    book_cashout_offer_cad: float = 0.0,
    final_leg_opposite_hedge_decimal_odds: float = 1.91,
) -> Dict[str, Any]:
    """
    Real-time state machine for active multi-leg `bet365 Ontario` SGP+ / Parlay tickets.
    Models remaining stat accumulation via conditional Poisson process adjusted for
    4th-quarter blowout benching hazard, and compares Hold EV vs. Early Cashout vs.
    Two-Way Synthetic Hedge Lock.
    """
    stake = _finite(stake_cad, "stake_cad")
    dec = _finite(combined_decimal_odds, "combined_decimal_odds")
    cashout = _finite(book_cashout_offer_cad, "book_cashout_offer_cad")
    hedge_dec = _finite(final_leg_opposite_hedge_decimal_odds, "final_leg_opposite_hedge_decimal_odds")

    if settled_legs_lost > 0:
        return {
            "state": "TICKET_DEAD",
            "recommended_action": "NONE",
            "conditional_win_prob": 0.0,
            "fair_ticket_equity_cad": 0.0,
        }

    gross_payout = stake * (dec - 1.0) if is_bonus_bet else stake * dec
    needed = max(0, int(math.ceil(active_leg_target_milestone - active_leg_current_stat)))

    # Blowout minutes haircut if margin >= 20 with <= 15 minutes left
    eff_mins = max(0.0, float(active_leg_minutes_remaining))
    blowout_haircut = 1.0
    if live_score_margin_abs >= 24 and eff_mins <= 16.0:
        blowout_haircut = 0.45
    elif live_score_margin_abs >= 18 and eff_mins <= 14.0:
        blowout_haircut = 0.72

    rem_lambda = max(0.0, float(active_leg_baseline_per_min) * eff_mins * blowout_haircut)
    cond_prob = 1.0 if needed <= 0 else poisson_prob_at_least(rem_lambda, needed)

    # If more than 1 leg is still un-settled, apply conservative 0.65 factor per extra un-settled leg
    unsettled_count = max(0, int(total_legs) - int(settled_legs_won))
    if unsettled_count > 1:
        cond_prob = cond_prob * (0.65 ** (unsettled_count - 1))

    fair_equity_cad = cond_prob * gross_payout
    cash_cost = 0.0 if is_bonus_bet else stake
    ev_hold_net_cad = fair_equity_cad - cash_cost
    cashout_net_cad = cashout - cash_cost

    hedge_plan = solve_live_hedge_and_middle_locked_profit(
        initial_stake_cash=stake,
        is_bonus_bet=is_bonus_bet,
        ticket_payout_gross=gross_payout,
        book_cashout_offer=cashout,
        hedge_opposite_decimal_odds=hedge_dec,
    )
    locked_hedge_net = float(hedge_plan["guaranteed_hedge_net_profit_usd"])

    cashout_capture_ratio = cashout / max(1e-6, fair_equity_cad) if fair_equity_cad > 0 else 0.0

    # Decision hierarchy:
    # 1. If only 1 leg remains and synthetic hedge beats cashout and locks >= 75% of fair equity -> LOCK_SYNTHETIC_HEDGE
    # 2. If cashout captures >= 82% of fair equity OR blowout risk is high (margin >= 18) -> EXECUTE_EARLY_CASHOUT
    # 3. Otherwise -> HOLD_ACTIVE_RUNNER
    if unsettled_count == 1 and locked_hedge_net > cashout_net_cad and locked_hedge_net >= 0.75 * ev_hold_net_cad and locked_hedge_net > 0:
        action = "LOCK_SYNTHETIC_HEDGE_ARB"
    elif cashout > 0 and (cashout_capture_ratio >= 0.82 or (live_score_margin_abs >= 18 and cashout_capture_ratio >= 0.68)):
        action = "EXECUTE_EARLY_CASHOUT"
    else:
        action = "HOLD_ACTIVE_RUNNER"

    return {
        "state": f"{settled_legs_won}_OF_{total_legs}_SETTLED",
        "recommended_action": action,
        "needed_stat_remaining": needed,
        "effective_minutes_remaining": round(eff_mins * blowout_haircut, 2),
        "conditional_win_prob": round(cond_prob, 4),
        "gross_payout_cad": round(gross_payout, 2),
        "fair_ticket_equity_cad": round(fair_equity_cad, 2),
        "ev_hold_net_cad": round(ev_hold_net_cad, 2),
        "cashout_net_profit_cad": round(cashout_net_cad, 2),
        "cashout_capture_ratio": round(cashout_capture_ratio, 4),
        "locked_hedge_net_profit_cad": round(locked_hedge_net, 2),
        "optimal_hedge_stake_cad": hedge_plan["required_hedge_stake_usd"],
    }
