#!/usr/bin/env python3
"""
Autonomous Sports Quantitative Edge Engine (v3.2)
==================================================
Pure Python + NumPy implementation (zero scipy dependency).
Upgraded Monday Oct 26, 2026 (12:25 PM ET):
- Rule A: Multi-Path Under Bonus & Game-Script / Spread-Total Directional Alignment Filter
- Rule B: Hard Hook / Line-Move Invalidation Guard (MAX PLAYABLE LINE & MIN PLAYABLE ODDS)
- Rule C: Optimal 3-to-4 Leg High-Conviction Core Parlay Sizing
- Rule D: Women's Sports Predictability Priority Boost (WNBA, WTA Tennis, WNBL, Women's Soccer)
"""

import glob
import json
import math
import os
import re
import sqlite3
import urllib.request
from datetime import datetime, timezone
import numpy as np

from injury_usage_matrix import evaluate_player_injury_and_redistribution, fetch_live_espn_injuries
from sgp_copula_engine import run_sgp_copula_engine, norm_cdf, norm_ppf
from clv_shadow_ledger import update_clv_ledger

OUTPUT_JSON = "/home/box/desk/latest_sports_math_edge.json"
SQLITE_DB = "/home/box/desk/desk_feature_store.sqlite"
WORKER_SYNC_URL = "https://bet-odds-scan.sonia-3e6f56284b.workers.dev/sync"

WOMENS_SPORTS = {"WNBA", "WTA", "WNBL", "WOMENS_SOCCER"}

BOOK_WEIGHTS = {
    "pinnacle": 3.0,
    "betonlineag": 2.0,
    "bovada": 1.6,
    "fanduel": 1.5,
    "draftkings": 1.5,
    "betrivers": 1.4,
    "betmgm": 1.2,
    "espnbet": 1.1,
    "hardrockbet": 1.1,
    "sportsinteraction": 1.0,
}

DISPERSION_ALPHA = {
    "player_points": 0.08,
    "player_rebounds": 0.10,
    "player_assists": 0.12,
    "player_threes": 0.14,
    "player_shots_on_goal": 0.12,
    "player_receptions": 0.04,
    "player_points_rebounds_assists": 0.06,
    "wta_total_games": 0.05,
    "wta_set1_games": 0.04,
}

YARDAGE_CV = {
    "player_reception_yds": 0.36,
    "player_rush_yds": 0.34,
    "player_pass_yds": 0.25,
}


def norm_name(s: str) -> str:
    s = (s or "").lower().replace(".", "").replace("'", "").replace("’", "").replace("-", " ")
    for suf in [" jr", " sr", " iii", " ii", " iv"]:
        if s.endswith(suf):
            s = s[: -len(suf)]
    return re.sub(r"\s+", " ", s).strip()


def dec_to_amer(d: float) -> str:
    if d <= 1.0:
        return "+100"
    if d >= 2.0:
        return f"+{int(round((d - 1.0) * 100))}"
    return f"{int(round(-100.0 / (d - 1.0)))}"


def shin_devig(over_dec: float, under_dec: float) -> tuple[float, float]:
    if over_dec <= 1.0 or under_dec <= 1.0:
        return 0.5, 0.5
    q_o = 1.0 / over_dec
    q_u = 1.0 / under_dec
    s = q_o + q_u
    if s <= 1.000001:
        return q_o / s, q_u / s
    z = (s - 1.0) / 2.0
    p_o = (math.sqrt(z * z + 4.0 * (1.0 - z) * (q_o * q_o) / s) - z) / (2.0 * (1.0 - z))
    p_u = (math.sqrt(z * z + 4.0 * (1.0 - z) * (q_u * q_u) / s) - z) / (2.0 * (1.0 - z))
    tot = p_o + p_u
    return p_o / tot, p_u / tot


def nb_pmf(k: int, r: float, p: float) -> float:
    log_c = math.lgamma(k + r) - math.lgamma(k + 1.0) - math.lgamma(r)
    return math.exp(log_c + r * math.log(max(1e-12, p)) + k * math.log(max(1e-12, 1.0 - p)))


