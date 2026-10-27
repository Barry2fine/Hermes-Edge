"""Tests for the two calibration-drift vetoes and the CLV judge.

* sovereign_bots/bots/polymarket/calibration_drift.py -> CalibrationDriftBot (veto)
* sovereign_bots/bots/sports/calibration_drift.py     -> CalibrationDriftBot (veto)
* sovereign_bots/bots/sports/clv.py                   -> CLVBot (no veto)

The bot modules are imported directly. The pack ``__init__`` files are
deliberately untouched by this change, so nothing here relies on a bot being
listed in a pack's ``BOTS``.
"""

import copy
import math
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sovereign_bots.bots.polymarket.calibration_drift import (  # noqa: E402
    CalibrationDriftBot as PolyCalibrationDriftBot,
)
from sovereign_bots.bots.sports.calibration_drift import (  # noqa: E402
    CalibrationDriftBot as SportsCalibrationDriftBot,
)
from sovereign_bots.bots.sports.clv import CLVBot  # noqa: E402
from sovereign_bots.core.bot import DECISIONS, Bot, Vote  # noqa: E402
from sovereign_bots.core.coordinator import decide  # noqa: E402

POLY = PolyCalibrationDriftBot()
SPORTS = SportsCalibrationDriftBot()
CLV = CLVBot()

ALL_BOTS = [POLY, SPORTS, CLV]
ALL_BOT_IDS = ["polymarket.calibration_drift", "sports.calibration_drift", "sports.clv"]


# --------------------------------------------------------------------------- helpers
def poly_ctx(**over):
    """A polymarket model that is sampled, beats baseline and is stable."""
    ctx = {
        "market_id": "fed-cut-march",
        "brier_model": 0.180,
        "brier_baseline": 0.240,
        "recent_brier": 0.175,
        "prior_brier": 0.170,
        "n": 140,
    }
    ctx.update(over)
    return ctx


def sports_ctx(**over):
    """A sports model that beats both baselines and is stable on both rules."""
    ctx = {
        "event_id": "nfl-2026-w8-ktc-buf",
        "market": "player_pass_yds_ou265.5",
        "brier_model": 0.192,
        "brier_baseline": 0.235,
        "logloss_model": 0.575,
        "logloss_baseline": 0.640,
        "recent_brier": 0.190,
        "prior_brier": 0.182,
        "n": 220,
    }
    ctx.update(over)
    return ctx


def clv_ctx(**over):
    ctx = {"clv_mean": 0.020, "clv_n": 75, "clv_hit_rate": 0.55}
    ctx.update(over)
    return ctx


def vote_of(bot, ctx):
    """Every vote must be a well-formed Vote with a finite confidence."""
    v = bot.vote(ctx)
    assert isinstance(v, Vote), "vote() must always return a Vote"
    assert v.bot == bot.id
    assert v.decision in DECISIONS
    assert isinstance(v.confidence, float)
    assert math.isfinite(v.confidence), "confidence must never be NaN/inf"
    assert 0.0 <= v.confidence <= 1.0
    assert isinstance(v.reasons, list) and v.reasons
    assert all(isinstance(r, str) and r for r in v.reasons)
    assert isinstance(v.evidence, dict)
    return v


class DummyApprove(Bot):
    """Unrelated vote used to prove the coordinator wiring, not the drift logic."""

    id = "dummy_approve"
    mandate = "Approve anything that reaches the coordinator."
    domains = ("polymarket",)
    veto = False

    def evaluate(self, context):
        return Vote(self.id, "approve", 0.75, ["edge_ok"], {"dummy": True})


class Boom:
    """A number-shaped object that refuses to be a number."""

    def __float__(self):
        raise RuntimeError("hostile __float__")


