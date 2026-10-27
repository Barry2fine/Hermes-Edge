#!/usr/bin/env python3
r"""
Dual Prediction-Market Consensus Engine: Kalshi v2 Public API + Polymarket CLOB
File: /home/box/desk/kalshi_polymarket_consensus.py

Queries Kalshi's zero-auth public v2 API alongside Polymarket's Gamma/CLOB APIs across:
  - Crypto: KXBTC, KXETH, KXBTCMAXMON, KXBTCMINMON, KXETHMAXMON, KXETHMINMON
  - Fed & Macro: KXFEDDECISION, KXCBDECISIONCANADA, KXCPI, KXCPICORE, KXPAYROLLS, KXGDP, KXRATEHIKE
  - Sports: KXNBAGAME, KXNHLGAME, KXMLBGAME, KXNFLGAME, KXNCAAFGAME, KXNBA, KXNHL

Computes:
  1. Kalshi No-Vig Midpoint & Liquidity Weight (p_Kalshi from yes_bid / yes_ask / last_price / volume).
  2. Cross-Prediction-Market Divergence (Delta p_{PM-Kalshi} in bps).
  3. Inverse-Variance Precision-Weighted Consensus Probability p^*_consensus:
       w_venue = \frac{\ln(10 + V_{24h})}{(\text{Spread}_{bps} + 25)^2}
       p^*_{\text{consensus}} = \frac{w_{\text{PM}} p_{\text{PM}} + w_{\text{Kalshi}} p_{\text{Kalshi}}}{w_{\text{PM}} + w_{\text{Kalshi}}}
"""

import json
import math
import os
import re
import time
import unicodedata
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

KALSHI_URL = "https://api.elections.kalshi.com/trade-api/v2"
GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
OUTPUT_JSON = "/home/box/desk/latest_kalshi_polymarket_consensus.json"


def http_get_json(url: str, timeout: float = 10.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 KalshiPolymarketConsensus/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_json_field(val: Any) -> List[Any]:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return []
    return []


def norm_team(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s)).encode("ascii", "ignore").decode().lower()
    s = re.sub(r"[^a-z0-9 ]", "", s)
    return " ".join(s.split())


def kalshi_novig_and_precision(m: Dict[str, Any]) -> Dict[str, float]:
    """
    Computes Kalshi no-vig midpoint probability, spread in bps, 24h volume, and
    inverse-variance precision weight w_kalshi.
    """
    yb = float(m.get("yes_bid_dollars") or m.get("yes_bid") or 0.0)
    ya = float(m.get("yes_ask_dollars") or m.get("yes_ask") or 0.0)
    nb = float(m.get("no_bid_dollars") or m.get("no_bid") or 0.0)
    na = float(m.get("no_ask_dollars") or m.get("no_ask") or 0.0)
    last_px = float(m.get("last_price_dollars") or m.get("last") or 0.0)
    vol24h = float(m.get("volume_24h_fp") or m.get("vol24h") or m.get("volume_fp") or 0.0)
    oi = float(m.get("open_interest_fp") or 0.0)

    if yb > 0 and ya > 0 and ya >= yb:
        mid = 0.5 * (yb + ya)
        spread_bps = (ya - yb) * 10000.0
    elif last_px > 0:
        mid = last_px
        spread_bps = 250.0
    elif ya > 0:
        mid = 0.5 * ya
        spread_bps = 400.0
    else:
        mid = 0.50
        spread_bps = 500.0

    # If both YES and NO books have valid asks, compute exact 2-way no-vig implied probability
    if ya > 0 and na > 0 and (ya + na) > 0:
        novig_ask = ya / (ya + na)
        p_kalshi = 0.65 * mid + 0.35 * novig_ask
    else:
        p_kalshi = mid

    p_kalshi = max(0.005, min(0.995, p_kalshi))
    eff_liq = max(vol24h, oi * 0.5, 50.0)
    eff_spread = max(20.0, min(800.0, spread_bps))
    precision_w = math.log(10.0 + eff_liq) / ((eff_spread / 100.0 + 0.25) ** 2)

    return {
        "p_kalshi": round(p_kalshi, 4),
        "p_kalshi_pct": round(p_kalshi * 100.0, 2),
        "yes_bid": round(yb, 4),
        "yes_ask": round(ya, 4),
        "no_bid": round(nb, 4),
        "no_ask": round(na, 4),
        "last_price": round(last_px, 4),
        "spread_bps": round(spread_bps, 1),
        "vol24h_contracts": round(vol24h, 1),
        "open_interest": round(oi, 1),
        "precision_weight": round(precision_w, 4),
    }