def nb_over_prob(mu: float, k_threshold: int, alpha: float) -> float:
    if mu <= 0:
        return 0.0
    if alpha <= 1e-6:
        cdf = sum(math.exp(-mu + i * math.log(mu) - math.lgamma(i + 1.0)) for i in range(k_threshold + 1))
        return max(0.0, min(1.0, 1.0 - cdf))
    r = 1.0 / alpha
    p = r / (r + mu)
    cdf = sum(nb_pmf(i, r, p) for i in range(k_threshold + 1))
    return max(0.0, min(1.0, 1.0 - cdf))


def solve_nb_mu(target_p_over: float, k_threshold: int, alpha: float) -> float:
    target_p_over = max(0.01, min(0.99, target_p_over))
    lo, hi = 0.05, 80.0
    for _ in range(40):
        mid = 0.5 * (lo + hi)
        if nb_over_prob(mid, k_threshold, alpha) < target_p_over:
            lo = mid
        else:
            hi = mid
    return 0.5 * (lo + hi)


def translate_yardage_over_prob(pt_book: float, p_over_book: float, target_line: float, market: str) -> float:
    if market == "player_pass_yds":
        sigma_est = max(38.0, 3.2 * math.sqrt(max(100.0, pt_book)))
    elif market == "player_reception_yds":
        sigma_est = max(15.0, 2.35 * math.sqrt(max(10.0, pt_book)) + 5.0)
    else:
        sigma_est = max(14.0, 2.25 * math.sqrt(max(10.0, pt_book)) + 4.5)
    z_book = norm_ppf(max(0.01, min(0.99, 1.0 - p_over_book)))
    mu_est = pt_book - sigma_est * z_book
    return float(1.0 - norm_cdf((target_line - mu_est) / sigma_est))


def translate_discrete_over_prob(pt_book: float, p_over_book: float, target_line: float, market: str) -> float:
    k_book = int(math.floor(pt_book))
    k_target = int(math.floor(target_line))
    if k_book == k_target:
        return p_over_book
    alpha = DISPERSION_ALPHA.get(market, 0.10)
    mu_est = solve_nb_mu(p_over_book, k_book, alpha)
    return nb_over_prob(mu_est, k_target, alpha)


def empirical_bernstein_lcb(probs: list[float], weights: list[float], delta: float = 0.15) -> tuple[float, float]:
    n = len(probs)
    if n == 0:
        return 0.0, 0.0
    w = np.array(weights, dtype=float)
    w = w / np.sum(w)
    p = np.array(probs, dtype=float)
    mean_p = float(np.sum(w * p))
    if n == 1:
        return max(0.0, mean_p - 0.018), 0.0
    var_p = float(np.sum(w * ((p - mean_p) ** 2)))
    n_eff = float(1.0 / np.sum(w ** 2))
    log_term = math.log(3.0 / delta)
    r_obs = max(0.012, float(np.max(p) - np.min(p)))
    penalty = math.sqrt((2.0 * var_p * log_term) / max(1.0, n_eff)) + (0.28 * r_obs * log_term) / max(1.5, n_eff - 0.5)
    penalty = min(penalty, 0.032)
    return max(0.0, mean_p - penalty), math.sqrt(var_p)


def compute_hard_hook_guard(market: str, side: str, line: float, p_regressed: float, b365_odds: float) -> tuple[str, str]:
    """Rule B: Hard Hook / Line-Move Invalidation Guard."""
    min_dec = max(1.05, 1.015 / max(0.05, p_regressed))
    min_amer = dec_to_amer(min_dec)
    if market in YARDAGE_CV:
        if side == "Over":
            max_line = line + 1.5
            guard_str = f"DO NOT BET ABOVE Over {max_line:.1f} (Target: Over {line:.1f})"
        else:
            min_line = line - 1.5
            guard_str = f"DO NOT BET BELOW Under {min_line:.1f} (Target: Under {line:.1f})"
    else:
        if side == "Over":
            k_int = int(math.floor(line)) + 1
            guard_str = f"HARD HOOK GUARD: DO NOT BET ABOVE Over {line:.1f} ({k_int}+) — SKIP if moved to Over {line+1.0:.1f}"
        else:
            guard_str = f"HARD HOOK GUARD: DO NOT BET BELOW Under {line:.1f} — SKIP if moved to Under {line-1.0:.1f}"
    return guard_str, f"{min_amer} ({min_dec:.2f})"


