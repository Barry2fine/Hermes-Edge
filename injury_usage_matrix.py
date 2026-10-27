#!/usr/bin/env python3
"""
Official NBA / NHL / NFL / MLB Real-Time Injury & Lineup Scratch Usage-Redistribution Matrix
File: /home/box/desk/injury_usage_matrix.py

Pulls live, zero-cost official injury and availability data from public ESPN APIs:
  - https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries
  - https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries
  - https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries
  - https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/injuries

Provides two mathematical layers for `sports_math_edge.py`:
1. Player Availability Sanity Gate:
   - Automatically VETOES (`NO_BET (INJURY_OR_ROSTER_VETO)`) any Over prop for a player
     who is listed as `Out`, `Injured Reserve`, `Suspended`, or `Doubtful`.
   - Applies a Self-Questionable Minute/Injury Penalty (delta_theta = -0.035 and +1.5%
     Empirical-Bernstein variance haircut) to any player listed as `Questionable` (e.g. Max Christie).
2. On/Off Teammate Usage & Minute Redistribution Matrix:
   - Computes the exact exponential-tilting boost `delta_theta_injury` for remaining active starters
     when high-usage teammates are `Out`, `Injured Reserve`, or `Doubtful`.
"""

import json
import os
import re
import time
import unicodedata
import urllib.request
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

INJURY_CACHE_PATH = "/home/box/desk/latest_injuries_matrix.json"

ESPN_INJURY_URLS = {
    "NBA": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries",
    "NHL": "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries",
    "NFL": "https://site.api.espn.com/apis/site/v2/sports/football/nfl/injuries",
    "MLB": "https://site.api.espn.com/apis/site/v2/sports/baseball/mlb/injuries",
}

