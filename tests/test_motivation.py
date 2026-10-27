"""Tests for the motivation / fatigue bot (sports + polymarket)."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sovereign_bots.bots.sports.motivation import (  # noqa: E402
    HIGH_TIER, LOW_TIER, MotivationBot, apply_to_probability, fatigue_penalty,
    motivation_delta, score,
)
from sovereign_bots.core.bot import Bot, Vote  # noqa: E402
from sovereign_bots.core.coordinator import decide  # noqa: E402

BOT = MotivationBot()


def rested_playoff():
    return {
        "player": "A", "rest_days": 3, "games_last_7": 2, "travel_miles": 0,
        "tz_shift": 0, "minutes_last_game": 28, "age": 26,
        "stakes": "playoff", "narrative": ["revenge"],
        "evidence": ["local beat writer: 'he wants this one badly'"],
        "model_p": 0.55,
    }


def tired_dead_rubber():
    return {
        "player": "B", "rest_days": 0, "games_last_7": 4, "travel_miles": 1800,
        "tz_shift": 3, "minutes_last_game": 39, "age": 35,
        "stakes": "dead_rubber", "narrative": [], "evidence": [],
        "model_p": 0.55,
    }


class MetaTests:
    def test_contract(self):
        assert BOT.id == "motivation"
        assert BOT.veto is False
        assert "sports" in BOT.domains and "polymarket" in BOT.domains

    def test_is_a_bot(self):
        assert isinstance(BOT, Bot)


def test_rested_playoff_scores_high():
    r = score(rested_playoff())
    assert r["score"] >= HIGH_TIER, r
    assert r["tier"] == "high"
    assert r["prob_adjust"] > 0


def test_tired_dead_rubber_scores_low():
    r = score(tired_dead_rubber())
    assert r["score"] < LOW_TIER, r
    assert r["tier"] == "low"
    assert r["prob_adjust"] < 0


def test_back_to_back_costs_points():
    rested = {"rest_days": 2, "games_last_7": 2}
    b2b = {"rest_days": 0, "games_last_7": 2}
    assert score(b2b)["score"] < score(rested)["score"]


def test_travel_and_tz_cost_points():
    near = {"rest_days": 2, "travel_miles": 100, "tz_shift": 0}
    far = {"rest_days": 2, "travel_miles": 2500, "tz_shift": 3}
    assert score(far)["score"] < score(near)["score"]


def test_minutes_load_and_age_cost_points():
    young = {"rest_days": 2, "minutes_last_game": 28, "age": 24}
    old = {"rest_days": 2, "minutes_last_game": 40, "age": 36}
    assert score(old)["score"] < score(young)["score"]


def test_fatigue_penalty_is_capped():
    fat = fatigue_penalty({"travel_miles": 99999, "tz_shift": 12})
    assert fat["penalty"] <= 8.0 + 7.0 + 1e-9


def test_stakes_move_score():
    low = score({"rest_days": 2, "stakes": "dead_rubber"})["score"]
    high = score({"rest_days": 2, "stakes": "playoff"})["score"]
    assert high > low


def test_unsourced_narrative_is_ignored():
    d = motivation_delta({"narrative": ["revenge"], "evidence": []})
    assert d["delta"] == 0.0
    assert any("unsourced:revenge" in x for x in d["unsourced"])


def test_sourced_narrative_counts():
    d = motivation_delta({"narrative": ["revenge"], "evidence": ["revenge game vs his old team"]})
    assert d["delta"] > 0


def test_negative_narrative_with_source_drops_score():
    base = score({"rest_days": 2, "stakes": "routine"})["score"]
    with_issue = score({"rest_days": 2, "stakes": "routine",
                        "narrative": ["personal_issue"],
                        "evidence": ["reported personal issue"]})["score"]
    assert with_issue < base


def test_unsourced_claims_reduce_confidence():
    sourced = score({"rest_days": 2, "stakes": "playoff",
                     "narrative": ["revenge"], "evidence": ["revenge"]})
    unsourced = score({"rest_days": 2, "stakes": "playoff",
                       "narrative": ["revenge"], "evidence": []})
    assert unsourced["confidence"] < sourced["confidence"]


def test_insufficient_signals_abstains():
    v = BOT.vote({"player": "X"})
    assert v.decision == "abstain"
    assert "insufficient_signals" in v.reasons[0]


def test_single_signal_is_not_enough():
    v = BOT.vote({"rest_days": 2})
    assert v.decision == "abstain"


def test_low_motivation_rejects():
    v = BOT.vote(tired_dead_rubber())
    assert v.decision == "reject"
    assert "low_motivation" in v.reasons[0]


def test_high_motivation_approves():
    v = BOT.vote(rested_playoff())
    assert v.decision == "approve"
    assert "high_motivation" in v.reasons[0]


def test_neutral_scores_neutral():
    ctx = {"rest_days": 2, "games_last_7": 3, "stakes": "routine"}
    r = score(ctx)
    assert r["tier"] == "neutral"
    assert BOT.vote(ctx).decision == "approve"


def test_prob_adjust_is_bounded():
    assert abs(score(rested_playoff())["prob_adjust"]) <= 0.04
    assert abs(score(tired_dead_rubber())["prob_adjust"]) <= 0.04


def test_apply_to_probability_scales_with_confidence():
    r = score(rested_playoff())
    p = apply_to_probability(0.55, r)
    assert p is not None and p > 0.55
    low = {"confidence": 0.0, "prob_adjust": 0.04}
    assert apply_to_probability(0.55, low) == 0.55


def test_apply_handles_missing_probability():
    assert apply_to_probability(None, score(rested_playoff())) is None


def test_never_raises_on_hostile_input():
    for ctx in (None, {}, [], "x", 42,
                {"rest_days": "soon", "travel_miles": object()},
                {"narrative": 5, "evidence": {"a": 1}},
                {"minutes_last_game": float("nan"), "age": float("inf")}):
        v = BOT.vote(ctx)
        assert isinstance(v, Vote)
        assert v.decision in ("approve", "reject", "abstain")
        assert 0.0 <= v.confidence <= 1.0


def test_coordinator_integrates_without_veto_power():
    class Dummy(Bot):
        id = "dummy"; veto = False; domains = ("sports",)
        def evaluate(self, c):
            return Vote("dummy", "approve", 0.8, ["ok"])

    # low motivation only rejects; it cannot veto on its own
    out = decide([BOT, Dummy()], tired_dead_rubber())
    assert out["decision"] == "REJECT"
    assert out["vetoes"] == 0

    # a motivation read plus a wiring dummy carries no edge model: opt out of
    # the coordinator's edge gate (see test_model_maths_bugs.py)
    out2 = decide([BOT, Dummy()], rested_playoff(), require_edge=False)
    assert out2["decision"] == "ALLOW"
