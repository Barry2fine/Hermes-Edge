"""Tests for fee_hurdle, carry, resolution_clarity (all derived from verified research)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sovereign_bots.bots.polymarket.carry import CarryBot, carry  # noqa: E402
from sovereign_bots.bots.polymarket.fee_hurdle import FeeHurdleBot, fee_math  # noqa: E402
from sovereign_bots.bots.polymarket.resolution_clarity import ResolutionClarityBot, grade  # noqa: E402
from sovereign_bots.core.bot import Bot, Vote  # noqa: E402
from sovereign_bots.core.coordinator import decide  # noqa: E402

HURDLE = FeeHurdleBot()
CARRY = CarryBot()
CLARITY = ResolutionClarityBot()
ALL = (HURDLE, CARRY, CLARITY)


def test_contract():
    assert HURDLE.id == "fee_hurdle" and HURDLE.veto is True
    assert CARRY.id == "carry" and CARRY.veto is False
    assert CLARITY.id == "resolution_clarity" and CLARITY.veto is True
    for b in ALL:
        assert isinstance(b, Bot) and "polymarket" in b.domains


# ------------------------------------------------------------- fee hurdle
def test_fee_math_cheap_contract_is_taxed_out():
    m = fee_math(0.02, 0.02)
    assert m["cost"] == 0.04
    assert m["fee_share"] == 0.5


def test_fee_math_no_upside_above_98c():
    assert fee_math(0.98, 0.02)["taxed_out"] is True
    assert fee_math(0.90, 0.02)["taxed_out"] is False


def test_fee_math_annualises():
    m = fee_math(0.42, 0.02, model_p=0.50, days=90)
    assert m["edge"] > 0.05
    assert m["annualised"] > 0


def test_hurdle_rejects_taxed_out():
    v = HURDLE.vote({"price": 0.985, "fee": 0.02, "model_p": 0.99})
    assert v.decision == "reject"
    assert "no_upside" in v.reasons[0]


def test_hurdle_rejects_sub_edge():
    v = HURDLE.vote({"price": 0.50, "fee": 0.02, "model_p": 0.515})
    assert v.decision == "reject"


def test_hurdle_rejects_low_annualised():
    v = HURDLE.vote({"price": 0.50, "fee": 0.02, "model_p": 0.53, "days_to_resolve": 300})
    assert v.decision == "reject"
    assert any("annualised" in r for r in v.reasons)


def test_hurdle_approves_good_edge():
    v = HURDLE.vote({"price": 0.42, "fee": 0.02, "model_p": 0.52, "days_to_resolve": 30})
    assert v.decision == "approve"


def test_hurdle_rejects_extreme_fee_share_without_model():
    v = HURDLE.vote({"price": 0.05, "fee": 0.02})
    assert v.decision == "reject"


def test_hurdle_rejects_bad_price():
    assert HURDLE.vote({"price": 0}).decision == "reject"
    assert HURDLE.vote({}).decision == "reject"


# ------------------------------------------------------------------ carry
def test_carry_discount_shrinks_midpoint():
    c = carry(0.40, 365)
    assert c["discount"] < 1.0
    assert c["fair"] < 0.40
    assert c["max_mid_shift"] > 0.02


def test_carry_near_term_is_small():
    c = carry(0.40, 1)
    assert c["max_mid_shift"] < 0.001


def test_carry_rejects_overpaying():
    v = CARRY.vote({"price": 0.44, "days_to_resolve": 365, "fee": 0.02})
    assert v.decision == "reject"
    assert "overpaying_carry" in v.reasons[0]


def test_carry_approves_with_edge():
    v = CARRY.vote({"price": 0.30, "days_to_resolve": 30, "fee": 0.02, "model_p": 0.45})
    assert v.decision == "approve"


def test_carry_abstains_without_model():
    v = CARRY.vote({"price": 0.20, "days_to_resolve": 10, "fee": 0.02})
    assert v.decision in ("abstain", "reject")


def test_carry_abstains_missing_inputs():
    assert CARRY.vote({}).decision == "abstain"
    assert CARRY.vote({"price": 0.5}).decision == "abstain"


# -------------------------------------------------------------- clarity
def test_grade_clean_contract_scores_zero():
    g = grade("Will BTC close above $80,000 on 2026-12-31 per Coinbase official close?",
              "Resolved by exchange staff using Coinbase official closing price.")
    assert g["score"] == 0, g


def test_grade_vague_contract_scores_high():
    g = grade("Will the economy be substantially weaker soon?")
    assert g["score"] >= 2


def test_clarity_rejects_vague():
    v = CLARITY.vote({"question": "Will things be substantially better for most people?"})
    assert v.decision == "reject"


def test_clarity_approves_clean():
    v = CLARITY.vote({
        "question": "Will BTC close above $80,000 on 2026-12-31?",
        "resolution_rules": "Settled by exchange staff from the Coinbase official close on that date.",
    })
    assert v.decision == "approve"
    assert v.confidence >= 0.8


def test_clarity_penalises_single_ambiguity():
    # exactly one flag: hedge language, with a threshold and a date present
    v = CLARITY.vote({"question": "Will inflation be substantially above 3% by June 2026?"})
    assert v.decision == "approve"
    assert v.confidence <= 0.5
    assert "one_ambiguity" in v.reasons[0]


def test_clarity_does_not_grade_authority_without_rules():
    # A clean question with no rules text must not be punished for missing
    # authority/source it was never given.
    g = grade("Will the Fed cut rates by 25 bps in March 2026?")
    assert g["score"] == 0


def test_clarity_rejects_missing_question():
    assert CLARITY.vote({}).decision == "reject"


# ------------------------------------------------------------ integration
def test_never_raises():
    for ctx in (None, {}, [], "x", 42,
                {"price": "cheap", "fee": object()},
                {"question": 123, "resolution_rules": {"a": 1}},
                {"price": float("nan"), "days_to_resolve": float("inf")}):
        for b in ALL:
            v = b.vote(ctx)
            assert isinstance(v, Vote)
            assert v.decision in ("approve", "reject", "abstain")
            assert 0.0 <= v.confidence <= 1.0


def test_coordinator_vetoes_on_fee_or_clarity():
    ctx = {"price": 0.985, "fee": 0.02, "model_p": 0.99,
           "days_to_resolve": 5,
           "question": "Will things maybe get better somehow?"}
    out = decide(ALL, ctx)
    assert out["decision"] == "VETO"
    assert out["vetoes"] >= 1


def test_coordinator_allows_clean_cheap_edge():
    ctx = {"price": 0.42, "fee": 0.02, "model_p": 0.52, "days_to_resolve": 21,
           "question": "Will BTC close above $80,000 on 2026-12-31?",
           "resolution_rules": "Settled by exchange staff from the Coinbase official close."}
    out = decide(ALL, ctx)
    assert out["decision"] == "ALLOW", out["reasons"]
