#!/usr/bin/env python3
"""
Polymarket & Kalshi (Wealthsimple Predict Ontario) Cross-Venue Alpha Engine
File: /home/box/desk/polymarket_edge.py

4 Quantitative Pillars:
  1. Crypto Barrier & Catalyst Alpha (--crypto)
     - Polymarket Gamma & CLOB order-book depth vs KuCoin/Hyperliquid perp marks
     - Reflection-principle analytical one-touch barrier model + Kalshi cross-check
  2. Macro & Fed Jump-Risk Sentinel (--macro)
     - FOMC, Bank of Canada (BoC), Core PCE, NFP/Unemployment, Tariffs, Geopolitics
     - CLOB midpoint, bid/ask spread (bps), top-3 book depth ($), 24h prob shifts
  3. Sports Sharp-Probability Cross-Check vs bet365 Ontario (--sports)
     - Today's (Oct 24, 2026) NBA, NHL, MLB, NFL, NCAAF (CFB), and UFC 333 games
     - Matches Polymarket zero-vig CLOB fair prob against bet365 Ontario (Action Network
       book 1270 + bet-odds-scan worker + /workspace/mo_mma_12pm.json)
     - Computes exact edge_vs_novig_bps and actual EV% vs bet365 raw payout odds
  4. Wealthsimple Predict Ontario <=30-Day Playbook (--wealthsimple)
     - Filters Polymarket CLOB markets resolving within <=30 days (Oct 24 - Nov 23, 2026)
       across Macro & Crypto + cross-references live Kalshi v2 API (which powers
       Wealthsimple Predict in Ontario at $0.02 USD/contract fee)
     - Computes exact max limit price on Wealthsimple to capture >=3% and >=5% net EV
"""

import argparse
import datetime
import json
import math
import os
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

GAMMA_URL = "https://gamma-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"
KALSHI_URL = "https://api.elections.kalshi.com/trade-api/v2"
BET_SCAN_URL = "https://bet-odds-scan.sonia-3e6f56284b.workers.dev/status"
ACTION_NET_URL = "https://api.actionnetwork.com/web/v2/scoreboard"
KC_FUT_URL = "https://api-futures.kucoin.com"
HL_INFO_URL = "https://api.hyperliquid.xyz/info"

WS_FEE_USD = 0.02  # Wealthsimple Predict flat fee per contract ($0.02 USD buy or sell)


def http_get_json(url: str, timeout: int = 10) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 PolymarketDesk/2.0",
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


def norm_cdf(x: float) -> float:
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def one_touch_prob(spot: float, barrier: float, days_left: float, ann_vol: float) -> float:
    """Analytical reflection-principle barrier touch probability under zero-drift GBM."""
    if spot <= 0 or barrier <= 0 or days_left <= 0 or ann_vol <= 0:
        return 0.0
    if (barrier >= spot and spot >= barrier) or (barrier <= spot and spot <= barrier):
        return 1.0
    t_years = max(days_left / 365.25, 1e-4)
    sigma_t = ann_vol * math.sqrt(t_years)
    d = abs(math.log(barrier / spot)) / sigma_t
    p = 2.0 * norm_cdf(-d)
    return min(max(p, 0.0), 1.0)


def fetch_clob_book_depth(token_id: str) -> Dict[str, Any]:
    """Fetches top-of-book and top-3 level USD depth from Polymarket CLOB /book."""
    if not token_id:
        return {"best_bid_size_usd": 0.0, "best_ask_size_usd": 0.0, "top3_bid_usd": 0.0, "top3_ask_usd": 0.0}
    try:
        b = http_get_json(f"{CLOB_URL}/book?token_id={token_id}", timeout=5)
        bids = sorted(b.get("bids") or [], key=lambda x: float(x.get("price") or 0), reverse=True)
        asks = sorted(b.get("asks") or [], key=lambda x: float(x.get("price") or 0))
        best_bid_usd = float(bids[0]["price"]) * float(bids[0]["size"]) if bids else 0.0
        best_ask_usd = float(asks[0]["price"]) * float(asks[0]["size"]) if asks else 0.0
        top3_bid_usd = sum(float(x["price"]) * float(x["size"]) for x in bids[:3])
        top3_ask_usd = sum(float(x["price"]) * float(x["size"]) for x in asks[:3])
        best_bid_shares = float(bids[0]["size"]) if bids else 0.0
        best_ask_shares = float(asks[0]["size"]) if asks else 0.0
        return {
            "best_bid_size_usd": round(best_bid_usd, 1),
            "best_ask_size_usd": round(best_ask_usd, 1),
            "best_bid_shares": round(best_bid_shares, 0),
            "best_ask_shares": round(best_ask_shares, 0),
            "top3_bid_usd": round(top3_bid_usd, 1),
            "top3_ask_usd": round(top3_ask_usd, 1),
        }
    except Exception:
        return {"best_bid_size_usd": 0.0, "best_ask_size_usd": 0.0, "top3_bid_usd": 0.0, "top3_ask_usd": 0.0}


def extract_event_markets(
    event: Dict[str, Any], max_markets: int = 10, with_clob_depth: bool = False
) -> List[Dict[str, Any]]:
    out = []
    mkts = event.get("markets") or []
    mkts_sorted = sorted(
        [m for m in mkts if m.get("active") and not m.get("closed")],
        key=lambda x: float(x.get("volume24hr") or x.get("volumeNum") or 0),
        reverse=True,
    )
    slug = event.get("slug") or ""
    for idx, m in enumerate(mkts_sorted[:max_markets]):
        outcomes = parse_json_field(m.get("outcomes"))
        prices = [float(p) for p in parse_json_field(m.get("outcomePrices")) if p is not None]
        clob_tokens = parse_json_field(m.get("clobTokenIds"))
        best_bid = float(m.get("bestBid") or 0)
        best_ask = float(m.get("bestAsk") or 0)
        mid = round((best_bid + best_ask) / 2.0, 4) if (best_bid > 0 and best_ask > 0) else (prices[0] if prices else None)
        spread_bps = (
            round((best_ask - best_bid) * 10000, 1)
            if (best_ask > 0 and best_bid > 0 and best_ask >= best_bid)
            else None
        )
        token_0 = clob_tokens[0] if clob_tokens else None
        depth = fetch_clob_book_depth(token_0) if (with_clob_depth and idx < 4 and token_0) else {}
        chg_1d = float(m.get("oneDayPriceChange") or 0.0)
        chg_1h = float(m.get("oneHourPriceChange") or 0.0)
        out.append({
            "question": m.get("question") or m.get("groupItemTitle") or event.get("title"),
            "group_item": m.get("groupItemTitle"),
            "outcomes": outcomes,
            "prices": [round(p, 4) for p in prices],
            "yes_prob_pct": round((mid if mid is not None else prices[0]) * 100, 2) if (mid is not None or prices) else None,
            "best_bid": round(best_bid, 4) if best_bid else None,
            "best_ask": round(best_ask, 4) if best_ask else None,
            "midpoint": mid,
            "clob_spread_bps": spread_bps,
            "change_1h_pts": round(chg_1h * 100, 2),
            "change_24h_pts": round(chg_1d * 100, 2),
            "vol24h_usd": round(float(m.get("volume24hr") or 0), 0),
            "liquidity_usd": round(float(m.get("liquidityNum") or m.get("liquidity") or 0), 0),
            "end_date": str(m.get("endDate") or event.get("endDate") or "")[:10],
            "token_id_0": token_0,
            "url": f"https://polymarket.com/event/{slug}",
            **depth,
        })
    return out


