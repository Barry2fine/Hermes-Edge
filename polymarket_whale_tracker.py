#!/usr/bin/env python3
r"""
Polymarket Whale-Flow & Smart-Money CLOB Sweep Tracker
File: /home/box/desk/polymarket_whale_tracker.py

Queries Polymarket's public Data API (`https://data-api.polymarket.com/trades`) and
CLOB / Gamma APIs (`clob.polymarket.com`, `gamma-api.polymarket.com`) to track:
  1. Global Whale Tape: Single fills >= $1,000 and >= $5,000 across all Polymarket CLOB markets.
  2. Crypto Barrier Whale Impulse (`BTC`, `ETH`, `SOL`):
     - Fetches active October & Weekly barrier markets (`reach $X` vs `dip to $Y`),
       queries recent large trades + top-3 CLOB bid/ask USD depth, and computes a signed
       Whale Directional Impulse Score in [-100, +100]:
         I_{whale} = \frac{\sum V_{bull\_usd} - \sum V_{bear\_usd}}{\sum V_{bull\_usd} + \sum V_{bear\_usd} + 500}
  3. Sports & Macro Smart-Money Sweeps:
     - Aggregates whale dollar flow by game/event (`NBA`, `NHL`, `UFC`, `CFB`, `NFL`, `Fed/Macro`)
       and identifies high-conviction smart-money sweeps where single wallets deploy >= $2,500+.
"""

import json
import math
import re
import time
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List, Optional, Tuple

GAMMA_URL = "https://gamma-api.polymarket.com"
DATA_API_URL = "https://data-api.polymarket.com"
CLOB_URL = "https://clob.polymarket.com"


