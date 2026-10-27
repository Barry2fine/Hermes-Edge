"""Tests for the GitHub-researched bots: prop_pmf, whale_consensus, xg, funding_carry."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sovereign_bots.bots.crypto.funding_carry import FundingCarryBot, carry_read  # noqa: E402
from sovereign_bots.bots.polymarket.whale_consensus import WhaleConsensusBot, consensus  # noqa: E402
from sovereign_bots.bots.sports.prop_pmf import (  # noqa: E402
    PropPMFBot, over_probability, poisson_cdf, poisson_pmf,
)
from sovereign_bots.bots.sports.xg import ExpectedGoalsBot, expected_goals  # noqa: E402
from sovereign_bots.core.bot import Bot, Vote  # noqa: E402
from sovereign_bots.core.coordinator import decide  # noqa: E402

PMF = PropPMFBot()
WHALE = WhaleConsensusBot()
XG = ExpectedGoalsBot()
CARRY = FundingCarryBot()
ALL = (PMF, WHALE, XG, CARRY)


def test_contract():
    assert PMF.id == "prop_pmf" and PMF.veto is False
    assert WHALE.id == "whale_consensus" and WHALE.veto is False
    assert "polymarket" in WHALE.domains
    assert XG.id == "xg" and XG.veto is False
    assert CARRY.id == "funding_carry" and CARRY.veto is False
    for b in ALL:
        assert isinstance(b, Bot) and len(b.mandate) > 10


# ------------------------------------------------------------------ PMF math
def test_poisson_pmf_sums_to_one():
    total = sum(poisson_pmf(k, 3.0) for k in range(0, 60))
    assert abs(total - 1.0) < 1e-6


def test_poisson_cdf_monotonic():
    assert poisson_cdf(2, 3.0) < poisson_cdf(5, 3.0)


def test_over_probability_half_line_has_no_push():
    d = over_probability(24.0, 23.5)
    assert d["p_push"] == 0.0
    assert abs(d["p_over"] + d["p_under"] - 1.0) < 1e-9


def test_over_probability_integer_line_has_push():
    d = over_probability(24.0, 24)
    assert d["p_push"] > 0
    assert abs(d["p_over"] + d["p_under"] + d["p_push"] - 1.0) < 1e-9


def test_over_probability_rises_with_projection():
    low = over_probability(18.0, 23.5)["p_over"]
    high = over_probability(30.0, 23.5)["p_over"]
    assert high > low


def test_pmf_bot_approves_real_edge():
    v = PMF.vote({"projection": 30.0, "line": 23.5, "market_p": 0.45,
                  "side": "over", "fee": 0.02})
    assert v.decision == "approve"
    assert "pmf_edge" in v.reasons[0]


def test_pmf_bot_rejects_when_overpriced():
    v = PMF.vote({"projection": 16.0, "line": 23.5, "market_p": 0.60,
                  "side": "over", "fee": 0.02})
    assert v.decision == "reject"


def test_pmf_bot_abstains_without_inputs():
    assert PMF.vote({}).decision == "abstain"
    assert PMF.vote({"projection": 20}).decision == "abstain"


# --------------------------------------------------------------- whale
def test_consensus_none_without_whales():
    assert consensus([], "yes")["consensus"] is None


def test_consensus_needs_min_whales():
    v = WHALE.vote({"side": "yes", "whales": [
        {"side": "yes", "size": 100, "rank": 0.9}]})
    assert v.decision == "abstain"


def test_whales_agree_approves():
    whales = [{"side": "yes", "size": 100, "rank": 0.9},
              {"side": "yes", "size": 80, "rank": 0.8},
              {"side": "yes", "size": 60, "rank": 0.7}]
    v = WHALE.vote({"side": "yes", "whales": whales})
    assert v.decision == "approve"
    assert "whales_agree" in v.reasons[0]


def test_whales_against_rejects():
    whales = [{"side": "no", "size": 100, "rank": 0.9},
              {"side": "no", "size": 80, "rank": 0.8},
              {"side": "no", "size": 60, "rank": 0.7}]
    v = WHALE.vote({"side": "yes", "whales": whales})
    assert v.decision == "reject"


def test_low_rank_whales_ignored():
    whales = [{"side": "yes", "size": 100, "rank": 0.1} for _ in range(5)]
    assert consensus(whales, "yes")["consensus"] is None


def test_mixed_whales_neutral():
    whales = [{"side": "yes", "size": 50, "rank": 0.8},
              {"side": "no", "size": 50, "rank": 0.8},
              {"side": "yes", "size": 10, "rank": 0.8}]
    r = consensus(whales, "yes")
    assert abs(r["on_us"]) < 0.35


# ------------------------------------------------------------------- xG
def test_xg_from_shots():
    r = expected_goals({"shots": 12, "shot_quality": 0.12})
    assert abs(r["xg"] - 1.44) < 1e-9


def test_xg_supplied_passthrough():
    assert expected_goals({"xg": 2.1})["xg"] == 2.1


def test_xg_missing():
    assert expected_goals({})["xg"] is None


def test_xg_bot_approves_when_chances_exceed_line():
    v = XG.vote({"xg_total": 3.6, "line": 2.5, "market_p": 0.45,
                 "side": "over", "fee": 0.02})
    assert v.decision == "approve"


def test_xg_bot_rejects_when_chances_fall_short():
    v = XG.vote({"xg_total": 1.4, "line": 2.5, "market_p": 0.55,
                 "side": "over", "fee": 0.02})
    assert v.decision == "reject"


def test_xg_bot_abstains_without_data():
    assert XG.vote({}).decision == "abstain"


# --------------------------------------------------------------- carry
def test_carry_long_pays_when_funding_positive():
    r = carry_read({"funding_rate": 0.0005, "direction": "long"})
    assert r["receivable"] < 0


def test_carry_short_receives_when_funding_positive():
    r = carry_read({"funding_rate": 0.0005, "direction": "short"})
    assert r["receivable"] > 0


def test_carry_rejects_paying_extreme():
    v = CARRY.vote({"funding_rate": 0.0006, "direction": "long"})
    assert v.decision == "reject"
    assert "paying_extreme_funding" in v.reasons[0]


def test_carry_approves_receiving():
    v = CARRY.vote({"funding_rate": 0.0002, "direction": "short"})
    assert v.decision == "approve"


def test_carry_abstains_without_rate():
    assert CARRY.vote({"direction": "long"}).decision == "abstain"


# ------------------------------------------------------------ resilience
def test_never_raises():
    for ctx in (None, {}, [], "x", 42,
                {"projection": "high", "line": object()},
                {"whales": "nope", "side": None},
                {"whales": [{"side": "yes", "size": float("inf"), "rank": 9}]},
                {"shots": -5, "shot_quality": 2},
                {"funding_rate": float("nan"), "direction": "long"}):
        for b in ALL:
            v = b.vote(ctx)
            assert isinstance(v, Vote)
            assert v.decision in ("approve", "reject", "abstain")
            assert 0.0 <= v.confidence <= 1.0


def test_coordinator_allows_good_combined_context():
    ctx = {
        "projection": 30.0, "line": 23.5, "market_p": 0.45, "side": "over",
        "fee": 0.02,
        # NB: no xg_total here -- this is a 23.5 points line, not a goals market.
        "whales": [{"side": "yes", "size": 100, "rank": 0.9},
                   {"side": "yes", "size": 80, "rank": 0.8},
                   {"side": "yes", "size": 60, "rank": 0.7}],
        "funding_rate": 0.0002, "direction": "short",
    }
    out = decide(ALL, ctx)
    assert out["decision"] == "ALLOW", out["reasons"]