def poly_precision_weight(spread_bps: Optional[float], vol24h_usd: float, depth_usd: float = 0.0) -> float:
    eff_liq = max(float(vol24h_usd or 0.0), float(depth_usd or 0.0) * 2.0, 100.0)
    eff_spread = max(15.0, min(800.0, float(spread_bps if spread_bps is not None else 120.0)))
    return round(math.log(10.0 + eff_liq) / ((eff_spread / 100.0 + 0.25) ** 2), 4)


def compute_consensus_pair(
    p_poly: float,
    w_poly: float,
    p_kalshi: float,
    w_kalshi: float,
) -> Dict[str, float]:
    tot_w = w_poly + w_kalshi
    if tot_w <= 0:
        p_cons = 0.5 * (p_poly + p_kalshi)
        share_poly = 0.5
    else:
        p_cons = (w_poly * p_poly + w_kalshi * p_kalshi) / tot_w
        share_poly = w_poly / tot_w

    div_bps = (p_poly - p_kalshi) * 10000.0
    return {
        "p_consensus": round(p_cons, 4),
        "p_consensus_pct": round(p_cons * 100.0, 2),
        "divergence_pm_minus_kalshi_bps": round(div_bps, 1),
        "poly_weight_share_pct": round(share_poly * 100.0, 1),
        "kalshi_weight_share_pct": round((1.0 - share_poly) * 100.0, 1),
    }


def fetch_kalshi_series_events(series_ticker: str, limit: int = 20) -> List[Dict[str, Any]]:
    try:
        d = http_get_json(
            f"{KALSHI_URL}/events?series_ticker={series_ticker}&status=open&with_nested_markets=true&limit={limit}",
            timeout=8.0,
        )
        return d.get("events") or []
    except Exception:
        return []


def fetch_kalshi_open_markets_snapshot() -> Dict[str, Any]:
    try:
        d = http_get_json(f"{KALSHI_URL}/markets?status=open&limit=200", timeout=8.0)
        mkts = d.get("markets") or []
        return {
            "open_sample_count": len(mkts),
            "top_volume_open_markets": sorted(
                [
                    {
                        "ticker": m.get("ticker"),
                        "event_ticker": m.get("event_ticker"),
                        "title": m.get("title"),
                        "subtitle": m.get("yes_sub_title") or m.get("subtitle"),
                        **kalshi_novig_and_precision(m),
                    }
                    for m in mkts
                ],
                key=lambda x: x["vol24h_contracts"],
                reverse=True,
            )[:15],
        }
    except Exception as e:
        return {"open_sample_count": 0, "error": str(e), "top_volume_open_markets": []}