# --------------------------------------------------------------------------- metadata
@pytest.mark.parametrize(
    "bot,bot_id,domain,veto",
    [
        (POLY, "calibration_drift", "polymarket", True),
        (SPORTS, "calibration_drift", "sports", True),
        (CLV, "clv", "sports", False),
    ],
    ids=ALL_BOT_IDS,
)
def test_bot_contract_metadata(bot, bot_id, domain, veto):
    assert isinstance(bot, Bot)
    assert bot.id == bot_id
    assert isinstance(bot.mandate, str) and len(bot.mandate) > 20
    assert bot.domains == (domain,)
    assert isinstance(bot.veto, bool) and bot.veto is veto


def test_modules_are_distinct_and_imported_directly():
    assert PolyCalibrationDriftBot is not SportsCalibrationDriftBot
    assert PolyCalibrationDriftBot.__module__ == (
        "sovereign_bots.bots.polymarket.calibration_drift"
    )
    assert SportsCalibrationDriftBot.__module__ == (
        "sovereign_bots.bots.sports.calibration_drift"
    )
    assert CLVBot.__module__ == "sovereign_bots.bots.sports.clv"


# ============================================================ polymarket drift
def test_poly_approves_good_model():
    v = vote_of(POLY, poly_ctx())
    assert v.decision == "approve"
    assert v.confidence >= 0.55
    assert any("beats_baseline" in r for r in v.reasons)


def test_poly_min_sample_boundary():
    assert vote_of(POLY, poly_ctx(n=30)).decision == "approve"
    thin = vote_of(POLY, poly_ctx(n=29))
    assert thin.decision == "reject"
    assert "insufficient_sample" in thin.reasons[0]
    assert vote_of(POLY, poly_ctx(n=0)).decision == "reject"


def test_poly_baseline_gate():
    # equal to baseline is not worse: it is allowed through
    assert vote_of(POLY, poly_ctx(brier_model=0.240, brier_baseline=0.240)).decision == "approve"
    worse = vote_of(POLY, poly_ctx(brier_model=0.2401, brier_baseline=0.240))
    assert worse.decision == "reject"
    assert "worse_than_baseline" in worse.reasons[0]


def test_poly_drift_requires_both_relative_and_absolute_breach():
    # +25% relative but only +0.5 point absolute -> not drift
    assert vote_of(POLY, poly_ctx(recent_brier=0.025, prior_brier=0.020)).decision == "approve"
    # +1.1 point absolute but only +6.5% relative -> not drift
    assert vote_of(POLY, poly_ctx(recent_brier=0.181, prior_brier=0.170)).decision == "approve"
    # exactly 1.2x relative without the absolute point -> not drift
    assert vote_of(POLY, poly_ctx(recent_brier=0.204, prior_brier=0.170)).decision == "approve"
    # both breached -> drift
    drift = vote_of(POLY, poly_ctx(recent_brier=0.215, prior_brier=0.170))
    assert drift.decision == "reject"
    assert "calibration_drift" in drift.reasons[0]


def test_poly_drift_on_tiny_prior_needs_the_absolute_floor():
    # 4x relative on a tiny base is still under one point absolute at +0.004
    assert vote_of(POLY, poly_ctx(recent_brier=0.008, prior_brier=0.004)).decision == "approve"
    # ... but +1.2 points on that base trips both gates
    assert vote_of(POLY, poly_ctx(recent_brier=0.016, prior_brier=0.004)).decision == "reject"


@pytest.mark.parametrize(
    "over",
    [
        {"n": None},
        {"n": "many"},
        {"brier_model": None},
        {"brier_baseline": None},
        {"brier_model": -0.01},
        {"brier_model": float("nan")},
        {"brier_baseline": float("inf")},
        {"brier_model": True},
    ],
)
def test_poly_unreadable_inputs_are_vetoes(over):
    v = vote_of(POLY, poly_ctx(**over))
    assert v.decision == "reject"
    assert v.reasons[0].startswith("missing_or_invalid:")
    assert v.confidence >= 0.85


