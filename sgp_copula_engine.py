#!/usr/bin/env python3
"""
Student-t & Gaussian Copula SGP + Multi-Leg Parlay Optimizer (v3.2)
===================================================================
Pure Python + NumPy implementation (zero scipy dependency).
Upgraded Monday Oct 26, 2026 (12:25 PM ET):
- Rule A: Multi-Path Under Bonus & Game-Script Directional Alignment
- Rule B: Hard Hook / Line-Move Invalidation Guard (MAX PLAYABLE LINE + MIN ODDS)
- Rule C: Optimal 3-to-4 Leg High-Conviction Core Parlay Sizing
- Rule D: Women's Sports Predictability Priority Boost (WNBA, WTA Tennis, WNBL, Women's Soccer)
"""

import json
import math
import os
from datetime import datetime, timezone
import numpy as np

OUTPUT_PATH = "/home/box/desk/latest_sgp_copula.json"

WOMENS_SPORTS = {"WNBA", "WTA", "WNBL", "WOMENS_SOCCER"}

HIGH_FTA_HUB_CENTERS = {
    "joel embiid", "nikola jokic", "giannis antetokounmpo", "anthony davis",
    "domantas sabonis", "victor wembanyama", "bam adebayo", "zion williamson",
    "alperen sengun", "julius randle", "paolo banchero", "karl-anthony towns"
}

COMBO_SCORING_GUARDS = {
    "tyrese maxey", "jamal murray", "dennis schroder", "dejounte murray",
    "kyrie irving", "donovan mitchell", "stephen curry", "deaaron fox",
    "ja morant", "keyonte george", "austin reaves", "cam thomas",
    "anfernee simons", "coby white", "jalen green", "brandon miller"
}


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def norm_ppf(p: float) -> float:
    p = max(1e-6, min(1.0 - 1e-6, p))
    lo, hi = -6.0, 6.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if norm_cdf(mid) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def student_t_cdf_df5(x: float) -> float:
    t2 = x * x
    theta = math.atan(x / math.sqrt(5.0))
    sin_t = x / math.sqrt(5.0 + t2)
    cos_t = math.sqrt(5.0 / (5.0 + t2))
    return 0.5 + (theta + sin_t * cos_t * (1.0 + (2.0 / 3.0) * (cos_t * cos_t))) / math.pi