def parse_barrier_strike(text: str) -> Optional[ Tuple[str, float] ]:
    """Parses direction ('UP' or 'DOWN') and numeric strike from group_item/question."""
    if not text:
        return None
    t = text.strip()
    direction = None
    if "↑" in t or "reach" in t.lower() or "above" in t.lower():
        direction = "UP"
    elif "↓" in t or "dip" in t.lower() or "below" in t.lower():
        direction = "DOWN"
    if not direction:
        return None
    nums = re.findall(r"[\d,]+(?:\.\d+)?", t)
    if not nums:
        return None
    for n in nums:
        val = float(n.replace(",", ""))
        if val not in (2026, 2027, 19, 24, 25, 30, 31):
            return (direction, val)
    return None


def analyze_crypto_barriers() -> Dict[str, Any]:
    # 1. Fetch live perp marks from KuCoin Futures
    spot_marks = {}
    for sym, key in [("XBTUSDTM", "BTC"), ("ETHUSDTM", "ETH"), ("SOLUSDTM", "SOL")]:
        try:
            c = http_get_json(f"{KC_FUT_URL}/api/v1/contracts/{sym}").get("data", {})
            spot_marks[key] = float(c.get("markPrice") or 0)
        except Exception:
            pass

    # 2. Fetch Polymarket Crypto, Ethereum, Solana events
    slugs_wanted = [
        "what-price-will-bitcoin-hit-in-october-2026",
        "what-price-will-bitcoin-hit-october-19-25-2026",
        "what-price-will-bitcoin-hit-on-october-24-2026",
        "what-price-will-bitcoin-hit-before-2027",
        "what-price-will-ethereum-hit-in-october-2026",
        "what-price-will-ethereum-hit-october-19-25-2026",
        "what-price-will-ethereum-hit-on-october-24-2026",
        "what-price-will-solana-hit-in-october-2026",
        "what-price-will-solana-hit-october-19-25-2026",
    ]
    raw_events = []
    seen = set()
    for tag in ["crypto", "ethereum", "solana"]:
        try:
            ev_list = http_get_json(
                f"{GAMMA_URL}/events?active=true&closed=false&tag_slug={tag}&order=volume24hr&ascending=false&limit=10"
            )
            for e in ev_list:
                s = e.get("slug")
                if s and s not in seen:
                    seen.add(s)
                    raw_events.append(e)
        except Exception:
            pass

    raw_events.sort(key=lambda e: (0 if e.get("slug") in slugs_wanted else 1, -float(e.get("volume24hr") or 0)))

    ann_vols = {"BTC": 0.55, "ETH": 0.68, "SOL": 0.80}
    now_utc = datetime.datetime.now(datetime.timezone.utc)

    parsed_events = []
    upside_probs = []
    downside_probs = []
    barrier_dislocations = []

    for e in raw_events[:10]:
        title = e.get("title") or ""
        slug = e.get("slug") or ""
        asset = "BTC" if "bitcoin" in slug else ("ETH" if "ethereum" in slug else ("SOL" if "solana" in slug else "CRYPTO"))
        spot = spot_marks.get(asset, 0.0)
        vol = ann_vols.get(asset, 0.60)

        # Estimate days remaining to event expiry
        if "october-24" in slug:
            days_left = 0.42  # ~10 hours left in Oct 24
        elif "october-19-25" in slug:
            days_left = 1.42  # ~34 hours left through Oct 25
        elif "in-october-2026" in slug:
            days_left = 7.42  # ~7.42 days left in October 2026
        elif "before-2027" in slug:
            days_left = 68.4
        else:
            days_left = 7.0

        sub_mkts = extract_event_markets(e, max_markets=8, with_clob_depth=True)
        for sm in sub_mkts:
            gi = str(sm.get("group_item") or sm.get("question") or "")
            yp = sm.get("yes_prob_pct")
            parsed_strike = parse_barrier_strike(gi)
            if parsed_strike and spot > 0 and yp is not None:
                direction, strike = parsed_strike
                # Check if barrier is out-of-the-money
                is_otm = (direction == "UP" and strike > spot) or (direction == "DOWN" and strike < spot)
                if is_otm:
                    dist_pct = abs(strike - spot) / spot * 100.0
                    model_p = round(one_touch_prob(spot, strike, days_left, vol) * 100.0, 2)
                    edge_pts = round(model_p - yp, 2)
                    sm["strike"] = strike
                    sm["direction"] = direction
                    sm["dist_from_spot_pct"] = round(dist_pct, 2)
                    sm["model_touch_prob_pct"] = model_p
                    sm["model_vs_poly_edge_pts"] = edge_pts
                    if 2.0 <= yp <= 95.0 and abs(edge_pts) >= 3.0 and days_left <= 10.0:
                        barrier_dislocations.append({
                            "asset": asset,
                            "event_slug": slug,
                            "horizon_days": days_left,
                            "barrier": f"{direction} ${strike:,.0f}",
                            "spot": spot,
                            "dist_pct": round(dist_pct, 2),
                            "poly_mid_pct": yp,
                            "best_bid": sm.get("best_bid"),
                            "best_ask": sm.get("best_ask"),
                            "spread_bps": sm.get("clob_spread_bps"),
                            "top3_bid_usd": sm.get("top3_bid_usd"),
                            "top3_ask_usd": sm.get("top3_ask_usd"),
                            "model_touch_pct": model_p,
                            "dislocation_pts": edge_pts,
                            "vol24h_usd": sm.get("vol24h_usd"),
                            "change_24h_pts": sm.get("change_24h_pts"),
                            "url": sm.get("url"),
                        })
            if yp is not None and 1.0 < yp < 98.0 and days_left <= 10.0:
                if "↑" in gi or "above" in gi.lower():
                    upside_probs.append((gi, yp))
                elif "↓" in gi or "below" in gi.lower():
                    downside_probs.append((gi, yp))

        parsed_events.append({
            "slug": slug,
            "asset": asset,
            "title": title,
            "days_left": days_left,
            "vol24h_usd": round(float(e.get("volume24hr") or 0), 0),
            "url": f"https://polymarket.com/event/{slug}",
            "markets": sub_mkts,
        })

    avg_up = sum(x[1] for x in upside_probs) / len(upside_probs) if upside_probs else 0.0
    avg_dn = sum(x[1] for x in downside_probs) / len(downside_probs) if downside_probs else 0.0
    skew = (
        "BEARISH_BARRIER_SKEW"
        if avg_dn > avg_up + 3.0
        else ("BULLISH_BARRIER_SKEW" if avg_up > avg_dn + 3.0 else "BALANCED_BARRIER_SKEW")
    )
    barrier_dislocations.sort(key=lambda x: abs(x["dislocation_pts"]), reverse=True)

    return {
        "live_perp_marks": spot_marks,
        "barrier_skew_signal": skew,
        "avg_active_upside_hit_prob_pct": round(avg_up, 2),
        "avg_active_downside_hit_prob_pct": round(avg_dn, 2),
        "barrier_dislocations": barrier_dislocations[:12],
        "events": parsed_events,
    }