def test_poly_unmeasured_drift_does_not_fabricate_a_veto():
    v = vote_of(POLY, poly_ctx(recent_brier=None, prior_brier=None))
    assert v.decision == "approve"
    assert v.evidence["drift"] is None
    assert any("drift_unmeasured" in r for r in v.reasons)


def test_poly_zero_prior_window_is_readable():
    # a prior window of exactly 0 makes the ratio undefined, not the vote
    v = vote_of(POLY, poly_ctx(recent_brier=0.0, prior_brier=0.0))
    assert v.decision == "approve"
    assert v.evidence["drift"] == 0.0
    assert v.evidence["drift_ratio"] is None
    assert any("rel=n/a" in r for r in v.reasons)


def test_poly_evidence_carries_four_scores_and_drift():
    ctx = poly_ctx()
    v = vote_of(POLY, ctx)
    for key in ("brier_model", "brier_baseline", "recent_brier", "prior_brier", "n", "drift"):
        assert key in v.evidence, f"missing evidence key {key}"
    assert v.evidence["brier_model"] == pytest.approx(ctx["brier_model"])
    assert v.evidence["brier_baseline"] == pytest.approx(ctx["brier_baseline"])
    assert v.evidence["recent_brier"] == pytest.approx(ctx["recent_brier"])
    assert v.evidence["prior_brier"] == pytest.approx(ctx["prior_brier"])
    assert v.evidence["n"] == ctx["n"]
    assert v.evidence["drift"] == pytest.approx(
        ctx["recent_brier"] - ctx["prior_brier"]
    )


def test_poly_evidence_survives_a_reject():
    v = vote_of(POLY, poly_ctx(n=3))
    for key in ("brier_model", "brier_baseline", "recent_brier", "prior_brier"):
        assert v.evidence[key] is not None


def test_poly_confidence_grows_with_the_edge():
    thin = vote_of(
        POLY, poly_ctx(brier_model=0.235, brier_baseline=0.240, recent_brier=0.170, prior_brier=0.170)
    ).confidence
    fat = vote_of(
        POLY, poly_ctx(brier_model=0.120, brier_baseline=0.240, recent_brier=0.170, prior_brier=0.170)
    ).confidence
    assert fat > thin
    assert fat <= 0.95


# ================================================================ sports drift
def test_sports_approves_good_model():
    v = vote_of(SPORTS, sports_ctx())
    assert v.decision == "approve"
    assert v.confidence >= 0.55


def test_sports_min_sample_boundary():
    assert vote_of(SPORTS, sports_ctx(n=30)).decision == "approve"
    thin = vote_of(SPORTS, sports_ctx(n=29))
    assert thin.decision == "reject"
    assert "insufficient_sample" in thin.reasons[0]
    assert vote_of(SPORTS, sports_ctx(n=7)).decision == "reject"


def test_sports_brier_worse_than_baseline():
    v = vote_of(SPORTS, sports_ctx(brier_model=0.250, brier_baseline=0.235))
    assert v.decision == "reject"
    assert "worse_than_baseline" in v.reasons[0] and "brier" in v.reasons[0]


def test_sports_logloss_worse_than_baseline():
    v = vote_of(SPORTS, sports_ctx(logloss_model=0.700, logloss_baseline=0.640))
    assert v.decision == "reject"
    assert "worse_than_baseline" in v.reasons[0] and "logloss" in v.reasons[0]


def test_sports_equal_scores_are_not_worse():
    v = vote_of(
        SPORTS,
        sports_ctx(brier_model=0.235, logloss_model=0.640,
                   recent_brier=0.180, prior_brier=0.182),
    )
    assert v.decision == "approve"


def test_sports_brier_drift_rejects():
    v = vote_of(SPORTS, sports_ctx(recent_brier=0.230, prior_brier=0.182))
    assert v.decision == "reject"
    assert "calibration_drift" in v.reasons[0] and "brier" in v.reasons[0]


def test_sports_logloss_drift_rejects():
    v = vote_of(SPORTS, sports_ctx(recent_logloss=0.800, prior_logloss=0.640))
    assert v.decision == "reject"
    assert "calibration_drift" in v.reasons[0] and "logloss" in v.reasons[0]