# High-usage / high-minute impact weights (0.0 to 1.0 scale of team usage/minute vacating)
# Stars and primary starters carry higher redistribution weight when ruled Out/IR/Doubtful.
HIGH_IMPACT_PLAYERS: Dict[str, Dict[str, Any]] = {
    # NBA
    "lebron james": {"tier": "SUPERSTAR_CREATOR", "usage_weight": 0.95, "boost_markets": ["player_assists", "player_points", "player_rebounds", "player_threes"]},
    "stephen curry": {"tier": "SUPERSTAR_CREATOR", "usage_weight": 0.98, "boost_markets": ["player_points", "player_assists", "player_threes"]},
    "jimmy butler": {"tier": "STAR_WING", "usage_weight": 0.82, "boost_markets": ["player_points", "player_assists", "player_rebounds"]},
    "kristaps porzingis": {"tier": "STAR_BIG", "usage_weight": 0.75, "boost_markets": ["player_rebounds", "player_points", "player_threes"]},
    "brandin podziemski": {"tier": "STARTER_GUARD", "usage_weight": 0.58, "boost_markets": ["player_assists", "player_threes", "player_rebounds"]},
    "fred vanvleet": {"tier": "STARTER_PG", "usage_weight": 0.78, "boost_markets": ["player_assists", "player_points", "player_threes"]},
    "steven adams": {"tier": "ROTATION_BIG", "usage_weight": 0.48, "boost_markets": ["player_rebounds"]},
    "cameron johnson": {"tier": "STARTER_WING", "usage_weight": 0.68, "boost_markets": ["player_threes", "player_points", "player_rebounds"]},
    "julian strawther": {"tier": "ROTATION_WING", "usage_weight": 0.45, "boost_markets": ["player_threes", "player_points"]},
    "cooper flagg": {"tier": "STAR_FORWARD", "usage_weight": 0.80, "boost_markets": ["player_points", "player_rebounds", "player_assists", "player_threes"]},
    "dereck lively": {"tier": "STARTER_BIG", "usage_weight": 0.65, "boost_markets": ["player_rebounds", "player_points"]},
    "kyrie irving": {"tier": "STAR_GUARD", "usage_weight": 0.88, "boost_markets": ["player_assists", "player_points", "player_threes"]},
    "caleb martin": {"tier": "ROTATION_WING", "usage_weight": 0.48, "boost_markets": ["player_threes", "player_rebounds"]},
    "santi aldama": {"tier": "ROTATION_BIG", "usage_weight": 0.50, "boost_markets": ["player_rebounds", "player_threes"]},
    "coby white": {"tier": "STARTER_GUARD", "usage_weight": 0.74, "boost_markets": ["player_assists", "player_points", "player_threes"]},
    "grant williams": {"tier": "ROTATION_FORWARD", "usage_weight": 0.45, "boost_markets": ["player_rebounds", "player_threes"]},
    "kon knueppel": {"tier": "ROTATION_WING", "usage_weight": 0.48, "boost_markets": ["player_threes", "player_points"]},
    "nic claxton": {"tier": "STARTER_BIG", "usage_weight": 0.68, "boost_markets": ["player_rebounds", "player_points"]},
    "aj dybantsa": {"tier": "STAR_FORWARD", "usage_weight": 0.82, "boost_markets": ["player_points", "player_rebounds", "player_assists", "player_threes"]},
    "tobias harris": {"tier": "STARTER_FORWARD", "usage_weight": 0.66, "boost_markets": ["player_rebounds", "player_points", "player_threes"]},
    "mark williams": {"tier": "STARTER_BIG", "usage_weight": 0.65, "boost_markets": ["player_rebounds", "player_points"]},
    "mouhamed gueye": {"tier": "ROTATION_BIG", "usage_weight": 0.42, "boost_markets": ["player_rebounds", "player_threes"]},
    "henri veesaar": {"tier": "ROTATION_BIG", "usage_weight": 0.38, "boost_markets": ["player_rebounds"]},
    # NHL
    "brock faber": {"tier": "TOP_PAIR_D", "usage_weight": 0.82, "boost_markets": ["player_points", "player_assists", "player_shots_on_goal"]},
    "marcus foligno": {"tier": "MID_SIX_F", "usage_weight": 0.42, "boost_markets": ["player_points", "player_shots_on_goal"]},
    "mitch marner": {"tier": "ELITE_PLAYMAKER", "usage_weight": 0.90, "boost_markets": ["player_points", "player_assists", "player_shots_on_goal"]},
    "jared mccann": {"tier": "TOP_LINE_SCORER", "usage_weight": 0.78, "boost_markets": ["player_shots_on_goal", "player_points", "player_assists"]},
    "ryan lindgren": {"tier": "TOP_FOUR_D", "usage_weight": 0.52, "boost_markets": ["player_points", "player_shots_on_goal"]},
    "mattias ekholm": {"tier": "TOP_PAIR_D", "usage_weight": 0.72, "boost_markets": ["player_points", "player_assists", "player_shots_on_goal"]},
    "john tavares": {"tier": "TOP_SIX_C", "usage_weight": 0.80, "boost_markets": ["player_points", "player_assists", "player_shots_on_goal"]},
    "chris tanev": {"tier": "TOP_FOUR_D", "usage_weight": 0.55, "boost_markets": ["player_shots_on_goal", "player_points"]},
    "evgeni malkin": {"tier": "TOP_SIX_C", "usage_weight": 0.82, "boost_markets": ["player_points", "player_assists", "player_shots_on_goal"]},
    "bryan rust": {"tier": "TOP_LINE_W", "usage_weight": 0.74, "boost_markets": ["player_shots_on_goal", "player_points"]},
}


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z ]", "", s)
    s = re.sub(r"\b(jr|sr|ii|iii|iv)\b", "", s)
    return " ".join(s.split())