def analyze_macro_and_fed() -> Dict[str, Any]:
    fed_events = http_get_json(
        f"{GAMMA_URL}/events?active=true&closed=false&tag_slug=fed&order=volume24hr&ascending=false&limit=8"
    )
    econ_events = http_get_json(
        f"{GAMMA_URL}/events?active=true&closed=false&tag_slug=economy&order=volume24hr&ascending=false&limit=30"
    )
    geo_events = http_get_json(
        f"{GAMMA_URL}/events?active=true&closed=false&tag_slug=geopolitics&order=volume24hr&ascending=false&limit=10"
    )
    tariff_events = http_get_json(
        f"{GAMMA_URL}/events?active=true&closed=false&tag_slug=tariffs&order=volume24hr&ascending=false&limit=6"
    )

    macro_list = []
    seen = set()
    for e in fed_events + econ_events + geo_events + tariff_events:
        slug = e.get("slug") or ""
        if not slug or slug in seen:
            continue
        title_l = (e.get("title") or "").lower()
        tags = [str(t.get("slug") or "").lower() for t in (e.get("tags") or [])]
        if any(
            k in tags or k in title_l
            for k in [
                "fed",
                "fomc",
                "bank of canada",
                "pce",
                "cpi",
                "inflation",
                "unemployment",
                "gdp",
                "tariff",
                "iran",
                "russia",
                "ukraine",
                "nato",
            ]
        ):
            seen.add(slug)
            macro_list.append({
                "slug": slug,
                "title": e.get("title"),
                "end_date": str(e.get("endDate") or "")[:10],
                "vol24h_usd": round(float(e.get("volume24hr") or 0), 0),
                "url": f"https://polymarket.com/event/{slug}",
                "markets": extract_event_markets(e, max_markets=5, with_clob_depth=True),
            })

    macro_list.sort(key=lambda x: x["vol24h_usd"], reverse=True)

    oct_fomc = next((m for m in macro_list if "fed-decision-in-october" in m["slug"]), None)
    fomc_summary = "NEUTRAL"
    if oct_fomc and oct_fomc["markets"]:
        top_outcome = max(oct_fomc["markets"], key=lambda x: x.get("yes_prob_pct") or 0)
        fomc_summary = f"{top_outcome.get('group_item') or top_outcome.get('question')}: {top_outcome.get('yes_prob_pct')}%"

    return {
        "october_fomc_consensus": fomc_summary,
        "macro_jump_risk_state": "MODERATE_PRE_FOMC_OCT28",
        "events": macro_list[:14],
    }


def american_to_decimal(odds: float) -> float:
    if odds > 0:
        return 1.0 + (odds / 100.0)
    elif odds < 0:
        return 1.0 + (100.0 / abs(odds))
    return 1.0


def decimal_to_american(dec: float) -> int:
    if dec <= 1.0:
        return 0
    if dec >= 2.0:
        return int(round((dec - 1.0) * 100))
    return int(round(-100.0 / (dec - 1.0)))


