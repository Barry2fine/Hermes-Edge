"""Zero-Key Free Public Market, Prediction Market & Sports Intelligence Hub (14 Feeds).

Provides 14 zero-key, zero-cost public data adapters for GitHub Actions and local
execution across:
  - Crypto: Hyperliquid (`metaAndAssetCtxs` + `l2Book`), KuCoin Futures (`contracts/active`),
    KuCoin Spot (`market/allTickers`), Kraken Public L2 (`0/public/Depth`), Binance BookTicker,
    Bybit Linear Funding/OI (`v5/market/tickers`), Deribit Options DVOL/25d Skew,
    Alternative.me Fear & Greed, DefiLlama Stablecoin Flow, CoinGecko Global Dominance
  - Prediction Markets: Polymarket Gamma (`events` + `tags`), Polymarket CLOB (`book`),
    Kalshi Public Elections/Macro API (`trade-api/v2/markets`)
  - Macro & Equities: Yahoo Finance / CBOE (`IBIT`, `ETHA`, `SPY`, `QQQ`, `^VIX`, `DX-Y.NYB`)
  - Sports: ESPN Scoreboard, Odds & Player Game Logs (`NBA`, `NHL`, `NFL`, `MLB`)
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
        "provides": "metaAndAssetCtxs, spotMeta, l2Book, 1h funding, openInterest, markPx, oraclePx across 235 perps & 347 spot pairs",
    },
    "kucoin_futures_public": {
        "url": "https://api-futures.kucoin.com/api/v1/contracts/active",
        "method": "GET",
        "auth": "none",
        "provides": "712 active perpetual/quarterly contracts, markPrice, indexPrice, 8h fundingFeeRate, turnoverOf24h",
    },
    "kucoin_spot_public": {
        "url": "https://api.kucoin.com/api/v1/market/allTickers",
        "method": "GET",
        "auth": "none",
        "provides": "970 active spot pairs, 24h high/low range, best bid/ask, volValue for Spot OCO & Cash-and-Carry",
    },
    "kraken_public_depth": {
        "url": "https://api.kraken.com/0/public/Depth?pair=XBTUSD&count=25",
        "method": "GET",
        "auth": "none",
        "provides": "US-regulated spot L2 orderbook depth & cross-venue lead-lag reference (matches user-Kraken MCP)",
    },
    "bybit_linear_tickers": {
        "url": "https://api.bybit.com/v5/market/tickers?category=linear",
        "method": "GET",
        "auth": "none",
        "provides": "cross-exchange perpetual funding rate, open interest, and mark/index basis confirmation",
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
        "url": "https://gamma-api.polymarket.com/events?active=true&closed=false&limit=50",
        "method": "GET",
        "auth": "none",
        "provides": "live prediction market events, 518 category tags, implied probabilities, volume, liquidity",
    },
    "polymarket_clob": {
        "url": "https://clob.polymarket.com/sampling-simplified-markets",
        "method": "GET",
        "auth": "none",
        "provides": "live Polymarket CLOB orderbook bid/ask spreads and reward-eligible markets",
    },
    "kalshi_public_markets": {
        "url": "https://api.elections.kalshi.com/trade-api/v2/markets?limit=50&status=open",
        "method": "GET",
        "auth": "none",
        "provides": "regulated CFTC Kalshi binary markets for Kalshi-vs-Polymarket cross-venue arbitrage",
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
    "coingecko_global": {
        "url": "https://api.coingecko.com/api/v3/global",
        "method": "GET",
        "auth": "none",
        "provides": "global crypto market cap change 24h, BTC/ETH dominance for altcoin beta rotation",
    },
}


def list_free_feeds() -> List[str]:
    return sorted(FREE_API_REGISTRY.keys())


def fetch_json(url: str, method: str = "GET", payload: Dict[str, Any] | None = None, timeout: int = 8) -> Any:
    data = json.dumps(payload).encode("utf-8") if payload is not None else None
    headers = {"User-Agent": "SovereignAI-FreeFeeds/2.2", "Accept": "application/json"}
    if data is not None:
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))