def norm_team(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return " ".join(s.split())


def classify_availability(status_raw: str, short_comment: str) -> str:
    st = (status_raw or "").strip().lower()
    sc = (short_comment or "").strip().lower()

    if st == "active":
        return "ACTIVE"
    if st in ("out", "injured reserve", "ir", "suspension", "suspended"):
        return "OUT"
    if st == "doubtful":
        return "DOUBTFUL"
    if st == "questionable":
        return "QUESTIONABLE"
    if st == "probable":
        return "PROBABLE"
    if st == "day-to-day":
        if "ruled out" in sc or "won't play" in sc or "will not play" in sc:
            return "OUT"
        if "doubtful" in sc or "probably not" in sc:
            return "DOUBTFUL"
        if "probable" in sc:
            return "PROBABLE"
        return "QUESTIONABLE"
    return "ACTIVE"


def fetch_live_espn_injuries(use_cache_max_age_sec: float = 300.0) -> Dict[str, Any]:
    if os.path.exists(INJURY_CACHE_PATH):
        age = time.time() - os.path.getmtime(INJURY_CACHE_PATH)
        if age <= use_cache_max_age_sec:
            try:
                with open(INJURY_CACHE_PATH, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                pass

    player_index: Dict[str, Dict[str, Any]] = {}
    team_index: Dict[str, Dict[str, Any]] = {}

    for sport, url in ESPN_INJURY_URLS.items():
        try:
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 SportsMathEdge/2.0"})
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception:
            continue

        for t in data.get("injuries") or []:
            tname = t.get("displayName") or ""
            nt = norm_team(tname)
            t_key = f"{sport}:{nt}"
            team_entry = {
                "sport": sport,
                "team_name": tname,
                "norm_team": nt,
                "out_players": [],
                "doubtful_players": [],
                "questionable_players": [],
                "probable_players": [],
                "vacated_usage_index": 0.0,
                "market_redistribution_theta": {
                    "player_points": 0.0,
                    "player_assists": 0.0,
                    "player_rebounds": 0.0,
                    "player_threes": 0.0,
                    "player_shots_on_goal": 0.0,
                    "player_pass_yds": 0.0,
                    "player_rush_yds": 0.0,
                    "player_reception_yds": 0.0,
                },
            }

            for inj in t.get("injuries") or []:
                ath = inj.get("athlete") or {}
                pname = ath.get("displayName") or ""
                if not pname:
                    continue
                np = norm_name(pname)
                pos = (ath.get("position") or {}).get("abbreviation") or ""
                status_raw = inj.get("status") or ""
                short_c = inj.get("shortComment") or ""
                ret_date = (inj.get("details") or {}).get("returnDate") or ""
                inj_type = (inj.get("details") or {}).get("type") or ""

                avail = classify_availability(status_raw, short_c)
                hi_info = HIGH_IMPACT_PLAYERS.get(np)
                u_weight = hi_info["usage_weight"] if hi_info else 0.22
                tier = hi_info["tier"] if hi_info else "DEPTH_ROTATION"

                rec = {
                    "player": pname,
                    "norm_player": np,
                    "sport": sport,
                    "team": tname,
                    "position": pos,
                    "espn_status": status_raw,
                    "availability": avail,
                    "injury_type": inj_type,
                    "return_date": ret_date,
                    "tier": tier,
                    "usage_weight": u_weight,
                    "short_comment": short_c[:180],
                }
                player_index[f"{sport}:{np}"] = rec

                if avail == "OUT":
                    team_entry["out_players"].append(rec)
                    eff_w = u_weight * 1.0
                elif avail == "DOUBTFUL":
                    team_entry["doubtful_players"].append(rec)
                    eff_w = u_weight * 0.75
                elif avail == "QUESTIONABLE":
                    team_entry["questionable_players"].append(rec)
                    eff_w = u_weight * 0.25 if hi_info else 0.0
                elif avail == "PROBABLE":
                    team_entry["probable_players"].append(rec)
                    eff_w = 0.0
                else:
                    eff_w = 0.0

                if eff_w > 0:
                    team_entry["vacated_usage_index"] = round(team_entry["vacated_usage_index"] + eff_w, 3)
                    boost_mkts = (
                        hi_info["boost_markets"]
                        if hi_info
                        else ["player_points", "player_rebounds", "player_assists", "player_threes", "player_shots_on_goal"]
                    )
                    for mkt in boost_mkts:
                        # Convert vacated teammate usage into an exponential tilt parameter delta_theta
                        # Capped at +0.032 (~+3.25% expectation boost) so projections stay calibrated
                        inc = 0.022 * eff_w if hi_info else 0.004 * eff_w
                        cur = team_entry["market_redistribution_theta"].get(mkt, 0.0)
                        team_entry["market_redistribution_theta"][mkt] = round(min(0.032, cur + inc), 5)

            team_index[t_key] = team_entry

    payload = {
        "as_of": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_indexed_players": len(player_index),
        "total_indexed_teams": len(team_index),
        "players": player_index,
        "teams": team_index,
    }
    with open(INJURY_CACHE_PATH, "w", encoding="utf-8") as f:
        json.dump(payload, f, indent=2)
    return payload


def match_team_injury_entry(
    sport: str,
    team_str: str,
    injury_payload: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    nt = norm_team(team_str)
    if not nt:
        return None
    teams_map = injury_payload.get("teams") or {}
    direct_key = f"{sport}:{nt}"
    if direct_key in teams_map:
        return teams_map[direct_key]

    words = [w for w in nt.split() if len(w) >= 4]
    if not words:
        return None
    nick = words[-1]
    for k, v in teams_map.items():
        if v.get("sport") != sport:
            continue
        if nick in v.get("norm_team", "").split():
            return v
    return None


def evaluate_player_injury_and_redistribution(
    sport: str,
    player_name: str,
    player_team_str: str,
    market_key: str,
    injury_payload: Dict[str, Any],
) -> Dict[str, Any]:
    """
    Returns:
      - `self_availability`: ACTIVE, PROBABLE, QUESTIONABLE, DOUBTFUL, or OUT
      - `veto_reason`: Non-None string if player is OUT or DOUBTFUL (triggers immediate NO_BET)
      - `delta_theta_injury`: Net exponential-tilt parameter adjustment (positive when key
        teammates are Out/Doubtful; negative when player themselves is Questionable)
      - `eb_variance_penalty`: Extra variance added to Empirical-Bernstein V_hat if Questionable
      - `key_missing_teammates`: Summary list of Out/Doubtful impact teammates on the player's team
    """
    np = norm_name(player_name)
    p_rec = (injury_payload.get("players") or {}).get(f"{sport}:{np}")
    t_rec = match_team_injury_entry(sport, player_team_str, injury_payload)

    self_avail = p_rec["availability"] if p_rec else "ACTIVE"
    self_comment = p_rec["short_comment"] if p_rec else ""
    self_type = p_rec["injury_type"] if p_rec else ""

    if self_avail in ("OUT", "DOUBTFUL"):
        return {
            "self_availability": self_avail,
            "self_injury_note": f"{self_avail} ({self_type}): {self_comment}",
            "veto_reason": f"NO_BET (INJURY_OR_ROSTER_VETO: {self_avail})",
            "delta_theta_injury": -1.0,
            "eb_variance_penalty": 0.05,
            "vacated_team_usage_index": t_rec["vacated_usage_index"] if t_rec else 0.0,
            "key_missing_teammates": [],
        }

    team_theta = 0.0
    missing_names = []
    vacated_idx = 0.0
    if t_rec:
        vacated_idx = t_rec.get("vacated_usage_index", 0.0)
        team_theta = (t_rec.get("market_redistribution_theta") or {}).get(market_key, 0.0)
        for m_p in (t_rec.get("out_players") or []) + (t_rec.get("doubtful_players") or []):
            if m_p["norm_player"] != np and m_p["usage_weight"] >= 0.35:
                missing_names.append(f"{m_p['player']} ({m_p['availability']})")

    # If the player themselves is QUESTIONABLE (e.g. Max Christie with shin soreness),
    # apply a self-injury minute-cap/re-aggravation penalty instead of a teammate boost!
    if self_avail == "QUESTIONABLE":
        net_theta = -0.028
        eb_var_pen = (0.018) ** 2
    else:
        net_theta = team_theta
        eb_var_pen = 0.0

    return {
        "self_availability": self_avail,
        "self_injury_note": f"{self_avail} ({self_type})" if self_type else self_avail,
        "veto_reason": None,
        "delta_theta_injury": round(net_theta, 5),
        "eb_variance_penalty": round(eb_var_pen, 6),
        "vacated_team_usage_index": vacated_idx,
        "key_missing_teammates": missing_names[:4],
    }


if __name__ == "__main__":
    payload = fetch_live_espn_injuries(use_cache_max_age_sec=0)
    print(f"Indexed {payload['total_indexed_players']} injured players across {payload['total_indexed_teams']} teams.")
    for tk, tv in sorted(payload["teams"].items(), key=lambda x: x[1]["vacated_usage_index"], reverse=True)[:12]:
        outs = [f"{p['player']} ({p['availability']}, w={p['usage_weight']})" for p in tv["out_players"] + tv["doubtful_players"]]
        quests = [f"{p['player']} (Q)" for p in tv["questionable_players"]]
        print(f"  [{tv['sport']}] {tv['team_name']}: vacated_idx={tv['vacated_usage_index']} | theta={tv['market_redistribution_theta']['player_assists']:+.4f} | Out/Doubtful={outs[:4]} | Quest={quests[:3]}")