def analyze_sports_vs_bet365() -> Dict[str, Any]:
    # 1. Pull Polymarket games across NBA, NHL, MLB, NFL, CFB, UFC
    poly_sports = []
    for tag in ["nba", "nhl", "mlb", "nfl", "cfb", "ufc"]:
        try:
            ev = http_get_json(
                f"{GAMMA_URL}/events?active=true&closed=false&tag_slug={tag}&order=volume24hr&ascending=false&limit=15"
            )
            for e in ev:
                title = e.get("title") or ""
                slug = e.get("slug") or ""
                if "vs." not in title and "vs" not in title.lower():
                    continue
                if "more-markets" in slug:
                    continue
                mkts = [m for m in (e.get("markets") or []) if m.get("active") and not m.get("closed")]
                # Find main 2-way moneyline market
                ml_mkt = None
                for m in mkts:
                    q = str(m.get("question") or "")
                    outs = parse_json_field(m.get("outcomes"))
                    if len(outs) == 2 and "O/U" not in q and "Spread" not in q and "1H" not in q and "distance" not in q.lower() and "KO" not in q:
                        if q == title or outs[0].lower() in title.lower():
                            ml_mkt = m
                            break
                if not ml_mkt:
                    continue
                if ml_mkt:
                    outcomes = parse_json_field(ml_mkt.get("outcomes"))
                    prices = [float(p) for p in parse_json_field(ml_mkt.get("outcomePrices")) if p is not None]
                    clob_tokens = parse_json_field(ml_mkt.get("clobTokenIds"))
                    if len(outcomes) == 2 and len(prices) == 2 and outcomes[0] not in ("Yes", "No", "Over", "Under"):
                        s = sum(prices)
                        fair_0 = prices[0] / s if s > 0 else prices[0]
                        fair_1 = prices[1] / s if s > 0 else prices[1]
                        bid_a = float(ml_mkt.get("bestBid") or 0)
                        ask_a = float(ml_mkt.get("bestAsk") or 0)
                        spread_bps = round((ask_a - bid_a) * 10000, 1) if (ask_a > 0 and bid_a > 0 and ask_a >= bid_a) else None
                        depth_a = fetch_clob_book_depth(clob_tokens[0]) if clob_tokens and float(e.get("volume24hr") or 0) > 100000 else {}
                        poly_sports.append({
                            "sport": tag.upper(),
                            "slug": slug,
                            "title": title,
                            "end_date": str(e.get("endDate") or "")[:10],
                            "vol24h_usd": round(float(e.get("volume24hr") or 0), 0),
                            "liquidity_usd": round(float(ml_mkt.get("liquidityNum") or 0), 0),
                            "team_a": outcomes[0],
                            "team_b": outcomes[1],
                            "poly_prob_a_pct": round(fair_0 * 100, 2),
                            "poly_prob_b_pct": round(fair_1 * 100, 2),
                            "clob_best_bid_a": round(bid_a, 4) if bid_a else None,
                            "clob_best_ask_a": round(ask_a, 4) if ask_a else None,
                            "clob_spread_bps": spread_bps,
                            "url": f"https://polymarket.com/event/{slug}",
                            **depth_a,
                        })
        except Exception:
            pass

    # 2. Fetch bet365 Ontario lines with exact team_id mapping from Action Network book 1270
    b365_catalog = []
    for lg, dt_str in [("nba", "20261024"), ("nhl", "20261024"), ("mlb", "20261024"), ("ncaaf", "20261024"), ("nfl", "20261025")]:
        try:
            d = http_get_json(f"{ACTION_NET_URL}/{lg}?bookIds=1270,15&date={dt_str}", timeout=8)
            for g in d.get("games") or []:
                m1270 = (g.get("markets") or {}).get("1270", {}).get("event", {}).get("moneyline") or []
                if not m1270:
                    continue
                team_map = {t["id"]: t.get("full_name") or t.get("display_name") for t in (g.get("teams") or []) if "id" in t}
                away_name = team_map.get(g.get("away_team_id"), "Away")
                home_name = team_map.get(g.get("home_team_id"), "Home")
                seen_sides = {}
                for o in m1270:
                    side = o.get("side")
                    if side in ("away", "home") and side not in seen_sides:
                        am_odds = float(o.get("odds") or 0)
                        dec_odds = american_to_decimal(am_odds)
                        seen_sides[side] = {
                            "team": away_name if side == "away" else home_name,
                            "american_odds": int(am_odds),
                            "decimal_odds": round(dec_odds, 4),
                            "raw_implied_prob": 1.0 / dec_odds if dec_odds > 0 else 0.0,
                        }
                if "away" in seen_sides and "home" in seen_sides:
                    tot_imp = seen_sides["away"]["raw_implied_prob"] + seen_sides["home"]["raw_implied_prob"]
                    seen_sides["away"]["novig_prob"] = seen_sides["away"]["raw_implied_prob"] / tot_imp if tot_imp > 0 else 0
                    seen_sides["home"]["novig_prob"] = seen_sides["home"]["raw_implied_prob"] / tot_imp if tot_imp > 0 else 0
                    if g.get("status") != "scheduled" or str(g.get("start_time") or "") <= time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()):
                        continue
                    b365_catalog.append({
                        "league": lg.upper(),
                        "start_time": g.get("start_time"),
                        "status": g.get("status"),
                        "away": seen_sides["away"],
                        "home": seen_sides["home"],
                        "source": "bet365 Ontario (ActionNetwork Book 1270)",
                    })
        except Exception:
            pass

    # 3. Also load UFC 333 bet365 Ontario decimal odds from /workspace/mo_mma_12pm.json if present
    if os.path.exists("/workspace/mo_mma_12pm.json"):
        try:
            with open("/workspace/mo_mma_12pm.json") as f:
                mma_data = json.load(f)
            for m in mma_data.get("matches") or []:
                fl = [
                    q
                    for q in (m.get("quotes") or [])
                    if q.get("group_name") == "Fight Lines" and q.get("line") == "To Win" and q.get("odds_dec")
                ]
                if len(fl) == 2:
                    d0 = float(fl[0]["odds_dec"])
                    d1 = float(fl[1]["odds_dec"])
                    imp0, imp1 = 1.0 / d0, 1.0 / d1
                    tot = imp0 + imp1
                    if m.get("in_play") or str(m.get("kickoff_utc") or "") <= time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()):
                        continue
                    b365_catalog.append({
                        "league": "UFC",
                        "start_time": m.get("kickoff_utc"),
                        "status": "inplay" if m.get("in_play") else "scheduled",
                        "away": {
                            "team": fl[0].get("side") or m.get("home"),
                            "american_odds": decimal_to_american(d0),
                            "decimal_odds": round(d0, 4),
                            "raw_implied_prob": imp0,
                            "novig_prob": imp0 / tot,
                        },
                        "home": {
                            "team": fl[1].get("side") or m.get("away"),
                            "american_odds": decimal_to_american(d1),
                            "decimal_odds": round(d1, 4),
                            "raw_implied_prob": imp1,
                            "novig_prob": imp1 / tot,
                        },
                        "source": "bet365 Ontario (/workspace/mo_mma_12pm.json)",
                    })
        except Exception:
            pass

    # 4. Match Polymarket games against bet365 Ontario catalog
    STOP_WORDS = {"state", "tech", "city", "united", "central", "northern", "southern", "eastern", "western", "college", "university"}
    def team_matches(poly_team: str, b365_team: str) -> bool:
        pt = poly_team.lower().replace(".", "").replace("'", "").strip()
        bt = b365_team.lower().replace(".", "").replace("'", "").strip()
        if pt == bt or (len(pt) >= 5 and (pt in bt or bt in pt)):
            # Avoid "Kansas State" matching "Mississippi State" via substring
            if "state" in pt and pt not in bt:
                return False
            return True
        pt_words = [w for w in pt.split() if len(w) > 3 and w not in STOP_WORDS]
        bt_words = [w for w in bt.split() if len(w) > 3 and w not in STOP_WORDS]
        return bool(pt_words) and all(w in bt_words for w in pt_words)

    cross_matches = []
    SPORT_TO_LEAGUE = {"NBA": "NBA", "NHL": "NHL", "MLB": "MLB", "NFL": "NFL", "CFB": "NCAAF", "UFC": "UFC"}
    for pg in poly_sports:
        ta, tb = pg["team_a"], pg["team_b"]
        expected_lg = SPORT_TO_LEAGUE.get(pg["sport"], pg["sport"])
        for bg in b365_catalog:
            if bg["league"] != expected_lg:
                continue
            t_away = bg["away"]["team"]
            t_home = bg["home"]["team"]
            match_direct = team_matches(ta, t_away) and team_matches(tb, t_home)
            match_flipped = team_matches(ta, t_home) and team_matches(tb, t_away)
            if match_direct or match_flipped:
                b_for_a = bg["away"] if match_direct else bg["home"]
                b_for_b = bg["home"] if match_direct else bg["away"]

                poly_p_a = pg["poly_prob_a_pct"] / 100.0
                poly_p_b = pg["poly_prob_b_pct"] / 100.0

                ev_a_pct = round((poly_p_a * b_for_a["decimal_odds"] - 1.0) * 100.0, 2)
                ev_b_pct = round((poly_p_b * b_for_b["decimal_odds"] - 1.0) * 100.0, 2)

                edge_novig_a_bps = round((poly_p_a - b_for_a["novig_prob"]) * 10000, 1)
                edge_novig_b_bps = round((poly_p_b - b_for_b["novig_prob"]) * 10000, 1)

                best_side = ta if ev_a_pct >= ev_b_pct else tb
                best_ev_pct = max(ev_a_pct, ev_b_pct)
                best_edge_bps = edge_novig_a_bps if ev_a_pct >= ev_b_pct else edge_novig_b_bps

                cross_matches.append({
                    "sport": pg["sport"],
                    "title": pg["title"],
                    "start_time": bg["start_time"],
                    "status": bg["status"],
                    "poly_vol24h_usd": pg["vol24h_usd"],
                    "poly_spread_bps": pg["clob_spread_bps"],
                    "poly_top3_bid_usd": pg.get("top3_bid_usd"),
                    "poly_top3_ask_usd": pg.get("top3_ask_usd"),
                    "url": pg["url"],
                    "team_a": {
                        "name": ta,
                        "poly_fair_pct": pg["poly_prob_a_pct"],
                        "b365_american": b_for_a["american_odds"],
                        "b365_decimal": b_for_a["decimal_odds"],
                        "b365_raw_implied_pct": round(b_for_a["raw_implied_prob"] * 100, 2),
                        "b365_novig_pct": round(b_for_a["novig_prob"] * 100, 2),
                        "edge_vs_novig_bps": edge_novig_a_bps,
                        "ev_vs_b365_raw_pct": ev_a_pct,
                    },
                    "team_b": {
                        "name": tb,
                        "poly_fair_pct": pg["poly_prob_b_pct"],
                        "b365_american": b_for_b["american_odds"],
                        "b365_decimal": b_for_b["decimal_odds"],
                        "b365_raw_implied_pct": round(b_for_b["raw_implied_prob"] * 100, 2),
                        "b365_novig_pct": round(b_for_b["novig_prob"] * 100, 2),
                        "edge_vs_novig_bps": edge_novig_b_bps,
                        "ev_vs_b365_raw_pct": ev_b_pct,
                    },
                    "best_side": best_side,
                    "best_ev_vs_b365_pct": best_ev_pct,
                    "best_edge_vs_novig_bps": best_edge_bps,
                })
                break

    cross_matches.sort(key=lambda x: x["best_ev_vs_b365_pct"], reverse=True)

    return {
        "polymarket_live_games_count": len(poly_sports),
        "bet365_matched_games_count": len(cross_matches),
        "positive_ev_opportunities": [m for m in cross_matches if m["best_ev_vs_b365_pct"] > 0],
        "bet365_cross_matches": cross_matches,
        "polymarket_games": poly_sports[:20],
    }