def evaluate_script_alignment(sport: str, market: str, side: str, line: float, pinnacle_line: float | None, p_sharp: float, p_be: float) -> tuple[float, str]:
    """
    Rule A & Rule D: Multi-Path Under Bonus + Women's Sports Predictability Priority Boost.
    """
    bonus = 0.0
    notes = []

    if sport in WOMENS_SPORTS:
        if p_sharp > p_be + 0.012:
            bonus += 0.014
            notes.append(f"WOMENS_PREDICTABILITY_PRIORITY_BOOST (+1.4%): Lower bookmaker attention + tighter usage/serve dispersion in {sport}")

    if side == "Under":
        if market in ("player_rush_yds", "player_pass_yds", "player_receptions", "player_reception_yds", "player_threes", "wta_total_games"):
            if pinnacle_line is not None and line >= pinnacle_line and p_sharp > p_be + 0.015:
                gap = line - pinnacle_line
                bonus += 0.012
                notes.append(f"MULTI_PATH_UNDER_BONUS (+1.2%): Wins via multiple game-script paths (b365 +{gap:.1f} vs Pin)")
            else:
                bonus += 0.005
                notes.append("STRUCTURAL_UNDER_STABILITY")
        else:
            bonus += 0.005
            notes.append("STRUCTURAL_UNDER_STABILITY")
    else:
        if market in ("player_reception_yds", "player_pass_yds") and pinnacle_line is not None and line > pinnacle_line:
            bonus -= 0.012
            notes.append("POSITIVE_SCRIPT_CEILING_RISK")
        elif not notes:
            notes.append("NEUTRAL_OVER_SCRIPT")

    return bonus, " | ".join(notes)


