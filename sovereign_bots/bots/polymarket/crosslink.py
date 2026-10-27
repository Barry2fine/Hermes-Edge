"""Crosslink bot: does this contract line up with another live desk signal?

A Polymarket position is more trustworthy when a directional crypto bias, a
sports slate or a macro calendar independently points the same way. Pure
context bot: no veto, abstains when the linkage data is absent.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List, Optional

from ...core.bot import Bot, Vote

_CRYPTO_TOKENS = (
    "bitcoin", "btc", "ethereum", "eth", "solana", "sol", "xrp", "doge",
    "crypto", "altcoin", "stablecoin", "defi", "blockchain", "halving",
)
_SPORTS_TOKENS = (
    "nba", "nfl", "mlb", "nhl", "premier league", "champions league", "world cup",
    "super bowl", "playoffs", "finals", "match", "game", "ufc", "f1", "grand prix",
    "tennis", "olympics", "cricket", "vs",
)
_MACRO_TOKENS = (
    "fed", "fomc", "rate cut", "rate hike", "inflation", "cpi", "pce", "gdp",
    "unemployment", "payroll", "recession", "treasury", "yield", "ecb", "boj",
    "central bank", "tariff", "s&p", "nasdaq", "earnings",
)

_TOKEN_RE = re.compile(r"[a-z0-9][a-z0-9&.'-]*")
_STOPWORDS = frozenset({"the", "and", "for", "with", "from", "its", "their",
                        "this", "that", "release", "event", "date"})


def _hits(text: str, words) -> List[str]:
    return [w for w in words if re.search(r"(?<![a-z0-9])" + re.escape(w) + r"(?![a-z0-9])", text)]


def _overlap(text: str, items: List[str]) -> List[str]:
    """Which named events/teams from a list are actually mentioned in the question."""
    out = []
    for raw in items:
        item = str(raw or "").strip().lower()
        if not item:
            continue
        if re.search(r"(?<![a-z0-9])" + re.escape(item) + r"(?![a-z0-9])", text):
            out.append(item)
            continue
        # Multi-word names: any distinctive token (>=4 chars) may be enough.
        toks = [t for t in _TOKEN_RE.findall(item)
                if len(t) >= 3 and t not in _STOPWORDS]
        if toks and any(re.search(r"(?<![a-z0-9])" + re.escape(t) + r"(?![a-z0-9])", text)
                        for t in toks):
            out.append(item)
    return out


def _as_list(value: Any) -> Optional[List[Any]]:
    if value is None:
        return None
    if isinstance(value, (list, tuple, set)):
        return [v for v in value if v is not None and str(v).strip()]
    if isinstance(value, str):
        parts = [p.strip() for p in value.split(",")]
        return [p for p in parts if p]
    return None


class CrosslinkBot(Bot):
    id = "crosslink"
    mandate = "Prefer contracts corroborated by a live crypto bias, sports slate or macro calendar."
    domains = ("polymarket",)
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        question = " ".join(str(context.get("question") or "").lower().split())
        category = context.get("category")
        category = category.strip().lower() if isinstance(category, str) else ""

        bias = context.get("crypto_bias")
        bias = bias.strip().lower() if isinstance(bias, str) else ""
        regime = context.get("crypto_regime")
        related_sports = _as_list(context.get("related_sports"))
        macro_events = _as_list(context.get("macro_events"))

        links: List[str] = []
        strengths: List[float] = []
        notes: List[str] = []
        evidence: Dict[str, Any] = {"links": links, "category": category or None,
                                    "crypto_bias": bias or None}

        crypto_topic = category == "crypto" or bool(_hits(question, _CRYPTO_TOKENS))
        sports_topic = category == "sports" or bool(_hits(question, _SPORTS_TOKENS))
        macro_topic = category == "macro" or bool(_hits(question, _MACRO_TOKENS))

        if crypto_topic:
            if bias in ("long", "short"):
                named = _hits(question, _CRYPTO_TOKENS)
                strength = 1.0 if (category == "crypto" and named) else 0.85
                links.append("crypto")
                strengths.append(strength)
                notes.append(f"crypto_bias={bias}"
                             + (f",regime={regime}" if isinstance(regime, str) and regime else ""))
            else:
                evidence["crypto_unlinked"] = "bias_not_directional"

        if sports_topic:
            if related_sports:
                matched = _overlap(question, related_sports)
                if matched:
                    links.append("sports")
                    strengths.append(1.0)
                    notes.append("sports_match=" + "|".join(matched[:3]))
                    evidence["sports_matched"] = matched[:5]
                else:
                    evidence["sports_unlinked"] = "no_slate_match"
            else:
                evidence["sports_unlinked"] = "no_related_sports"

        if macro_topic:
            if macro_events:
                matched = _overlap(question, macro_events)
                if matched:
                    links.append("macro")
                    strengths.append(1.0 if len(matched) > 1 else 0.9)
                    notes.append("macro_match=" + "|".join(matched[:3]))
                    evidence["macro_matched"] = matched[:5]
                else:
                    evidence["macro_unlinked"] = "no_calendar_match"
            else:
                evidence["macro_unlinked"] = "no_macro_events"

        if not links:
            topic = ("crypto" if crypto_topic else
                     "sports" if sports_topic else
                     "macro" if macro_topic else None)
            reason = f"no_crosslink:{topic}" if topic else "no_linked_domain"
            return Vote(self.id, "abstain", 0.0, [reason], evidence)

        conf = min(0.92, 0.50 + 0.20 * sum(strengths) + 0.05 * (len(links) - 1))
        evidence["strength"] = round(sum(strengths), 3)
        return Vote(self.id, "approve", conf,
                    notes + [f"links={len(links)}"], evidence)