def build_sports_consensus() -> Dict[str, Any]:
    """
    Matches live NBA, NHL, MLB, NFL, and NCAAF games across Kalshi (`KXNBAGAME`, `KXNHLGAME`,
    `KXMLBGAME`, `KXNFLGAME`) and Polymarket CLOB, producing exact team-level consensus
    probabilities `p_consensus` and cross-prediction-market divergence in bps.
    """
    series_map = {
        "NBA": "KXNBAGAME",
        "NHL": "KXNHLGAME",
        "MLB": "KXMLBGAME",
        "NFL": "KXNFLGAME",
    }
    kalshi_games: List[Dict[str, Any]] = []
    for sport, ser in series_map.items():
        evs = fetch_kalshi_series_events(ser, limit=20)
        for e in evs:
            mkts = [m for m in (e.get("markets") or []) if m.get("status") in ("active", "open", "initialized", None)]
            if len(mkts) != 2:
                continue
            m0 = kalshi_novig_and_precision(mkts[0])
            m1 = kalshi_novig_and_precision(mkts[1])
            s_prob = m0["p_kalshi"] + m1["p_kalshi"]
            f0 = m0["p_kalshi"] / s_prob if s_prob > 0 else 0.5
            f1 = m1["p_kalshi"] / s_prob if s_prob > 0 else 0.5
            t0_name = mkts[0].get("yes_sub_title") or mkts[0].get("subtitle") or ""
            t1_name = mkts[1].get("yes_sub_title") or mkts[1].get("subtitle") or ""
            kalshi_games.append({
                "sport": sport,
                "event_ticker": e.get("event_ticker"),
                "title": e.get("title"),
                "teams": [
                    {
                        "name": t0_name,
                        "norm": norm_team(t0_name),
                        "ticker": mkts[0].get("ticker"),
                        "p_kalshi_novig": round(f0, 4),
                        "p_kalshi_novig_pct": round(f0 * 100.0, 2),
                        **m0,
                    },
                    {
                        "name": t1_name,
                        "norm": norm_team(t1_name),
                        "ticker": mkts[1].get("ticker"),
                        "p_kalshi_novig": round(f1, 4),
                        "p_kalshi_novig_pct": round(f1 * 100.0, 2),
                        **m1,
                    },
                ],
                "total_vol24h_contracts": round(m0["vol24h_contracts"] + m1["vol24h_contracts"], 1),
            })

    # Also pull KXNBA and KXNHL championship futures top contenders
    futures_summary = {}
    for fut_ser in ("KXNBA", "KXNHL"):
        evs = fetch_kalshi_series_events(fut_ser, limit=3)
        if evs:
            mkts = evs[0].get("markets") or []
            top_m = sorted(
                [
                    {
                        "ticker": m.get("ticker"),
                        "team": m.get("yes_sub_title") or m.get("subtitle") or m.get("title"),
                        **kalshi_novig_and_precision(m),
                    }
                    for m in mkts
                ],
                key=lambda x: x["p_kalshi"],
                reverse=True,
            )[:5]
            futures_summary[fut_ser] = top_m

    # Pull Polymarket live sports games
    poly_games = []
    for tag in ["nba", "nhl", "mlb", "nfl"]:
        try:
            ev_list = http_get_json(
                f"{GAMMA_URL}/events?active=true&closed=false&tag_slug={tag}&order=volume24hr&ascending=false&limit=18",
                timeout=8.0,
            )
            for e in ev_list:
                title = e.get("title") or ""
                slug = e.get("slug") or ""
                if "vs" not in title.lower() or "more-markets" in slug:
                    continue
                mkts = [m for m in (e.get("markets") or []) if m.get("active") and not m.get("closed")]
                ml_mkt = None
                for m in mkts:
                    q = str(m.get("question") or "")
                    outs = parse_json_field(m.get("outcomes"))
                    if len(outs) == 2 and "O/U" not in q and "Spread" not in q and "1H" not in q:
                        if q == title or outs[0].lower() in title.lower():
                            ml_mkt = m
                            break
                if not ml_mkt:
                    continue
                outs = parse_json_field(ml_mkt.get("outcomes"))
                prices = [float(p) for p in parse_json_field(ml_mkt.get("outcomePrices")) if p is not None]
                if len(outs) == 2 and len(prices) == 2:
                    s = sum(prices)
                    f0 = prices[0] / s if s > 0 else 0.5
                    f1 = prices[1] / s if s > 0 else 0.5
                    bid_a = float(ml_mkt.get("bestBid") or 0.0)
                    ask_a = float(ml_mkt.get("bestAsk") or 0.0)
                    sprd_bps = round((ask_a - bid_a) * 10000.0, 1) if (ask_a > 0 and bid_a > 0 and ask_a >= bid_a) else 100.0
                    vol24 = float(e.get("volume24hr") or 0.0)
                    liq = float(ml_mkt.get("liquidityNum") or 0.0)
                    w_pm = poly_precision_weight(sprd_bps, vol24, liq)
                    poly_games.append({
                        "sport": tag.upper(),
                        "slug": slug,
                        "title": title,
                        "vol24h_usd": round(vol24, 0),
                        "spread_bps": sprd_bps,
                        "precision_weight": w_pm,
                        "team_a": {"name": outs[0], "norm": norm_team(outs[0]), "p_poly": round(f0, 4)},
                        "team_b": {"name": outs[1], "norm": norm_team(outs[1]), "p_poly": round(f1, 4)},
                    })
        except Exception:
            pass

    # City/Nickname mapping table so Kalshi city names ("Golden State", "Minnesota", "Philadelphia") match Polymarket nicknames ("Warriors", "Wild", "76ers")
    NICKNAME_TO_CITY = {
        # NBA
        "hawks": "atlanta", "celtics": "boston", "nets": "brooklyn", "hornets": "charlotte",
        "bulls": "chicago", "cavaliers": "cleveland", "mavericks": "dallas", "nuggets": "denver",
        "pistons": "detroit", "warriors": "golden state", "rockets": "houston", "pacers": "indiana",
        "clippers": "los angeles c", "lakers": "los angeles l", "grizzlies": "memphis", "heat": "miami",
        "bucks": "milwaukee", "timberwolves": "minnesota", "pelicans": "new orleans", "knicks": "new york",
        "thunder": "oklahoma city", "magic": "orlando", "76ers": "philadelphia", "suns": "phoenix",
        "trail blazers": "portland", "blazers": "portland", "kings": "sacramento", "spurs": "san antonio",
        "raptors": "toronto", "jazz": "utah", "wizards": "washington",
        # NHL
        "ducks": "anaheim", "bruins": "boston", "sabres": "buffalo", "flames": "calgary",
        "hurricanes": "carolina", "blackhawks": "chicago", "avalanche": "colorado", "blue jackets": "columbus",
        "stars": "dallas", "red wings": "detroit", "oilers": "edmonton", "panthers": "florida",
        "kings_nhl": "los angeles", "wild": "minnesota", "canadiens": "montreal", "predators": "nashville",
        "devils": "new jersey", "islanders": "new york i", "rangers": "new york r", "senators": "ottawa",
        "flyers": "philadelphia", "penguins": "pittsburgh", "sharks": "san jose", "kraken": "seattle",
        "blues": "st louis", "lightning": "tampa bay", "maple leafs": "toronto", "utah hockey club": "utah",
        "mammoth": "utah", "canucks": "vancouver", "golden knights": "vegas", "capitals": "washington",
        "jets": "winnipeg",
        # MLB / NFL
        "rays": "tampa bay", "brewers": "milwaukee", "steelers": "pittsburgh", "saints": "new orleans",
        "bengals": "cincinnati", "ravens": "baltimore", "browns": "cleveland", "titans": "tennessee",
        "colts": "indianapolis", "vikings": "minnesota", "falcons": "atlanta", "panthers_nfl": "carolina",
        "texans": "houston", "jaguars": "jacksonville", "pats": "new england", "patriots": "new england",
        "jets_nfl": "new york j", "giants": "new york g", "eagles": "philadelphia", "commanders": "washington",
        "bears": "chicago", "lions": "detroit", "packers": "green bay", "cardinals": "arizona",
        "rams": "los angeles r", "49ers": "san francisco", "seahawks": "seattle", "cowboys": "dallas",
        "broncos": "denver", "chiefs": "kansas city", "raiders": "las vegas", "chargers": "los angeles c",
        "bills": "buffalo", "dolphins": "miami", "buccaneers": "tampa bay",
    }

    def match_team_names(poly_norm: str, kalshi_norm: str, sport: str) -> bool:
        if not poly_norm or not kalshi_norm:
            return False
        if poly_norm == kalshi_norm or poly_norm in kalshi_norm or kalshi_norm in poly_norm:
            return True
        mapped = NICKNAME_TO_CITY.get(f"{poly_norm}_{sport.lower()}") or NICKNAME_TO_CITY.get(poly_norm)
        if mapped and (mapped == kalshi_norm or mapped in kalshi_norm or kalshi_norm in mapped):
            return True
        return False

    matched_games = []
    team_consensus_lookup: Dict[str, Dict[str, Any]] = {}

    for pg in poly_games:
        ta, tb = pg["team_a"], pg["team_b"]
        sport = pg["sport"]
        matched_k = None
        flipped = False
        for kg in kalshi_games:
            if kg["sport"] != sport:
                continue
            ka, kb = kg["teams"][0], kg["teams"][1]
            if match_team_names(ta["norm"], ka["norm"], sport) and match_team_names(tb["norm"], kb["norm"], sport):
                matched_k = kg
                flipped = False
                break
            if match_team_names(ta["norm"], kb["norm"], sport) and match_team_names(tb["norm"], ka["norm"], sport):
                matched_k = kg
                flipped = True
                break

        if matched_k:
            ka = matched_k["teams"][1] if flipped else matched_k["teams"][0]
            kb = matched_k["teams"][0] if flipped else matched_k["teams"][1]
            c_a = compute_consensus_pair(ta["p_poly"], pg["precision_weight"], ka["p_kalshi_novig"], ka["precision_weight"])
            c_b = compute_consensus_pair(tb["p_poly"], pg["precision_weight"], kb["p_kalshi_novig"], kb["precision_weight"])

            row = {
                "sport": sport,
                "poly_title": pg["title"],
                "kalshi_event_ticker": matched_k["event_ticker"],
                "kalshi_title": matched_k["title"],
                "poly_vol24h_usd": pg["vol24h_usd"],
                "kalshi_vol24h_contracts": matched_k["total_vol24h_contracts"],
                "team_a": {
                    "poly_name": ta["name"],
                    "kalshi_name": ka["name"],
                    "kalshi_ticker": ka["ticker"],
                    "p_poly_pct": round(ta["p_poly"] * 100.0, 2),
                    "p_kalshi_pct": ka["p_kalshi_novig_pct"],
                    "kalshi_bid_ask": f"{ka['yes_bid']:.2f}/{ka['yes_ask']:.2f}",
                    **c_a,
                },
                "team_b": {
                    "poly_name": tb["name"],
                    "kalshi_name": kb["name"],
                    "kalshi_ticker": kb["ticker"],
                    "p_poly_pct": round(tb["p_poly"] * 100.0, 2),
                    "p_kalshi_pct": kb["p_kalshi_novig_pct"],
                    "kalshi_bid_ask": f"{kb['yes_bid']:.2f}/{kb['yes_ask']:.2f}",
                    **c_b,
                },
                "abs_divergence_bps": abs(c_a["divergence_pm_minus_kalshi_bps"]),
            }
            matched_games.append(row)

            for t_poly_norm, t_kalshi_norm, c_obj, p_pm, p_k in [
                (ta["norm"], ka["norm"], c_a, ta["p_poly"], ka["p_kalshi_novig"]),
                (tb["norm"], kb["norm"], c_b, tb["p_poly"], kb["p_kalshi_novig"]),
            ]:
                entry = {
                    "sport": sport,
                    "p_poly": p_pm,
                    "p_kalshi": p_k,
                    "p_consensus": c_obj["p_consensus"],
                    "divergence_bps": c_obj["divergence_pm_minus_kalshi_bps"],
                    "kalshi_event_ticker": matched_k["event_ticker"],
                }
                team_consensus_lookup[f"{sport}:{t_poly_norm}"] = entry
                team_consensus_lookup[f"{sport}:{t_kalshi_norm}"] = entry

    matched_games.sort(key=lambda x: x["abs_divergence_bps"], reverse=True)
    return {
        "matched_games_count": len(matched_games),
        "kalshi_open_sports_games_count": len(kalshi_games),
        "matched_games": matched_games,
        "team_consensus_lookup": team_consensus_lookup,
        "kalshi_championship_futures": futures_summary,
    }