def collect_bet365_props() -> list[dict]:
    b365_props = []

    nfl_path = "/workspace/mo_american_football_mon.json" if os.path.exists("/workspace/mo_american_football_mon.json") else "/workspace/mo_american_football_sun.json"
    if os.path.exists(nfl_path):
        mo_nfl = json.load(open(nfl_path, encoding="utf-8"))
        group_map = {
            "Receiving Yards O/U": ("player_reception_yds", "ou"),
            "Rushing Yards O/U": ("player_rush_yds", "ou"),
            "Passing Yards O/U": ("player_pass_yds", "ou"),
            "Receptions O/U": ("player_receptions", "ou"),
            "Receiving Yards": ("player_reception_yds", "milestone"),
            "Rushing Yards": ("player_rush_yds", "milestone"),
            "Receptions": ("player_receptions", "milestone"),
        }
        for m in mo_nfl.get("matches", []):
            if m.get("league") != "NFL" or m.get("in_play"):
                continue
            match_str = f"{m.get('away')} @ {m.get('home')}"
            for q in m.get("quotes", []):
                gname = q.get("group_name") or ""
                if gname not in group_map:
                    continue
                mkt, mtype = group_map[gname]
                player = (q.get("line") or "").strip()
                dec = float(q.get("odds_dec") or 0.0)
                if not player or dec <= 1.05 or dec > 4.50:
                    continue
                if mtype == "ou":
                    side = q.get("side")
                    if side not in ("Over", "Under") or q.get("handicap") is None:
                        continue
                    line = float(q["handicap"])
                else:
                    try:
                        ms = int(str(q.get("side")).strip())
                    except Exception:
                        continue
                    line = ms - 0.5
                    side = "Over"
                b365_props.append({
                    "sport": "NFL",
                    "is_womens_sport": False,
                    "match": match_str,
                    "away": m.get("away"),
                    "home": m.get("home"),
                    "team": q.get("n2") or "",
                    "player": player,
                    "norm_player": norm_name(player),
                    "market": mkt,
                    "b365_group": gname,
                    "mtype": mtype,
                    "side": side,
                    "line": line,
                    "b365_odds": round(dec, 3),
                    "b365_amer": dec_to_amer(dec),
                    "b365_ts": q.get("ts"),
                })

    nba_path = "/workspace/mo_basketball_mon.json" if os.path.exists("/workspace/mo_basketball_mon.json") else "/workspace/mo_basketball_sun.json"
    if os.path.exists(nba_path):
        mo_nba = json.load(open(nba_path, encoding="utf-8"))
        group_map_nba = {
            "Points O/U": ("player_points", "ou"),
            "Rebounds O/U": ("player_rebounds", "ou"),
            "Assists O/U": ("player_assists", "ou"),
            "Threes Made O/U": ("player_threes", "ou"),
            "Points, Assists & Rebounds O/U": ("player_points_rebounds_assists", "ou"),
            "Points": ("player_points", "milestone"),
            "Rebounds": ("player_rebounds", "milestone"),
            "Assists": ("player_assists", "milestone"),
            "Threes Made": ("player_threes", "milestone"),
            "Points, Assists & Rebounds": ("player_points_rebounds_assists", "milestone"),
        }
        for m in mo_nba.get("matches", []):
            lg = m.get("league")
            if lg not in ("NBA", "WNBA") or m.get("in_play"):
                continue
            is_w = (lg == "WNBA")
            match_str = f"{m.get('away')} @ {m.get('home')}"
            for q in m.get("quotes", []):
                gname = q.get("group_name") or ""
                if gname not in group_map_nba:
                    continue
                mkt, mtype = group_map_nba[gname]
                player = (q.get("line") or "").strip()
                dec = float(q.get("odds_dec") or 0.0)
                if not player or dec <= 1.05 or dec > 4.50:
                    continue
                if mtype == "ou":
                    side = q.get("side")
                    if side not in ("Over", "Under") or q.get("handicap") is None:
                        continue
                    line = float(q["handicap"])
                else:
                    try:
                        ms = int(str(q.get("side")).strip())
                    except Exception:
                        continue
                    line = ms - 0.5
                    side = "Over"
                b365_props.append({
                    "sport": lg,
                    "is_womens_sport": is_w,
                    "match": match_str,
                    "away": m.get("away"),
                    "home": m.get("home"),
                    "team": q.get("n2") or "",
                    "player": player,
                    "norm_player": norm_name(player),
                    "market": mkt,
                    "b365_group": gname,
                    "mtype": mtype,
                    "side": side,
                    "line": line,
                    "b365_odds": round(dec, 3),
                    "b365_amer": dec_to_amer(dec),
                    "b365_ts": q.get("ts"),
                })

    nhl_path = "/workspace/mo_ice_hockey_mon.json" if os.path.exists("/workspace/mo_ice_hockey_mon.json") else "/workspace/mo_ice_hockey_sun.json"
    if os.path.exists(nhl_path):
        mo_nhl = json.load(open(nhl_path, encoding="utf-8"))
        for m in mo_nhl.get("matches", []):
            if m.get("league") != "NHL" or m.get("in_play"):
                continue
            match_str = f"{m.get('away')} @ {m.get('home')}"
            for q in m.get("quotes", []):
                gname = q.get("group_name") or ""
                if gname not in ("Shots on Goal O/U", "Shots on Goal"):
                    continue
                mkt = "player_shots_on_goal"
                mtype = "ou" if "O/U" in gname else "milestone"
                player = (q.get("line") or "").strip()
                dec = float(q.get("odds_dec") or 0.0)
                if not player or dec <= 1.05 or dec > 4.50:
                    continue
                if mtype == "ou":
                    side = q.get("side")
                    if side not in ("Over", "Under") or q.get("handicap") is None:
                        continue
                    line = float(q["handicap"])
                else:
                    try:
                        ms = int(str(q.get("side")).strip())
                    except Exception:
                        continue
                    line = ms - 0.5
                    side = "Over"
                b365_props.append({
                    "sport": "NHL",
                    "is_womens_sport": False,
                    "match": match_str,
                    "away": m.get("away"),
                    "home": m.get("home"),
                    "team": q.get("n2") or "",
                    "player": player,
                    "norm_player": norm_name(player),
                    "market": mkt,
                    "b365_group": gname,
                    "mtype": mtype,
                    "side": side,
                    "line": line,
                    "b365_odds": round(dec, 3),
                    "b365_amer": dec_to_amer(dec),
                    "b365_ts": q.get("ts"),
                })

    # Collect WTA Tennis Games O/U and 1st Set Games O/U
    tennis_path = "/workspace/mo_tennis_mon.json"
    if os.path.exists(tennis_path):
        mo_t = json.load(open(tennis_path, encoding="utf-8"))
        for m in mo_t.get("matches", []):
            lg = m.get("league") or ""
            if "WTA" not in lg or m.get("in_play"):
                continue
            match_label = f"{m.get('home')} vs {m.get('away')}"
            for q in m.get("quotes", []):
                gname = q.get("group_name") or ""
                if gname == "Total Games 2-Way":
                    mkt = "wta_total_games"
                elif gname == "1st Set Total Games":
                    mkt = "wta_set1_games"
                else:
                    continue
                side = q.get("side")
                if side not in ("Over", "Under"):
                    continue
                try:
                    line = float(q.get("handicap") if q.get("handicap") is not None else q.get("line"))
                except Exception:
                    continue
                dec = float(q.get("odds_dec") or 0.0)
                if dec <= 1.05 or dec > 4.50:
                    continue
                b365_props.append({
                    "sport": "WTA",
                    "is_womens_sport": True,
                    "match": f"[{lg}] {match_label}",
                    "away": m.get("away"),
                    "home": m.get("home"),
                    "team": lg,
                    "player": match_label,
                    "norm_player": norm_name(match_label),
                    "market": mkt,
                    "b365_group": gname,
                    "mtype": "ou",
                    "side": side,
                    "line": line,
                    "b365_odds": round(dec, 3),
                    "b365_amer": dec_to_amer(dec),
                    "b365_ts": q.get("ts"),
                })

    return b365_props


