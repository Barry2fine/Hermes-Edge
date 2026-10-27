"""Zero-Key Free Public Market & Sports Intelligence Hub.

Provides 8 zero-key, zero-cost public data adapters for GitHub Actions and local
execution across Crypto (Hyperliquid, KuCoin Futures, Binance, Deribit DVOL/Skew,
Alternative.me Fear & Greed, DefiLlama Stablecoin Flow), Polymarket (Gamma + CLOB),
and Sports (ESPN Scoreboard, Odds & Player Game Logs).
"""

from __future__ import annotations
import json
import urllib.request
from typing import Any, Dict, List

FREE_API_REGISTRY: Dict[str, Dict[str, str]] = {
    "hyperliquid_info": {
        "url": "https://api.hyperliquid.xyz/info",
        "method": "POST",
        "auth": "none",
        "provides": "metaAndAssetCtxs, l2Book, funding, openInterest, markPx, oraclePx",
    },
    "kucoin_futures_public": {
        "url": "https://api-futures.kucoin.com/api/v1/contracts/active",
        "method": "GET",
        "auth": "none",
        "provides": "active perpetual contracts, markPrice, indexPrice, fundingFeeRate, turnoverOf24h",
    },
    "deribit_options_dvol": {
        "url": "https://www.deribit.com/api/v2/public/get_book_summary_by_currency?currency=BTC&kind=option",
        "method": "GET",
        "auth": "none",
        "provides": "BTC/ETH implied volatility surface, 25-delta put/call skew, open interest",
    },
    "binance_book_ticker": {
        "url": "https://api.binance.com/api/v3/ticker/bookTicker",
        "method": "GET",
        "auth": "none",
        "provides": "top-of-book bid/ask lead-lag reference across all major crypto pairs",
    },
    "polymarket_gamma": {
        "url": "https://gamma-api.polymarket.com/events?active=true&closed=false&limit=25",
        "method": "GET",
        "auth": "none",
        "provides": "live prediction market events, implied probabilities, volume, liquidity",
    },
    "espn_sports_core": {
        "url": "https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard",
        "method": "GET",
        "auth": "none",
        "provides": "live NBA/NHL/MLB/NFL lines, spreads, totals, injury reports, player gamelogs",
    },
    "alternative_fng": {
        "url": "https://api.alternative.me/fng/?limit=1",
        "method": "GET",
        "auth": "none",
        "provides": "daily crypto Fear & Greed sentiment index for narrative divergence checks",
    },
    "defillama_stablecoins": {
        "url": "https://stablecoins.llama.fi/stablecoins?includePrices=false",
        "method": "GET",
        "auth": "none",
        "provides": "aggregate stablecoin market-cap mint/burn liquidity impulse",
    },
}


def list_free_feeds() -> List[str]:
    return sorted(FREE_API_REGISTRY.keys())


def fetch_json(url: str, method: str = "GET", payload: Dict[str, Any] | None = None, timeout: int = 8) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"User-Agent": "SovereignAI-FreeFeeds/1.0", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))
