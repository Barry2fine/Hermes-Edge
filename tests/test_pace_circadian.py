"""Tests for pace_adjust and circadian (sports)."""

import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sovereign_bots.bots.sports.circadian import CircadianBot, load  # noqa: E402
from sovereign_bots.bots.sports.pace_adjust import PaceAdjustBot, adjust  # noqa: E402
from sovereign_bots.core.bot import Bot, Vote  # noqa: E402
from sovereign_bots.core.coordinator import decide  # noqa: E402

PACE = PaceAdjustBot()
CIRC = CircadianBot()
ALL = (PACE, CIRC)


def test_contract():
    assert PACE.id == "pace_adjust" and PACE.veto is False
    assert CIRC.id == "circadian" and CIRC.veto is False
    for b in ALL:
        assert isinstance(b, Bot) and "sports" in b.domains


# ------------------------------------------------------------------ pace
def test_adjust_fast_game_inflates():
    a = adjust(20.0, 108.0, 106.0, 99.0)
    assert a["adjusted"] > 20.0
    assert a["factor"] > 1.0


def test_adjust_slow_game_deflates():
    a = adjust(20.0, 92.0, 90.0, 99.0)
    assert a["adjusted"] < 20.0


def test_adjust_neutral_pace():
    a = adjust(20.0, 99.0, 99.0, 99.0)
    assert abs(a["adjusted"] - 20.0) < 1e-9
    assert a["factor"] == 1.0


def test_adjust_factor_is_capped():
    a = adjust(20.0, 400.0, 400.0, 99.0)
    assert a["factor"] == 1.25


def test_pace_approves_when_fast_game_beats_line():
    v = PACE.vote({"projection": 20.0, "team_pace": 106.0, "opp_pace": 107.0,
                   "league_pace": 99.0, "line": 20.0, "side": "over"})
    assert v.decision == "approve"


def test_pace_rejects_when_slow_game_misses_line():
    v = PACE.vote({"projection": 20.0, "team_pace": 92.0, "opp_pace": 91.0,
                   "league_pace": 99.0, "line": 20.0, "side": "over"})
    assert v.decision == "reject"


def test_pace_abstains_without_line():
    v = PACE.vote({"projection": 20.0, "team_pace": 100.0, "opp_pace": 100.0})
    assert v.decision == "abstain"


def test_pace_abstains_missing_inputs():
    assert PACE.vote({}).decision == "abstain"


# -------------------------------------------------------------- circadian
def test_eastward_is_worse_than_westward():
    east = load({"tz_shift": 3, "rest_days": 2})
    west = load({"tz_shift": -3, "rest_days": 2})
    assert east["penalty"] > west["penalty"]


def test_late_body_clock_start_costs():
    late = load({"start_hour_local": 22})
    early = load({"start_hour_local": 18})
    assert late["penalty"] > early["penalty"]


def test_back_to_back_costs():
    b2b = load({"rest_days": 0})
    rested = load({"rest_days": 3})
    assert b2b["penalty"] > rested["penalty"]


def test_penalty_is_capped():
    assert load({"tz_shift": 12, "rest_days": 0, "start_hour_local": 23})["penalty"] <= 18.0


def test_severe_circadian_risk_rejects():
    v = CIRC.vote({"tz_shift": 3, "rest_days": 0, "start_hour_local": 22})
    assert v.decision == "reject"
    assert "circadian_risk" in v.reasons[0]


def test_mild_drag_is_low_confidence_approve():
    v = CIRC.vote({"tz_shift": 2, "start_hour_local": 19})
    assert v.decision == "approve"
    assert v.confidence <= 0.5


def test_clean_travel_approves():
    v = CIRC.vote({"tz_shift": 0, "start_hour_local": 18, "rest_days": 3})
    assert v.decision == "approve"
    assert v.confidence >= 0.5


def test_circadian_abstains_without_data():
    assert CIRC.vote({}).decision == "abstain"


# ------------------------------------------------------------ resilience
def test_never_raises():
    for ctx in (None, {}, [], "x", 42,
                {"projection": "big", "team_pace": object()},
                {"tz_shift": float("nan"), "start_hour_local": float("inf")}):
        for b in ALL:
            v = b.vote(ctx)
            assert isinstance(v, Vote)
            assert v.decision in ("approve", "reject", "abstain")
            assert 0.0 <= v.confidence <= 1.0


def test_coordinator_allows_fast_rested_home_game():
    ctx = {"projection": 20.0, "team_pace": 107.0, "opp_pace": 106.0,
           "league_pace": 99.0, "line": 20.0, "side": "over",
           "tz_shift": 0, "start_hour_local": 19, "rest_days": 3}
    out = decide(ALL, ctx)
    assert out["decision"] == "ALLOW", out["reasons"]