def student_t_ppf_df5(p: float) -> float:
    p = max(1e-5, min(1.0 - 1e-5, p))
    lo, hi = -12.0, 12.0
    for _ in range(45):
        mid = 0.5 * (lo + hi)
        if student_t_cdf_df5(mid) < p:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def estimate_pairwise_correlation(leg_a: dict, leg_b: dict) -> tuple[float, str]:
    if leg_a.get("match") != leg_b.get("match"):
        return 0.0, "INDEPENDENT_CROSS_GAME"

    pa = (leg_a.get("player") or "").strip().lower()
    pb = (leg_b.get("player") or "").strip().lower()
    if pa == pb:
        return -0.50, "SAME_PLAYER_DUPLICATE_VETO"

    ma = leg_a.get("market") or ""
    mb = leg_b.get("market") or ""
    sa = leg_a.get("side") or "Over"
    sb = leg_b.get("side") or "Over"
    markets = {ma, mb}

    q_a = [x.lower() for x in (leg_a.get("questionable_teammates") or [])]
    q_b = [x.lower() for x in (leg_b.get("questionable_teammates") or [])]
    if pb in q_a or pa in q_b:
        return -0.35, "QUESTIONABLE_STAR_CONDITIONAL_CONTRADICTION_VETO"

    if sa == "Under" and sb == "Under":
        if leg_a.get("sport") == "NFL" and leg_b.get("sport") == "NFL":
            if markets == {"player_rush_yds", "player_receptions"}:
                return 0.08, "LOW_PLAY_VOLUME_DEFENSIVE_SCRIPT_UNDER_SYNERGY"
            if ma == "player_receptions" and mb == "player_receptions":
                return 0.10, "LOW_PASS_VOLUME_DUAL_RECEPTION_UNDER_SYNERGY"
        if leg_a.get("sport") == "WNBA" and leg_b.get("sport") == "WNBA":
            if ma == "player_threes" and mb == "player_threes":
                return 0.09, "WNBA_PERIMETER_DEFENSE_PACE_UNDER_SYNERGY"

    if sa == "Over" and sb == "Over":
        if markets in ({"player_assists", "player_points"}, {"player_assists", "player_threes"}):
            ast_leg = leg_a if ma == "player_assists" else leg_b
            pts_leg = leg_b if ma == "player_assists" else leg_a
            ast_p = (ast_leg.get("player") or "").strip().lower()
            pts_p = (pts_leg.get("player") or "").strip().lower()

            if markets == {"player_assists", "player_points"}:
                if (pts_p in HIGH_FTA_HUB_CENTERS or float(pts_leg.get("line") or 0) >= 24.5) and (
                    ast_p in COMBO_SCORING_GUARDS or float(ast_leg.get("line") or 0) >= 6.5
                ):
                    return -0.18, "COMBO_GUARD_AST_VS_HIGH_FTA_HUB_PTS_CANNIBALIZATION"
                return 0.16, "TEAMMATE_PLAYMAKER_SCORER_MODEST_SYNERGY"
            if markets == {"player_assists", "player_threes"}:
                return 0.24, "TEAMMATE_ASSIST_TO_PERIMETER_SHOOTER_SYNERGY"

        if markets == {"player_threes", "player_rebounds"}:
            return -0.14, "TEAMMATE_MADE_THREES_REDUCE_OREB_TRACK_MEET_PENALTY"

        if ma == "player_rebounds" and mb == "player_rebounds":
            return -0.25, "TEAMMATE_REBOUND_CANNIBALIZATION"

        if ma == "player_points" and mb == "player_points":
            return -0.15, "TEAMMATE_USAGE_SCORING_CANNIBALIZATION"

        if ma == "player_assists" and mb == "player_assists":
            return -0.22, "TEAMMATE_BALL_DOMINANCE_ASSIST_CANNIBALIZATION"

        if markets <= {"player_reception_yds", "player_receptions"}:
            return -0.10, "TEAMMATE_TARGET_SHARE_COMPETITION"

        if markets == {"player_rush_yds", "player_reception_yds"}:
            return -0.12, "RUN_PASS_GAME_SCRIPT_INVERSE_CORRELATION"

    if (
        (ma == "player_reception_yds" and sa == "Over" and mb == "player_receptions" and sb == "Under") or
        (mb == "player_reception_yds" and sb == "Over" and ma == "player_receptions" and sa == "Under")
    ):
        return 0.12, "DOWNFIELD_WR_TARGET_CONCENTRATION_VS_CHECKDOWN_UNDER"

    return 0.04, "NEUTRAL_PACE_CORRELATION"


def is_parlay_eligible_v3_1(prop: dict, core_mode: bool = True) -> tuple[bool, str]:
    p_sharp = float(prop.get("p_sharp") or 0.0)
    p_be = float(prop.get("p_be") or 1.0)
    p_lcb = float(prop.get("p_lcb") or 0.0)
    ev_reg = float(prop.get("ev_regressed") or 0.0)
    n_books = int(prop.get("num_books") or 0)
    mkt = prop.get("market") or ""
    side = prop.get("side") or "Over"
    line = float(prop.get("line") or 0.0)
    b365_odds = float(prop.get("b365_odds") or 0.0)
    has_pin = prop.get("pinnacle_prob") is not None
    is_womens = bool(prop.get("is_womens_sport")) or (prop.get("sport") in WOMENS_SPORTS)

    if n_books < 2 and not has_pin:
        return False, "REQUIRES_PINNACLE_OR_2_SHARP_BOOKS"
    if p_sharp <= p_be + 0.012:
        return False, "RAW_SHARP_BELOW_BREAKEVEN_BUFFER"
    if ev_reg < 0.020:
        return False, "REGRESSED_EV_BELOW_2.0_PCT"
    if p_lcb <= p_be - 0.005:
        return False, "FAILS_LCB_GATE"
    if core_mode:
        # Rule D: Women's sports allow Pinnacle-anchored WTA/WNBA markets & high-probability Unders (e.g. -190 / 1.526)
        if not is_womens and (n_books < 2 or not has_pin):
            return False, "CORE_REQUIRES_BOTH_PINNACLE_AND_KAMBI_CONFIRMATION"
        min_odds = 1.48 if is_womens else 1.65
        if b365_odds < min_odds or b365_odds > 2.22:
            return False, "OUTSIDE_HIGH_HIT_RATE_CORE_ODDS_BAND"
        if p_sharp < 0.520:
            return False, "CORE_LEG_WIN_PROB_BELOW_52.0_PCT"
    else:
        min_odds = 1.38 if is_womens else 1.60
        if b365_odds < min_odds or b365_odds > 2.65:
            return False, "ODDS_OUTSIDE_PARLAY_BAND"
    if mkt in ("player_reception_yds", "player_rush_yds") and line < 40.0:
        return False, "LOW_YARDAGE_BACKUP_GADGET_BANNED"
    if mkt == "player_receptions" and line < 2.5:
        return False, "LOW_RECEPTION_VOLUME_BANNED"
    if mkt == "player_threes" and side == "Over" and line <= 1.5 and p_sharp < 0.48:
        return False, "LOW_VOLUME_3PT_TRAP_BANNED"
    return True, "ELIGIBLE_V3_2"


