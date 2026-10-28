#!/usr/bin/env python3
"""Public-source observations only; absent model/committee evidence always abstains."""
from __future__ import annotations

import html
import json
import math
import os
import urllib.request
from typing import Any, Dict

from sovereign_bots.core.telegram_alerts import send_institutional_telegram


def _http_json(url: str, timeout: int = 12) -> Any:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "SovereignAI-MultiDesk-Daemon/2.0", "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode("utf-8"))


def _number(value):
    if isinstance(value, bool):
        raise ValueError("boolean is not numeric evidence")
    number = float(value)
    if not math.isfinite(number):
        raise ValueError("nonfinite evidence")
    return number


def _array(value):
    value = json.loads(value) if isinstance(value, str) else value
    if not isinstance(value, list):
        raise ValueError("expected array")
    return value


def scan_polymarket_gamma(limit: int = 40) -> Dict[str, Any]:
    raw = _http_json(f"https://gamma-api.polymarket.com/markets?active=true&closed=false&limit={limit}")
    if not isinstance(raw, list):
        raise ValueError("invalid Gamma response")
    markets = []
    invalid = 0
    for market in raw:
        try:
            if not isinstance(market, dict):
                raise ValueError("invalid market")
            question = market.get("question") or market.get("title")
            if not isinstance(question, str) or not question.strip():
                raise ValueError("missing question")
            labels = _array(market.get("outcomes"))
            prices = _array(market.get("outcomePrices"))
            normalized = [label.strip().lower() for label in labels if isinstance(label, str)]
            if len(labels) != 2 or sorted(normalized) != ["no", "yes"] or len(prices) != 2:
                raise ValueError("unavailable binary outcome mapping")
            prices = [_number(price) for price in prices]
            if any(not 0 < price < 1 for price in prices):
                raise ValueError("invalid outcome price")
            volume = _number(market.get("volume24hr"))
            liquidity = _number(market.get("liquidityNum", market.get("liquidity")))
            if volume < 0 or liquidity < 0:
                raise ValueError("negative volume/liquidity")
            markets.append({
                "question": question, "yes_prob": prices[normalized.index("yes")],
                "volume_24h": volume, "liquidity": liquidity,
                "decision": "ABSTAIN", "status": "MODEL_AND_COMMITTEE_UNAVAILABLE",
                "edge_bps": None, "stake": 0.0,
            })
        except (TypeError, ValueError, OverflowError):
            invalid += 1
    return {"total_scanned": len(raw), "markets": markets[:5], "invalid_records": invalid,
            "status": "EMPTY" if not raw else "ABSTAIN"}


def _ml_to_dec(ml) -> float:
    ml = _number(ml)
    if not ml.is_integer() or abs(ml) < 100:
        raise ValueError("invalid American moneyline")
    return 1.0 + (100.0 / abs(ml) if ml < 0 else ml / 100.0)


def scan_sports_slate() -> Dict[str, Any]:
    from datetime import datetime
    from zoneinfo import ZoneInfo
    today_str = datetime.now(ZoneInfo("America/Toronto")).strftime("%Y%m%d")
    leagues = [
        ("NBA", f"https://site.api.espn.com/apis/site/v2/sports/basketball/nba/scoreboard?dates={today_str}"),
        ("NHL", f"https://site.api.espn.com/apis/site/v2/sports/hockey/nhl/scoreboard?dates={today_str}"),
    ]
    total = 0
    observations = []
    invalid = 0
    for league, url in leagues:
        data = _http_json(url)
        if not isinstance(data, dict) or not isinstance(data.get("events"), list):
            raise ValueError(f"invalid {league} scoreboard response")
        for event in data["events"]:
            total += 1
            try:
                name = event.get("shortName") or event.get("name")
                if not isinstance(name, str) or not name.strip():
                    raise ValueError("missing event label")
                competition = event["competitions"][0]
                odds = competition["odds"][0]
                provider = odds["provider"]["name"]
                if not isinstance(provider, str) or not provider.strip():
                    raise ValueError("missing bookmaker")
                competitors = competition["competitors"]
                labels = {c["homeAway"]: c["team"]["displayName"] for c in competitors}
                if not all(isinstance(labels.get(side), str) and labels[side].strip() for side in ("home", "away")):
                    raise ValueError("missing outcome labels")
                lines = []
                for side in ("home", "away"):
                    american = odds[f"{side}TeamOdds"]["moneyLine"]
                    lines.append({"selection": labels[side], "decimal_odds": _ml_to_dec(american)})
                observations.append({
                    "matchup": f"[{league}] {name}", "book": provider, "lines": lines,
                    "fair_prob": None, "ev_pct": None, "quarter_kelly_units": 0.0,
                    "decision": "ABSTAIN", "status": "MODEL_AND_COMMITTEE_UNAVAILABLE",
                })
            except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError):
                invalid += 1
    return {"total_events_scanned": total, "edges": observations[:6],
            "parlay_summary": None, "invalid_records": invalid,
            "status": "EMPTY" if total == 0 else "ABSTAIN"}


def _observation_card(label, result):
    lines = [f"<b>{html.escape(label)}: source observations</b>",
             "ABSTAIN — model probabilities and committee verdicts unavailable. Stake: 0.",
             f"Status: {result['status']}; invalid records: {result['invalid_records']}"]
    for market in result.get("markets", []):
        lines.append(f"{html.escape(market['question'][:100])}: YES price {market['yes_prob']:.3f}; "
                     f"24h volume {market['volume_24h']:.0f}; liquidity {market['liquidity']:.0f}")
    for event in result.get("edges", []):
        lines.append(f"{html.escape(event['matchup'][:100])} — {html.escape(event['book'][:60])}")
        for line in event['lines']:
            lines.append(f"  {html.escape(line['selection'][:60])}: decimal odds {line['decimal_odds']:.3f}")
    return "\n".join(lines)


def main() -> int:
    send = os.environ.get("SEND_TELEGRAM", "1").strip().lower() not in ("0", "false", "no", "off")
    failed = False
    for label, scan in (("Polymarket", scan_polymarket_gamma), ("Sports", scan_sports_slate)):
        try:
            result = scan()
        except Exception as exc:
            print(f"[{label}] ACQUISITION_FAILED: {type(exc).__name__}")
            failed = True
            continue
        print(f"[{label}] {result['status']}; invalid records: {result['invalid_records']}")
        if send:
            try:
                delivery = send_institutional_telegram(_observation_card(label, result))
                if not isinstance(delivery, dict) or delivery.get("ok") is not True:
                    failed = True
                    print(f"[{label}] DELIVERY_FAILED")
            except Exception as exc:
                failed = True
                print(f"[{label}] DELIVERY_FAILED: {type(exc).__name__}")
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
