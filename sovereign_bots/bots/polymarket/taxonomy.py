"""Taxonomy bot: what kind of question is this, and do we know how to price it?

Keyword scoring over the raw question text. If nothing recognizable comes back,
the bot abstains instead of pretending the contract is tradable.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Tuple

from ...core.bot import Bot, Vote

CATEGORIES: Tuple[str, ...] = (
    "politics",
    "crypto",
    "macro",
    "geopolitics",
    "sports",
    "entertainment",
    "weather",
    "other",
)

KEYWORDS: Dict[str, Tuple[str, ...]] = {
    "politics": (
        "election", "president", "presidential", "senate", "senator", "congress",
        "house", "governor", "parliament", "prime minister", "chancellor", "mayor",
        "vote", "poll", "ballot", "primary", "nominee", "impeach", "cabinet",
        "democrat", "republican", "tory", "labour", "coalition", "referendum",
    ),
    "crypto": (
        "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "doge",
        "crypto", "token", "altcoin", "stablecoin", "defi", "blockchain",
        "halving", "etf inflow", "spot etf", "binance", "coinbase", "on-chain",
    ),
    "macro": (
        "fed", "fomc", "rate cut", "rate hike", "interest rate", "inflation", "cpi",
        "pce", "gdp", "unemployment", "nonfarm", "payroll", "recession", "treasury",
        "yield", "ecb", "boj", "boe", "central bank", "tariff", "jobless",
        "s&p", "nasdaq", "index", "earnings", "oil", "gold",
    ),
    "geopolitics": (
        "war", "ceasefire", "truce", "invasion", "sanctions", "nato",
        "united nations", "taiwan", "ukraine", "russia", "china", "iran", "israel",
        "gaza", "north korea", "hostage", "airstrike", "missile", "treaty", "summit",
    ),
    "sports": (
        "nba", "nfl", "mlb", "nhl", "mls", "premier league", "champions league",
        "world cup", "super bowl", "stanley cup", "playoffs", "finals", "match",
        "game", "team", "goal", "touchdown", "f1", "grand prix", "ufc", "boxing",
        "tennis", "olympics", "wimbledon", "cricket", "series", "vs", "beat",
    ),
    "entertainment": (
        "oscar", "academy award", "grammy", "emmy", "box office", "movie", "film",
        "album", "song", "billboard", "spotify", "netflix", "taylor swift",
        "kardashian", "celebrity", "tv show", "series finale", "rotten tomatoes",
        "eurovision", "met gala",
    ),
    "weather": (
        "weather", "hurricane", "storm", "tropical", "temperature", "rain",
        "snow", "heatwave", "heat wave", "tornado", "flood", "drought", "noaa",
        "el nino", "la nina", "degrees", "celsius", "fahrenheit",
    ),
}

# Category priority when scores tie: the narrower domain wins.
_PRIORITY = ("weather", "crypto", "macro", "geopolitics", "sports",
             "entertainment", "politics", "other")


def _pattern(word: str) -> "re.Pattern[str]":
    # Whole-token match so "eth" does not fire on "whether" and "war" not on "award".
    # Optional plural suffix so "oscar" covers "Oscars" and "token" covers "tokens".
    return re.compile(r"(?<![a-z0-9])" + re.escape(word.strip())
                      + r"(?:s|es)?(?![a-z0-9])")


_PATTERNS: Dict[str, Tuple[Tuple[str, "re.Pattern[str]"], ...]] = {
    cat: tuple((w, _pattern(w)) for w in words) for cat, words in KEYWORDS.items()
}


def classify(question: str) -> Tuple[str, Dict[str, int], List[str]]:
    """Return (category, scores, matched_keywords)."""
    text = " ".join(str(question or "").lower().split())
    scores: Dict[str, int] = {c: 0 for c in CATEGORIES}
    matched: List[str] = []
    for cat, words in _PATTERNS.items():
        for word, pat in words:
            if pat.search(text):
                scores[cat] += 1
                matched.append(word)
    best = "other"
    best_score = 0
    for cat in _PRIORITY:
        if scores.get(cat, 0) > best_score:
            best, best_score = cat, scores[cat]
    return best, scores, matched


class TaxonomyBot(Bot):
    id = "taxonomy"
    mandate = "Name the question's domain before anyone prices it; abstain on unrecognized text."
    domains = ("polymarket",)
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        question = context.get("question")
        if not isinstance(question, str) or len(question.strip()) < 8:
            return Vote(self.id, "abstain", 0.0, ["missing_question"],
                        {"category": None})

        category, scores, matched = classify(question)
        declared = context.get("category")
        declared = declared.strip().lower() if isinstance(declared, str) else None

        evidence: Dict[str, Any] = {
            "category": category,
            "scores": {k: v for k, v in scores.items() if v},
            "matched": matched[:8],
            "declared_category": declared,
            "agrees_with_declared": (declared == category) if declared else None,
        }

        if category == "other" or not matched:
            return Vote(self.id, "abstain", 0.0,
                        ["unrecognized_category"], evidence)

        hits = scores[category]
        conf = min(0.90, 0.50 + 0.10 * hits)
        reasons = [f"category={category} hits={hits}"]

        if declared and declared in CATEGORIES and declared != "other":
            if declared == category:
                conf = min(0.95, conf + 0.10)
                reasons.append("declared_category_agrees")
            else:
                conf = max(0.30, conf - 0.15)
                reasons.append(f"declared_category_conflict:{declared}")
                evidence["conflict"] = True

        return Vote(self.id, "approve", conf, reasons, evidence)