def test_sports_drift_needs_both_gates():
    # +8% relative on logloss is not drift even if it is +0.05 absolute
    assert vote_of(SPORTS, sports_ctx(recent_logloss=0.690, prior_logloss=0.640)).decision == "approve"
    # +1 point on a tiny base but only +20% relative is not drift either
    assert vote_of(SPORTS, sports_ctx(recent_logloss=0.120, prior_logloss=0.100)).decision == "approve"


def test_sports_zero_prior_window_is_readable():
    v = vote_of(SPORTS, sports_ctx(recent_brier=0.0, prior_brier=0.0))
    assert v.decision == "approve"
    assert v.evidence["drift_brier"] == 0.0
    assert v.evidence["degraded"]["brier"] is False


def test_sports_logloss_only_context_is_enough():
    ctx = {
        "event_id": "nba-2026-11-02-bos-nyk",
        "logloss_model": 0.610,
        "logloss_baseline": 0.700,
        "recent_logloss": 0.605,
        "prior_logloss": 0.600,
        "n": 90,
    }
    v = vote_of(SPORTS, ctx)
    assert v.decision == "approve"
    assert any("logloss_ok" in r for r in v.reasons)
    assert v.evidence["brier_model"] is None


def test_sports_brier_only_context_is_enough():
    ctx = {
        "brier_model": 0.180,
        "brier_baseline": 0.230,
        "n": 60,
    }
    v = vote_of(SPORTS, ctx)
    assert v.decision == "approve"
    assert v.evidence["logloss_model"] is None
    assert any("brier_ok" in r for r in v.reasons)


def test_sports_no_calibration_pair_is_a_veto():
    v = vote_of(SPORTS, {"n": 500})
    assert v.decision == "reject"
    assert "no_calibration_pair" in v.reasons[0]


@pytest.mark.parametrize(
    "ctx",
    [
        {"n": 100, "brier_model": 0.19},
        {"n": 100, "brier_baseline": 0.23},
        {"n": 100, "logloss_model": 0.60},
        {"n": 100, "logloss_baseline": 0.65},
    ],
    ids=["half-brier", "half-brier-baseline", "half-logloss", "half-logloss-baseline"],
)
def test_sports_half_a_pair_is_a_veto(ctx):
    v = vote_of(SPORTS, ctx)
    assert v.decision == "reject"
    assert v.reasons[0].startswith("missing_or_invalid:")
    assert "pair" in v.reasons[0]


def test_sports_missing_n_is_a_veto():
    v = vote_of(SPORTS, sports_ctx(n=None))
    assert v.decision == "reject"
    assert "n" in v.reasons[0]


def test_sports_evidence_carries_both_rules_and_drifts():
    ctx = sports_ctx()
    v = vote_of(SPORTS, ctx)
    for key in (
        "brier_model", "brier_baseline", "logloss_model", "logloss_baseline",
        "recent_brier", "prior_brier", "drift_brier", "n",
    ):
        assert key in v.evidence, f"missing evidence key {key}"
    assert v.evidence["logloss_model"] == pytest.approx(ctx["logloss_model"])
    assert v.evidence["drift_brier"] == pytest.approx(
        ctx["recent_brier"] - ctx["prior_brier"]
    )


# ======================================================================== CLV
def test_clv_approves_positive_book():
    v = vote_of(CLV, clv_ctx(clv_mean=0.020))
    assert v.decision == "approve"
    assert any("positive_clv" in r for r in v.reasons)


def test_clv_approve_confidence_formula():
    for clv_mean in (0.001, 0.005, 0.020, 0.050):
        v = vote_of(CLV, clv_ctx(clv_mean=clv_mean))
        assert v.decision == "approve"
        assert v.confidence == pytest.approx(0.55 + min(0.35, clv_mean * 5.0))


