"""Tests for the Polymarket bot pack (shape, per-bot pass/fail, coordinator)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sovereign_bots.bots.polymarket import BOTS  # noqa: E402
from sovereign_bots.bots.polymarket.barrier import BarrierBot  # noqa: E402
from sovereign_bots.bots.polymarket.crosslink import CrosslinkBot  # noqa: E402
from sovereign_bots.bots.polymarket.liquidity import LiquidityBot  # noqa: E402
from sovereign_bots.bots.polymarket.resolution import ResolutionBot  # noqa: E402
from sovereign_bots.bots.polymarket.taxonomy import TaxonomyBot, classify  # noqa: E402
from sovereign_bots.bots.polymarket.time_decay import TimeDecayBot  # noqa: E402
from sovereign_bots.core.bot import DECISIONS, Bot, Vote  # noqa: E402
from sovereign_bots.core.coordinator import decide  # noqa: E402
from sovereign_bots.core.registry import domain_bots  # noqa: E402

EXPECTED_IDS = ["barrier", "taxonomy", "resolution", "liquidity", "crosslink", "time_decay"]


def good_context(**overrides):
    ctx = {
        "question": "Will ETH close above $4,000 on 2026-12-31?",
        "category": "crypto",
        "market_p": 0.42,
        "model_p": 0.60,
        "lower_bound_p": 0.51,
        "fee": 0.02,
        "liquidity": 80_000,
        "volume": 250_000,
        "days_to_resolve": 45,
        "spread_bps": 60,
        "resolution_rules_present": True,
        "crypto_bias": "long",
        "crypto_regime": "risk-on",
        "related_sports": [],
        "macro_events": [],
        # market-structure evidence (fee_hurdle / carry / resolution_clarity)
        "price": 0.42,
        "ask": 0.42,
        "resolution_rules": ("Settled by exchange staff from the Coinbase official "
                             "closing price at 16:00 UTC on the expiry date."),
        # wave-2 evidence
        "brier_model": 0.18,
        "brier_baseline": 0.25,
        "recent_brier": 0.18,
        "prior_brier": 0.19,
        "n": 120,
    }
    ctx.update(overrides)
    return ctx


def vote_of(bot, ctx):
    v = bot.vote(ctx)
    assert isinstance(v, Vote)
    assert v.bot == bot.id
    return v


# --------------------------------------------------------------------------- shape


def test_pack_exposes_at_least_six_instances():
    assert isinstance(BOTS, list)
    assert len(BOTS) >= 6
    assert all(isinstance(b, Bot) for b in BOTS)


def test_ids_unique_and_expected():
    ids = [b.id for b in BOTS]
    assert set(EXPECTED_IDS) <= set(ids)
    assert len(set(ids)) == len(ids)


def test_every_bot_declares_polymarket_domain_and_mandate():
    for b in BOTS:
        assert "polymarket" in b.domains
        assert isinstance(b.mandate, str) and len(b.mandate) > 10


def test_resolution_and_liquidity_veto():
    assert {"resolution", "liquidity"} <= {b.id for b in BOTS if b.veto}


def test_registry_discovers_pack():
    assert set(EXPECTED_IDS) <= {b.id for b in domain_bots("polymarket")}


@pytest.mark.parametrize("ctx", [good_context(), {}, None])
def test_votes_are_well_formed(ctx):
    for b in BOTS:
        v = b.vote(ctx)
        assert isinstance(v, Vote)
        assert v.decision in DECISIONS
        assert 0.0 <= v.confidence <= 1.0
        assert isinstance(v.reasons, list) and v.reasons
        assert isinstance(v.evidence, dict)


def test_bots_never_raise_on_hostile_context():
    hostile = [
        {"market_p": "abc", "model_p": [], "liquidity": {}, "volume": "x",
         "spread_bps": object(), "days_to_resolve": "soon",
         "resolution_rules_present": "maybe", "question": 42},
        {"crypto_bias": ["long"], "related_sports": 7, "macro_events": {"a": 1}},
        {"question": "x" * 5000, "market_p": 1.5, "model_p": -3},
    ]
    for ctx in hostile:
        for b in BOTS:
            assert isinstance(b.vote(ctx), Vote)


# ------------------------------------------------------------------------- barrier


def test_barrier_approves_when_net_edge_clears_hurdle():
    v = vote_of(BarrierBot(), good_context())
    assert v.decision == "approve"
    assert v.evidence["anchor"] == "lower_bound_p"
    assert v.evidence["net_edge"] > 0.02


def test_barrier_uses_lower_bound_over_model_point():
    # Point estimate is juicy, lower bound is below market: no trade.
    v = vote_of(BarrierBot(), good_context(model_p=0.72, lower_bound_p=0.44, market_p=0.45))
    assert v.decision == "reject"
    assert v.evidence["anchor"] == "lower_bound_p"


def test_barrier_rejects_negative_edge():
    v = vote_of(BarrierBot(), good_context(model_p=0.40, lower_bound_p=0.38, market_p=0.50))
    assert v.decision == "reject"
    assert v.evidence["net_edge"] < 0


def test_barrier_abstains_on_thin_edge_inside_fee_budget():
    v = vote_of(BarrierBot(), good_context(model_p=0.53, lower_bound_p=0.53, market_p=0.50))
    assert v.decision == "abstain"
    assert any("thin_edge" in r for r in v.reasons)


def test_barrier_fee_default_and_fee_kills_edge():
    thin = good_context(model_p=0.535, lower_bound_p=0.535, market_p=0.50)
    thin.pop("fee")
    v = vote_of(BarrierBot(), thin)
    assert v.evidence["fee"] == pytest.approx(0.02)
    assert v.decision == "abstain"

    free = dict(thin, fee=0.0)
    assert vote_of(BarrierBot(), free).decision == "approve"


def test_barrier_confidence_scales_with_edge():
    small = vote_of(BarrierBot(), good_context(model_p=0.55, lower_bound_p=0.55, market_p=0.45))
    big = vote_of(BarrierBot(), good_context(model_p=0.85, lower_bound_p=0.85, market_p=0.30))
    assert small.decision == big.decision == "approve"
    assert big.confidence > small.confidence


def test_barrier_abstains_without_prices():
    for ctx in ({"market_p": 0.4}, {"model_p": 0.6}, {}):
        v = vote_of(BarrierBot(), ctx)
        assert v.decision == "abstain"
        assert any("missing_price" in r for r in v.reasons)


def test_barrier_rejects_out_of_range_probabilities_as_missing():
    assert vote_of(BarrierBot(), good_context(market_p=1.4)).decision == "abstain"
    assert vote_of(BarrierBot(), good_context(model_p=-0.2, lower_bound_p=None)).decision == "abstain"


# ------------------------------------------------------------------------ taxonomy


@pytest.mark.parametrize("question,expected", [
    ("Will Bitcoin hit $150k by December 31, 2026?", "crypto"),
    ("Will the Fed cut rates in March 2026?", "macro"),
    ("Will the Lakers win the NBA Finals?", "sports"),
    ("Will the Oscars go to a first-time nominee?", "entertainment"),
    ("Will a ceasefire be signed in Gaza by June 2026?", "geopolitics"),
    ("Will Candidate A win the presidential election?", "politics"),
    ("Will it snow more than 3 inches in Chicago?", "weather"),
])
def test_taxonomy_classifies_domains(question, expected):
    assert classify(question)[0] == expected
    v = vote_of(TaxonomyBot(), {"question": question, "category": expected})
    assert v.decision == "approve"
    assert v.evidence["category"] == expected


def test_taxonomy_abstains_on_unrecognized_question():
    v = vote_of(TaxonomyBot(), {"question": "Will the thing happen with the stuff?"})
    assert v.decision == "abstain"
    assert v.evidence["category"] == "other"


def test_taxonomy_abstains_without_question():
    assert vote_of(TaxonomyBot(), {}).decision == "abstain"
    assert vote_of(TaxonomyBot(), {"question": "  "}).decision == "abstain"


def test_taxonomy_penalizes_declared_mismatch():
    agree = vote_of(TaxonomyBot(), {"question": "Will Bitcoin hit 150k?", "category": "crypto"})
    clash = vote_of(TaxonomyBot(), {"question": "Will Bitcoin hit 150k?", "category": "sports"})
    assert agree.decision == clash.decision == "approve"
    assert clash.confidence < agree.confidence
    assert clash.evidence["conflict"] is True


# ---------------------------------------------------------------------- resolution


def test_resolution_approves_clean_market():
    bot = ResolutionBot()
    v = vote_of(bot, good_context())
    assert v.decision == "approve"
    assert bot.veto is True


@pytest.mark.parametrize("value", [False, None, "no", 0])
def test_resolution_rejects_missing_rules_failclosed(value):
    v = vote_of(ResolutionBot(), good_context(resolution_rules_present=value))
    assert v.decision == "reject"
    assert any("resolution_rules" in r for r in v.reasons)


def test_resolution_rejects_when_rules_key_absent():
    ctx = good_context()
    ctx.pop("resolution_rules_present")
    assert vote_of(ResolutionBot(), ctx).decision == "reject"


@pytest.mark.parametrize("days", [0, -1, -30])
def test_resolution_rejects_expired_horizon(days):
    v = vote_of(ResolutionBot(), good_context(days_to_resolve=days))
    assert v.decision == "reject"
    assert any("expired" in r for r in v.reasons)


def test_resolution_rejects_missing_or_garbage_horizon():
    ctx = good_context()
    ctx.pop("days_to_resolve")
    assert vote_of(ResolutionBot(), ctx).decision == "reject"
    assert vote_of(ResolutionBot(), good_context(days_to_resolve="soon")).decision == "reject"


def test_resolution_rejects_ambiguous_question():
    v = vote_of(ResolutionBot(), good_context(question="Will it be resolved favorably?"))
    assert v.decision == "reject"
    assert any("ambiguous_question" in r for r in v.reasons)


def test_resolution_accepts_vague_pronoun_when_threshold_or_date_present():
    v = vote_of(ResolutionBot(), good_context(
        question="Will it rain more than 2 inches in NYC on June 30?"))
    assert v.decision == "approve"
    assert v.evidence["has_anchor"] is True


def test_resolution_rejects_trivial_question():
    assert vote_of(ResolutionBot(), good_context(question="Will it?")).decision == "reject"
    assert vote_of(ResolutionBot(), {"question": None}).decision == "reject"


# ----------------------------------------------------------------------- liquidity


def test_liquidity_approves_deep_book():
    bot = LiquidityBot()
    v = vote_of(bot, good_context())
    assert v.decision == "approve"
    assert bot.veto is True


@pytest.mark.parametrize("overrides,needle", [
    ({"liquidity": 9_999}, "illiquid"),
    ({"volume": 4_999}, "thin_volume"),
    ({"spread_bps": 201}, "wide_spread"),
])
def test_liquidity_rejects_below_floors(overrides, needle):
    v = vote_of(LiquidityBot(), good_context(**overrides))
    assert v.decision == "reject"
    assert any(needle in r for r in v.reasons)


def test_liquidity_boundaries_pass_exactly_at_floor():
    v = vote_of(LiquidityBot(), good_context(liquidity=10_000, volume=5_000, spread_bps=200))
    assert v.decision == "approve"


@pytest.mark.parametrize("key", ["liquidity", "volume", "spread_bps"])
def test_liquidity_rejects_missing_data(key):
    v = vote_of(LiquidityBot(), good_context(**{key: None}))
    assert v.decision == "reject"
    assert any("missing_data" in r and key in r for r in v.reasons)


def test_liquidity_accepts_numeric_strings():
    v = vote_of(LiquidityBot(), good_context(liquidity="50000", volume="20000", spread_bps="80"))
    assert v.decision == "approve"


def test_liquidity_confidence_rises_with_depth():
    shallow = vote_of(LiquidityBot(), good_context(liquidity=11_000, volume=5_500, spread_bps=190))
    deep = vote_of(LiquidityBot(), good_context(liquidity=500_000, volume=900_000, spread_bps=5))
    assert deep.confidence > shallow.confidence


# ----------------------------------------------------------------------- crosslink


def test_crosslink_approves_directional_crypto():
    v = vote_of(CrosslinkBot(), good_context())
    assert v.decision == "approve"
    assert "crypto" in v.evidence["links"]


@pytest.mark.parametrize("bias", ["neutral", None, "", "sideways"])
def test_crosslink_abstains_when_crypto_bias_not_directional(bias):
    ctx = good_context(crypto_bias=bias, category="crypto")
    v = vote_of(CrosslinkBot(), ctx)
    assert v.decision == "abstain"
    assert any("no_crosslink" in r for r in v.reasons)


def test_crosslink_approves_sports_slate_match():
    v = vote_of(CrosslinkBot(), {
        "question": "Will the Lakers beat the Celtics on opening night?",
        "category": "sports", "related_sports": ["Lakers", "Celtics"],
    })
    assert v.decision == "approve"
    assert v.evidence["sports_matched"]


def test_crosslink_abstains_when_sports_slate_missing_or_unmatched():
    base = {"question": "Will the Lakers beat the Celtics?", "category": "sports"}
    assert vote_of(CrosslinkBot(), base).decision == "abstain"
    ctx = dict(base, related_sports=["Yankees", "Mets"])
    v = vote_of(CrosslinkBot(), ctx)
    assert v.decision == "abstain"
    assert v.evidence["sports_unlinked"] == "no_slate_match"


def test_crosslink_approves_macro_calendar_match():
    v = vote_of(CrosslinkBot(), {
        "question": "Will CPI print above 3% in March?",
        "category": "macro", "macro_events": ["CPI release March 12", "FOMC"],
    })
    assert v.decision == "approve"
    assert v.evidence["macro_matched"]


def test_crosslink_abstains_with_no_macro_events():
    v = vote_of(CrosslinkBot(), {"question": "Will CPI print above 3%?", "category": "macro"})
    assert v.decision == "abstain"
    assert v.evidence["macro_unlinked"] == "no_macro_events"


def test_crosslink_confidence_grows_with_multiple_links():
    one = vote_of(CrosslinkBot(), good_context())
    two = vote_of(CrosslinkBot(), good_context(
        question="Will ETH hit 5k after the CPI print?", macro_events=["CPI release"]))
    assert two.decision == "approve"
    assert two.confidence > one.confidence
    assert len(two.evidence["links"]) == 2


def test_crosslink_abstains_when_nothing_is_linked():
    v = vote_of(CrosslinkBot(), {"question": "Will the bridge reopen by June 2026?"})
    assert v.decision == "abstain"
    assert v.reasons == ["no_linked_domain"]


# ----------------------------------------------------------------------- time_decay


def test_time_decay_approves_long_runway():
    bot = TimeDecayBot()
    v = vote_of(bot, good_context())
    assert v.decision == "approve"
    assert bot.veto is False


@pytest.mark.parametrize("days,p", [(3, 0.86), (1, 0.99), (0.5, 0.9)])
def test_time_decay_abstains_on_late_stage_tail(days, p):
    v = vote_of(TimeDecayBot(), good_context(days_to_resolve=days, market_p=p))
    assert v.decision == "abstain"
    assert v.confidence > 0
    assert "late_stage_tail" in v.reasons[0]


def test_time_decay_approves_near_expiry_when_not_a_tail():
    v = vote_of(TimeDecayBot(), good_context(days_to_resolve=2, market_p=0.55))
    assert v.decision == "approve"
    assert v.evidence["late_stage_tail"] is False


def test_time_decay_abstains_on_missing_data():
    assert vote_of(TimeDecayBot(), good_context(days_to_resolve=None)).decision == "abstain"
    assert vote_of(TimeDecayBot(), good_context(market_p=None)).decision == "abstain"


def test_time_decay_does_not_fire_on_long_runway_tail():
    v = vote_of(TimeDecayBot(), good_context(days_to_resolve=30, market_p=0.95))
    assert v.decision == "approve"


# ---------------------------------------------------------------------- coordinator


def test_coordinator_allows_good_context():
    res = decide(BOTS, good_context())
    assert res["decision"] == "ALLOW"
    assert res["n"] == len(BOTS)
    assert res["vetoes"] == 0
    assert res["rejects"] == 0
    assert res["approves"] >= 4
    assert len(res["votes"]) == len(BOTS)
    assert all(v["decision"] in DECISIONS for v in res["votes"])
    assert set(EXPECTED_IDS) <= {v["bot"] for v in res["votes"]}


def test_coordinator_vetoes_missing_resolution_rules():
    res = decide(BOTS, good_context(resolution_rules_present=False))
    assert res["decision"] == "VETO"
    assert res["vetoes"] >= 1
    assert any("resolution_rules" in r for r in res["reasons"])


def test_coordinator_vetoes_thin_liquidity():
    res = decide(BOTS, good_context(liquidity=3_000, volume=1_000, spread_bps=420))
    assert res["decision"] == "VETO"
    assert any("liquidity" in r for r in res["reasons"])


def test_coordinator_rejects_when_edge_is_negative():
    res = decide(BOTS, good_context(model_p=0.30, lower_bound_p=0.28, market_p=0.50))
    # fee_hurdle is a veto seat, so a negative post-fee edge now vetoes outright.
    assert res["decision"] in ("REJECT", "VETO")
    assert any("edge" in r.lower() or "hurdle" in r.lower() for r in res["reasons"])


def test_coordinator_waits_on_quorum_shortfall():
    assert decide(BOTS, good_context(), quorum=99)["decision"] == "WAIT"


def test_coordinator_fails_closed_on_empty_context():
    res = decide(BOTS, {})
    assert res["decision"] == "VETO"
    veto_bots = {v["bot"] for v in res["votes"] if v["decision"] == "reject"}
    assert {"resolution", "liquidity"} <= veto_bots


def test_coordinator_min_confidence_filters_weak_approvals():
    res = decide(BOTS, good_context(), min_confidence=0.95)
    assert res["decision"] == "WAIT"


def test_coordinator_hash_is_stable_and_context_sensitive():
    a = decide(BOTS, good_context())
    b = decide(BOTS, good_context())
    c = decide(BOTS, good_context(market_p=0.10))
    assert a["votes_hash"] == b["votes_hash"]
    assert a["votes_hash"] != c["votes_hash"]
