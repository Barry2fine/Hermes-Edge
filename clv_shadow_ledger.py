#!/usr/bin/env python3
"""
Closing Line Value (CLV) Shadow Ledger & Empirical-Bernstein Auto-Recalibrator
File: /home/box/desk/clv_shadow_ledger.py

Maintains `/home/box/desk/clv_ledger.json`:
1. Snapshots every `STRICT_PASS` prop and top near-miss prop with entry timestamp,
   bet365 Ontario entry odds & implied breakeven probability, Pinnacle no-vig fair
   probability, Polymarket CLOB team probability, Entropic-Tilted p_tilted, and p_LCB.
2. On every subsequent scan, compares current Pinnacle no-vig and Entropic-Tilted
   probabilities against entry to compute:
     - CLV_bps = (p_Pinnacle_now - p_bet365_entry_implied) * 10000
     - Tilted_CLV_bps = (p_tilted_now - p_bet365_entry_implied) * 10000
     - Intraday_Steam_bps = (p_tilted_now - p_tilted_entry) * 10000
3. Auto-Calibration Feedback Loop:
   - Dynamically tunes the Empirical-Bernstein confidence parameter delta_market in [0.11, 0.18]
     per market family (`player_assists`, `player_threes`, `player_rebounds`, `player_points`,
     `player_shots_on_goal`) based on realized CLV capture and false-positive rejection rates.
"""

import collections
import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List

CLV_LEDGER_PATH = "/home/box/desk/clv_ledger.json"

DEFAULT_MARKET_DELTAS: Dict[str, float] = {
    "player_assists": 0.155,
    "player_threes": 0.150,
    "player_rebounds": 0.145,
    "player_points": 0.140,
    "player_shots_on_goal": 0.145,
    "player_pass_yds": 0.140,
    "player_rush_yds": 0.140,
    "player_reception_yds": 0.140,
    "pitcher_strikeouts": 0.140,
    "batter_total_bases": 0.135,
}


def load_clv_ledger() -> Dict[str, Any]:
    if os.path.exists(CLV_LEDGER_PATH):
        try:
            with open(CLV_LEDGER_PATH, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "updated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "total_runs": 0,
        "calibrated_market_deltas": dict(DEFAULT_MARKET_DELTAS),
        "market_calibration_stats": {},
        "entries": {},
    }


def get_calibrated_deltas() -> Dict[str, float]:
    ledger = load_clv_ledger()
    deltas = dict(DEFAULT_MARKET_DELTAS)
    deltas.update(ledger.get("calibrated_market_deltas") or {})
    return deltas