def simulate_joint_prob_copula(probs: list[float], corr_matrix: np.ndarray, df: int = 5, n_sims: int = 80000, seed: int = 20261026) -> dict:
    k = len(probs)
    p_indep = float(np.prod(probs))
    if k == 1:
        return {"p_indep": p_indep, "p_gauss": p_indep, "p_student_t": p_indep}

    eigvals, eigvecs = np.linalg.eigh(corr_matrix)
    eigvals = np.maximum(eigvals, 1e-4)
    psd_corr = eigvecs @ np.diag(eigvals) @ eigvecs.T
    d_inv = np.diag(1.0 / np.sqrt(np.diag(psd_corr)))
    psd_corr = d_inv @ psd_corr @ d_inv

    rng = np.random.default_rng(seed)
    L = np.linalg.cholesky(psd_corr)
    z = rng.standard_normal((n_sims, k)) @ L.T

    g_thresh = [norm_ppf(1.0 - p) for p in probs]
    g_hits = np.ones(n_sims, dtype=bool)
    for i in range(k):
        g_hits &= (z[:, i] > g_thresh[i])
    p_gauss = float(np.mean(g_hits))

    chi2 = rng.chisquare(df, size=(n_sims, 1))
    t_draws = z / np.sqrt(chi2 / df)
    t_thresh = [student_t_ppf_df5(1.0 - p) for p in probs]
    t_hits = np.ones(n_sims, dtype=bool)
    for i in range(k):
        t_hits &= (t_draws[:, i] > t_thresh[i])
    p_t = float(np.mean(t_hits))

    return {
        "p_indep": round(p_indep, 6),
        "p_gauss": round(p_gauss, 6),
        "p_student_t": round(p_t, 6),
    }


def build_parlay_slip(legs: list[dict], stake_usd: float = 20.0) -> dict:
    k = len(legs)
    corr = np.eye(k)
    pair_notes = []
    for i in range(k):
        for j in range(i + 1, k):
            rho, reason = estimate_pairwise_correlation(legs[i], legs[j])
            corr[i, j] = rho
            corr[j, i] = rho
            if rho != 0.0:
                pair_notes.append(f"{legs[i]['player']} + {legs[j]['player']}: rho={rho:+.2f} ({reason})")

    probs_reg = [float(x["p_regressed"]) for x in legs]
    probs_sharp = [float(x["p_sharp"]) for x in legs]
    cop_reg = simulate_joint_prob_copula(probs_reg, corr, df=5)
    cop_sharp = simulate_joint_prob_copula(probs_sharp, corr, df=5)

    combined_dec = float(np.prod([float(x["b365_odds"]) for x in legs]))
    combined_amer = f"+{int(round((combined_dec - 1.0) * 100))}" if combined_dec >= 2.0 else f"{int(round(-100.0 / (combined_dec - 1.0)))}"
    p_joint = cop_reg["p_student_t"]
    p_joint_sharp = cop_sharp["p_student_t"]
    ev_joint = (p_joint * combined_dec) - 1.0
    ev_joint_sharp = (p_joint_sharp * combined_dec) - 1.0
    total_return = round(stake_usd * combined_dec, 2)
    net_profit = round(total_return - stake_usd, 2)
    expected_profit = round(stake_usd * ev_joint, 2)

    b_net = combined_dec - 1.0
    f_kelly = max(0.0, (b_net * p_joint - (1.0 - p_joint)) / b_net) if b_net > 0 else 0.0

    return {
        "num_legs": k,
        "stake_usd": stake_usd,
        "combined_decimal_odds": round(combined_dec, 3),
        "combined_american_odds": combined_amer,
        "potential_total_return_usd": total_return,
        "potential_net_profit_usd": net_profit,
        "joint_win_prob_regressed": round(p_joint, 5),
        "joint_win_prob_sharp": round(p_joint_sharp, 5),
        "parlay_ev_regressed_pct": round(ev_joint * 100.0, 2),
        "parlay_ev_sharp_pct": round(ev_joint_sharp * 100.0, 2),
        "expected_profit_on_stake_usd": expected_profit,
        "quarter_kelly_bankroll_pct": round(0.25 * f_kelly * 100.0, 3),
        "correlation_notes": pair_notes if pair_notes else ["100% Cross-Game Independent (rho = 0.00)"],
        "legs": [
            {
                "leg_num": idx + 1,
                "sport": x["sport"],
                "is_womens_sport": bool(x.get("is_womens_sport")),
                "match": x["match"],
                "player": x["player"],
                "market": x["b365_group"],
                "pick": f"{x['side']} {x['line']}",
                "b365_odds": x["b365_odds"],
                "b365_amer": x["b365_amer"],
                "p_be_pct": round(float(x["p_be"]) * 100.0, 1),
                "p_sharp_pct": round(float(x["p_sharp"]) * 100.0, 1),
                "p_regressed_pct": round(float(x["p_regressed"]) * 100.0, 1),
                "leg_ev_regressed_pct": round(float(x["ev_regressed"]) * 100.0, 2),
                "max_playable_line": x.get("max_playable_line"),
                "min_playable_odds": x.get("min_playable_odds_amer"),
                "script_alignment": x.get("script_alignment_note"),
                "sharp_anchor": x["sharp_summary"],
            }
            for idx, x in enumerate(legs)
        ],
    }