def max_ws_limit_price(fair_prob: float, target_ev_pct: float = 3.0, fee: float = WS_FEE_USD) -> float:
    """
    Computes the max contract price P on Wealthsimple Predict (Kalshi) to achieve target_ev_pct
    given a $1.00 payout and a flat $0.02 USD buy fee per contract:
      Total cost = P + fee
      Expected value = fair_prob * 1.00
      ROI = (fair_prob / (P + fee)) - 1 >= target_ev_pct / 100
      => P <= (fair_prob / (1 + target_ev_pct/100)) - fee
    """
    if fair_prob <= 0:
        return 0.0
    p_max = (fair_prob / (1.0 + target_ev_pct / 100.0)) - fee
    return round(max(0.01, min(0.99, p_max)), 4)


def fetch_kalshi_event_map() -> Dict[str, Dict[str, Any]]:
    """Fetches key Kalshi open events that power Wealthsimple Predict in Canada."""
    tickers = [
        "KXFEDDECISION-26OCT",
        "KXFEDDECISION-26DEC",
        "KXCBDECISIONCANADA-26OCT",
        "KXCBDECISIONCANADA-26DEC",
        "KXCANCUTS-26DEC31",
        "KXRATEHIKE-2026",
        "KXBTCMAXMON-BTC-26OCT31",
        "KXBTCMINMON-BTC-26OCT31",
        "KXETHMAXMON-ETH-26OCT31",
        "KXETHMINMON-ETH-26OCT31",
        "KXCPI-26OCT",
        "KXCPICORE-26OCT",
        "KXPAYROLLS-26OCT",
        "KXGDP-26OCT30",
    ]
    out = {}
    for t in tickers:
        try:
            d = http_get_json(f"{KALSHI_URL}/events/{t}?with_nested_markets=true", timeout=6)
            ev = d.get("event") or {}
            mkts = {}
            for m in ev.get("markets") or []:
                mkts[m.get("ticker")] = {
                    "ticker": m.get("ticker"),
                    "subtitle": m.get("yes_sub_title") or m.get("subtitle") or m.get("title"),
                    "yes_bid": float(m.get("yes_bid_dollars") or 0),
                    "yes_ask": float(m.get("yes_ask_dollars") or 0),
                    "no_bid": float(m.get("no_bid_dollars") or 0),
                    "no_ask": float(m.get("no_ask_dollars") or 0),
                    "last": float(m.get("last_price_dollars") or 0),
                    "vol24h": round(float(m.get("volume_24h_fp") or 0), 0),
                    "expiration": str(m.get("close_time") or m.get("expiration_time") or "")[:16],
                }
            out[t] = {"title": ev.get("title"), "markets": mkts}
        except Exception:
            pass
    return out