@pytest.mark.parametrize("clv_mean", [0.070, 0.100, 1.500, 42.0])
def test_clv_confidence_is_capped(clv_mean):
    v = vote_of(CLV, clv_ctx(clv_mean=clv_mean))
    assert v.decision == "approve"
    assert v.confidence == pytest.approx(0.90)


@pytest.mark.parametrize("clv_mean", [-0.01, -0.02, -0.05, -0.40])
def test_clv_rejects_negative_book(clv_mean):
    v = vote_of(CLV, clv_ctx(clv_mean=clv_mean))
    assert v.decision == "reject"
    assert "negative_clv" in v.reasons[0]
    assert 0.70 <= v.confidence <= 0.95


def test_clv_rejects_at_the_exact_loser_threshold():
    assert vote_of(CLV, clv_ctx(clv_mean=-0.01)).decision == "reject"
    assert vote_of(CLV, clv_ctx(clv_mean=-0.0099)).decision == "abstain"


@pytest.mark.parametrize(
    "over",
    [
        {"clv_n": 19},
        {"clv_n": 19.9},
        {"clv_n": 0},
        {"clv_n": None},
        {"clv_n": "many"},
        {"clv_mean": None},
        {"clv_mean": 0.0},
        {"clv_mean": -0.005},
        {"clv_mean": -0.0099},
    ],
)
def test_clv_abstains_without_a_readable_sample(over):
    v = vote_of(CLV, clv_ctx(**over))
    assert v.decision == "abstain"
    assert v.confidence == 0.0


def test_clv_sample_boundary():
    assert vote_of(CLV, clv_ctx(clv_n=20)).decision == "approve"
    assert vote_of(CLV, clv_ctx(clv_n=19)).decision == "abstain"


def test_clv_missing_mean_abstains_even_with_a_large_sample():
    v = vote_of(CLV, clv_ctx(clv_mean=None, clv_n=500))
    assert v.decision == "abstain"
    assert "missing_clv_mean" in v.reasons[0]


def test_clv_evidence_is_exactly_the_three_fields():
    v = vote_of(CLV, clv_ctx())
    assert set(v.evidence) == {"clv_mean", "clv_n", "clv_hit_rate"}
    assert v.evidence["clv_mean"] == pytest.approx(0.020)
    assert v.evidence["clv_n"] == 75
    assert v.evidence["clv_hit_rate"] == pytest.approx(0.55)


def test_clv_hit_rate_is_optional_and_never_decides():
    without = vote_of(CLV, clv_ctx(clv_hit_rate=None))
    assert without.decision == "approve"
    assert without.evidence["clv_hit_rate"] is None
    # a poor hit rate on a positive-CLV book does not flip the vote
    assert vote_of(CLV, clv_ctx(clv_hit_rate=0.31)).decision == "approve"


def test_clv_confidence_is_monotone_in_clv():
    lo = vote_of(CLV, clv_ctx(clv_mean=0.005)).confidence
    mid = vote_of(CLV, clv_ctx(clv_mean=0.030)).confidence
    hi = vote_of(CLV, clv_ctx(clv_mean=0.060)).confidence
    assert lo < mid < hi


# =================================================================== vote shape
@pytest.mark.parametrize(
    "bot,ctx,expected",
    [
        (POLY, poly_ctx(), "approve"),
        (POLY, poly_ctx(n=1), "reject"),
        (POLY, poly_ctx(brier_model=0.30), "reject"),
        (POLY, poly_ctx(recent_brier=0.25), "reject"),
        (SPORTS, sports_ctx(), "approve"),
        (SPORTS, sports_ctx(n=2), "reject"),
        (SPORTS, sports_ctx(logloss_model=0.9), "reject"),
        (SPORTS, sports_ctx(recent_logloss=0.9, prior_logloss=0.6), "reject"),
        (CLV, clv_ctx(), "approve"),
        (CLV, clv_ctx(clv_mean=-0.03), "reject"),
        (CLV, clv_ctx(clv_n=3), "abstain"),
        (CLV, clv_ctx(clv_mean=-0.002), "abstain"),
    ],
)
def test_every_decision_path_yields_a_clean_vote(bot, ctx, expected):
    v = vote_of(bot, ctx)
    assert v.decision == expected
    payload = v.as_dict()
    assert set(payload) == {"bot", "decision", "confidence", "reasons", "evidence"}
    assert payload["bot"] == bot.id