def collect_sharp_odds() -> tuple[dict, dict]:
    now_utc = datetime.now(timezone.utc)
    sharp_index = {}
    player_meta = {}

    files = sorted(glob.glob("/workspace/mon1026/oddsapi_*.json"))
    if not files:
        files = sorted(glob.glob("/workspace/sun1025/*.json"))

    for fn in files:
        try:
            d = json.load(open(fn, encoding="utf-8"))
        except Exception:
            continue
        ct_str = d.get("commence_time")
        if ct_str:
            ct = datetime.fromisoformat(ct_str.replace("Z", "+00:00"))
            if ct <= now_utc:
                continue

        ev_match = f"{d.get('away_team')} @ {d.get('home_team')}"
        for b in d.get("bookmakers", []):
            bkey = b.get("key") or ""
            if bkey not in BOOK_WEIGHTS:
                continue
            for m in b.get("markets", []):
                mkey = m.get("key") or ""
                by_p_pt = {}
                for o in m.get("outcomes", []):
                    p_norm = norm_name(o.get("description") or "")
                    pt = o.get("point")
                    if not p_norm or pt is None:
                        continue
                    by_p_pt.setdefault((p_norm, float(pt)), {})[o.get("name")] = float(o.get("price"))
                    player_meta[p_norm] = {"commence_time": ct_str, "event_match": ev_match}

                for (p_norm, pt), sides in by_p_pt.items():
                    if "Over" in sides and "Under" in sides:
                        p_o, p_u = shin_devig(sides["Over"], sides["Under"])
                        sharp_index.setdefault((p_norm, mkey), []).append({
                            "book": bkey,
                            "weight": BOOK_WEIGHTS[bkey],
                            "point": pt,
                            "over_dec": sides["Over"],
                            "under_dec": sides["Under"],
                            "p_over_novig": p_o,
                            "p_under_novig": p_u,
                        })

    return sharp_index, player_meta