def build_crypto_and_macro_consensus(wealthsimple_rows: List[Dict[str, Any]]) -> Dict[str, Any]:
    """
    Enriches cross-venue Crypto (KXBTC, KXETH, KXBTCMAXMON, KXBTCMINMON, KXETHMAXMON, KXETHMINMON)
    and Macro/Fed (KXFEDDECISION, KXCBDECISIONCANADA, KXCPI, KXCPICORE, KXPAYROLLS, KXGDP)
    markets with inverse-variance precision-weighted consensus probabilities.
    """
    enriched_pairs = []
    for r in wealthsimple_rows:
        p_poly = (r.get("poly_yes_mid_pct") or 50.0) / 100.0
        w_poly = poly_precision_weight(
            r.get("poly_spread_bps"),
            float(r.get("poly_vol24h_usd") or 0.0),
            float(r.get("poly_top3_depth_usd") or 0.0),
        )
        k_bid = float(r.get("kalshi_yes_bid") or 0.0)
        k_ask = float(r.get("kalshi_yes_ask") or 0.0)
        k_vol = float(r.get("kalshi_vol24h_contracts") or 0.0)
        k_metrics = kalshi_novig_and_precision({
            "yes_bid_dollars": k_bid,
            "yes_ask_dollars": k_ask,
            "no_bid_dollars": r.get("kalshi_no_bid") or 0.0,
            "no_ask_dollars": r.get("kalshi_no_ask") or 0.0,
            "volume_24h_fp": k_vol,
        })
        cons = compute_consensus_pair(p_poly, w_poly, k_metrics["p_kalshi"], k_metrics["precision_weight"])
        enriched_pairs.append({
            "category": r.get("category"),
            "label": r.get("label"),
            "kalshi_ticker": r.get("kalshi_ticker"),
            "poly_slug": r.get("poly_slug"),
            "p_poly_pct": round(p_poly * 100.0, 2),
            "p_kalshi_pct": k_metrics["p_kalshi_pct"],
            "p_consensus_pct": cons["p_consensus_pct"],
            "p_consensus": cons["p_consensus"],
            "divergence_pm_minus_kalshi_bps": cons["divergence_pm_minus_kalshi_bps"],
            "poly_weight_share_pct": cons["poly_weight_share_pct"],
            "kalshi_weight_share_pct": cons["kalshi_weight_share_pct"],
            "kalshi_spread_bps": k_metrics["spread_bps"],
            "poly_spread_bps": r.get("poly_spread_bps"),
            "poly_vol24h_usd": r.get("poly_vol24h_usd"),
            "kalshi_vol24h_contracts": k_metrics["vol24h_contracts"],
        })

    # Also query Kalshi intraday/daily KXBTC and KXETH range series directly
    kxbtc_events = fetch_kalshi_series_events("KXBTC", limit=4)
    kxeth_events = fetch_kalshi_series_events("KXETH", limit=4)
    crypto_series_summary = []
    for asset, evs in [("BTC", kxbtc_events), ("ETH", kxeth_events)]:
        for e in evs[:2]:
            mkts = e.get("markets") or []
            top_strikes = sorted(
                [
                    {
                        "ticker": m.get("ticker"),
                        "subtitle": m.get("yes_sub_title") or m.get("subtitle") or m.get("title"),
                        **kalshi_novig_and_precision(m),
                    }
                    for m in mkts
                ],
                key=lambda x: x["vol24h_contracts"],
                reverse=True,
            )[:4]
            crypto_series_summary.append({
                "asset": asset,
                "event_ticker": e.get("event_ticker"),
                "title": e.get("title"),
                "top_strikes": top_strikes,
            })

    enriched_pairs.sort(key=lambda x: abs(x["divergence_pm_minus_kalshi_bps"]), reverse=True)
    return {
        "cross_venue_consensus_pairs": enriched_pairs,
        "kalshi_intraday_crypto_series": crypto_series_summary,
    }


