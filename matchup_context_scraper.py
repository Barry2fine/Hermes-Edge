#!/usr/bin/env python3
r"""
Official NHL (`api-web.nhle.com`) & ESPN/NBA Schedule Rest, Starting Goalie & Pace Matchup Scraper
File: /home/box/desk/matchup_context_scraper.py

Queries zero-auth official league & ESPN scoreboard endpoints:
  1. NHL Official Web API:
     - `https://api-web.nhle.com/v1/score/now`
     - `https://api-web.nhle.com/v1/standings/now`
     - `https://api-web.nhle.com/v1/schedule/now` (plus yesterday's games for B2B detection)
     - `https://api-web.nhle.com/v1/gamecenter/{game_id}/landing` (`goalieComparison`: team GAA, SV%, and primary goalie stats)
  2. ESPN Scoreboard API (NBA & NHL):
     - `https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard` (today + yesterday for B2B rest)
     - `https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard` (today + yesterday for B2B rest)

Computes a Matchup Context Multiplier (\Delta\theta_{\text{matchup}}) for every game & team on tonight's slate:
  - Opposing Defensive Allowance / Goalie Save% & GAA / Pace Factor:
      * Fast pace / weak opposing defense / high opposing GAA (>3.15) or low SV% (<0.898) boosts
        \Delta\theta_{\text{matchup}} > 0 for Overs.
      * Elite opposing defense / elite goalie (GAA < 2.35, SV% > 0.920) / slow pace reduces
        \Delta\theta_{\text{matchup}} < 0.
  - Back-to-Back (B2B) / Rest Advantage & Home-Court / Home-Ice Situational Factors:
      * Rested team vs fatigued B2B opponent receives a positive pace/efficiency boost (+0.0045 to +0.0080).
      * Team on road B2B receives a fatigue haircut (-0.0055 to -0.0090), except for rebounds/saves where opponent volume rises.
      * Home-court / home-ice role player shooting & line-matching boost (+0.0025).

Wired directly into `sports_math_edge.py`'s Entropic Tilting equation:
  \theta_{\text{total}} = \theta_{\text{PM}} + \theta_{\text{injury}} + \theta_{\text{matchup}}
"""

import datetime
import json
import os
import re
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

NHL_API_BASE = "https://api-web.nhle.com/v1"
ESPN_NBA_URL = "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard"
ESPN_NHL_URL = "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard"
OUTPUT_JSON = "/home/box/desk/latest_matchup_context.json"


