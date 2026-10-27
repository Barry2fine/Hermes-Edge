"""Resolution bot (VETO): can this market actually be settled unambiguously?

Fail-closed. Missing rules, an expired/zero horizon, or a question with no
threshold or date gets the trade killed before sizing is ever discussed.
"""

from __future__ import annotations

import re
from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

_MONTHS = (
    "january", "february", "march", "april", "may", "june", "july", "august",
    "september", "october", "november", "december", "jan", "feb", "mar", "apr",
    "jun", "jul", "aug", "sep", "sept", "oct", "nov", "dec", "q1", "q2", "q3", "q4",
)

# Phrases that hide the actual subject/level of the bet.
_VAGUE = (
    "will it", "will they", "will this", "will that", "will he", "will she",
    "something", "someone", "somehow", "tbd", "tba", "to be determined",
    "to be announced", "roughly", "approximately", "about the same", "etc",
)

# Anything that pins the claim down: a number, a date, a named threshold word.
_ANCHOR = re.compile(
    r"\d"
    r"|\b(?:above|below|over|under|at least|at most|more than|less than|"
    r"greater than|higher than|lower than|exceed|reach|close at|close above|"
    r"close below|hit|by|before|after|end of|by the end|on or before)\b"
    r"|[$€£%]"
    r"|\b(?:" + "|".join(_MONTHS) + r")\b"
)


def _num(value: Any) -> Optional[float]:
    if value is None or isinstance(value, bool):
        return None
    try:
        out = float(value)
    except (TypeError, ValueError):
        return None
    if out != out or out in (float("inf"), float("-inf")):
        return None
    return out


def _truthy(value: Any) -> bool:
    if value is True:
        return True
    if isinstance(value, str):
        return value.strip().lower() in ("true", "yes", "y", "1", "present")
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return value == 1
    return False


def ambiguity_flags(question: str) -> Dict[str, Any]:
    text = " ".join(str(question or "").lower().split())
    vague = [m for m in _VAGUE if re.search(r"(?<![a-z])" + re.escape(m) + r"(?![a-z])", text)]
    return {
        "vague_markers": vague,
        "has_anchor": bool(_ANCHOR.search(text)),
        "length": len(text),
    }


class ResolutionBot(Bot):
    id = "resolution"
    mandate = "No tradable market without published rules, a live horizon and a testable question."
    domains = ("polymarket",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        question = context.get("question")
        evidence: Dict[str, Any] = {
            "resolution_rules_present": context.get("resolution_rules_present"),
            "days_to_resolve": context.get("days_to_resolve"),
        }

        if not isinstance(question, str) or len(question.strip()) < 12:
            return Vote(self.id, "reject", 0.95, ["missing_or_trivial_question"], evidence)

        if not _truthy(context.get("resolution_rules_present")):
            return Vote(self.id, "reject", 0.95,
                        ["resolution_rules_absent:no_published_settlement_rules"], evidence)

        days = _num(context.get("days_to_resolve"))
        if days is None:
            return Vote(self.id, "reject", 0.90,
                        ["missing_days_to_resolve:cannot_verify_horizon"], evidence)
        if days <= 0:
            return Vote(self.id, "reject", 0.95,
                        [f"expired_or_immediate_resolution:days={days:.2f}"], evidence)

        flags = ambiguity_flags(question)
        evidence.update(flags)
        if flags["vague_markers"] and not flags["has_anchor"]:
            return Vote(self.id, "reject", 0.85,
                        ["ambiguous_question:"
                         + ",".join(flags["vague_markers"])
                         + ",no_threshold_or_date"], evidence)

        return Vote(self.id, "approve", 0.92,
                    ["rules_present", f"horizon_days={days:.1f}",
                     "question_has_threshold_or_date"], evidence)