def update_clv_ledger(all_props: List[Dict[str, Any]]) -> Dict[str, Any]:
    ledger = load_clv_ledger()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    ledger["updated_at"] = now_iso
    ledger["total_runs"] = int(ledger.get("total_runs", 0)) + 1

    entries: Dict[str, Dict[str, Any]] = ledger.setdefault("entries", {})

    # Track all STRICT_PASS props plus top near-miss props (raw_ev_pct >= 3.0%)
    tracked_props = [
        p for p in all_props
        if p.get("gate_status") == "STRICT_PASS" or p.get("raw_ev_pct", -99.0) >= 3.0
    ]

    for p in tracked_props:
        pid = f"{p['sport']}|{p['game']}|{p['player']}|{p['prop']}"
        pin_now = float(p.get("pinnacle_fair_prob_pct", p.get("sharp_consensus_prob_pct", 50.0)))
        tilt_now = float(p.get("p_tilted_pct", 50.0))
        lcb_now = float(p.get("p_lcb_pct", 45.0))
        b365_be = float(p.get("b365_breakeven_pct", 50.0))
        poly_now = p.get("poly_team_clob_prob_pct")

        if pid not in entries:
            entries[pid] = {
                "prop_id": pid,
                "sport": p["sport"],
                "game": p["game"],
                "player": p["player"],
                "player_team": p.get("player_team", ""),
                "prop": p["prop"],
                "market_key": p.get("market_key", ""),
                "entry_ts": now_iso,
                "entry_b365_american": p["b365_american"],
                "entry_b365_decimal": p["b365_decimal"],
                "entry_b365_breakeven_pct": b365_be,
                "entry_pinnacle_fair_pct": pin_now,
                "entry_poly_team_prob_pct": poly_now,
                "entry_p_tilted_pct": tilt_now,
                "entry_p_lcb_pct": lcb_now,
                "latest_ts": now_iso,
                "latest_b365_american": p["b365_american"],
                "latest_pinnacle_fair_pct": pin_now,
                "latest_poly_team_prob_pct": poly_now,
                "latest_p_tilted_pct": tilt_now,
                "latest_p_lcb_pct": lcb_now,
                "clv_pinnacle_vs_b365_entry_bps": round((pin_now - b365_be) * 100.0, 1),
                "clv_tilted_vs_b365_entry_bps": round((tilt_now - b365_be) * 100.0, 1),
                "clv_lcb_vs_b365_entry_bps": round((lcb_now - b365_be) * 100.0, 1),
                "intraday_steam_drift_bps": 0.0,
                "gate_status": p["gate_status"],
                "kelly_stake_usd": p.get("kelly_stake_usd", 0.0),
                "observations": 1,
            }
        else:
            e = entries[pid]
            e["latest_ts"] = now_iso
            e["latest_b365_american"] = p["b365_american"]
            e["latest_pinnacle_fair_pct"] = pin_now
            e["latest_poly_team_prob_pct"] = poly_now
            e["latest_p_tilted_pct"] = tilt_now
            e["latest_p_lcb_pct"] = lcb_now
            e["clv_pinnacle_vs_b365_entry_bps"] = round((pin_now - e["entry_b365_breakeven_pct"]) * 100.0, 1)
            e["clv_tilted_vs_b365_entry_bps"] = round((tilt_now - e["entry_b365_breakeven_pct"]) * 100.0, 1)
            e["clv_lcb_vs_b365_entry_bps"] = round((lcb_now - e["entry_b365_breakeven_pct"]) * 100.0, 1)
            e["intraday_steam_drift_bps"] = round((tilt_now - e["entry_p_tilted_pct"]) * 100.0, 1)
            e["gate_status"] = p["gate_status"]
            e["kelly_stake_usd"] = p.get("kelly_stake_usd", 0.0)
            e["observations"] = int(e.get("observations", 1)) + 1

    # Auto-Calibration Feedback Loop across all evaluated props by market family
    by_mkt: Dict[str, List[Dict[str, Any]]] = collections.defaultdict(list)
    for p in all_props:
        by_mkt[p.get("market_key", "other")].append(p)

    calibrated_deltas: Dict[str, float] = {}
    cal_stats: Dict[str, Any] = {}
    for mkt, m_props in by_mkt.items():
        base_d = DEFAULT_MARKET_DELTAS.get(mkt, 0.145)
        raw_pos = [x for x in m_props if x.get("raw_ev_pct", 0.0) > 0]
        strict_pass = [x for x in m_props if x.get("gate_status") == "STRICT_PASS"]
        if strict_pass:
            mean_clv_bps = sum((x["p_tilted_pct"] - x["b365_breakeven_pct"]) * 100.0 for x in strict_pass) / len(strict_pass)
        elif raw_pos:
            mean_clv_bps = sum((x["p_tilted_pct"] - x["b365_breakeven_pct"]) * 100.0 for x in raw_pos) / len(raw_pos)
        else:
            mean_clv_bps = 0.0

        false_pos_rate = (
            (len(raw_pos) - len(strict_pass)) / len(raw_pos)
            if len(raw_pos) > 0 else 1.0
        )

        # Tune delta_market: reward high CLV (>250 bps) up to +0.015, penalize high false-positive dispersion
        adj = 0.0
        if mean_clv_bps >= 350.0 and len(strict_pass) >= 1:
            adj += 0.012
        elif mean_clv_bps >= 200.0 and len(strict_pass) >= 1:
            adj += 0.006
        elif mean_clv_bps < 100.0:
            adj -= 0.010

        if false_pos_rate > 0.92:
            adj -= 0.005

        new_delta = round(max(0.110, min(0.175, base_d + adj)), 4)
        calibrated_deltas[mkt] = new_delta
        cal_stats[mkt] = {
            "total_props": len(m_props),
            "raw_pos_ev_count": len(raw_pos),
            "strict_pass_count": len(strict_pass),
            "false_positive_rejection_rate_pct": round(false_pos_rate * 100.0, 1),
            "mean_edge_clv_bps": round(mean_clv_bps, 1),
            "base_delta": base_d,
            "calibrated_delta": new_delta,
        }

    ledger["calibrated_market_deltas"] = calibrated_deltas
    ledger["market_calibration_stats"] = cal_stats

    with open(CLV_LEDGER_PATH, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=2)

    return ledger
