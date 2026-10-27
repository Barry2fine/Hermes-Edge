"""Regression tests for five confirmed model-maths bugs (sovereign-bots half).

Each test below FAILS on the pre-fix code and passes after the fix:

  1) kelly_portfolio.kelly_fraction used 1/price as if it were the *net* win
     odds (gross payout), so an overpriced contract sized like a winner.
  2) coordinator.decide returned ALLOW when every edge-bearing seat abstained
     and only generic (non-edge) seats approved.

Pure, offline, no network, no clock.
"""

from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sovereign_bots.bots.crypto.kelly_portfolio import (  # noqa: E402
    CLUSTER_CAP, KELLY_FRACTION, PER_NAME_CAP, TOTAL_CAP, KellyPortfolioBot,
    kelly_fraction,
)
from sovereign_bots.core.bot import Bot, Vote  # noqa: E402
from sovereign_bots.core import coordinator  # noqa: E402

KELLY = KellyPortfolioBot()


# ============================================================ bug 1: Kelly odds
def test_kelly_uses_net_win_odds_not_gross_payout():
    """b = (1-price)/price; f* = (p-price)/(1-price) — NOT the 1/price form."""
    p, price = 0.60, 0.50
    b = (1.0 - price) / price                    # 1.0 net win per 1.0 staked
    assert kelly_fraction(p, price) == pytest.approx((p * (b + 1.0) - 1.0) / b)
    assert kelly_fraction(p, price) == pytest.approx((p - price) / (1.0 - price))
    assert kelly_fraction(p, price) == pytest.approx(0.20)
    # the old gross-payout formula returned 0.40 here: exactly double the truth
    gross = (p * (1.0 / price + 1.0) - 1.0) / (1.0 / price)
    assert gross == pytest.approx(0.40)
    assert kelly_fraction(p, price) == pytest.approx(gross / 2.0)


def test_kelly_overpriced_contract_is_negative():
    """p=0.40 at price=0.60 is a losing bet: f* must be negative, not positive."""
    assert kelly_fraction(0.40, 0.60) < 0
    assert kelly_fraction(0.40, 0.60) == pytest.approx((0.40 - 0.60) / 0.40)


def test_kelly_fair_price_is_zero():
    """p == price means no edge: full Kelly is exactly zero."""
    assert kelly_fraction(0.50, 0.50) == pytest.approx(0.0)
    assert kelly_fraction(0.25, 0.25) == pytest.approx(0.0)


def test_kelly_bot_rejects_the_overpriced_candidate():
    """The desk must NOT approve p=0.40 @ 0.60 (pre-fix it approved ~2%/name)."""
    v = KELLY.evaluate({"bankroll": 1000.0,
                        "candidates": [{"name": "trap", "p": 0.40,
                                        "price": 0.60, "cluster": "x"}]})
    assert v.decision == "reject"
    assert "no_positive_edge" in v.reasons
    assert v.evidence["weights"] == {}
    assert v.evidence["total"] == 0.0


def test_kelly_bot_fair_price_is_not_tradeable():
    v = KELLY.evaluate({"bankroll": 1000.0,
                        "candidates": [{"name": "fair", "p": 0.50,
                                        "price": 0.50, "cluster": "x"}]})
    assert v.decision == "reject"


def test_kelly_caps_and_half_kelly_still_apply_after_the_formula_fix():
    """2% per name, 6% per cluster, 10% total, half Kelly — all preserved."""
    cands = [{"name": f"n{i}", "p": 0.90, "price": 0.50, "cluster": f"c{i}"}
             for i in range(8)]
    v = KELLY.evaluate({"bankroll": 1000.0, "candidates": cands})
    assert v.decision == "approve"
    # full Kelly on p=0.90 @ 0.50 is 0.80; half is 0.40 -> clipped by the 2% cap
    assert kelly_fraction(0.90, 0.50) == pytest.approx(0.80)
    assert all(w <= PER_NAME_CAP + 1e-9 for w in v.evidence["weights"].values())
    assert v.evidence["total"] == pytest.approx(TOTAL_CAP, abs=1e-8)
    assert all(t <= CLUSTER_CAP + 1e-9 for t in v.evidence["per_cluster"].values())
    assert KELLY_FRACTION == 0.5


# ==================================================== bug 2: coordinator edge gate
class EdgeApprove(Bot):
    """An edge-bearing seat that approves (models a positive, priced edge)."""

    id = "edge_approve"
    edge_seat = True
    veto = False

    def evaluate(self, ctx):
        return Vote(self.id, "approve", 0.80, ["edge=+0.03"], {"edge": 0.03})