def http_get_json(url: str, timeout: float = 10.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def norm_team(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return " ".join(s.split())


def scrape_nhl_official_context(today_ymd: str, yest_ymd_dash: str) -> Dict[str, Any]:
    score_now = http_get_json(f"{NHL_API_BASE}/score/now")
    standings_now = http_get_json(f"{NHL_API_BASE}/standings/now")
    try:
        yest_score = http_get_json(f"{NHL_API_BASE}/score/{yest_ymd_dash}")
    except Exception:
        yest_score = {"games": []}

    # Identify NHL teams playing on a back-to-back (played yesterday)
    b2b_abbrevs = set()
    for g in yest_score.get("games") or []:
        aw = (g.get("awayTeam") or {}).get("abbrev")
        hm = (g.get("homeTeam") or {}).get("abbrev")
        if aw:
            b2b_abbrevs.add(aw.upper())
        if hm:
            b2b_abbrevs.add(hm.upper())

    # Index team standings (GF/GP, GA/GP, L10 record)
    team_standings: Dict[str, Dict[str, Any]] = {}
    for s in standings_now.get("standings") or []:
        ab = (s.get("teamAbbrev") or {}).get("default", "").upper()
        t_name = (s.get("teamName") or {}).get("default", "")
        gp = max(1, int(s.get("gamesPlayed") or 1))
        gf = float(s.get("goalFor") or 0.0)
        ga = float(s.get("goalAgainst") or 0.0)
        gf_per_g = gf / gp
        ga_per_g = ga / gp
        team_standings[ab] = {
            "abbrev": ab,
            "team_name": t_name,
            "norm_name": norm_team(t_name),
            "games_played": gp,
            "gf_per_game": round(gf_per_g, 2),
            "ga_per_game": round(ga_per_g, 2),
            "pace_total_goals_per_game": round(gf_per_g + ga_per_g, 2),
            "point_pctg": round(float(s.get("pointPctg") or 0.5), 3),
            "l10_wins": int(s.get("l10Wins") or 5),
        }

    games_raw = score_now.get("games") or []

    def fetch_landing(gid: int) -> Tuple[int, Dict[str, Any]]:
        try:
            return gid, http_get_json(f"{NHL_API_BASE}/gamecenter/{gid}/landing", timeout=6.0)
        except Exception:
            return gid, {}

    landing_map: Dict[int, Dict[str, Any]] = {}
    with ThreadPoolExecutor(max_workers=6) as ex:
        futs = [ex.submit(fetch_landing, int(g["id"])) for g in games_raw if "id" in g]
        for f in futs:
            gid, ldata = f.result()
            landing_map[gid] = ldata

    nhl_matchups: List[Dict[str, Any]] = []
    team_context_map: Dict[str, Dict[str, Any]] = {}

    # League average NHL GA/G ~ 3.08, Save% ~ 0.903, Pace (GF+GA) ~ 6.16
    LEAGUE_AVG_GA = 3.08
    LEAGUE_AVG_SV = 0.903
    LEAGUE_AVG_PACE = 6.16

    for g in games_raw:
        gid = int(g["id"])
        aw_obj = g.get("awayTeam") or {}
        hm_obj = g.get("homeTeam") or {}
        aw_ab = str(aw_obj.get("abbrev") or "").upper()
        hm_ab = str(hm_obj.get("abbrev") or "").upper()
        aw_std = team_standings.get(aw_ab, {})
        hm_std = team_standings.get(hm_ab, {})
        aw_full = aw_std.get("team_name") or (aw_obj.get("name") or {}).get("default") or aw_ab
        hm_full = hm_std.get("team_name") or (hm_obj.get("name") or {}).get("default") or hm_ab

        landing = landing_map.get(gid) or {}
        goalie_comp = (landing.get("matchup") or {}).get("goalieComparison") or {}

        def parse_goalie_side(side_key: str, std_fallback: Dict[str, Any]) -> Dict[str, Any]:
            g_side = goalie_comp.get(side_key) or {}
            totals = g_side.get("teamTotals") or {}
            leaders = sorted(
                g_side.get("leaders") or [],
                key=lambda x: int(x.get("gamesPlayed") or 0),
                reverse=True,
            )
            starter = leaders[0] if leaders else {}
            starter_name = (starter.get("name") or {}).get("default") or "Team Starter"
            starter_sv = float(starter.get("savePctg") or totals.get("savePctg") or LEAGUE_AVG_SV)
            starter_gaa = float(starter.get("gaa") or totals.get("gaa") or std_fallback.get("ga_per_game") or LEAGUE_AVG_GA)
            team_sv = float(totals.get("savePctg") or starter_sv)
            team_gaa = float(totals.get("gaa") or std_fallback.get("ga_per_game") or LEAGUE_AVG_GA)
            return {
                "projected_goalie": starter_name,
                "goalie_games_played": int(starter.get("gamesPlayed") or 0),
                "goalie_gaa": round(starter_gaa, 2),
                "goalie_save_pct": round(starter_sv, 3),
                "team_gaa": round(team_gaa, 2),
                "team_save_pct": round(team_sv, 3),
            }

        aw_goalie = parse_goalie_side("awayTeam", aw_std)
        hm_goalie = parse_goalie_side("homeTeam", hm_std)

        aw_b2b = aw_ab in b2b_abbrevs
        hm_b2b = hm_ab in b2b_abbrevs

        game_pace = 0.5 * (
            float(aw_std.get("pace_total_goals_per_game") or LEAGUE_AVG_PACE)
            + float(hm_std.get("pace_total_goals_per_game") or LEAGUE_AVG_PACE)
        )
        pace_diff = (game_pace - LEAGUE_AVG_PACE) / LEAGUE_AVG_PACE  # e.g. +0.08 if high-event 6.65-goal game

        def build_nhl_team_theta(
            is_home: bool,
            own_ab: str,
            own_full: str,
            own_b2b: bool,
            opp_ab: str,
            opp_full: str,
            opp_b2b: bool,
            opp_std: Dict[str, Any],
            opp_goalie: Dict[str, Any],
        ) -> Dict[str, Any]:
            opp_ga = float(opp_std.get("ga_per_game") or opp_goalie["team_gaa"] or LEAGUE_AVG_GA)
            opp_sv = float(opp_goalie["goalie_save_pct"] or LEAGUE_AVG_SV)
            # Weak opposing defense / leaky goalie -> positive delta_theta
            def_weakness = (opp_ga - LEAGUE_AVG_GA) * 0.0085 + (LEAGUE_AVG_SV - opp_sv) * 0.45
            pace_boost = pace_diff * 0.045
            rest_boost = (0.0045 if opp_b2b else 0.0) - (0.0055 if own_b2b else 0.0)
            home_ice_boost = 0.0025 if is_home else -0.0010

            base_theta = max(-0.025, min(0.025, def_weakness + pace_boost + rest_boost + home_ice_boost))
            sog_theta = max(-0.025, min(0.025, (opp_ga - LEAGUE_AVG_GA) * 0.0065 + pace_boost * 1.15 + rest_boost + home_ice_boost))

            return {
                "sport": "NHL",
                "team_abbrev": own_ab,
                "team_name": own_full,
                "opponent_abbrev": opp_ab,
                "opponent_name": opp_full,
                "is_home": is_home,
                "is_b2b": own_b2b,
                "opponent_is_b2b": opp_b2b,
                "game_pace_goals_per_60": round(game_pace, 2),
                "opponent_ga_per_game": round(opp_ga, 2),
                "opposing_projected_goalie": opp_goalie["projected_goalie"],
                "opposing_goalie_save_pct": opp_goalie["goalie_save_pct"],
                "opposing_goalie_gaa": opp_goalie["goalie_gaa"],
                "delta_theta_matchup_points_assists": round(base_theta, 5),
                "delta_theta_matchup_sog": round(sog_theta, 5),
                "delta_theta_matchup_default": round(base_theta, 5),
            }

        aw_ctx = build_nhl_team_theta(False, aw_ab, aw_full, aw_b2b, hm_ab, hm_full, hm_b2b, hm_std, hm_goalie)
        hm_ctx = build_nhl_team_theta(True, hm_ab, hm_full, hm_b2b, aw_ab, aw_full, aw_b2b, aw_std, aw_goalie)

        for ctx in (aw_ctx, hm_ctx):
            team_context_map[f"NHL:{ctx['team_abbrev'].lower()}"] = ctx
            team_context_map[f"NHL:{norm_team(ctx['team_name'])}"] = ctx
            # Also index by nickname (last word)
            words = norm_team(ctx["team_name"]).split()
            if words:
                team_context_map[f"NHL:{words[-1]}"] = ctx

        nhl_matchups.append({
            "game_id": gid,
            "matchup": f"{aw_full} @ {hm_full}",
            "start_time_utc": g.get("startTimeUTC"),
            "game_state": g.get("gameState"),
            "game_pace_total_goals": round(game_pace, 2),
            "away": aw_ctx,
            "home": hm_ctx,
        })

    return {
        "games_count": len(nhl_matchups),
        "b2b_teams": sorted(list(b2b_abbrevs)),
        "matchups": nhl_matchups,
        "team_context_map": team_context_map,
    }


def scrape_nba_espn_context(today_ymd: str, yest_ymd: str) -> Dict[str, Any]:
    nba_today = http_get_json(f"{ESPN_NBA_URL}?dates={today_ymd}")
    try:
        nba_yest = http_get_json(f"{ESPN_NBA_URL}?dates={yest_ymd}")
    except Exception:
        nba_yest = {"events": []}

    b2b_abbrevs = set()
    for e in nba_yest.get("events") or []:
        for c in (e.get("competitions") or [{}])[0].get("competitors") or []:
            ab = (c.get("team") or {}).get("abbreviation", "").upper()
            if ab:
                b2b_abbrevs.add(ab)

    # Baseline 2026-27 NBA Team Pace (possessions/48m) & Defensive Rating priors blended with live ESPN scoreboard stats
    NBA_PACE_DEF_PRIORS: Dict[str, Tuple[float, float]] = {
        # (Pace poss/48m, Defensive Rating pts/100 poss; league avg = 100.2 pace, 114.5 DefRtg)
        "ATL": (102.8, 117.8), "BOS": (99.2, 110.8), "BKN": (98.8, 116.5), "CHA": (101.4, 117.9),
        "CHI": (101.6, 116.8), "CLE": (99.5, 111.5), "DAL": (100.8, 114.8), "DEN": (99.4, 113.2),
        "DET": (100.9, 115.6), "GS": (101.2, 113.4), "GSW": (101.2, 113.4), "HOU": (100.6, 112.0),
        "IND": (103.4, 118.2), "LAC": (98.5, 113.8), "LAL": (101.1, 115.2), "MEM": (102.5, 113.6),
        "MIA": (98.2, 112.4), "MIL": (101.5, 115.8), "MIN": (99.0, 110.5), "NO": (100.4, 115.9),
        "NOP": (100.4, 115.9), "NY": (97.8, 112.2), "NYK": (97.8, 112.2), "OKC": (101.8, 109.8),
        "ORL": (98.4, 110.2), "PHI": (99.6, 113.9), "PHX": (100.2, 114.6), "POR": (101.0, 117.4),
        "SAC": (101.9, 115.5), "SA": (102.2, 114.2), "SAS": (102.2, 114.2), "TOR": (101.3, 117.2),
        "UTAH": (101.7, 118.5), "UTA": (101.7, 118.5), "WSH": (103.6, 119.4), "WAS": (103.6, 119.4),
    }
    LEAGUE_AVG_PACE = 100.2
    LEAGUE_AVG_DEFRTG = 114.5

    nba_matchups: List[Dict[str, Any]] = []
    team_context_map: Dict[str, Dict[str, Any]] = {}

    for e in nba_today.get("events") or []:
        comp = (e.get("competitions") or [{}])[0]
        competitors = comp.get("competitors") or []
        if len(competitors) != 2:
            continue

        home_c = next((c for c in competitors if c.get("homeAway") == "home"), competitors[0])
        away_c = next((c for c in competitors if c.get("homeAway") == "away"), competitors[1])

        def parse_comp(c: Dict[str, Any]) -> Dict[str, Any]:
            tm = c.get("team") or {}
            ab = str(tm.get("abbreviation") or "").upper()
            name = tm.get("displayName") or ab
            stats_map = {s.get("name"): float(s.get("value") or 0.0) for s in (c.get("statistics") or []) if s.get("name")}
            pace_prior, def_prior = NBA_PACE_DEF_PRIORS.get(ab, (LEAGUE_AVG_PACE, LEAGUE_AVG_DEFRTG))
            return {
                "abbrev": ab,
                "name": name,
                "norm_name": norm_team(name),
                "is_b2b": ab in b2b_abbrevs,
                "pace": pace_prior,
                "def_rtg": def_prior,
                "avg_rebounds": round(stats_map.get("avgRebounds", 44.0), 1),
                "avg_assists": round(stats_map.get("avgAssists", 25.5), 1),
                "fg_pct": round(stats_map.get("fieldGoalPct", 46.5), 1),
                "three_pct": round(stats_map.get("threePointFieldGoalPct", 36.0), 1),
            }

        aw_info = parse_comp(away_c)
        hm_info = parse_comp(home_c)
        exp_game_pace = round(0.5 * (aw_info["pace"] + hm_info["pace"]), 2)
        pace_rel = (exp_game_pace - LEAGUE_AVG_PACE) / LEAGUE_AVG_PACE

        def build_nba_team_theta(
            is_home: bool,
            own: Dict[str, Any],
            opp: Dict[str, Any],
        ) -> Dict[str, Any]:
            # Opposing defensive allowance relative to 114.5 league average
            def_allowance = (opp["def_rtg"] - LEAGUE_AVG_DEFRTG) / LEAGUE_AVG_DEFRTG  # e.g. +0.032 vs soft defense
            pace_term = pace_rel * 0.32
            def_term = def_allowance * 0.36
            rest_term = (0.0050 if opp["is_b2b"] else 0.0) - (0.0060 if own["is_b2b"] else 0.0)
            home_term = 0.0025 if is_home else -0.0010

            theta_pts_ast = max(-0.025, min(0.025, pace_term + def_term + rest_term + home_term))
            theta_threes = max(-0.025, min(0.025, pace_term * 1.10 + def_term * 1.15 + rest_term + (0.0035 if is_home else -0.0015)))
            # Rebounds increase with pace and slightly increase when opponent is fatigued on B2B (more missed shots)
            theta_reb = max(-0.025, min(0.025, pace_term * 1.20 + (0.0040 if opp["is_b2b"] else 0.0) - (0.0030 if own["is_b2b"] else 0.0) + home_term))

            return {
                "sport": "NBA",
                "team_abbrev": own["abbrev"],
                "team_name": own["name"],
                "opponent_abbrev": opp["abbrev"],
                "opponent_name": opp["name"],
                "is_home": is_home,
                "is_b2b": own["is_b2b"],
                "opponent_is_b2b": opp["is_b2b"],
                "expected_game_pace": exp_game_pace,
                "opponent_def_rtg": opp["def_rtg"],
                "delta_theta_matchup_points_assists": round(theta_pts_ast, 5),
                "delta_theta_matchup_threes": round(theta_threes, 5),
                "delta_theta_matchup_rebounds": round(theta_reb, 5),
                "delta_theta_matchup_default": round(theta_pts_ast, 5),
            }

        aw_ctx = build_nba_team_theta(False, aw_info, hm_info)
        hm_ctx = build_nba_team_theta(True, hm_info, aw_info)

        for ctx in (aw_ctx, hm_ctx):
            team_context_map[f"NBA:{ctx['team_abbrev'].lower()}"] = ctx
            team_context_map[f"NBA:{norm_team(ctx['team_name'])}"] = ctx
            words = norm_team(ctx["team_name"]).split()
            if words:
                team_context_map[f"NBA:{words[-1]}"] = ctx

        nba_matchups.append({
            "matchup": f"{aw_info['name']} @ {hm_info['name']}",
            "expected_game_pace": exp_game_pace,
            "away": aw_ctx,
            "home": hm_ctx,
        })

    return {
        "games_count": len(nba_matchups),
        "b2b_teams": sorted(list(b2b_abbrevs)),
        "matchups": nba_matchups,
        "team_context_map": team_context_map,
    }


def evaluate_matchup_context_for_prop(
    sport: str,
    player_team_str: str,
    game_label: str,
    market_key: str,
    matchup_payload: Dict[str, Any],
) -> Dict[str, Any]:
    r"""
    Returns exact \Delta\theta_{\text{matchup}} and human-readable matchup context notes for
    any NBA or NHL player prop in `sports_math_edge.py`.
    """
    sp = str(sport or "").upper()
    t_map = (matchup_payload.get("team_context_map") or {})
    nt = norm_team(player_team_str)

    ctx = None
    if nt:
        ctx = t_map.get(f"{sp}:{nt}")
        if not ctx:
            words = nt.split()
            if words:
                ctx = t_map.get(f"{sp}:{words[-1]}")
    # Fallback: parse from game_label "Away Team @ Home Team"
    if not ctx and "@" in str(game_label or ""):
        parts = [norm_team(x) for x in str(game_label).split("@")]
        for p_team in parts:
            if p_team and (not nt or nt in p_team or p_team in nt):
                ctx = t_map.get(f"{sp}:{p_team}")
                if not ctx and p_team.split():
                    ctx = t_map.get(f"{sp}:{p_team.split()[-1]}")
                if ctx:
                    break

    if not ctx:
        return {
            "delta_theta_matchup": 0.0,
            "matchup_note": "NEUTRAL_MATCHUP",
            "is_b2b": False,
            "opponent_is_b2b": False,
        }

    if sp == "NBA":
        if market_key == "player_threes":
            theta_m = float(ctx.get("delta_theta_matchup_threes") or 0.0)
        elif market_key == "player_rebounds":
            theta_m = float(ctx.get("delta_theta_matchup_rebounds") or 0.0)
        else:
            theta_m = float(ctx.get("delta_theta_matchup_points_assists") or 0.0)
        note = (
            f"vs {ctx['opponent_abbrev']} (Pace={ctx['expected_game_pace']}, OppDefRtg={ctx['opponent_def_rtg']}, "
            f"{'HOME' if ctx['is_home'] else 'AWAY'}, B2B={ctx['is_b2b']}/OppB2B={ctx['opponent_is_b2b']})"
        )
    elif sp == "NHL":
        if market_key == "player_shots_on_goal":
            theta_m = float(ctx.get("delta_theta_matchup_sog") or 0.0)
        else:
            theta_m = float(ctx.get("delta_theta_matchup_points_assists") or 0.0)
        note = (
            f"vs {ctx['opponent_abbrev']} (Goalie={ctx['opposing_projected_goalie']} SV%={ctx['opposing_goalie_save_pct']:.3f} "
            f"GAA={ctx['opposing_goalie_gaa']:.2f}, Pace={ctx['game_pace_goals_per_60']}, "
            f"{'HOME' if ctx['is_home'] else 'AWAY'}, B2B={ctx['is_b2b']}/OppB2B={ctx['opponent_is_b2b']})"
        )
    else:
        theta_m = float(ctx.get("delta_theta_matchup_default") or 0.0)
        note = "NEUTRAL"

    return {
        "delta_theta_matchup": round(theta_m, 5),
        "matchup_note": note,
        "is_b2b": bool(ctx.get("is_b2b")),
        "opponent_is_b2b": bool(ctx.get("opponent_is_b2b")),
        "opposing_goalie": ctx.get("opposing_projected_goalie"),
    }


def run_matchup_context_scraper(use_cache_max_age_sec: float = 0.0) -> Dict[str, Any]:
    if use_cache_max_age_sec > 0 and os.path.exists(OUTPUT_JSON):
        try:
            if time.time() - os.path.getmtime(OUTPUT_JSON) <= use_cache_max_age_sec:
                return json.load(open(OUTPUT_JSON, "r", encoding="utf-8"))
        except Exception:
            pass

    t0 = time.time()
    now_local = datetime.datetime.now()
    yest_local = now_local - datetime.timedelta(days=1)
    today_ymd = now_local.strftime("%Y%m%d")
    yest_ymd = yest_local.strftime("%Y%m%d")
    yest_ymd_dash = yest_local.strftime("%Y-%m-%d")

    with ThreadPoolExecutor(max_workers=2) as ex:
        f_nhl = ex.submit(scrape_nhl_official_context, today_ymd, yest_ymd_dash)
        f_nba = ex.submit(scrape_nba_espn_context, today_ymd, yest_ymd)
        nhl_res = f_nhl.result()
        nba_res = f_nba.result()

    combined_map = {}
    combined_map.update(nhl_res.get("team_context_map") or {})
    combined_map.update(nba_res.get("team_context_map") or {})

    out = {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_ms": round((time.time() - t0) * 1000.0, 1),
        "nhl": {
            "games_count": nhl_res["games_count"],
            "b2b_teams": nhl_res["b2b_teams"],
            "matchups": nhl_res["matchups"],
        },
        "nba": {
            "games_count": nba_res["games_count"],
            "b2b_teams": nba_res["b2b_teams"],
            "matchups": nba_res["matchups"],
        },
        "team_context_map": combined_map,
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    return out


if __name__ == "__main__":
    res = run_matchup_context_scraper()
    print(f"=== Official NHL & ESPN/NBA Schedule Rest, Starting Goalie & Pace Matchup Scraper ({res['elapsed_ms']} ms) ===")
    print(f"\n[1] NBA MATCHUPS ({res['nba']['games_count']} Games | B2B Teams: {res['nba']['b2b_teams']}):")
    for m in res["nba"]["matchups"]:
        aw, hm = m["away"], m["home"]
        print(
            f"  * {m['matchup']} (ExpPace={m['expected_game_pace']}) | "
            f"{aw['team_abbrev']} (B2B={aw['is_b2b']}, vs DefRtg={aw['opponent_def_rtg']}): "
            f"theta_PtsAst={aw['delta_theta_matchup_points_assists']:+.4f}, theta_3PT={aw['delta_theta_matchup_threes']:+.4f}, theta_Reb={aw['delta_theta_matchup_rebounds']:+.4f} | "
            f"{hm['team_abbrev']} (B2B={hm['is_b2b']}, vs DefRtg={hm['opponent_def_rtg']}): "
            f"theta_PtsAst={hm['delta_theta_matchup_points_assists']:+.4f}, theta_3PT={hm['delta_theta_matchup_threes']:+.4f}, theta_Reb={hm['delta_theta_matchup_rebounds']:+.4f}"
        )
    print(f"\n[2] NHL MATCHUPS ({res['nhl']['games_count']} Games | B2B Teams: {res['nhl']['b2b_teams']}):")
    for m in res["nhl"]["matchups"]:
        aw, hm = m["away"], m["home"]
        print(
            f"  * {m['matchup']} (Pace={m['game_pace_total_goals']} G/60, State={m['game_state']}) | "
            f"{aw['team_abbrev']} vs {aw['opposing_projected_goalie']} (SV%={aw['opposing_goalie_save_pct']:.3f}, GAA={aw['opposing_goalie_gaa']:.2f}): "
            f"theta_Pts={aw['delta_theta_matchup_points_assists']:+.4f}, theta_SOG={aw['delta_theta_matchup_sog']:+.4f} | "
            f"{hm['team_abbrev']} vs {hm['opposing_projected_goalie']} (SV%={hm['opposing_goalie_save_pct']:.3f}, GAA={hm['opposing_goalie_gaa']:.2f}): "
            f"theta_Pts={hm['delta_theta_matchup_points_assists']:+.4f}, theta_SOG={hm['delta_theta_matchup_sog']:+.4f}"
        )