def evaluate_board_v3_1(inj_matrix: dict) -> list[dict]:
    b365_props = collect_bet365_props()
    sharp_index, player_meta = collect_sharp_odds()

    dedup_b365 = {}
    for bp in b365_props:
        key = (bp["norm_player"], bp["market"], bp["side"], bp["line"])
        if key not in dedup_b365 or bp["b365_odds"] > dedup_b365[key]["b365_odds"]:
            dedup_b365[key] = bp

    evaluated = []
    for bp in dedup_b365.values():
        p_norm = bp["norm_player"]
        mkt = bp["market"]
        side = bp["side"]
        target_line = bp["line"]
        b365_dec = bp["b365_odds"]
        p_be = 1.0 / b365_dec

        books = sharp_index.get((p_norm, mkt), [])
        if not books:
            continue

        best_per_book = {}
        for b in books:
            bk = b["book"]
            dist = abs(b["point"] - target_line)
            if bk not in best_per_book or dist < abs(best_per_book[bk]["point"] - target_line):
                best_per_book[bk] = b

        book_list = list(best_per_book.values())
        has_pin_book = any(b["book"] == "pinnacle" for b in book_list)
        if len(book_list) < 2 and not has_pin_book:
            continue

        trans_probs = []
        weights = []
        book_Breakdown = {}
        sharp_summaries = []
        pinnacle_prob = None
        pinnacle_line = None

        for b in book_list:
            pt_b = b["point"]
            p_o_b = b["p_over_novig"]
            if mkt in YARDAGE_CV:
                if abs(pt_b - target_line) > 12.5:
                    continue
                p_o_target = translate_yardage_over_prob(pt_b, p_o_b, target_line, mkt)
            elif mkt in ("player_points_rebounds_assists", "wta_total_games", "wta_set1_games"):
                max_dist = 0.5 if mkt.startswith("wta_") else 2.5
                if abs(pt_b - target_line) > max_dist:
                    continue
                p_o_target = translate_discrete_over_prob(pt_b, p_o_b, target_line, mkt)
            else:
                if abs(pt_b - target_line) > 1.5:
                    continue
                p_o_target = translate_discrete_over_prob(pt_b, p_o_b, target_line, mkt)

            p_side_target = p_o_target if side == "Over" else (1.0 - p_o_target)
            trans_probs.append(p_side_target)
            weights.append(b["weight"])
            book_Breakdown[b["book"]] = {
                "book_line": pt_b,
                "over_dec": b["over_dec"],
                "under_dec": b["under_dec"],
                "translated_prob": round(p_side_target, 4),
            }
            if b["book"] == "pinnacle":
                pinnacle_prob = p_side_target
                pinnacle_line = pt_b
                sharp_summaries.insert(0, f"Pinnacle {pt_b} (O {dec_to_amer(b['over_dec'])}/U {dec_to_amer(b['under_dec'])} -> {p_side_target*100:.1f}%)")
            else:
                sharp_summaries.append(f"{b['book']} {pt_b} (O {dec_to_amer(b['over_dec'])}/U {dec_to_amer(b['under_dec'])} -> {p_side_target*100:.1f}%)")

        if not trans_probs:
            continue
        if len(trans_probs) < 2 and pinnacle_prob is None:
            continue

        w_arr = np.array(weights, dtype=float)
        w_arr = w_arr / np.sum(w_arr)
        p_sharp = float(np.sum(w_arr * np.array(trans_probs, dtype=float)))

        # Rule A & Rule D: Game-Script / Multi-Path Under + Women's Predictability Priority Boost
        script_adj, script_note = evaluate_script_alignment(
            bp["sport"], mkt, side, target_line, pinnacle_line, p_sharp, p_be
        )

        p_regressed = 0.78 * p_sharp + 0.22 * p_be + script_adj
        p_regressed = max(0.01, min(0.99, p_regressed))

        inj_info = evaluate_player_injury_and_redistribution(
            bp["sport"],
            bp["player"],
            f"{bp['away']} {bp['home']}",
            mkt,
            inj_matrix,
        )
        raw_theta_inj = float(inj_info.get("theta_injury_logit") or 0.0)
        if side == "Over" and p_sharp > p_be + 0.01:
            theta_inj = min(0.015, max(-0.02, raw_theta_inj * 0.25))
        else:
            theta_inj = 0.0

        logit_reg = math.log(max(1e-4, min(1.0 - 1e-4, p_regressed)) / (1.0 - max(1e-4, min(1.0 - 1e-4, p_regressed))))
        p_tilted = 1.0 / (1.0 + math.exp(-(logit_reg + theta_inj)))

        reg_book_probs = [min(0.99, max(0.01, 0.78 * tp + 0.22 * p_be + script_adj)) for tp in trans_probs]
        p_lcb, std_books = empirical_bernstein_lcb(reg_book_probs, weights, delta=0.15)

        ev_raw = (p_sharp * b365_dec) - 1.0
        ev_regressed = (p_regressed * b365_dec) - 1.0
        ev_tilted = (p_tilted * b365_dec) - 1.0
        ev_lcb = (p_lcb * b365_dec) - 1.0

        b_net = b365_dec - 1.0
        f_star = max(0.0, (b_net * p_regressed - (1.0 - p_regressed)) / b_net) if b_net > 0 else 0.0
        kelly_stake_pct = round(0.25 * f_star * 100.0, 3)

        max_line_guard, min_odds_guard = compute_hard_hook_guard(mkt, side, target_line, p_regressed, b365_dec)

        low_vol_3pt_trap = (mkt == "player_threes" and side == "Over" and target_line <= 1.5 and p_sharp < 0.48)
        if low_vol_3pt_trap:
            gate = "NO_BET (LOW_VOLUME_3PT_TRAP)"
        elif p_sharp <= p_be + 0.012:
            gate = "NO_BET (SHARP_BELOW_BREAKEVEN)"
        elif p_lcb >= p_be - 0.005 and ev_regressed >= 0.020:
            gate = "STRICT_PASS"
        elif ev_regressed >= 0.012:
            gate = "MARGINAL_POSITIVE_EV"
        else:
            gate = "NO_BET (FAILS_LCB_GATE)"

        meta = player_meta.get(p_norm, {})
        evaluated.append({
            "sport": bp["sport"],
            "is_womens_sport": bool(bp.get("is_womens_sport")),
            "match": bp["match"],
            "team": bp.get("team", ""),
            "commence_time": meta.get("commence_time"),
            "player": bp["player"],
            "market": mkt,
            "b365_group": bp["b365_group"],
            "side": side,
            "line": target_line,
            "b365_odds": b365_dec,
            "b365_amer": bp["b365_amer"],
            "b365_ts": bp["b365_ts"],
            "p_be": round(p_be, 5),
            "p_sharp": round(p_sharp, 5),
            "p_regressed": round(p_regressed, 5),
            "p_tilted": round(p_tilted, 5),
            "p_lcb": round(p_lcb, 5),
            "pinnacle_prob": round(pinnacle_prob, 5) if pinnacle_prob is not None else None,
            "pinnacle_line": pinnacle_line,
            "std_across_books": round(std_books, 5),
            "num_books": len(trans_probs),
            "ev_raw": round(ev_raw, 5),
            "ev_regressed": round(ev_regressed, 5),
            "ev_tilted": round(ev_tilted, 5),
            "ev_lcb": round(ev_lcb, 5),
            "kelly_stake_pct": kelly_stake_pct,
            "decision_gate": gate,
            "max_playable_line": max_line_guard,
            "min_playable_odds_amer": min_odds_guard,
            "script_alignment_note": script_note,
            "theta_injury": round(theta_inj, 5),
            "injury_rationale": inj_info.get("injury_rationale", "NEUTRAL"),
            "questionable_teammates": inj_info.get("questionable_teammates", []),
            "sharp_summary": " | ".join(sharp_summaries[:3]),
            "sharp_books_breakdown": book_Breakdown,
        })

    # Sort with Women's STRICT_PASS edges and high-EV STRICT_PASS edges prioritized
    evaluated.sort(
        key=lambda x: (
            1 if x["decision_gate"] == "STRICT_PASS" else 0,
            x["ev_regressed"] + (0.015 if x.get("is_womens_sport") else 0.0),
        ),
        reverse=True,
    )
    return evaluated