def test_votes_are_stateless():
    # same instance, three contexts in a row: no leakage between calls
    assert vote_of(POLY, poly_ctx()).decision == "approve"
    assert vote_of(POLY, poly_ctx(n=0)).decision == "reject"
    assert vote_of(POLY, poly_ctx()).decision == "approve"
    assert vote_of(CLV, clv_ctx(clv_mean=-0.5)).decision == "reject"
    assert vote_of(CLV, clv_ctx()).decision == "approve"


# ================================================================= coordinator
def test_coordinator_allows_poly_drift_with_a_dummy_approve():
    # certification pack: no seat prices a ticket, so the edge gate is opted out
    out = decide([PolyCalibrationDriftBot(), DummyApprove()], poly_ctx(), require_edge=False)
    assert out["decision"] == "ALLOW"
    assert out["n"] == 2
    assert out["vetoes"] == 0
    assert out["approves"] == 2
    assert len(out["votes_hash"]) == 64


def test_coordinator_vetoes_when_poly_drift_rejects():
    out = decide([PolyCalibrationDriftBot(), DummyApprove()], poly_ctx(n=12))
    assert out["decision"] == "VETO"
    assert out["vetoes"] == 1
    assert out["approves"] == 1  # the dummy still approved; the veto wins
    assert any(r.startswith("calibration_drift:") for r in out["reasons"])


def test_coordinator_vetoes_on_baseline_failure():
    out = decide([PolyCalibrationDriftBot(), DummyApprove()], poly_ctx(brier_model=0.28))
    assert out["decision"] == "VETO"
    assert out["rejects"] == 0 and out["vetoes"] == 1


def test_coordinator_allows_sports_pack_with_clv_approval():
    ctx = sports_ctx(clv_mean=0.02, clv_n=75, clv_hit_rate=0.54)
    out = decide([SportsCalibrationDriftBot(), CLVBot(), DummyApprove()], ctx)
    assert out["decision"] == "ALLOW"
    assert out["approves"] == 3


def test_coordinator_sports_drift_veto_outranks_a_positive_clv():
    ctx = sports_ctx(clv_mean=0.05, clv_n=200, clv_hit_rate=0.58, n=4)
    out = decide([SportsCalibrationDriftBot(), CLVBot()], ctx)
    assert out["decision"] == "VETO"
    assert out["vetoes"] == 1


def test_coordinator_clv_reject_is_a_reject_not_a_veto():
    ctx = clv_ctx(clv_mean=-0.03, clv_n=120)
    out = decide([CLVBot(), DummyApprove()], ctx)
    assert out["decision"] == "REJECT"
    assert out["vetoes"] == 0
    assert out["rejects"] == 1


def test_coordinator_waits_when_the_only_voter_abstains():
    out = decide([CLVBot()], {})
    assert out["decision"] == "WAIT"
    assert out["votes"][0]["decision"] == "abstain"


def test_coordinator_respects_quorum():
    ctx = poly_ctx()
    assert decide([PolyCalibrationDriftBot(), DummyApprove()], ctx, quorum=2, require_edge=False)["decision"] == "ALLOW"
    # CLV abstains on a polymarket-only context, so quorum 2 is not met
    assert decide([PolyCalibrationDriftBot(), CLVBot()], ctx, quorum=2)["decision"] == "WAIT"


def test_coordinator_does_not_mutate_context():
    ctx = poly_ctx()
    snapshot = copy.deepcopy(ctx)
    decide([PolyCalibrationDriftBot(), SportsCalibrationDriftBot(), CLVBot()], ctx)
    assert ctx == snapshot