class EdgeAbstain(Bot):
    """An edge-bearing seat with no model probability: it must not vote yes."""

    id = "edge_abstain"
    edge_seat = True
    veto = False

    def evaluate(self, ctx):
        return Vote(self.id, "abstain", 0.0, ["missing:model_p"], {})


class GenericApprove(Bot):
    """A quality gate: its approval is permission to trade, not a reason to."""

    id = "generic_approve"
    veto = False

    def evaluate(self, ctx):
        return Vote(self.id, "approve", 0.90, ["context_clean"], {})


class GenericVeto(Bot):
    id = "generic_veto"
    veto = True

    def evaluate(self, ctx):
        return Vote(self.id, "reject", 0.95, ["rules_unclear"], {})


class GenericReject(Bot):
    id = "generic_reject"
    veto = False

    def evaluate(self, ctx):
        return Vote(self.id, "reject", 0.70, ["stale_feed"], {})


def test_establishes_edge_reads_the_declaration():
    assert coordinator.establishes_edge(EdgeApprove()) is True
    assert coordinator.establishes_edge(GenericApprove()) is False
    assert coordinator.establishes_edge(KELLY) is True


def test_no_edge_approval_is_wait_not_allow():
    """barrier/fee_hurdle/carry/kelly abstained, only generic bots approved."""
    pack = [EdgeAbstain(), EdgeAbstain(), GenericApprove(), GenericApprove()]
    out = coordinator.decide(pack, {})
    assert out["decision"] == "WAIT"
    assert out["approves"] == 2          # the generic approvals are still counted
    assert out["edge_approves"] == 0
    assert out["edge_seats"] == 2
    assert out["edge_gate"] == "blocked"
    assert any(r.startswith("edge_gate:no_edge_approval") for r in out["reasons"])


def test_one_edge_approval_unlocks_allow():
    pack = [EdgeAbstain(), EdgeApprove(), GenericApprove()]
    out = coordinator.decide(pack, {})
    assert out["decision"] == "ALLOW"
    assert out["edge_approves"] == 1
    assert out["edge_gate"] == "open"


def test_opt_out_restores_the_old_rule_for_edge_free_callers():
    pack = [EdgeAbstain(), GenericApprove(), GenericApprove()]
    out = coordinator.decide(pack, {}, require_edge=False)
    assert out["decision"] == "ALLOW"
    assert out["edge_gate"] == "disabled"
    assert out["require_edge"] is False


def test_pack_without_any_edge_seat_cannot_allow():
    """Default is fail-closed: approvals with no edge-bearing seat never ALLOW."""
    out = coordinator.decide([GenericApprove(), GenericApprove()], {})
    assert out["edge_gate"] == "no_edge_seats"
    assert out["edge_seats"] == 0
    assert out["decision"] == "WAIT"
    assert any(r.startswith("edge_gate:no_edge_seats") for r in out["reasons"])
    # an explicit opt-out is the documented way for pure context packs
    opted_out = coordinator.decide([GenericApprove(), GenericApprove()], {},
                                   require_edge=False)
    assert opted_out["decision"] == "ALLOW"
    assert opted_out["edge_gate"] == "disabled"


def test_edge_gate_never_overrides_veto_or_reject():
    vetoed = coordinator.decide([EdgeAbstain(), GenericApprove(), GenericVeto()], {})
    assert vetoed["decision"] == "VETO"
    assert vetoed["vetoes"] == 1
    rejected = coordinator.decide([EdgeApprove(), GenericReject()], {})
    assert rejected["decision"] == "REJECT"
    assert rejected["rejects"] == 1


def test_edge_gate_respects_min_confidence_and_quorum():
    # an edge approve below the confidence floor does not unlock ALLOW
    assert coordinator.decide([EdgeApprove(), GenericApprove()], {},
                  min_confidence=0.99)["decision"] == "WAIT"
    assert coordinator.decide([EdgeApprove()], {}, quorum=2)["decision"] == "WAIT"


def test_edge_payload_keys_are_added_without_breaking_the_contract():
    out = coordinator.decide([EdgeApprove(), GenericApprove()], {})
    for key in ("decision", "votes", "reasons", "votes_hash", "n", "vetoes",
                "rejects", "approves", "edge_seats", "edge_approves", "edge_gate"):
        assert key in out
    assert len(out["votes_hash"]) == 64