def persist_to_sqlite(props: list[dict], sgp_payload: dict) -> None:
    conn = sqlite3.connect(SQLITE_DB)
    cur = conn.cursor()
    cur.execute("""
        CREATE TABLE IF NOT EXISTS prop_evaluations_v3 (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            evaluated_at_utc TEXT,
            sport TEXT,
            match TEXT,
            player TEXT,
            market TEXT,
            side TEXT,
            line REAL,
            b365_odds REAL,
            p_be REAL,
            p_sharp REAL,
            p_regressed REAL,
            p_lcb REAL,
            ev_regressed REAL,
            num_books INTEGER,
            decision_gate TEXT
        )
    """)
    ts = datetime.now(timezone.utc).isoformat()
    for p in props[:100]:
        cur.execute("""
            INSERT INTO prop_evaluations_v3 (
                evaluated_at_utc, sport, match, player, market, side, line,
                b365_odds, p_be, p_sharp, p_regressed, p_lcb, ev_regressed, num_books, decision_gate
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """, (
            ts, p["sport"], p["match"], p["player"], p["market"], p["side"], p["line"],
            p["b365_odds"], p["p_be"], p["p_sharp"], p["p_regressed"], p["p_lcb"],
            p["ev_regressed"], p["num_books"], p["decision_gate"]
        ))
    conn.commit()
    conn.close()


