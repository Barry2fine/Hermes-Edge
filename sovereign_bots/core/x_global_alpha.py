"""
X Global Alpha & Breaking-News Intelligence Engine.
Monitors curated worldwide X accounts and news catalysts across:
  1. Sports & Betting (`@ShamsCharania`, `@AdamSchefter`, `@OptaAnalyst`, NBA/NHL/NFL/MLB/WNBA injury & lineup wires)
  2. Crypto & Trading (`@WatcherGuru`, `@whale_alert`, `@tier10k`, `@unusual_whales`, `@cobie`, on-chain mints/transfers & macro)
  3. Polymarket & Prediction Markets (`@Polymarket`, `@Kalshi`, geopolitical, election, regulatory, and cross-exchange arbs)

Uses TypeSafe System One (`jev-latest`) via `typesafe_client.py` to answer atomic calibrated
questions (`noul` / `choice`) on each catalyst, then maps those probabilities through a
deterministic truth table into cross-desk profit opportunities.
"""

from __future__ import annotations

import html
from typing import Any, Dict, List, Optional, Sequence

from sovereign_bots.core.typesafe_client import query_system_one

# Canonical roster of high-signal worldwide X accounts by desk
CURATED_X_WATCHLIST: Dict[str, List[Dict[str, str]]] = {
    "polymarket_prediction": [
        {"username": "Polymarket", "id": "1261335549215989760", "focus": "Global prediction market catalysts & odds shifts"},
        {"username": "Kalshi", "id": "1071484733543079936", "focus": "CFTC-regulated event contracts & cross-exchange arb"},
    ],
    "sports_betting": [
        {"username": "ShamsCharania", "id": "178580925", "focus": "NBA breaking injuries, starters, scratches & trades"},
        {"username": "AdamSchefter", "id": "51263592", "focus": "NFL breaking injury reports, practice status & actives"},
        {"username": "OptaAnalyst", "id": "179500584", "focus": "Data-driven xG, pace, and player efficiency models"},
    ],
    "crypto_trading": [
        {"username": "tier10k", "id": "2361601055", "focus": "Tier-1 breaking macro, SEC/CFTC, ETF & crypto headlines"},
        {"username": "WatcherGuru", "id": "1387497871751196672", "focus": "Real-time global finance, Fed & crypto alerts"},
        {"username": "whale_alert", "id": "1039833297751302144", "focus": "Large Treasury mints, Aave/CEX whale transfers"},
        {"username": "unusual_whales", "id": "1200616796295847936", "focus": "Options flow, GEX, sector rotation & macro catalysts"},
        {"username": "cobie", "id": "2259434528", "focus": "Crypto market structure & regime commentary"},
    ],
}


