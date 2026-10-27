"""Resolution-clarity bot — if the rules are vague, the trade is a coin flip.

Open research found no implementation that grades rule ambiguity, so this is a
deliberately simple, auditable five-flag detector. Each flag is something a
human settler would have to guess about:

1. **No named settlement authority** — who decides? (AP, official body, exchange staff)
2. **No numeric threshold or comparator** — "substantially higher" is not a level
3. **No date/deadline or timezone** — when is it measured?
4. **Hedge words** — "substantially", "credible reports", "in the judgement of"
5. **No source of record** — which publication or index settles it?

Score 0–5. **Two or more flags => veto.** One flag => trade only with reduced
confidence. Zero => clean.
"""

from __future__ import annotations

import re
from typing import Any, Dict, List

from ...core.bot import Bot, Vote

HEDGES = (
    "substantially", "materially", "credible reports", "in the judgement of",
    "in the judgment of", "reasonably", "generally understood", "significant",
    "widely reported", "as determined by", "at its discretion", "roughly",
    "approximately", "arguably", "if applicable",
)

AUTHORITIES = (
    "associated press", "reuters", "bloomberg", "official", "board of",
    "federal reserve", "bureau of labor statistics", "election commission",
    "exchange staff", "kalshi staff", "polymarket", "uma", "coinbase",
    "binance", "chainlink", "cme", "nielsen", "osc", "agco", "league office",
)

SOURCES = (
    "ap", "reuters", "bloomberg", "official website", "press release",
    "coinbase", "binance", "kraken", "chainlink", "cme group", ".gov",
    ".org", "sec filing", "company filing",
)

MONTHS = ("january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december",
          "jan", "feb", "mar", "apr", "jun", "jul", "aug", "sep", "sept",
          "oct", "nov", "dec")

COMPARATORS = ("above", "below", "at least", "at most", "greater than",
               "less than", "exceed", "reach", "close at", "over", "under",
               "before", "after", "by ", "on or before", "no later than")

_NUM = re.compile(r"\d")


def grade(question: str, rules: str = "") -> Dict[str, Any]:
    text = " ".join(f"{question or ''} {rules or ''}".lower().split())
    has_rules = bool((rules or "").strip())
    flags: List[str] = []

    # Authority and source-of-record can only be judged when rules were supplied.
    # Grading them from the question alone would reject every well-formed market.
    has_authority = any(a in text for a in AUTHORITIES)
    if has_rules and not has_authority:
        flags.append("no_settlement_authority")

    has_number = bool(_NUM.search(text))
    has_comparator = any(c in text for c in COMPARATORS)
    if not (has_number and has_comparator):
        flags.append("no_numeric_threshold")

    has_date = any(m in text for m in MONTHS) or bool(re.search(r"\d{4}-\d{2}-\d{2}", text))
    if not has_date:
        flags.append("no_deadline")

    hedges = [h for h in HEDGES if h in text]
    if hedges:
        flags.append("hedge_language")

    has_source = any(s in text for s in SOURCES)
    if has_rules and not has_source:
        flags.append("no_source_of_record")

    return {"flags": flags, "score": len(flags), "hedges": hedges[:5],
            "has_authority": has_authority, "has_threshold": has_number and has_comparator,
            "has_date": has_date, "has_source": has_source}


class ResolutionClarityBot(Bot):
    id = "resolution_clarity"
    mandate = ("A vague contract is a coin flip with fees: refuse to trade when "
               "the settlement rules are ambiguous in two or more ways.")
    domains = ("polymarket",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        question = ctx.get("question")
        rules = ctx.get("resolution_rules") or ctx.get("rules") or ""
        evidence: Dict[str, Any] = {"question": question}
        if not isinstance(question, str) or len(question.strip()) < 10:
            return Vote(self.id, "reject", 0.90, ["missing_question"], evidence)

        g = grade(question, str(rules))
        evidence.update(g)

        if g["score"] >= 2:
            return Vote(self.id, "reject", min(0.95, 0.60 + 0.10 * g["score"]),
                        [f"ambiguous_rules:{g['score']}"] + g["flags"], evidence)
        if g["score"] == 1:
            return Vote(self.id, "approve", 0.45,
                        [f"one_ambiguity:{g['flags'][0]}"], evidence)
        return Vote(self.id, "approve", 0.85, ["clear_rules"], evidence)
