"""Tests for the soft-signal layer: schedule_spot, public_bias, conditions, coach_intent."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sovereign_bots.bots.sports.coach_intent import CoachIntentBot  # noqa: E402
from sovereign_bots.bots.sports.conditions import ConditionsBot  # noqa: E402
from sovereign_bots.bots.sports.public_bias import PublicBiasBot  # noqa: E402
from sovereign_bots.bots.sports.schedule_spot import ScheduleSpotBot  # noqa: E402
from sovereign_bots.core.bot import Bot, Vote  # noqa: E402
from sovereign_bots.core.coordinator import decide  # noqa: E402

SPOT = ScheduleSpotBot()
PUBLIC = PublicBiasBot()
COND = ConditionsBot()
COACH = CoachIntentBot()
ALL = (SPOT, PUBLIC, COND, COACH)


def test_contract_and_domains():
    assert SPOT.id == "schedule_spot" and SPOT.veto is False
    assert PUBLIC.id == "public_bias" and PUBLIC.veto is False
    assert COND.id == "conditions" and COND.veto is False
    assert COACH.id == "coach_intent" and COACH.veto is False
    assert "sports" in PUBLIC.domains and "polymarket" in PUBLIC.domains
    for b in ALL:
        assert isinstance(b, Bot) and len(b.mandate) > 10


# ---------------------------------------------------------------- schedule
def test_letdown_spot_rejects():
    v = SPOT.vote({"spots": ["letdown"], "evidence": {"letdown": "beat a contender in primetime"}})
    assert v.decision == "reject"


def test_unsourced_letdown_ignored():
    v = SPOT.vote({"spots": ["letdown"], "evidence": []})
    assert v.decision == "approve"
    assert not any("letdown" in r for r in v.reasons)


def test_revenge_spot_with_source_approves():
    v = SPOT.vote({"spots": ["revenge_spot"], "evidence": {"revenge_spot": "faces his former team"}})
    assert v.decision == "approve"
    assert v.confidence > 0


def test_b2b_rest_drag():
    v = SPOT.vote({"rest_days": 0})
    assert v.decision in ("reject", "approve")
    assert any("b2b" in r for r in v.reasons)


def test_sandwich_and_lookahead_stack():
    v = SPOT.vote({"spots": ["sandwich", "lookahead"],
                   "evidence": {"sandwich": "between two contenders", "lookahead": "playoff game next"}})
    assert v.decision == "reject"


def test_schedule_no_signals_abstains():
    assert SPOT.vote({}).decision == "abstain"


# ------------------------------------------------------------------ public
def test_heavy_public_on_us_rejects():
    v = PUBLIC.vote({"side": "over", "public_bet_pct": 0.88})
    assert v.decision == "reject"


def test_public_fading_us_approves():
    v = PUBLIC.vote({"side": "over", "public_bet_pct": 0.20})
    assert v.decision == "approve"
    assert any("contrarian" in r for r in v.reasons)


def test_public_percent_forms():
    a = PUBLIC.vote({"side": "over", "public_bet_pct": 88})
    b = PUBLIC.vote({"side": "over", "public_bet_pct": 0.88})
    assert a.decision == b.decision == "reject"


def test_money_vs_tickets_divergence_penalised():
    v = PUBLIC.vote({"side": "over", "public_bet_pct": 0.60, "public_money_pct": 0.80})
    assert any("divergence" in r for r in v.reasons)


def test_public_no_data_abstains():
    assert PUBLIC.vote({}).decision == "abstain"


# -------------------------------------------------------------- conditions
def test_high_wind_rejects():
    v = COND.vote({"wind_mph": 24})
    assert v.decision == "reject"


def test_indoor_neutralises():
    v = COND.vote({"indoor": True, "wind_mph": 0})
    assert v.decision == "approve"
    assert v.confidence > 0


def test_altitude_helps():
    v = COND.vote({"altitude_ft": 5200})
    assert v.decision == "approve"


def test_conditions_missing_abstains():
    assert COND.vote({}).decision == "abstain"


# ------------------------------------------------------------------- coach
def test_seeding_locked_fades():
    v = COACH.vote({"seeding_locked": True, "player": "A"})
    assert v.decision == "reject"


def test_load_management_fades():
    v = COACH.vote({"load_management": True})
    assert v.decision == "reject"


def test_must_play_approves():
    v = COACH.vote({"must_play": True, "minutes_trend": 5})
    assert v.decision == "approve"


def test_blowout_risk_fades():
    v = COACH.vote({"expected_spread": -14})
    assert v.decision == "reject"


def test_coach_missing_abstains():
    assert COACH.vote({}).decision == "abstain"


# ------------------------------------------------------------ integration
def test_never_raises():
    for ctx in (None, {}, [], "x", 42,
                {"spots": 5, "evidence": {"a": 1}},
                {"public_bet_pct": float("nan"), "side": None},
                {"wind_mph": object(), "temp_f": "cold"},
                {"expected_spread": float("inf")}):
        for b in ALL:
            v = b.vote(ctx)
            assert isinstance(v, Vote)
            assert v.decision in ("approve", "reject", "abstain")
            assert 0.0 <= v.confidence <= 1.0


def test_coordinator_good_soft_context_allows():
    ctx = {
        "spots": ["revenge_spot", "must_win"],
        "evidence": {"revenge_spot": "faces former team", "must_win": "playoff clincher"},
        "rest_days": 3,
        "side": "over",
        "public_bet_pct": 0.25,
        "indoor": True,
        "must_play": True,
        "minutes_trend": 5,
    }
    # soft signals are permission, not edge: this pack has no edge-bearing seat,
    # so it must opt out of the coordinator edge gate to reach ALLOW.
    out = decide(ALL, ctx, require_edge=False)
    assert out["decision"] == "ALLOW", out["reasons"]
    assert out["edge_gate"] == "disabled" and out["edge_seats"] == 0


def test_coordinator_bad_soft_context_rejects():
    ctx = {
        "spots": ["letdown", "lookahead"],
        "evidence": {"letdown": "after a big win", "lookahead": "marquee game next"},
        "rest_days": 0,
        "side": "over",
        "public_bet_pct": 0.90,
        "wind_mph": 25,
        "seeding_locked": True,
    }
    out = decide(ALL, ctx)
    assert out["decision"] == "REJECT"
    assert out["vetoes"] == 0