def evaluate_x_signals_with_typesafe(
    items: Sequence[Dict[str, Any]],
    *,
    api_key: Optional[str] = None,
) -> List[Dict[str, Any]]:
    """
    Evaluate a batch of X posts / news stories using TypeSafe System One (`jev-latest`).
    Following the `typesafe-ai` skill:
      - Jev makes atomic calibrated judgments (`is_breaking_factual_event`, `has_direct_price_or_line_catalyst`,
        `already_priced_in_or_stale_opinion`, `target_desk`, `direction_bias`).
      - Deterministic Python truth table decides whether the item is an actionable opportunity (`ACTIONABLE_EDGE`,
        `WATCH_CATALYST`, or `IGNORE_NOISE`).
    """
    evaluated: List[Dict[str, Any]] = []
    for item in items:
        text = str(item.get("text") or item.get("summary") or item.get("name") or "").strip()
        if not text:
            continue
        author = str(item.get("username") or item.get("category") or "X").strip()
        url = str(item.get("url") or "").strip()

        state = {
            "source_handle": author,
            "post_text": text,
            "headline": str(item.get("name") or ""),
        }
        questions = {
            "is_concrete_event": {
                "type": "noul",
                "instructions": "Does `post_text` report a concrete factual event, transaction, injury/roster update, or official announcement rather than a joke, poll, or general opinion?",
            },
            "moves_market_odds_or_price": {
                "type": "noul",
                "instructions": "Would a trader on a sportsbook, cryptocurrency exchange, or prediction market expect `post_text` to shift a line, probability, or asset price?",
            },
            "primary_desk": {
                "type": "choice",
                "instructions": "Which trading desk is most directly affected by `post_text`?",
                "criteria": {
                    "crypto_perp_spot": "Cryptocurrency prices, stablecoin mints, on-chain whale transfers, or AI/tech equity/token catalysts.",
                    "polymarket_event": "Geopolitics, elections, regulation, corporate deals, or prediction-market exchange announcements.",
                    "sports_betting": "NBA, NFL, NHL, MLB, or WNBA player injuries, starting lineups, arena/schedule changes, or team odds.",
                },
            },
            "directional_lean": {
                "type": "choice",
                "instructions": "What directional impact does `post_text` imply for its primary subject?",
                "criteria": {
                    "bullish_or_yes_up": "Positive catalyst, large stablecoin mint/inflow, player returning from injury, or rising probability.",
                    "bearish_or_no_up": "Negative catalyst, player ruled out, regulatory crackdown, or falling probability.",
                    "neutral_or_structural": "Structural market update or two-sided news without a single directional lean.",
                },
            },
        }

        res = query_system_one(state=state, questions=questions, api_key=api_key)
        if res.get("ok"):
            ans = res.get("answers", {})
            concrete_p = float((ans.get("is_concrete_event") or {}).get("noul") or 0.0)
            impact_p = float((ans.get("moves_market_odds_or_price") or {}).get("noul") or 0.0)
            desk = str((ans.get("primary_desk") or {}).get("choice") or "polymarket_event")
            lean = str((ans.get("directional_lean") or {}).get("choice") or "neutral_or_structural")
        else:
            concrete_p = 0.5
            impact_p = 0.5
            desk = "polymarket_event"
            lean = "neutral_or_structural"

        # Deterministic truth table in code (per `typesafe-ai` skill: 0.7 / 0.3 Noul thresholds)
        composite_score = round(0.55 * impact_p + 0.45 * concrete_p, 3)
        if concrete_p >= 0.70 and impact_p >= 0.65:
            tier = "ACTIONABLE_ALPHA"
        elif concrete_p >= 0.55 and impact_p >= 0.45:
            tier = "WATCH_CATALYST"
        else:
            tier = "NOISE_FILTERED"

        evaluated.append(
            {
                "source": author,
                "text": text,
                "headline": str(item.get("name") or ""),
                "url": url,
                "concrete_noul": round(concrete_p, 2),
                "impact_noul": round(impact_p, 2),
                "composite_score": composite_score,
                "desk": desk,
                "directional_lean": lean,
                "tier": tier,
            }
        )

    evaluated.sort(key=lambda x: x["composite_score"], reverse=True)
    return evaluated


def format_x_global_alpha_card(signals: Sequence[Dict[str, Any]]) -> str:
    """Format an institutional Telegram HTML card for X Global Alpha opportunities."""
    lines = [
        "🌍 <b>SOVEREIGN AI • X GLOBAL ALPHA &amp; CATALYST DESK</b>",
        "⚡ <b>TYPESAFE JEV-1.14 CALIBRATED WORLDWIDE SIGNAL SCAN</b>",
        "──────────────────────────────",
    ]
    actionable = [s for s in signals if s.get("tier") in ("ACTIONABLE_ALPHA", "WATCH_CATALYST")]
    if not actionable:
        lines.append("• <b>Status:</b> <code>No X catalysts cleared the Jev-1.14 concrete + market-impact truth table</code>")
    else:
        for idx, sig in enumerate(actionable[:6], 1):
            src = html.escape(str(sig.get("source") or "X"))
            desk = html.escape(str(sig.get("desk") or "").upper())
            lean = html.escape(str(sig.get("directional_lean") or ""))
            tier = html.escape(str(sig.get("tier") or ""))
            snippet = html.escape(str(sig.get("headline") or sig.get("text") or "")[:115])
            c_noul = float(sig.get("concrete_noul") or 0.0)
            i_noul = float(sig.get("impact_noul") or 0.0)
            lines.append(
                f"<b>{idx}. [{desk}] @{src}</b> — <code>{tier}</code>\n"
                f"   ▫️ {snippet}\n"
                f"   ▫️ Jev Noul: Concrete <code>{c_noul:.2f}</code> | Impact <code>{i_noul:.2f}</code> | Lean: <code>{lean}</code>"
            )
    return "\n".join(lines)
