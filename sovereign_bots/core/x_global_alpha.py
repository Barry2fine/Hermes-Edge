"""
X Global Alpha Ingestion & Multi-Domain Routing Engine (`x_global_alpha.py`).

Bridges live X / Twitter signals from the `@barrypentagram` curated X Lists:
  - Sovereign Global Alpha (`2115237148941066370`)
  - Alpha: Sports & Injury (`2115237100127674820`)
  - Alpha: Crypto & Macro (`2115237160525733938`)
  - Alpha: Polymarket & Macro (`2115237125251624970`)

Every breaking post is evaluated through TypeSafe System One (`jev-latest`)
using a bundled 6-question speculative truth-table call:
  1. `concrete_event` (noul): Confirmed official injury/lineup/macro/on-chain event?
  2. `market_impact` (noul): Materially shifts probabilities, player usage, or perp price?
  3. `already_priced_in` (noul): Is this recap/stale news already priced in?
  4. `ambiguity_or_rumor` (noul): Is this unverified speculation, parody, or engagement bait?
  5. `primary_desk` (choice): `sports_betting`, `crypto_perp_spot`, or `polymarket_event`
  6. `directional_lean` (choice): `bullish_over_yes`, `bearish_under_no`, or `neutral_volatility`
"""

from __future__ import annotations

from typing import Any, Dict, List

from sovereign_bots.core.domain_tables import (
    CROSS_DOMAIN_SPILLOVER_TABLE,
    evaluate_multi_domain_signal_truth_table,
)
from sovereign_bots.core.typesafe_client import query_system_one

X_ALPHA_LISTS: Dict[str, str] = {
    "sovereign_global_alpha": "2115237148941066370",
    "sports_and_injury": "2115237100127674820",
    "crypto_and_macro": "2115237160525733938",
    "polymarket_and_macro": "2115237125251624970",
}

CORE_ALPHA_HANDLES: Dict[str, List[str]] = {
    "sports_betting": [
        "ShamsCharania",
        "AdamSchefter",
        "OptaAnalyst",
        "PassTheBall",
        "TheDunkCentral",
        "FriedgeHNIC",
        "JeffPassan",
    ],
    "crypto_perp_spot": [
        "tier10k",
        "WatcherGuru",
        "whale_alert",
        "unusual_whales",
        "cobie",
        "KobeissiLetter",
    ],
    "polymarket_event": [
        "Polymarket",
        "Kalshi",
        "Domahhhh",
        "bennpeifert",
    ],
}


def evaluate_x_signal_with_typesafe(
    author_handle: str,
    tweet_text: str,
    created_at: str = "",
    metrics: Dict[str, Any] | None = None,
) -> Dict[str, Any]:
    """
    Run a bundled 6-question TypeSafe System One (`jev-latest`) truth-table evaluation
    on an X post and route it across Sports Betting, Crypto, and Polymarket desks
    including cross-domain spillover targets.
    """
    m = metrics or {}
    state = {
        "author_handle": f"@{author_handle}",
        "created_at": created_at or "live",
        "likes": int(m.get("like_count", 0)),
        "retweets": int(m.get("retweet_count", 0)),
        "post_text": tweet_text,
    }
    questions = {
        "concrete_event": {
            "type": "noul",
            "instructions": "Does `post_text` report a concrete, verifiable real-world event (official injury/lineup change, regulatory/macro announcement, or confirmed on-chain/ETF flow)?",
        },
        "market_impact": {
            "type": "noul",
            "instructions": "Does the event in `post_text` materially shift the fair probability, player usage/minutes projection, or asset price of a live sports, prediction, or crypto market?",
        },
        "already_priced_in": {
            "type": "noul",
            "instructions": "Is `post_text` merely a retrospective box-score recap, commentary, or stale summary of an event that already finished?",
        },
        "ambiguity_or_rumor": {
            "type": "noul",
            "instructions": "Is `post_text` unverified rumor, joke, parody, or engagement bait without a credible source?",
        },
        "primary_desk": {
            "type": "choice",
            "instructions": "Which trading desk is the primary beneficiary of the information in `post_text`?",
            "criteria": {
                "sports_betting": "Professional sports injuries, starting lineups, roster moves, or game matchups.",
                "crypto_perp_spot": "Cryptocurrency whale transfers, ETF inflows/outflows, token launches, or exchange/macro liquidity.",
                "polymarket_event": "Elections, geopolitics, legislation, court rulings, corporate earnings/AI deals, or prediction market odds.",
            },
        },
        "directional_lean": {
            "type": "choice",
            "instructions": "What is the immediate directional implication of `post_text` for the primary subject mentioned?",
            "criteria": {
                "bullish_over_yes": "Positive catalyst, player activation/return, institutional buying inflow, or higher probability of YES.",
                "bearish_under_no": "Negative catalyst, player injury/absence, exchange dump/outflow, or lower probability of YES.",
                "neutral_volatility": "Two-sided or ambiguous catalyst that increases volatility without a single clear direction.",
            },
        },
    }
    res = query_system_one(state=state, questions=questions)
    ans = res.get("answers") or {}
    if not res.get("ok") or not ans:
        return {
            "ok": False,
            "tier": "UNVERIFIED",
            "action": "HOLD_MANUAL_REVIEW",
            "author": author_handle,
            "text": tweet_text,
            "error": res.get("error", "incomplete_answers"),
        }

    concrete_noul = float((ans.get("concrete_event") or {}).get("noul") or 0.0)
    impact_noul = float((ans.get("market_impact") or {}).get("noul") or 0.0)
    already_priced_in_noul = float((ans.get("already_priced_in") or {}).get("noul") or 0.5)
    ambiguity_or_rumor_noul = float((ans.get("ambiguity_or_rumor") or {}).get("noul") or 0.5)
    desk_obj = ans.get("primary_desk") or {}
    lean_obj = ans.get("directional_lean") or {}

    primary_desk = desk_obj.get("choice") or "polymarket_event"
    directional_lean = lean_obj.get("choice") or "neutral_volatility"

    decision = evaluate_multi_domain_signal_truth_table(
        concrete_noul=concrete_noul,
        impact_noul=impact_noul,
        already_priced_in_noul=already_priced_in_noul,
        ambiguity_or_rumor_noul=ambiguity_or_rumor_noul,
        primary_desk=primary_desk,
        directional_lean=directional_lean,
    )

    return {
        "ok": True,
        "model": res.get("model"),
        "author": author_handle,
        "text": tweet_text,
        "concrete_noul": round(concrete_noul, 4),
        "impact_noul": round(impact_noul, 4),
        "already_priced_in_noul": round(already_priced_in_noul, 4),
        "ambiguity_or_rumor_noul": round(ambiguity_or_rumor_noul, 4),
        "primary_desk": primary_desk,
        "desk_distribution": desk_obj.get("distribution", {}),
        "directional_lean": directional_lean,
        "lean_distribution": lean_obj.get("distribution", {}),
        "CrossDomainSpilloverRules": len(CROSS_DOMAIN_SPILLOVER_TABLE),
        **decision,
    }