def optimize_parlay_v3_1(props: list[dict], stake_usd: float = 20.0) -> dict:
    core_candidates = []
    booster_candidates = []
    womens_candidates = []
    for p in props:
        ok_core, _ = is_parlay_eligible_v3_1(p, core_mode=True)
        ok_boost, _ = is_parlay_eligible_v3_1(p, core_mode=False)
        if ok_core:
            core_candidates.append(p)
        elif ok_boost:
            booster_candidates.append(p)
        if (p.get("is_womens_sport") or p.get("sport") in WOMENS_SPORTS) and (ok_core or ok_boost):
            womens_candidates.append(p)

    def score_leg(p: dict) -> float:
        has_pin = p.get("pinnacle_prob") is not None
        exact_pin = has_pin and abs(float(p.get("pinnacle_line") or 0) - float(p.get("line") or 0)) < 0.01
        pin_mult = 1.45 if exact_pin else (1.20 if has_pin else 0.80)
        ou_mult = 1.25 if "O/U" in (p.get("b365_group") or "") or "Games" in (p.get("b365_group") or "") else 1.00
        under_mult = 1.20 if p.get("side") == "Under" else 1.00
        womens_mult = 1.38 if (p.get("is_womens_sport") or p.get("sport") in WOMENS_SPORTS) else 1.00
        n_books = max(1, int(p.get("num_books") or 1))
        return float(p.get("ev_regressed") or 0.0) * (float(p.get("p_regressed") or 0.5) ** 2.0) * math.sqrt(n_books) * pin_mult * ou_mult * under_mult * womens_mult

    best_core_by_player = {}
    for p in core_candidates:
        k = (p.get("player") or "").lower()
        sc = score_leg(p)
        if k not in best_core_by_player or sc > best_core_by_player[k][0]:
            best_core_by_player[k] = (sc, p)

    ranked_core = [item[1] for item in sorted(best_core_by_player.values(), key=lambda x: x[0], reverse=True)]

    ordered_core = []
    secondary_core = []
    used_players = set()
    used_games = set()
    for p in ranked_core:
        pl = (p.get("player") or "").lower()
        g = p.get("match") or ""
        if pl in used_players:
            continue
        if g not in used_games:
            ordered_core.append(p)
            used_players.add(pl)
            used_games.add(g)
        else:
            secondary_core.append(p)
    for p in secondary_core:
        pl = (p.get("player") or "").lower()
        if pl in used_players:
            continue
        conflict = False
        for ex in ordered_core:
            rho, _ = estimate_pairwise_correlation(ex, p)
            if rho < 0.0:
                conflict = True
                break
        if not conflict:
            ordered_core.append(p)
            used_players.add(pl)

    best_boost_by_player = {}
    for p in booster_candidates:
        k = (p.get("player") or "").lower()
        if k in used_players:
            continue
        sc = score_leg(p)
        if k not in best_boost_by_player or sc > best_boost_by_player[k][0]:
            best_boost_by_player[k] = (sc, p)
    ranked_boost = [item[1] for item in sorted(best_boost_by_player.values(), key=lambda x: x[0], reverse=True)]

    all_ordered = list(ordered_core)
    for p in ranked_boost:
        pl = (p.get("player") or "").lower()
        if pl in used_players:
            continue
        conflict = False
        for ex in all_ordered:
            rho, _ = estimate_pairwise_correlation(ex, p)
            if rho < 0.0:
                conflict = True
                break
        if not conflict:
            all_ordered.append(p)
            used_players.add(pl)

    # Also build Dedicated All-Women's Sports Parlay Pool
    best_womens = {}
    for p in womens_candidates:
        k = (p.get("player") or "").lower()
        sc = score_leg(p)
        if k not in best_womens or sc > best_womens[k][0]:
            best_womens[k] = (sc, p)
    ranked_womens = [item[1] for item in sorted(best_womens.values(), key=lambda x: x[0], reverse=True)]
    ordered_womens = []
    used_w_players = set()
    for p in ranked_womens:
        pl = (p.get("player") or "").lower()
        if pl in used_w_players:
            continue
        conflict = False
        for ex in ordered_womens:
            rho, _ = estimate_pairwise_correlation(ex, p)
            if rho < 0.0:
                conflict = True
                break
        if not conflict:
            ordered_womens.append(p)
            used_w_players.add(pl)

    slips = {}
    for k in [3, 4, 5, 6]:
        pool = ordered_core if len(ordered_core) >= k else all_ordered
        if len(pool) >= k:
            slips[f"{k}_leg_parlay"] = build_parlay_slip(pool[:k], stake_usd=stake_usd)

    if len(ordered_womens) >= 4:
        slips["womens_4_leg_parlay"] = build_parlay_slip(ordered_womens[:4], stake_usd=stake_usd)
    if len(ordered_womens) >= 3:
        slips["womens_3_leg_parlay"] = build_parlay_slip(ordered_womens[:3], stake_usd=stake_usd)

    return slips