def test_coordinator_hash_tracks_the_context():
    bots = [PolyCalibrationDriftBot(), DummyApprove()]
    good = decide(bots, poly_ctx())
    bad = decide(bots, poly_ctx(n=3))
    assert good["votes_hash"] != bad["votes_hash"]


# ============================================================== hostile inputs
HOSTILE_CONTEXTS = [
    pytest.param(None, id="none"),
    pytest.param({}, id="empty-dict"),
    pytest.param([], id="list"),
    pytest.param("context", id="string"),
    pytest.param(42, id="int"),
    pytest.param(3.5, id="float"),
    pytest.param(True, id="bool"),
    pytest.param(object(), id="object"),
    pytest.param({"n": None, "brier_model": None, "brier_baseline": None}, id="all-none"),
    pytest.param({"brier_model": "abc", "brier_baseline": "xyz", "n": "many"}, id="numeric-strings"),
    pytest.param(
        {"brier_model": float("nan"), "brier_baseline": float("inf"), "n": float("-inf")},
        id="nan-inf",
    ),
    pytest.param({"brier_model": 10 ** 400, "brier_baseline": 10 ** 400, "n": 10 ** 400}, id="huge-int"),
    pytest.param({"brier_model": {"x": 1}, "brier_baseline": [1, 2], "n": (3,)}, id="containers"),
    pytest.param({"brier_model": True, "brier_baseline": False, "n": True}, id="bools"),
    pytest.param({"brier_model": -0.2, "brier_baseline": -0.1, "n": -40}, id="negatives"),
    pytest.param({"brier_model": 0.1, "brier_baseline": 0.2, "n": 100, "recent_brier": Boom()}, id="hostile-drift"),
    pytest.param(
        {"brier_model": 0.1, "brier_baseline": 0.2, "logloss_model": Boom(),
         "logloss_baseline": Boom(), "n": 100},
        id="hostile-logloss",
    ),
    pytest.param({"clv_mean": float("nan"), "clv_n": float("inf"), "clv_hit_rate": None}, id="clv-nan"),
    pytest.param({"clv_mean": "-0.02", "clv_n": "many", "clv_hit_rate": "55%"}, id="clv-strings"),
    pytest.param({"clv_mean": 10 ** 400, "clv_n": -1, "clv_hit_rate": {}}, id="clv-huge"),
    pytest.param({"clv_mean": Boom(), "clv_n": 100}, id="clv-hostile-float"),
    pytest.param({"brier_model": 0.2, "brier_baseline": 0.3, "n": 100, "extra": {"deep": [1, 2] * 100}}, id="extra-noise"),
    pytest.param(
        {"brier_model": 0.05, "brier_baseline": 0.2, "recent_brier": 0.0,
         "prior_brier": 0.0, "n": 100},
        id="zero-prior-window",
    ),
]


@pytest.mark.parametrize("ctx", HOSTILE_CONTEXTS)
@pytest.mark.parametrize("bot", ALL_BOTS, ids=ALL_BOT_IDS)
def test_hostile_contexts_never_raise(bot, ctx):
    # evaluate() itself must be total - not merely rescued by Bot.vote()
    v = bot.evaluate(ctx)
    assert isinstance(v, Vote)
    assert v.decision in DECISIONS
    assert math.isfinite(v.confidence)
    # and the public wrapper agrees
    v2 = vote_of(bot, ctx)
    assert v2.decision in DECISIONS


@pytest.mark.parametrize("bot", ALL_BOTS, ids=ALL_BOT_IDS)
def test_hostile_contexts_keep_veto_fail_closed(bot):
    for ctx in (None, {}, "junk"):
        v = bot.vote(ctx)
        assert isinstance(v, Vote)
        if bot.veto:
            assert v.decision == "reject", "a veto bot must fail closed on no data"
        else:
            assert v.decision == "abstain"