def analyze_wealthsimple_30d() -> Dict[str, Any]:
    """
    4th Pillar: Wealthsimple Predict Ontario (<=30-day window: Oct 24, 2026 - Nov 23, 2026)
    Cross-references Polymarket's sharp zero-vig CLOB midpoints against Kalshi's live order book
    (which powers Wealthsimple Predict in Ontario) and computes exact max limit prices after
    Wealthsimple's $0.02/contract fee.
    """
    kalshi_map = fetch_kalshi_event_map()

    # Curated cross-venue pairs (Polymarket slug + sub-market matcher -> Kalshi event & market ticker)
    cross_specs = [
        {
            "category": "Macro / Fed (<=30d)",
            "poly_slug": "fed-decision-in-october-20260617190323537",
            "poly_match": "No change",
            "kalshi_event": "KXFEDDECISION-26OCT",
            "kalshi_ticker": "KXFEDDECISION-26OCT-H0",
            "label": "US Fed Oct 28 Decision: Hold / No Change",
        },
        {
            "category": "Macro / Fed (<=30d)",
            "poly_slug": "fed-decision-in-october-20260617190323537",
            "poly_match": "25 bps increase",
            "kalshi_event": "KXFEDDECISION-26OCT",
            "kalshi_ticker": "KXFEDDECISION-26OCT-H25",
            "label": "US Fed Oct 28 Decision: Hike 25 bps",
        },
        {
            "category": "Macro / Canada BoC (<=30d)",
            "poly_slug": "bank-of-canada-decision-in-october-20260715203359314",
            "poly_match": "25 bps increase",
            "kalshi_event": "KXCBDECISIONCANADA-26OCT",
            "kalshi_ticker": "KXCBDECISIONCANADA-26OCT-H25",
            "label": "Bank of Canada Oct 28 Decision: Hike 25 bps",
        },
        {
            "category": "Macro / Canada BoC (<=30d)",
            "poly_slug": "bank-of-canada-decision-in-october-20260715203359314",
            "poly_match": "No Change",
            "kalshi_event": "KXCBDECISIONCANADA-26OCT",
            "kalshi_ticker": "KXCBDECISIONCANADA-26OCT-H0",
            "label": "Bank of Canada Oct 28 Decision: Hold / No Change",
        },
        {
            "category": "Crypto Barrier (<=30d)",
            "poly_slug": "what-price-will-bitcoin-hit-in-october-2026",
            "poly_match": "↓ 77,500",
            "kalshi_event": "KXBTCMINMON-BTC-26OCT31",
            "kalshi_ticker": "KXBTCMINMON-BTC-26OCT31-7750000",
            "label": "BTC Oct 31 One-Touch Low: Below $77,500",
        },
        {
            "category": "Crypto Barrier (<=30d)",
            "poly_slug": "what-price-will-bitcoin-hit-in-october-2026",
            "poly_match": "↓ 75,000",
            "kalshi_event": "KXBTCMINMON-BTC-26OCT31",
            "kalshi_ticker": "KXBTCMINMON-BTC-26OCT31-7500000",
            "label": "BTC Oct 31 One-Touch Low: Below $75,000",
        },
        {
            "category": "Crypto Barrier (<=30d)",
            "poly_slug": "what-price-will-bitcoin-hit-in-october-2026",
            "poly_match": "↑ 87,500",
            "kalshi_event": "KXBTCMAXMON-BTC-26OCT31",
            "kalshi_ticker": "KXBTCMAXMON-BTC-26OCT31-8750000",
            "label": "BTC Oct 31 One-Touch High: Above $87,500",
        },
        {
            "category": "Crypto Barrier (<=30d)",
            "poly_slug": "what-price-will-bitcoin-hit-in-october-2026",
            "poly_match": "↑ 90,000",
            "kalshi_event": "KXBTCMAXMON-BTC-26OCT31",
            "kalshi_ticker": "KXBTCMAXMON-BTC-26OCT31-9000000",
            "label": "BTC Oct 31 One-Touch High: Above $90,000",
        },
        {
            "category": "Crypto Barrier (<=30d)",
            "poly_slug": "what-price-will-ethereum-hit-in-october-2026",
            "poly_match": "↓ 2,200",
            "kalshi_event": "KXETHMINMON-ETH-26OCT31",
            "kalshi_ticker": "KXETHMINMON-ETH-26OCT31-225000",
            "label": "ETH Oct 31 One-Touch Low: Below $2,200 (Poly) vs $2,250 (Kalshi)",
        },
        {
            "category": "Crypto Barrier (<=30d)",
            "poly_slug": "what-price-will-ethereum-hit-in-october-2026",
            "poly_match": "↓ 2,000",
            "kalshi_event": "KXETHMINMON-ETH-26OCT31",
            "kalshi_ticker": "KXETHMINMON-ETH-26OCT31-200000",
            "label": "ETH Oct 31 One-Touch Low: Below $2,000",
        },
        {
            "category": "Macro / Fed (Dec >=30d CIRO)",
            "poly_slug": "fed-decision-in-december-20260729232808632",
            "poly_match": "No change",
            "kalshi_event": "KXFEDDECISION-26DEC",
            "kalshi_ticker": "KXFEDDECISION-26DEC-H0",
            "label": "US Fed Dec 9 Decision: Hold / No Change",
        },
        {
            "category": "Macro / Fed (Dec >=30d CIRO)",
            "poly_slug": "another-fed-rate-hike-in-2026",
            "poly_match": "Another Fed rate hike",
            "kalshi_event": "KXRATEHIKE-2026",
            "kalshi_ticker": "KXRATEHIKE-2026-2",
            "label": "Another Fed Rate Hike in 2026 (Kalshi: Exactly 2 hikes in 2026)",
        },
    ]

    # Cache Polymarket events by slug
    poly_cache = {}
    for spec in cross_specs:
        s = spec["poly_slug"]
        if s not in poly_cache:
            try:
                ev_arr = http_get_json(f"{GAMMA_URL}/events?slug={s}", timeout=8)
                if ev_arr:
                    poly_cache[s] = ev_arr[0]
            except Exception:
                pass

    playbook_rows = []
    for spec in cross_specs:
        ev = poly_cache.get(spec["poly_slug"])
        if not ev:
            continue
        mkts = extract_event_markets(ev, max_markets=15, with_clob_depth=True)
        pm = next(
            (
                m
                for m in mkts
                if spec["poly_match"].lower() in str(m.get("group_item") or "").lower()
                or spec["poly_match"].lower() in str(m.get("question") or "").lower()
            ),
            None,
        )
        if not pm or pm.get("yes_prob_pct") is None:
            continue

        poly_yes = pm["yes_prob_pct"] / 100.0
        poly_no = 1.0 - poly_yes

        km = (kalshi_map.get(spec["kalshi_event"]) or {}).get("markets", {}).get(spec["kalshi_ticker"], {})
        k_yes_bid = km.get("yes_bid")
        k_yes_ask = km.get("yes_ask")
        k_no_bid = km.get("no_bid")
        k_no_ask = km.get("no_ask")
        k_mid = round((k_yes_bid + k_yes_ask) / 2.0, 4) if (k_yes_bid is not None and k_yes_ask is not None and k_yes_ask > 0) else None

        # Max limit prices on Wealthsimple (including $0.02 fee) for YES and NO
        yes_max_3pct = max_ws_limit_price(poly_yes, 3.0)
        yes_max_5pct = max_ws_limit_price(poly_yes, 5.0)
        no_max_3pct = max_ws_limit_price(poly_no, 3.0)
        no_max_5pct = max_ws_limit_price(poly_no, 5.0)

        # Check if Kalshi live ask or resting limit is actionable vs Polymarket fair
        dislocation_bps = round((poly_yes - k_mid) * 10000, 0) if k_mid is not None else None
        recommended_side = "YES" if (dislocation_bps or 0) >= 0 else "NO"
        target_fair = poly_yes if recommended_side == "YES" else poly_no

        playbook_rows.append({
            "category": spec["category"],
            "label": spec["label"],
            "end_date": pm.get("end_date"),
            "poly_slug": spec["poly_slug"],
            "poly_url": pm.get("url"),
            "poly_yes_mid_pct": pm["yes_prob_pct"],
            "poly_no_mid_pct": round(poly_no * 100, 2),
            "poly_bid": pm.get("best_bid"),
            "poly_ask": pm.get("best_ask"),
            "poly_spread_bps": pm.get("clob_spread_bps"),
            "poly_top3_depth_usd": round((pm.get("top3_bid_usd") or 0) + (pm.get("top3_ask_usd") or 0), 1),
            "poly_vol24h_usd": pm.get("vol24h_usd"),
            "kalshi_ticker": spec["kalshi_ticker"],
            "kalshi_yes_bid": k_yes_bid,
            "kalshi_yes_ask": k_yes_ask,
            "kalshi_no_bid": k_no_bid,
            "kalshi_no_ask": k_no_ask,
            "kalshi_mid_pct": round(k_mid * 100, 2) if k_mid is not None else None,
            "kalshi_vol24h_contracts": km.get("vol24h"),
            "poly_minus_kalshi_bps": dislocation_bps,
            "recommended_side": recommended_side,
            "ws_max_limit_yes_3pct_ev": yes_max_3pct,
            "ws_max_limit_yes_5pct_ev": yes_max_5pct,
            "ws_max_limit_no_3pct_ev": no_max_3pct,
            "ws_max_limit_no_5pct_ev": no_max_5pct,
            "ws_recommended_max_limit_3pct": yes_max_3pct if recommended_side == "YES" else no_max_3pct,
            "ws_recommended_max_limit_5pct": yes_max_5pct if recommended_side == "YES" else no_max_5pct,
        })

    # Also include Kalshi-native <=30d macro markets (US CPI Oct, Core CPI Oct, NFP Oct, Q3 GDP, Canada Cuts)
    kalshi_only_highlights = []
    for ev_t, tk_list in [
        ("KXCPI-26OCT", ["KXCPI-26OCT-T0.1", "KXCPI-26OCT-T0.2"]),
        ("KXCPICORE-26OCT", ["KXCPICORE-26OCT-T0.2", "KXCPICORE-26OCT-T0.3"]),
        ("KXPAYROLLS-26OCT", ["KXPAYROLLS-26OCT-T60000", "KXPAYROLLS-26OCT-T70000"]),
        ("KXGDP-26OCT30", ["KXGDP-26OCT30-T3.0", "KXGDP-26OCT30-T3.5"]),
        ("KXCANCUTS-26DEC31", ["KXCANCUTS-26DEC31-E0", "KXCANCUTS-26DEC31-E1"]),
    ]:
        ev_obj = kalshi_map.get(ev_t) or {}
        for tk in tk_list:
            m = (ev_obj.get("markets") or {}).get(tk)
            if m:
                kalshi_only_highlights.append({
                    "event_ticker": ev_t,
                    "title": ev_obj.get("title"),
                    **m,
                })

    return {
        "exchange_provider": "Kalshi (CFTC-regulated exchange powering Wealthsimple Predict in Canada under CIRO approval)",
        "fee_structure": "$0.02 USD per contract on buy or sell; contracts settle at $1.00 USD",
        "ciro_rules": "Permitted categories: Economic Indicators, Financial Markets, Climate (Sports & Politics excluded in Canada).",
        "cross_venue_playbook": playbook_rows,
        "kalshi_macro_books_30d": kalshi_only_highlights,
    }


