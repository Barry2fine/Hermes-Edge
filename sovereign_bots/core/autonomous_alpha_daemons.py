"""
Institutional Autonomous Alpha Daemons (`sovereign_bots/core/autonomous_alpha_daemons.py`).

Implements three zero-slop, end-to-end quantitative engines designed to run autonomously
on GitHub Actions (zero Grok Bot usage) and feed live edges into the 103-seat committee:

1. Pre-Tipoff Official Scratch & Teammate Spillover Engine
   (`evaluate_pre_tipoff_scratch_spillover`, `scan_live_espn_scratches_and_spillovers`):
   - Evaluates confirmed starter absences (`OUT` / `DOUBTFUL`) within the pre-tipoff window.
   -Applies exponential repricing decay (`exp(-ln(2) * elapsed_min / half_life_min)`) from
     `STAR_ABSENCE_SPILLOVER_TABLE` so stale news (>2 half-lives old) is automatically discounted
     or vetoed once `bet365 Ontario` and sharp books have already moved the prop line.
   - Projects exact Negative-Binomial fair probability, fair decimal odds, and EV% via
     `project_teammate_prop_under_star_absence`.

2. Cross-Exchange Funding & Basis Harvester Engine
   (`evaluate_cross_exchange_funding_and_basis`, `scan_live_kucoin_hyperliquid_funding_arb`):
   - Normalizes perpetual funding rates to an 8-hour basis (`funding_8h_bps`) and computes
     mark-to-index basis (`basis_bps = (mark_px - index_px) / index_px * 10000`) across
     `KuCoin Futures` and `Hyperliquid`.
   - Classifies each contract into:
     * `DIRECTIONAL_CONTRARIAN_CARRY_REVERSAL`: Extreme crowded funding (`|funding_8h_bps| >= 15.0`)
       confirmed by overextended mark-index basis (`sign(basis_bps) == sign(funding_8h_bps)`),
       yielding both 8h carry collection and basis mean-reversion via post-only maker entry.
     * `CROSS_VENUE_DELTA_NEUTRAL_FUNDING_ARB`: Cross-exchange spread
       `|kucoin_8h_bps - hyperliquid_8h_bps| >= 12.0 bps` with net positive APR after
       round-trip execution friction.
     * `NEUTRAL_NO_HARVEST`: Below institutional threshold after fees.

3. Nightly Post-Settlement CLV, Brier & Feature-Ablation Grader
   (`run_nightly_clv_brier_ablation_audit`):
   - Replays settled legs in `data/sports_postmortems.jsonl`, computes out-of-sample Brier Score,
     Brier Skill Score (`BSS`), Log-Loss, Expected Calibration Error (`ECE`), and mean CLV (bps).
   - Reports missing held-out feature evidence without generating or persisting weights.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from .market_evidence import assess_market, market_request, observation
import math
import urllib.request
from typing import Any, Dict, List, Optional, Sequence

from sovereign_bots.core.domain_tables import (
    STAR_ABSENCE_SPILLOVER_TABLE,
    project_teammate_prop_under_star_absence,
)
from sovereign_bots.core.walk_forward_calibrator import (
    replay_sports_postmortems,
)


def _finite_float(val: Any, name: str) -> float:
    if isinstance(val, bool) or val is None:
        raise ValueError(f"{name} must be a finite number")
    f = float(val)
    if not math.isfinite(f):
        raise ValueError(f"{name} must be finite")
    return f


# ============================================================================
# 1. PRE-TIPOFF OFFICIAL SCRATCH & TEAMMATE SPILLOVER ENGINE
# ============================================================================

def evaluate_pre_tipoff_scratch_spillover(
    *,
    archetype: str,
    absent_star_name: str,
    teammate_name: str,
    stat_category: str,
    baseline_per_minute_rate: float,
    baseline_minutes: float,
    milestone_target: int,
    offered_decimal_odds: float,
    news_age_minutes: float = 8.0,
    minutes_until_tipoff: float = 35.0,
    blowout_spread_abs: float = 4.5,
) -> Dict[str, Any]:
    """
    Evaluate a teammate milestone prop when a primary starter is confirmed OUT
    prior to tipoff, incorporating exponential bookmaker repricing decay:
      `decay_factor = 0.5 ** (news_age_minutes / sharp_repricing_half_life_min)`

    As `news_age_minutes` grows past the half-life (`22-35 min`), sportsbooks absorb
    the usage shift into the offered line, decaying the unpriced alpha edge.
    """
    spec = STAR_ABSENCE_SPILLOVER_TABLE.get(archetype)
    if not isinstance(spec, dict):
        spec = STAR_ABSENCE_SPILLOVER_TABLE["nba_heliocentric_guard_out"]

    base_rate = max(0.01, _finite_float(baseline_per_minute_rate, "baseline_per_minute_rate"))
    base_mins = max(8.0, min(42.0, _finite_float(baseline_minutes, "baseline_minutes")))
    dec_odds = max(1.02, _finite_float(offered_decimal_odds, "offered_decimal_odds"))
    age_min = max(0.0, _finite_float(news_age_minutes, "news_age_minutes"))
    tipoff_min = max(0.0, _finite_float(minutes_until_tipoff, "minutes_until_tipoff"))
    spread_abs = max(0.0, _finite_float(blowout_spread_abs, "blowout_spread_abs"))
    target = max(1, int(milestone_target))

    half_life = float(spec.get("sharp_repricing_half_life_min", 25.0))
    # Exponential decay of unpriced alpha as news ages
    unpriced_alpha_retention = math.pow(0.5, age_min / max(5.0, half_life))

    cat = str(stat_category or "assists").strip().lower()
    if cat in ("assists", "ast", "points_assists"):
        raw_elasticity = float(spec.get("secondary_creator_ast_elasticity", 0.22))
    elif cat in ("rebounds", "reb"):
        raw_elasticity = float(spec.get("primary_big_reb_elasticity", 0.25))
    else:
        raw_elasticity = 0.35 * float(spec.get("vacated_usage_share_transferred", 0.55))

    effective_role_multiplier = 1.0 + raw_elasticity * unpriced_alpha_retention
    effective_minutes_boost = float(spec.get("minutes_ceiling_boost", 3.0)) * unpriced_alpha_retention

    proj = project_teammate_prop_under_star_absence(
        baseline_per_minute_rate=base_rate,
        baseline_minutes=base_mins,
        minutes_boost=effective_minutes_boost,
        usage_or_role_multiplier=effective_role_multiplier,
        stat_category=cat,
        milestone_target=target,
        offered_decimal_odds=dec_odds,
        blowout_spread_abs=spread_abs,
    )

    ev_pct = float(proj["ev_pct"])
    fair_prob = float(proj.get("fair_probability", proj.get("fair_prob", 0.5)))
    implied_prob = 1.0 / dec_odds
    edge_bps = (fair_prob - implied_prob) * 10000.0

    if age_min > 2.2 * half_life:
        action = "PASS_STALE_SCRATCH_ALREADY_PRICED"
    elif tipoff_min > 180.0:
        action = "WATCH_PRE_LINEUP_CONFIRMATION"
    elif ev_pct >= 3.0 and edge_bps >= 180.0:
        action = "FIRE_PRE_TIPOFF_SPILLOVER_OVER"
    else:
        action = "PASS_INSUFFICIENT_EV"

    return {
        "archetype": archetype,
        "absent_star": absent_star_name,
        "teammate": teammate_name,
        "stat_category": cat,
        "milestone_target": target,
        "offered_decimal_odds": round(dec_odds, 4),
        "news_age_minutes": round(age_min, 2),
        "minutes_until_tipoff": round(tipoff_min, 2),
        "sharp_repricing_half_life_min": round(half_life, 2),
        "unpriced_alpha_retention": round(unpriced_alpha_retention, 4),
        "effective_role_multiplier": round(effective_role_multiplier, 4),
        "effective_minutes_boost": round(effective_minutes_boost, 3),
        "projected_minutes": proj["projected_minutes"],
        "projected_mean": proj["projected_mean"],
        "fair_prob": round(fair_prob, 4),
        "fair_decimal_odds": proj["fair_decimal_odds"],
        "edge_bps": round(edge_bps, 2),
        "ev_pct": round(ev_pct, 2),
        "action": action,
    }


def scan_live_espn_scratches_and_spillovers(
    timeout: int = 10,
    fetch_fn: Any = None,
) -> Dict[str, Any]:
    """
    Query public ESPN NBA & NHL scoreboard (for today's America/Toronto date) and
    injury endpoints to inspect pre-game status, spreads, and confirmed OUT/Doubtful
    players mapped to `STAR_ABSENCE_SPILLOVER_TABLE` archetypes.
    """
    from datetime import datetime
    from zoneinfo import ZoneInfo

    def _default_fetch(url: str) -> Any:
        req = urllib.request.Request(
            url,
            headers={"User-Agent": "SovereignAI-PreTipoffMonitor/2.0", "Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    getter = fetch_fn or _default_fetch
    today_str = datetime.now(ZoneInfo("America/Toronto")).strftime("%Y%m%d")
    endpoints = [
        ("NBA", f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={today_str}"),
        ("NHL", f"https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates={today_str}"),
    ]
    games_inspected = 0
    pre_tipoff_windows: List[Dict[str, Any]] = []
    active_teams: set[str] = set()

    for league, url in endpoints:
        try:
            payload = getter(url)
        except Exception:
            continue
        if not isinstance(payload, dict):
            continue
        for ev in payload.get("events") or []:
            if not isinstance(ev, dict):
                continue
            games_inspected += 1
            short_name = str(ev.get("shortName") or ev.get("name") or "")
            status_type = ((ev.get("status") or {}).get("type") or {})
            state = str(status_type.get("state") or "unknown")
            detail = str(status_type.get("shortDetail") or "")
            comp = (ev.get("competitions") or [{}])[0]
            for c in comp.get("competitors") or []:
                t_name = str(((c.get("team") or {}).get("displayName")) or "")
                if t_name:
                    active_teams.add(t_name)
            odds_list = comp.get("odds") or []
            spread_abs = None
            if odds_list and isinstance(odds_list[0], dict):
                try:
                    spread_abs = abs(_finite_float(odds_list[0].get("spread"), "spread"))
                except (TypeError, ValueError):
                    spread_abs = None
            if state == "pre":
                pre_tipoff_windows.append({
                    "league": league,
                    "matchup": short_name,
                    "start_detail": detail,
                    "spread_abs": round(spread_abs, 2) if spread_abs is not None else None,
                })

    confirmed_scratches: List[Dict[str, Any]] = []
    if active_teams:
        for league, inj_url in [
            ("NBA", "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/injuries"),
            ("NHL", "https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/injuries"),
        ]:
            try:
                inj_data = getter(inj_url)
            except Exception:
                continue
            if not isinstance(inj_data, dict):
                continue
            for team_block in inj_data.get("injuries") or []:
                team_name = str(team_block.get("displayName") or "")
                if team_name not in active_teams:
                    continue
                for item in team_block.get("injuries") or []:
                    status = str(item.get("status") or "")
                    if status.lower() != "out":
                        continue
                    ath = item.get("athlete") or {}
                    p_name = str(ath.get("displayName") or "")
                    pos = str(((ath.get("position") or {}).get("abbreviation")) or "")
                    if league == "NHL":
                        arch = "nhl_top_line_center_or_pp1_qb_out"
                    elif pos in ("PG", "G", "SG"):
                        arch = "nba_heliocentric_guard_out"
                    elif pos == "C":
                        arch = "nba_starting_rim_center_out"
                    else:
                        arch = "nba_high_usage_wing_scorer_out"
                    identity = str(ath.get("id") or p_name)
                    received = datetime.now(timezone.utc).isoformat()
                    # An injury bulletin supplies neither starter confirmation nor
                    # a teammate distribution/offer. Preserve that missing evidence.
                    evidence = {"lineup": observation({"starter_confirmed": None, "injury_status": "confirmed_out"},
                        {"starter_confirmed": "boolean", "injury_status": "category"}, inj_url, identity, received)}
                    assessment = assess_market(market_request("sports.player_prop", identity, evidence))
                    confirmed_scratches.append({
                        "league": league,
                        "team": team_name,
                        "player": p_name,
                        "position": pos,
                        "status": status,
                        "archetype": arch,
                        "archetype_basis": "position_heuristic_not_verified_role",
                        "market_evidence": assessment,
                    })

    return {
        "games_inspected": games_inspected,
        "pre_tipoff_games": pre_tipoff_windows[:10],
        "confirmed_scratches_count": len(confirmed_scratches),
        "confirmed_scratches_sample": confirmed_scratches[:8],
        "status": "ACTIVE_SLATE" if pre_tipoff_windows else "NO_PREGAME_WINDOWS",
    }


# ============================================================================
# 2. CROSS-EXCHANGE FUNDING & BASIS HARVESTER ENGINE (KUCOIN vs HYPERLIQUID)
# ============================================================================

def evaluate_cross_exchange_funding_and_basis(
    *,
    symbol: str,
    kucoin_funding_8h_bps: float,
    kucoin_mark_price: float,
    kucoin_index_price: float,
    hyperliquid_funding_8h_bps: Optional[float] = None,
    kucoin_turnover_24h_usd: float = 5_000_000.0,
    round_trip_fee_bps: float = 4.0,
) -> Dict[str, Any]:
    """
    Evaluate a perpetual contract for:
      1. `DIRECTIONAL_CONTRARIAN_CARRY_REVERSAL`:
         When `|kucoin_funding_8h_bps| >= 15.0` (164%+ APR) and mark-to-index basis
         confirms retail crowding (`basis_bps` has the same sign as `funding_8h_bps`
         with `|basis_bps| >= 5.0`), fading the crowded side via post-only maker order
         harvests both the 8h funding payout and basis compression.
      2. `CROSS_VENUE_DELTA_NEUTRAL_FUNDING_ARB`:
         When `hyperliquid_funding_8h_bps` is available and
         `|kucoin_funding_8h_bps - hyperliquid_funding_8h_bps| >= 12.0 bps` with
         `turnover_24h_usd >= $1,000,000`, computes net annualized spread APR after
         `round_trip_fee_bps` amortized over a 3-day (9 funding intervals) hold.
    """
    kc_f8 = _finite_float(kucoin_funding_8h_bps, "kucoin_funding_8h_bps")
    mark_px = _finite_float(kucoin_mark_price, "kucoin_mark_price")
    idx_px = _finite_float(kucoin_index_price, "kucoin_index_price")
    turnover = max(0.0, _finite_float(kucoin_turnover_24h_usd, "kucoin_turnover_24h_usd"))
    fee_bps = max(0.0, _finite_float(round_trip_fee_bps, "round_trip_fee_bps"))

    if mark_px <= 0.0 or idx_px <= 0.0:
        raise ValueError("mark_price and index_price must be positive")

    basis_bps = ((mark_px - idx_px) / idx_px) * 10000.0
    kc_annual_apr_pct = (kc_f8 / 10000.0) * 3.0 * 365.0 * 100.0

    hl_f8: Optional[float] = None
    cross_spread_8h_bps: Optional[float] = None
    net_arb_apr_pct: Optional[float] = None
    arb_legs: Optional[str] = None

    if hyperliquid_funding_8h_bps is not None:
        hl_f8 = _finite_float(hyperliquid_funding_8h_bps, "hyperliquid_funding_8h_bps")
        cross_spread_8h_bps = kc_f8 - hl_f8
        # Amortize round-trip entry/exit friction across 9 funding periods (72 hours)
        net_8h_bps = abs(cross_spread_8h_bps) - (fee_bps / 9.0)
        net_arb_apr_pct = (net_8h_bps / 10000.0) * 3.0 * 365.0 * 100.0
        if cross_spread_8h_bps > 0:
            arb_legs = "SHORT_KUCOIN_PERP_LONG_HYPERLIQUID_PERP"
        elif cross_spread_8h_bps < 0:
            arb_legs = "LONG_KUCOIN_PERP_SHORT_HYPERLIQUID_PERP"
        else:
            arb_legs = "FLAT"

    # Check directional contrarian carry + basis mean reversion first
    crowded_long = kc_f8 >= 15.0 and basis_bps >= 5.0
    crowded_short = kc_f8 <= -15.0 and basis_bps <= -5.0

    if turnover < 500_000.0:
        regime = "PASS_ILLIQUID_TURNOVER"
        recommended_side = "flat"
        expected_8h_edge_bps = 0.0
    elif crowded_long:
        regime = "DIRECTIONAL_CONTRARIAN_CARRY_REVERSAL"
        recommended_side = "short"
        expected_8h_edge_bps = kc_f8 + 0.35 * basis_bps - (0.5 * fee_bps)
    elif crowded_short:
        regime = "DIRECTIONAL_CONTRARIAN_CARRY_REVERSAL"
        recommended_side = "long"
        expected_8h_edge_bps = abs(kc_f8) + 0.35 * abs(basis_bps) - (0.5 * fee_bps)
    elif (
        cross_spread_8h_bps is not None
        and abs(cross_spread_8h_bps) >= 12.0
        and (net_arb_apr_pct or 0.0) >= 35.0
    ):
        regime = "CROSS_VENUE_DELTA_NEUTRAL_FUNDING_ARB"
        recommended_side = "short" if cross_spread_8h_bps > 0 else "long"
        expected_8h_edge_bps = abs(cross_spread_8h_bps) - (fee_bps / 9.0)
    else:
        regime = "NEUTRAL_NO_HARVEST"
        recommended_side = "flat"
        expected_8h_edge_bps = 0.0

    return {
        "symbol": str(symbol).upper(),
        "regime": regime,
        "recommended_kucoin_side": recommended_side,
        "kucoin_funding_8h_bps": round(kc_f8, 3),
        "kucoin_annual_apr_pct": round(kc_annual_apr_pct, 2),
        "basis_bps": round(basis_bps, 3),
        "hyperliquid_funding_8h_bps": round(hl_f8, 3) if hl_f8 is not None else None,
        "cross_spread_8h_bps": round(cross_spread_8h_bps, 3) if cross_spread_8h_bps is not None else None,
        "net_arb_apr_pct": round(net_arb_apr_pct, 2) if net_arb_apr_pct is not None else None,
        "arb_legs": arb_legs,
        "expected_8h_edge_bps": round(expected_8h_edge_bps, 3),
        "turnover_24h_usd": round(turnover, 2),
    }


def _normalize_base_asset(raw_symbol: str) -> str:
    s = str(raw_symbol or "").upper().strip()
    for suffix in ("USDTM", "USDCM", "USDM", "USDT", "USDC", "-PERP", "PERP"):
        if s.endswith(suffix) and len(s) > len(suffix):
            s = s[: -len(suffix)]
            break
    if s == "XBT":
        return "BTC"
    return s.strip("-_")


def scan_live_kucoin_hyperliquid_funding_arb(
    timeout: int = 10,
    fetch_fn: Any = None,
    top_n: int = 6,
) -> Dict[str, Any]:
    """
    Fetch active contracts from `KuCoin Futures` (`api-futures.kucoin.com/api/v1/contracts/active`)
    and `Hyperliquid` (`api.hyperliquid.xyz/info` `metaAndAssetCtxs`), normalize both to 8-hour
    funding bps, and rank the highest-edge contrarian carry and cross-venue arbitrage opportunities.
    """
    def _default_fetch(url: str, method: str = "GET", payload: Optional[Dict[str, Any]] = None) -> Any:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        headers = {"User-Agent": "SovereignAI-FundingHarvester/2.0", "Accept": "application/json"}
        if data is not None:
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    getter = fetch_fn or _default_fetch

    # 1. Fetch Hyperliquid 1h funding rates and convert to 8h bps (`1h_rate * 8 * 10000`)
    hl_funding_8h_by_coin: Dict[str, float] = {}
    peer_assessments = []
    try:
        hl_raw = getter("https://api.hyperliquid.xyz/info", "POST", {"type": "metaAndAssetCtxs"})
        if isinstance(hl_raw, list) and len(hl_raw) >= 2:
            universe = (hl_raw[0] or {}).get("universe") or []
            ctxs = hl_raw[1] or []
            for meta, ctx in zip(universe, ctxs):
                if isinstance(meta, dict) and isinstance(ctx, dict):
                    coin = _normalize_base_asset(str(meta.get("name") or ""))
                    f_1h = ctx.get("funding")
                    if coin and f_1h is not None:
                        try:
                            received = datetime.now(timezone.utc).isoformat()
                            peer_evidence = {
                                "price": observation({"price": _finite_float(ctx.get("markPx"), "markPx")}, {"price": "quote/base"},
                                    "https://api.hyperliquid.xyz/info#metaAndAssetCtxs", coin, received, ctx.get("updatedAt")),
                                "funding": observation({"funding_rate_bps": _finite_float(f_1h, "hyperliquid_funding") * 10000, "interval_hours": 1},
                                    {"funding_rate_bps": "bps", "interval_hours": "hours"},
                                    "https://api.hyperliquid.xyz/info#metaAndAssetCtxs", coin, received, ctx.get("updatedAt")),
                            }
                            peer_assessment = assess_market(market_request("crypto.perpetual", coin, peer_evidence))
                            peer_assessments.append(peer_assessment)
                            if peer_assessment["status"] == "READY":
                                hl_funding_8h_by_coin[coin] = peer_assessment["calculations"]["funding_8h_bps"]
                        except (TypeError, ValueError):
                            pass
    except Exception:
        pass

    # 2. Fetch KuCoin Futures active contracts
    kc_contracts: List[Dict[str, Any]] = []
    try:
        kc_raw = getter("https://api-futures.kucoin.com/api/v1/contracts/active", "GET", None)
        if isinstance(kc_raw, dict) and isinstance(kc_raw.get("data"), list):
            kc_contracts = kc_raw["data"]
    except Exception:
        pass

    opportunities: List[Dict[str, Any]] = []
    assessments = []
    scanned = 0
    received_at = datetime.now(timezone.utc).isoformat()
    source = "https://api-futures.kucoin.com/api/v1/contracts/active"
    for c in kc_contracts:
        if not isinstance(c, dict):
            assessments.append(assess_market(None))
            continue
        sym = c.get("symbol")
        def numeric(value, scale=1.0):
            if isinstance(value, bool):
                return value
            try:
                return float(value) * scale
            except (TypeError, ValueError, OverflowError):
                return value
        rate = numeric(c.get("fundingFeeRate"), 10000)
        interval = numeric(c.get("currentFundingRateGranularity", c.get("fundingRateGranularity")), 1 / 3600000)
        m_p, i_p = numeric(c.get("markPrice")), numeric(c.get("indexPrice"))
        turn_usd = numeric(c.get("turnoverOf24h"))
        evidence = {}
        for name, values, units in (
            ("price", {"price": m_p}, {"price": "quote/base"}),
            ("funding", {"funding_rate_bps": rate, "interval_hours": interval}, {"funding_rate_bps": "bps", "interval_hours": "hours"}),
            ("reference_price", {"index_price": i_p}, {"index_price": "quote/base"}),
            ("turnover", {"volume_24h_quote": turn_usd}, {"volume_24h_quote": "quote"}),
        ):
            evidence[name] = observation(values, units, source, sym, received_at, c.get("updatedAt"))
        request = market_request("crypto.perpetual", sym, evidence)
        request["market"]["quote_currency"] = c.get("quoteCurrency")
        assessment = assess_market(request)
        if c.get("quoteCurrency") != "USDT":
            assessment.update(status="UNSUPPORTED", calculations={}, reasons=["funding_research_requires_USDT_quote_currency"])
        assessments.append(assessment)
        if assessment["status"] != "READY":
            continue
        kc_f8_bps = assessment["calculations"]["funding_8h_bps"]
        scanned += 1
        base_coin = _normalize_base_asset(sym)
        hl_f8_bps = hl_funding_8h_by_coin.get(base_coin)

        eval_res = evaluate_cross_exchange_funding_and_basis(
            symbol=sym,
            kucoin_funding_8h_bps=kc_f8_bps,
            kucoin_mark_price=m_p,
            kucoin_index_price=i_p,
            hyperliquid_funding_8h_bps=hl_f8_bps,
            kucoin_turnover_24h_usd=turn_usd,
        )
        eval_res.update(market_evidence=assessment, decision="ABSTAIN", committee_evaluated=False, execution_allowed=False)
        if eval_res["regime"] != "NEUTRAL_NO_HARVEST" and eval_res["regime"] != "PASS_ILLIQUID_TURNOVER":
            opportunities.append(eval_res)

    opportunities.sort(key=lambda x: float(x.get("expected_8h_edge_bps") or 0.0), reverse=True)
    return {
        "market_evidence": assessments,
        "peer_market_evidence": peer_assessments,
        "kucoin_contracts_scanned": scanned,
        "hyperliquid_coins_mapped": len(hl_funding_8h_by_coin),
        "opportunities_found": len(opportunities),
        "top_opportunities": opportunities[: max(1, int(top_n))],
    }


# ============================================================================
# 3. NIGHTLY POST-SETTLEMENT CLV, BRIER & FEATURE-ABLATION GRADER
# ============================================================================

def run_nightly_clv_brier_ablation_audit(
    postmortem_path: Optional[str] = None,
    knowledge_path: Optional[str] = None,
    persist: bool = True,
) -> Dict[str, Any]:
    """Score original settled prediction evidence; report unavailable ablation data."""
    replay = replay_sports_postmortems(
        postmortem_path=postmortem_path,
        knowledge_path=knowledge_path,
        persist=persist,
    )
    if not replay.get("ok"):
        return {
            "ok": False,
            "reason": replay.get("reason", "replay_failed"),
            "replay": replay,
        }

    # Historical tickets do not contain held-out feature predictions. Hardcoded
    # cohorts cannot establish empirical weights; leave existing state untouched.
    return {
        "ok": True,
        "status": replay["status"],
        "calibration": replay["calibration"],
        "tickets_evaluated": replay["tickets_evaluated"],
        "legs_evaluated": replay["legs_evaluated"],
        "total_net_profit_cad": replay["total_net_profit_cad"],
        "invalid_or_missing_evidence": replay["invalid_or_missing_evidence"],
        "feature_ablation_weights": {},
        "ablation_results": {},
        "ablation_status": "HELD_OUT_FEATURE_EVIDENCE_UNAVAILABLE",
    }