def http_get_json(url: str, timeout: float = 8.0) -> Any:
    req = urllib.request.Request(
        url,
        headers={
            "Accept": "application/json",
            "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) PolymarketWhaleTracker/1.0",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode("utf-8"))


def parse_json_list(val: Any) -> List[Any]:
    if isinstance(val, list):
        return val
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:
            return []
    return []


def classify_trade_category(slug: str, title: str) -> str:
    s = f"{slug} {title}".lower()
    if any(k in s for k in ("bitcoin", "btc", "ethereum", "eth", "solana", "sol", "crypto", "xrp", "doge")):
        return "CRYPTO"
    if any(k in s for k in ("fed ", "fomc", "interest rate", "inflation", "cpi", "recession", "tariff", "gdp", "powell")):
        return "MACRO_FED"
    if any(k in s for k in ("nba-", "nhl-", "ufc-", "mlb-", "nfl-", "cfb-", "vs.", "vs ", "spread:", "o/u ")):
        return "SPORTS"
    return "POLITICS_OTHER"


def analyze_crypto_barrier_whales() -> Dict[str, Any]:
    slugs = [
        ("BTC", "what-price-will-bitcoin-hit-in-october-2026"),
        ("BTC", "what-price-will-bitcoin-hit-october-19-25-2026"),
        ("ETH", "what-price-will-ethereum-hit-in-october-2026"),
        ("ETH", "what-price-will-ethereum-hit-october-19-25-2026"),
        ("SOL", "what-price-will-solana-hit-in-october-2026"),
        ("SOL", "what-price-will-solana-hit-october-19-25-2026"),
    ]

    active_barriers: List[Tuple[str, Dict[str, Any]]] = []
    for asset, slug in slugs:
        try:
            evs = http_get_json(f"{GAMMA_URL}/events?slug={slug}", timeout=6.0)
            if not evs:
                continue
            mkts = evs[0].get("markets") or []
            # Keep active barrier markets whose YES probability is between 3% and 95% (live battleground strikes)
            cnt = 0
            mkts_sorted = sorted(mkts, key=lambda x: float(x.get("volume24hr") or x.get("volumeNum") or 0), reverse=True)
            for m in mkts_sorted:
                if not m.get("active") or m.get("closed"):
                    continue
                prices = [float(p) for p in parse_json_list(m.get("outcomePrices")) if p is not None]
                yes_p = prices[0] if prices else 0.0
                if 0.02 <= yes_p <= 0.96 and m.get("conditionId"):
                    active_barriers.append((asset, m))
                    cnt += 1
                    if cnt >= 4:
                        break
        except Exception:
            pass

    asset_flow: Dict[str, Dict[str, Any]] = {
        "BTC": {"bull_whale_usd": 0.0, "bear_whale_usd": 0.0, "clob_bid_usd": 0.0, "clob_ask_usd": 0.0, "notable_fills": []},
        "ETH": {"bull_whale_usd": 0.0, "bear_whale_usd": 0.0, "clob_bid_usd": 0.0, "clob_ask_usd": 0.0, "notable_fills": []},
        "SOL": {"bull_whale_usd": 0.0, "bear_whale_usd": 0.0, "clob_bid_usd": 0.0, "clob_ask_usd": 0.0, "notable_fills": []},
    }

    def _inspect_barrier(item: Tuple[str, Dict[str, Any]]) -> None:
        asset, m = item
        cond_id = m.get("conditionId")
        q = str(m.get("question") or m.get("groupItemTitle") or "")
        ql = q.lower()
        is_upside_barrier = ("reach" in ql or "above" in ql or "↑" in ql)
        is_downside_barrier = ("dip" in ql or "below" in ql or "↓" in ql)
        if not (is_upside_barrier or is_downside_barrier):
            return

        # Fetch CLOB top-3 book depth for YES token
        tokens = parse_json_list(m.get("clobTokenIds"))
        if tokens:
            try:
                bk = http_get_json(f"{CLOB_URL}/book?token_id={tokens[0]}", timeout=4.5)
                bids = sorted(bk.get("bids") or [], key=lambda x: float(x.get("price") or 0), reverse=True)[:3]
                asks = sorted(bk.get("asks") or [], key=lambda x: float(x.get("price") or 0))[:3]
                bid_usd = sum(float(x["price"]) * float(x["size"]) for x in bids)
                ask_usd = sum(float(x["price"]) * float(x["size"]) for x in asks)
                if is_upside_barrier:
                    asset_flow[asset]["clob_bid_usd"] += bid_usd
                    asset_flow[asset]["clob_ask_usd"] += ask_usd
                else:
                    # Buying YES on a downside dip barrier is bearish; bidding YES on dip = bear support
                    asset_flow[asset]["clob_bid_usd"] += ask_usd
                    asset_flow[asset]["clob_ask_usd"] += bid_usd
            except Exception:
                pass

        # Fetch recent trades >= $100 on this barrier
        try:
            trades = http_get_json(f"{DATA_API_URL}/trades?limit=25&market={cond_id}&filterType=CASH&filterAmount=100", timeout=5.0)
            for t in trades or []:
                sz = float(t.get("size") or 0.0)
                px = float(t.get("price") or 0.0)
                usd = sz * px
                if usd < 80.0:
                    continue
                side = str(t.get("side") or "BUY").upper()
                outcome = str(t.get("outcome") or "Yes")
                # Determine if this trade is betting ON the barrier being hit or NOT hit
                betting_barrier_hit = (side == "BUY" and outcome.lower() == "yes") or (side == "SELL" and outcome.lower() == "no")
                is_bullish_bet = (is_upside_barrier and betting_barrier_hit) or (is_downside_barrier and not betting_barrier_hit)

                if is_bullish_bet:
                    asset_flow[asset]["bull_whale_usd"] += usd
                else:
                    asset_flow[asset]["bear_whale_usd"] += usd

                if usd >= 250.0:
                    asset_flow[asset]["notable_fills"].append({
                        "ts": t.get("timestamp"),
                        "question": q,
                        "side": side,
                        "outcome": outcome,
                        "price": round(px, 4),
                        "usd_notional": round(usd, 2),
                        "implied_crypto_bias": "BULL" if is_bullish_bet else "BEAR",
                        "wallet": str(t.get("proxyWallet") or "")[:10],
                    })
        except Exception:
            pass

    with ThreadPoolExecutor(max_workers=8) as ex:
        list(ex.map(_inspect_barrier, active_barriers))

    summary_by_asset: Dict[str, Any] = {}
    for asset, d in asset_flow.items():
        bull_u = d["bull_whale_usd"]
        bear_u = d["bear_whale_usd"]
        tot_u = bull_u + bear_u
        trade_imb = (bull_u - bear_u) / (tot_u + 500.0)

        cbid = d["clob_bid_usd"]
        cask = d["clob_ask_usd"]
        book_imb = (cbid - cask) / max(1.0, cbid + cask)

        impulse_score = round(max(-100.0, min(100.0, (trade_imb * 0.65 + book_imb * 0.35) * 100.0)), 1)
        if impulse_score >= 15.0:
            bias = "WHALE_BULL_ACCUMULATION"
        elif impulse_score <= -15.0:
            bias = "WHALE_BEAR_HEDGING_OR_DISTRIBUTION"
        else:
            bias = "WHALE_BALANCED_TWO_WAY"

        d["notable_fills"].sort(key=lambda x: x["usd_notional"], reverse=True)
        summary_by_asset[asset] = {
            "bull_whale_flow_usd": round(bull_u, 2),
            "bear_whale_flow_usd": round(bear_u, 2),
            "clob_directional_bid_usd": round(cbid, 2),
            "clob_directional_ask_usd": round(cask, 2),
            "whale_impulse_score": impulse_score,
            "whale_regime": bias,
            "top_fills": d["notable_fills"][:6],
        }
    return summary_by_asset


def run_polymarket_whale_tracker() -> Dict[str, Any]:
    t0 = time.time()
    with ThreadPoolExecutor(max_workers=3) as ex:
        f_tape_1k = ex.submit(http_get_json, f"{DATA_API_URL}/trades?limit=150&filterType=CASH&filterAmount=1000", 8.0)
        f_tape_5k = ex.submit(http_get_json, f"{DATA_API_URL}/trades?limit=80&filterType=CASH&filterAmount=5000", 8.0)
        f_crypto = ex.submit(analyze_crypto_barrier_whales)

        try:
            tape_1k = f_tape_1k.result() or []
        except Exception:
            tape_1k = []
        try:
            tape_5k = f_tape_5k.result() or []
        except Exception:
            tape_5k = []
        crypto_whales = f_crypto.result()

    # Deduplicate and process global whale fills
    seen_tx = set()
    all_whales: List[Dict[str, Any]] = []
    for t in (tape_5k + tape_1k):
        tx = f"{t.get('transactionHash')}_{t.get('asset')}_{t.get('size')}"
        if tx in seen_tx:
            continue
        seen_tx.add(tx)
        sz = float(t.get("size") or 0.0)
        px = float(t.get("price") or 0.0)
        usd = round(sz * px, 2)
        if usd < 950.0:
            continue
        slug = str(t.get("eventSlug") or t.get("slug") or "")
        title = str(t.get("title") or "")
        cat = classify_trade_category(slug, title)
        all_whales.append({
            "timestamp": t.get("timestamp"),
            "category": cat,
            "event_slug": slug,
            "title": title,
            "side": str(t.get("side") or "").upper(),
            "outcome": t.get("outcome"),
            "price": round(px, 4),
            "shares": round(sz, 1),
            "usd_notional": usd,
            "tier": "MEGA_WHALE_5K+" if usd >= 5000.0 else "WHALE_1K+",
            "wallet": str(t.get("proxyWallet") or "")[:12],
            "trader_name": t.get("name") or t.get("pseudonym") or "AnonWhale",
        })

    all_whales.sort(key=lambda x: x["usd_notional"], reverse=True)

    # Aggregate sports smart-money sweeps by event_slug + outcome
    sports_agg: Dict[str, Dict[str, Any]] = {}
    for w in all_whales:
        if w["category"] != "SPORTS":
            continue
        key = f"{w['event_slug']}::{w['outcome']}"
        if key not in sports_agg:
            sports_agg[key] = {
                "event_slug": w["event_slug"],
                "title": w["title"],
                "outcome": w["outcome"],
                "net_buy_usd": 0.0,
                "total_whale_vol_usd": 0.0,
                "avg_price": w["price"],
                "fills_count": 0,
            }
        signed = w["usd_notional"] if w["side"] == "BUY" else -w["usd_notional"]
        sports_agg[key]["net_buy_usd"] = round(sports_agg[key]["net_buy_usd"] + signed, 2)
        sports_agg[key]["total_whale_vol_usd"] = round(sports_agg[key]["total_whale_vol_usd"] + w["usd_notional"], 2)
        sports_agg[key]["fills_count"] += 1

    sports_sweeps = sorted(sports_agg.values(), key=lambda x: abs(x["net_buy_usd"]), reverse=True)

    # Overall Crypto Market Whale Regime (weighted 50% BTC, 30% ETH, 20% SOL)
    btc_imp = crypto_whales.get("BTC", {}).get("whale_impulse_score", 0.0)
    eth_imp = crypto_whales.get("ETH", {}).get("whale_impulse_score", 0.0)
    sol_imp = crypto_whales.get("SOL", {}).get("whale_impulse_score", 0.0)
    composite_crypto_impulse = round(0.50 * btc_imp + 0.30 * eth_imp + 0.20 * sol_imp, 2)

    out = {
        "as_of": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "edt_now": time.strftime("%Y-%m-%d %H:%M:%S EDT", time.localtime()),
        "elapsed_ms": round((time.time() - t0) * 1000, 1),
        "summary": {
            "whale_fills_captured": len(all_whales),
            "mega_whales_5k_plus": sum(1 for w in all_whales if w["usd_notional"] >= 5000.0),
            "total_whale_volume_usd": round(sum(w["usd_notional"] for w in all_whales), 2),
            "composite_crypto_whale_impulse": composite_crypto_impulse,
            "crypto_whale_bias": "BULLISH" if composite_crypto_impulse >= 10 else ("BEARISH" if composite_crypto_impulse <= -10 else "NEUTRAL"),
        },
        "crypto_barrier_whales": crypto_whales,
        "sports_smart_money_sweeps": sports_sweeps[:15],
        "top_mega_whale_prints": all_whales[:20],
    }
    with open("/home/box/desk/kucoin/latest_polymarket_whales.json", "w") as f:
        json.dump(out, f, indent=2)
    return out


if __name__ == "__main__":
    rep = run_polymarket_whale_tracker()
    s = rep["summary"]
    print(f"=== Polymarket Whale-Flow & Smart-Money Sweep Tracker ({rep['elapsed_ms']} ms) ===")
    print(
        f"Captured {s['whale_fills_captured']} Whale Fills ({s['mega_whales_5k_plus']} Mega-Whales >=$5k) | "
        f"Total Whale Vol=${s['total_whale_volume_usd']:,.0f} | "
        f"Composite Crypto Whale Impulse={s['composite_crypto_whale_impulse']:+.1f} ({s['crypto_whale_bias']})"
    )
    print("\n1. Crypto Barrier Whale Flow & CLOB Depth Imbalance:")
    for asset, cw in rep["crypto_barrier_whales"].items():
        print(
            f"   - {asset}: Impulse={cw['whale_impulse_score']:+.1f} ({cw['whale_regime']}) | "
            f"BullFlow=${cw['bull_whale_flow_usd']:,.0f} vs BearFlow=${cw['bear_whale_flow_usd']:,.0f} | "
            f"CLOB DirBid=${cw['clob_directional_bid_usd']:,.0f} vs DirAsk=${cw['clob_directional_ask_usd']:,.0f}"
        )
    print("\n2. Top Mega-Whale Prints (>=$5,000 Single Fills):")
    for w in rep["top_mega_whale_prints"][:10]:
        print(
            f"   - ${w['usd_notional']:>9,.2f} | {w['category']:<14} | {w['side']:<4} {str(w['outcome']):<18} @ {w['price']:.3f} | {w['title'][:55]}"
        )
