"""Coordinator: collect votes, apply vetoes, require quorum and a real edge.

No yes-men: a pack of generic seats ("context looks clean", "rules are clear",
"liquidity is fine") can only *permit* a trade, never *make* one.  An ALLOW
therefore needs at least one approving seat that itself establishes edge — see
``Bot.edge_seat`` and ``establishes_edge``.

Decision ladder (unchanged precedence, one new gate at the bottom):
  1. any veto-seat reject          -> VETO
  2. any other reject              -> REJECT
  3. speakers < quorum             -> WAIT
  4. no qualifying approve         -> WAIT
  5. require_edge and no edge-bearing approve -> WAIT    (never ALLOW)
  6. otherwise                     -> ALLOW
"""

from __future__ import annotations

import hashlib
import json
from typing import Any, Dict, Iterable, List


def _hash(obj: Any) -> str:
    return hashlib.sha256(json.dumps(obj, sort_keys=True, default=str).encode()).hexdigest()


def establishes_edge(bot: Any) -> bool:
    """True when this seat's approve claims an edge, not just permission.

    A seat opts in with the ``edge_seat = True`` class attribute (see
    ``sovereign_bots.core.bot.Bot``): a modelled probability beating the offered
    price net of costs, a forward-record skill certification (calibration drift
    / CLV), or a stake sized off that edge.  Everything else — veto gates, data
    integrity, resolution clarity, liquidity, narrative spots — is ``False``.
    """
    return bool(getattr(bot, "edge_seat", False))


def decide(bots: Iterable[Any], context: Dict[str, Any], quorum: int = 1,
           min_confidence: float = 0.0, require_edge: bool = True) -> Dict[str, Any]:
    """Run every bot and return {decision, votes, reasons, votes_hash, ...}.

    decision: ALLOW only when no reject and at least one qualifying approve that
    establishes edge.  Any veto-bot reject => VETO. Any reject => REJECT. Not
    enough speakers => WAIT. No edge-bearing approve => WAIT (never ALLOW).

    ``require_edge`` (default True) is the fail-closed edge gate: if no
    edge-bearing seat approves, the answer is WAIT.  Generic approvals — clear
    rules, decent liquidity, a clean-looking context — are permission to trade,
    never a reason to trade.  The gate is reported in ``edge_gate``:

      * ``"open"``          an edge-bearing seat approved;
      * ``"blocked"``       edge seats voted but none approved;
      * ``"no_edge_seats"`` nothing in this pack can establish edge (WAIT);
      * ``"disabled"``      the caller passed ``require_edge=False``.

    Callers that genuinely have no edge model (a pure context/sanity pack, a
    soft-signal layer) opt out explicitly with ``require_edge=False``; the
    decision then follows the pre-existing quorum/approve rule.  This opt-out is
    deliberately explicit: a pack whose seats were never marked
    ``edge_seat = True`` returns WAIT rather than silently allowing trades with
    no edge behind them.

    Veto/reject precedence is untouched: a veto or a reject still decides the
    outcome regardless of the edge gate.
    """
    bots = list(bots or [])
    votes = []
    vetoes = []
    rejects = []
    approves = []
    edge_approves = []
    edge_seats = 0
    for b in bots:
        v = b.vote(context)
        votes.append(v)
        seat = establishes_edge(b)
        if seat:
            edge_seats += 1
        if b.veto and v.decision == "reject":
            vetoes.append(v)
        elif v.decision == "reject":
            rejects.append(v)
        elif v.decision == "approve" and v.confidence >= min_confidence:
            approves.append(v)
            if seat:
                edge_approves.append(v)

    if not require_edge:
        edge_gate = "disabled"
    elif edge_approves:
        edge_gate = "open"
    elif edge_seats:
        edge_gate = "blocked"        # edge seats voted, none approved
    else:
        edge_gate = "no_edge_seats"  # nothing in this pack can establish edge

    if vetoes:
        decision = "VETO"
    elif rejects:
        decision = "REJECT"
    elif len([v for v in votes if v.decision != "abstain"]) < max(1, int(quorum)):
        decision = "WAIT"
    elif not approves:
        decision = "WAIT"
    elif require_edge and not edge_approves:
        decision = "WAIT"
    else:
        decision = "ALLOW"

    reasons: List[str] = []
    for v in votes:
        if v.decision in ("reject", "approve"):
            reasons.extend(f"{v.bot}: {r}" for r in v.reasons[:3])
    if decision == "WAIT" and edge_gate == "blocked":
        reasons.append(
            f"edge_gate:no_edge_approval: {edge_seats} edge seat(s) voted, "
            f"none approved; {len(approves)} non-edge approval(s) are permission only"
        )
    elif decision == "WAIT" and edge_gate == "no_edge_seats":
        reasons.append(
            "edge_gate:no_edge_seats: no seat in this pack establishes edge "
            "(pass require_edge=False for a pure context pack)"
        )

    payload = {
        "decision": decision,
        "votes": [v.as_dict() for v in votes],
        "reasons": reasons,
        "n": len(votes),
        "vetoes": len(vetoes),
        "rejects": len(rejects),
        "approves": len(approves),
        "edge_seats": edge_seats,
        "edge_approves": len(edge_approves),
        "edge_gate": edge_gate,
        "require_edge": bool(require_edge),
    }
    payload["votes_hash"] = _hash(payload["votes"])
    return payload