def run_polymarket_edge() -> Dict[str, Any]:
    import polymarket_whale_tracker
    import kalshi_polymarket_consensus
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=6) as ex:
        f_crypto = ex.submit(analyze_crypto_barriers)
        f_macro = ex.submit(analyze_macro_and_fed)
        f_sports = ex.submit(analyze_sports_vs_bet365)
        f_ws = ex.submit(analyze_wealthsimple_30d)
        f_whales = ex.submit(polymarket_whale_tracker.run_polymarket_whale_tracker)
        f_kalshi = ex.submit(kalshi_polymarket_consensus.run_kalshi_polymarket_consensus)
        crypto_res = f_crypto.result()
        macro_res = f_macro.result()
        sports_res = f_sports.result()
        ws_res = f_ws.result()
        whale_res = f_whales.result()
        kalshi_res = f_kalshi.result()

    out = {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "crypto_barrier_alpha": crypto_res,
        "macro_fed_regime": macro_res,
        "sports_clob_arbitrage": sports_res,
        "wealthsimple_30d_playbook": ws_res,
        "whale_flow_tracker": whale_res,
        "kalshi_polymarket_consensus": kalshi_res,
    }
    with open("/home/box/desk/latest_polymarket_edge.json", "w") as f:
        json.dump(out, f, indent=2)
    return out


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Polymarket & Wealthsimple/Kalshi Cross-Venue Alpha Engine")
    parser.add_argument("--crypto", action="store_true", help="Run Pillar 1: Crypto Barrier & Catalyst Alpha")
    parser.add_argument("--macro", action="store_true", help="Run Pillar 2: Macro & Fed Jump-Risk Sentinel")
    parser.add_argument("--sports", action="store_true", help="Run Pillar 3: Sports Sharp-Prob vs bet365 Ontario")
    parser.add_argument("--wealthsimple", action="store_true", help="Run Pillar 4: Wealthsimple Ontario <=30d Playbook")
    parser.add_argument("--json", action="store_true", help="Print full JSON output")
    args = parser.parse_args()

    res = run_polymarket_edge()
    if args.json:
        print(json.dumps(res, indent=2))
        raise SystemExit(0)

    run_all = not (args.crypto or args.macro or args.sports or args.wealthsimple)
    print(f"=== Polymarket & Wealthsimple/Kalshi Cross-Venue Alpha Engine ({res['elapsed_ms']} ms) ===")

    if run_all or args.crypto:
        cb = res["crypto_barrier_alpha"]
        print(
            f"\n[1] CRYPTO BARRIER ALPHA | Marks: {cb['live_perp_marks']} | Skew: {cb['barrier_skew_signal']} "
            f"(Up Avg: {cb['avg_active_upside_hit_prob_pct']}%, Down Avg: {cb['avg_active_downside_hit_prob_pct']}%)"
        )
        print("  Top Barrier Dislocations (Model Touch Prob vs Polymarket CLOB Mid):")
        for d in cb["barrier_dislocations"][:8]:
            print(
                f"   * [{d['asset']} {d['barrier']} | {d['horizon_days']}d] Spot=${d['spot']:,.2f} ({d['dist_pct']}% away) | "
                f"Poly={d['poly_mid_pct']}% (bid={d['best_bid']}/ask={d['best_ask']}, sprd={d['spread_bps']}bps, "
                f"top3=${(d.get('top3_bid_usd') or 0)+(d.get('top3_ask_usd') or 0):,.0f}) vs Model={d['model_touch_pct']}% "
                f"-> Edge={d['dislocation_pts']:+.2f} pts | 24hVol=${d['vol24h_usd']:,.0f}"
            )

    if run_all or args.macro:
        mf = res["macro_fed_regime"]
        print(f"\n[2] MACRO & FED JUMP-RISK SENTINEL | Oct FOMC: {mf['october_fomc_consensus']} | State: {mf['macro_jump_risk_state']}")
        for ev in mf["events"][:8]:
            print(f"   - [{ev['slug']}] (End: {ev['end_date']}, 24hVol: ${ev['vol24h_usd']:,.0f}): {ev['title']}")
            for m in ev["markets"][:3]:
                print(
                    f"       * {m['group_item'] or m['question']}: Mid={m['yes_prob_pct']}% "
                    f"(bid={m['best_bid']}/ask={m['best_ask']}, sprd={m['clob_spread_bps']}bps, "
                    f"24hChg={m['change_24h_pts']:+.2f}pt, top3=${(m.get('top3_bid_usd') or 0)+(m.get('top3_ask_usd') or 0):,.0f})"
                )

    if run_all or args.sports:
        sp = res["sports_clob_arbitrage"]
        print(
            f"\n[3] SPORTS SHARP-PROBABILITY vs bet365 ONTARIO "
            f"({sp['polymarket_live_games_count']} Poly games, {sp['bet365_matched_games_count']} matched on bet365 ON):"
        )
        for m in sp["bet365_cross_matches"][:14]:
            ta, tb = m["team_a"], m["team_b"]
            print(
                f"   * [{m['sport']}] {m['title']} (24hVol=${m['poly_vol24h_usd']:,.0f}, sprd={m['poly_spread_bps']}bps) | "
                f"{ta['name']}: Poly {ta['poly_fair_pct']}% vs b365 {ta['b365_american']:+d} ({ta['b365_ decimal' if 'b365_ decimal' in ta else 'b365_decimal']}x, novig {ta['b365_novig_pct']}%) [EV={ta['ev_vs_b365_raw_pct']:+.2f}%] | "
                f"{tb['name']}: Poly {tb['poly_fair_pct']}% vs b365 {tb['b365_american']:+d} ({tb['b365_decimal']}x, novig {tb['b365_novig_pct']}%) [EV={tb['ev_vs_b365_raw_pct']:+.2f}%]"
            )

    if run_all or args.wealthsimple:
        ws = res["wealthsimple_30d_playbook"]
        print(f"\n[4] WEALTHSIMPLE PREDICT ONTARIO (<=30d & Kalshi Cross-Venue Playbook | Fee=${WS_FEE_USD}/contract):")
        for r in ws["cross_venue_playbook"]:
            print(
                f"   * [{r['category']}] {r['label']} (Exp: {r['end_date']}) | "
                f"Poly YES={r['poly_yes_mid_pct']}% (bid={r['poly_bid']}/ask={r['poly_ask']}, depth=${r['poly_top3_depth_usd']:,.0f}) vs "
                f"Kalshi/WS [{r['kalshi_ticker']}] YES={r['kalshi_yes_bid']}/{r['kalshi_yes_ask']} (NO={r['kalshi_no_bid']}/{r['kalshi_no_ask']}) | "
                f"Diff={r['poly_minus_kalshi_bps']}bps | Rec={r['recommended_side']} MaxLimit(3%EV)=${r['ws_recommended_max_limit_3pct']:.3f}, MaxLimit(5%EV)=${r['ws_recommended_max_limit_5pct']:.3f}"
            )