def run_sgp_copula_engine(props: list[dict]) -> dict:
    by_game = {}
    for p in props:
        ok, _ = is_parlay_eligible_v3_1(p, core_mode=False)
        if ok:
            by_game.setdefault(p["match"], []).append(p)

    sgp_slips = []
    vetoed_sgps = []
    for match, g_props in by_game.items():
        n = len(g_props)
        for i in range(n):
            for j in range(i + 1, n):
                a, b = g_props[i], g_props[j]
                rho, reason = estimate_pairwise_correlation(a, b)
                if a["player"].lower() == b["player"].lower():
                    continue
                if rho < 0.0:
                    vetoed_sgps.append({
                        "match": match,
                        "leg_1": f"{a['player']} {a['side']} {a['line']} {a['b365_group']}",
                        "leg_2": f"{b['player']} {b['side']} {b['line']} {b['b365_group']}",
                        "rho": rho,
                        "veto_reason": reason,
                    })
                    continue
                corr = np.array([[1.0, rho], [rho, 1.0]])
                cop = simulate_joint_prob_copula([float(a["p_regressed"]), float(b["p_regressed"])], corr, df=5, n_sims=40000)
                comb_dec = float(a["b365_odds"]) * float(b["b365_odds"])
                ev_sgp = cop["p_student_t"] * comb_dec - 1.0
                if ev_sgp > 0.03:
                    sgp_slips.append({
                        "match": match,
                        "leg_1": f"{a['player']} {a['side']} {a['line']} ({a['b365_amer']})",
                        "leg_2": f"{b['player']} {b['side']} {b['line']} ({b['b365_amer']})",
                        "rho": rho,
                        "correlation_rationale": reason,
                        "combined_decimal_odds": round(comb_dec, 3),
                        "joint_prob_student_t": cop["p_student_t"],
                        "sgp_ev_pct": round(ev_sgp * 100.0, 2),
                    })

    sgp_slips.sort(key=lambda x: x["sgp_ev_pct"], reverse=True)
    parlay_slips = optimize_parlay_v3_1(props, stake_usd=20.0)

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "total_positive_sgp_combinations": len(sgp_slips),
        "top_sgp_slips": sgp_slips[:15],
        "vetoed_negative_correlation_sgps": vetoed_sgps[:20],
        "parlay_optimizer_v3_1": parlay_slips,
    }
    with open(OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return payload
