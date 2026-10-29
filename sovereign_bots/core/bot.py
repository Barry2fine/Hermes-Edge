"""Bot contract: a mandate, a domain, a veto flag, and one pure vote."""

from __future__ import annotations

import math

from dataclasses import asdict, dataclass, field
from typing import Any, Dict, List, Tuple

DECISIONS = ("approve", "reject", "abstain")


@dataclass
class Vote:
    bot: str
    decision: str
    confidence: float = 0.0
    reasons: List[str] = field(default_factory=list)
    evidence: Dict[str, Any] = field(default_factory=dict)

    def __post_init__(self):
        self.validate()

    def validate(self):
        if self.decision not in DECISIONS:
            raise ValueError(f"bad decision {self.decision!r}")
        if isinstance(self.confidence, bool) or not isinstance(self.confidence, (int, float)) or not math.isfinite(self.confidence) or not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be finite and between zero and one")

    def as_dict(self) -> Dict[str, Any]:
        return asdict(self)


class Bot:
    id: str = "bot"
    mandate: str = ""
    domains: Tuple[str, ...] = ()
    veto: bool = False
    #: True when an *approve* from this seat establishes a real edge (a modelled
    #: probability beating the offered price net of costs, a forward-record skill
    #: certification, or a stake sized off that edge).  Quality gates, vetoes and
    #: generic "context looks fine" seats leave this False: their approval is
    #: permission to trade, never a reason to trade.  The coordinator uses it to
    #: refuse ALLOW unless an edge-bearing seat actually approved.
    edge_seat: bool = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        raise NotImplementedError

    def vote(self, context: Dict[str, Any]) -> Vote:
        """Never raises. A failing veto bot rejects (fail-closed)."""
        try:
            v = self.evaluate(context or {})
            if not isinstance(v, Vote):
                raise TypeError("evaluate must return Vote")
            v.validate()
            return v
        except Exception as exc:  # noqa: BLE001
            return Vote(self.id, "reject" if self.veto else "abstain", 0.0,
                        [f"error:{type(exc).__name__}:{exc}"], {"error": True})