def run_kalshi_polymarket_consensus(wealthsimple_rows: Optional[List[Dict[str, Any]]] = None) -> Dict[str, Any]:
    t0 = time.time()
    if wealthsimple_rows is None:
        if os.path.exists("/home/box/desk/latest_polymarket_edge.json"):
            try:
                d = json.load(open("/home/box/desk/latest_polymarket_edge.json"))
                wealthsimple_rows = (d.get("wealthsimple_30d_playbook") or {}).get("cross_venue_playbook") or []
            except Exception:
                wealthsimple_rows = []
        else:
            wealthsimple_rows = []

    with ThreadPoolExecutor(max_workers=3) as ex:
        f_open = ex.submit(fetch_kalshi_open_markets_snapshot)
        f_sports = ex.submit(build_sports_consensus)
        f_cm = ex.submit(build_crypto_and_macro_consensus, wealthsimple_rows)
        open_snap = f_open.result()
        sports_cons = f_sports.result()
        cm_cons = f_cm.result()

    out = {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_ms": round((time.time() - t0) * 1000.0, 1),
        "kalshi_v2_open_markets_snapshot": open_snap,
        "crypto_and_macro_consensus": cm_cons,
        "sports_dual_venue_consensus": sports_cons,
    }
    with open(OUTPUT_JSON, "w", encoding="utf-8") as f:
        json.dump(out, f, indent=2)
    return out


