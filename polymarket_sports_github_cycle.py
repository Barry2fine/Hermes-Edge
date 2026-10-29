#!/usr/bin/env python3
"""Public-source observations only; absent model/committee evidence always abstains."""
from __future__ import annotations

import html
import json
import math
import os
from datetime import datetime, timezone
import urllib.request
from typing import Any, Dict

from sovereign_bots.core.telegram_alerts import send_institutional_telegram
from sovereign_bots.core.market_evidence import assess_market, market_request, observation, timestamp


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
    received_at = datetime.now(timezone.utc).isoformat()
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
            identity = str(market.get("id") or question)
            observed_at = market.get("updatedAt")
            source = "https://gamma-api.polymarket.com/markets"
            evidence = {
                "binary_prices": observation({"yes_price": prices[normalized.index("yes")], "no_price": prices[normalized.index("no")]},
                    {"yes_price": "probability", "no_price": "probability"}, source, identity, received_at, observed_at),
                "liquidity": observation({"liquidity_usd": liquidity, "volume_24h_usd": volume},
                    {"liquidity_usd": "USD", "volume_24h_usd": "USD"}, source, identity, received_at, observed_at),
            }
            if market.get("description") and market.get("endDate"):
                try:
                    days = (timestamp(market["endDate"]) - timestamp(received_at)).total_seconds() / 86400
                except (ValueError, TypeError):
                    days = None
                evidence["resolution"] = observation({"rules": market["description"], "days_to_resolve": days},
                    {"rules": "text", "days_to_resolve": "days"}, source, identity, received_at, observed_at)
            assessment = assess_market(market_request("polymarket.binary", identity, evidence))
            markets.append({
                "question": question, "yes_prob": prices[normalized.index("yes")],
                "volume_24h": volume, "liquidity": liquidity,
                "decision": "ABSTAIN", "status": "MODEL_AND_COMMITTEE_UNAVAILABLE",
                "edge_bps": None, "stake": 0.0, "market_evidence": assessment,
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
        received_at = datetime.now(timezone.utc).isoformat()
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
                identity = str(event.get("id") or name)
                evidence = {"team_odds": observation(
                    {"home_decimal": lines[0]["decimal_odds"], "away_decimal": lines[1]["decimal_odds"]},
                    {"home_decimal": "decimal_odds", "away_decimal": "decimal_odds"},
                    url + "#" + provider, identity, received_at, odds.get("lastUpdated"))}
                assessment = assess_market(market_request("sports.moneyline", identity, evidence))
                observations.append({
                    "matchup": f"[{league}] {name}", "book": provider, "lines": lines,
                    "fair_prob": None, "ev_pct": None, "quarter_kelly_units": 0.0,
                    "decision": "ABSTAIN", "status": "MODEL_AND_COMMITTEE_UNAVAILABLE", "market_evidence": assessment,
                })
            except (KeyError, IndexError, TypeError, ValueError, AttributeError, OverflowError):
                invalid += 1
    return {"total_events_scanned": total, "edges": observations[:6],
            "parlay_summary": None, "invalid_records": invalid,
            "status": "EMPTY" if total == 0 else "ABSTAIN"}


def scan_market(profile):
    scanners = {"polymarket.binary": scan_polymarket_gamma, "sports.moneyline": scan_sports_slate}
    if profile not in scanners:
        raise ValueError("unsupported acquisition profile")
    return scanners[profile]()


def _observation_card(label, result):
    lines = [f"<b>{html.escape(label)}: source observations</b>",
             "ABSTAIN — model probabilities and committee verdicts unavailable. Stake: 0.",
             f"Status: {result['status']}; invalid records: {result['invalid_records']}"]
    for row in result.get("markets", []) + result.get("edges", []):
        assessment = row.get("market_evidence", {})
        coverage = assessment.get("coverage", {})
        lines.append(f"Evidence {html.escape(str(assessment.get('profile', 'unknown')))}: "
                     f"{assessment.get('status', 'UNSUPPORTED')}; "
                     f"{coverage.get('available_required', 0)}/{coverage.get('required', 0)} required groups available")
        if assessment.get("reasons"):
            lines.append(html.escape(", ".join(assessment["reasons"])))
        calculations = assessment.get("calculations", {})
        if "two_way_overround" in calculations:
            lines.append(f"Observed two-way overround: {calculations['two_way_overround']:.4f} (not model edge)")
        if any(r.get("reason") == "collection_freshness_only" for r in assessment.get("requirements", {}).values()):
            lines.append("Collection timestamp only; source quote time unverified.")
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
    for label, profile in (("Polymarket", "polymarket.binary"), ("Sports", "sports.moneyline")):
        try:
            result = scan_market(profile)
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