def sync_to_worker(payload: dict) -> dict:
    try:
        req = urllib.request.Request(
            WORKER_SYNC_URL,
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json", "User-Agent": "SportsMathEdge/3.2"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=15) as resp:
            return {"synced": True, "status": resp.status}
    except Exception as e:
        return {"synced": False, "error": str(e)}


def main():
    print("[v3.2] Running ESPN Injury Usage Matrix...")
    inj_matrix = fetch_live_espn_injuries()

    print("[v3.2] Evaluating Monday Oct 26, 2026 Multi-Sport + Women's Sports Priority Board (WNBA + WTA + NFL + NBA + NHL)...")
    props = evaluate_board_v3_1(inj_matrix)
    strict_passes = [p for p in props if p["decision_gate"] == "STRICT_PASS"]
    womens_passes = [p for p in strict_passes if p.get("is_womens_sport")]

    print(f"[v3.2] Total evaluated props: {len(props)} | STRICT_PASS: {len(strict_passes)} | Women's STRICT_PASS: {len(womens_passes)}")

    print("[v3.2] Running Student-t Copula SGP & v3.2 Women's-Priority 3-to-4 Leg Core Parlay Optimizer...")
    sgp_payload = run_sgp_copula_engine(props)

    print("[v3.2] Updating CLV Shadow Ledger...")
    clv_summary = update_clv_ledger(props)

    persist_to_sqlite(props, sgp_payload)

    output = {
        "engine_version": "3.2-womens-sports-priority-multipath-under-hook-guard",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "post_mortem_upgrades_v3_2": [
            "Rule A (Multi-Path Under Bonus & Game-Script Filter): +1.2% structural edge bonus for Unders at/above Pinnacle's line.",
            "Rule B (Hard Hook / Line-Move Invalidation Guard): Outputs explicit MAX PLAYABLE LINE and MIN PLAYABLE ODDS on every leg.",
            "Rule C (Optimal 3-to-4 Leg High-Conviction Core Parlay Sizing): Prioritizes a high-hit-rate 3-to-4 leg core while listing 5th/6th legs as optional kickers.",
            "Rule D (Women's Sports Predictability Priority Boost): Prioritizes WNBA, WTA Tennis, WNBL, and Women's Soccer (+1.4% structural predictability bonus + 1.38x Core Parlay ranking multiplier + standalone All-Women's parlays).",
        ],
        "summary": {
            "total_props_evaluated": len(props),
            "strict_lcb_passes": len(strict_passes),
            "womens_strict_passes": len(womens_passes),
            "positive_sgp_combinations": sgp_payload.get("total_positive_sgp_combinations", 0),
            "vetoed_negative_correlation_sgps": len(sgp_payload.get("vetoed_negative_correlation_sgps", [])),
        },
        "womens_strict_pass_props": womens_passes[:25],
        "strict_pass_props": strict_passes[:35],
        "top_parlay_slips_v3_1": sgp_payload.get("parlay_optimizer_v3_1", {}),
        "top_sgp_copula_slips": sgp_payload.get("top_sgp_slips", [])[:10],
        "vetoed_cannibalized_sgps": sgp_payload.get("vetoed_negative_correlation_sgps", [])[:10],
        "clv_shadow_summary": clv_summary,
        "all_evaluated_props": props[:100],
    }

    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(output, f, indent=2)

    sync_res = sync_to_worker(output)
    print(f"[v3.2] Saved {OUTPUT_JSON} and synced to Worker: {sync_res}")


if __name__ == "__main__":
    main()