if __name__ == "__main__":
    res = run_kalshi_polymarket_consensus()
    print(f"=== Dual Prediction-Market Consensus Engine: Kalshi v2 + Polymarket CLOB ({res['elapsed_ms']} ms) ===")
    cm = res["crypto_and_macro_consensus"]["cross_venue_consensus_pairs"]
    print(f"\n[1] CRYPTO & MACRO/FED PRECISION-WEIGHTED CONSENSUS ({len(cm)} Cross-Venue Pairs):")
    for r in cm[:8]:
        print(
            f"  * [{r['category']}] {r['label']} | Poly={r['p_poly_pct']}% (w={r['poly_weight_share_pct']}%) vs "
            f"Kalshi [{r['kalshi_ticker']}]={r['p_kalshi_pct']}% (w={r['kalshi_weight_share_pct']}%) -> "
            f"Consensus p*={r['p_consensus_pct']}% | Div={r['divergence_pm_minus_kalshi_bps']:+.1f} bps"
        )
    sp = res["sports_dual_venue_consensus"]
    print(f"\n[2] SPORTS DUAL-VENUE CONSENSUS ({sp['matched_games_count']} Matched Games across Kalshi & Polymarket):")
    for g in sp["matched_games"][:10]:
        ta, tb = g["team_a"], g["team_b"]
        print(
            f"  * [{g['sport']}] {g['poly_title']} ({g['kalshi_event_ticker']}) | "
            f"{ta['poly_name']}: Poly={ta['p_poly_pct']}% vs Kalshi={ta['p_kalshi_pct']}% ({ta['kalshi_bid_ask']}) -> "
            f"p*={ta['p_consensus_pct']}% (Div={ta['divergence_pm_minus_kalshi_bps']:+.1f} bps) | "
            f"{tb['poly_name']}: p*={tb['p_consensus_pct']}%"
        )
