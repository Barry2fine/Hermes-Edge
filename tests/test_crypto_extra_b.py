"""Tests for the CRYPTO wave-2 pack B: memory, kelly, calibration_drift.

Each bot gets a pass context and at least one fail context, the Vote shape is
checked field by field, the coordinator is exercised end to end (ALLOW, REJECT,
VETO) and every bot is fed hostile contexts to prove nothing escapes.

Run:  python3 -m pytest tests/test_crypto_extra_b.py -q
"""

import copy
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sovereign_bots.bots.crypto.calibration_drift import CalibrationDriftBot  # noqa: E402
from sovereign_bots.bots.crypto.kelly_portfolio import (  # noqa: E402
    CLUSTER_CAP, PER_NAME_CAP, TOTAL_CAP, KellyPortfolioBot, kelly_fraction,
)
from sovereign_bots.bots.crypto.memory_base_rate import MemoryBaseRateBot  # noqa: E402
from sovereign_bots.core.bot import DECISIONS, Bot, Vote  # noqa: E402
from sovereign_bots.core.coordinator import decide  # noqa: E402

MEM = MemoryBaseRateBot()
KELLY = KellyPortfolioBot()
CAL = CalibrationDriftBot()

ALL = (MEM, KELLY, CAL)
BY_ID = {b.id: b for b in ALL}


# --------------------------------------------------------------------------- #
# fixture builders
# --------------------------------------------------------------------------- #
def memory_rows(n=10, wins=8, r=1.0, width=3, spread=0.05):
    """Deterministic case memory around the origin: `wins` winners out of n."""
    rows = []
    for i in range(n):
        win = i < wins
        rows.append({
            "features": [spread * (i + 1), -spread * (i + 1), spread * 0.5 * i],
            "win": win,
            "r": r if win else -r,
        })
    assert len(rows[0]["features"]) == width
    return rows


def winsome_memory():
    """Every neighbour is a winner: base rate 1.0, expected R positive."""
    return [{"features": [0.01 * i, -0.01 * i, 0.0], "win": True, "r": 1.2}
            for i in range(12)]


def losing_memory():
    """Two winners out of ten: base rate 0.2 -> reject."""
    return [{"features": [0.01 * i, -0.01 * i, 0.0], "win": i < 2,
             "r": 1.0 if i < 2 else -0.8} for i in range(10)]


def kelly_pass_ctx():
    return {
        "bankroll": 10_000.0,
        "candidates": [
            {"name": "BTC-moon", "p": 0.62, "price": 0.52, "cluster": "btc"},
            {"name": "ETH-flip", "p": 0.58, "price": 0.50, "cluster": "eth"},
        ],
    }


def cal_pass_ctx():
    return {
        "brier_model": 0.18,
        "brier_baseline": 0.25,
        "recent_brier": 0.19,
        "prior_brier": 0.185,
        "n": 120,
    }


def full_pass_ctx():
    ctx = {
        "features": [0.0, 0.0, 0.0],
        "memory": winsome_memory(),
    }
    ctx.update(kelly_pass_ctx())
    ctx.update(cal_pass_ctx())
    return ctx


# --------------------------------------------------------------------------- #
# 1) memory / base rate
# --------------------------------------------------------------------------- #
def test_memory_metadata():
    assert MEM.id == "memory"
    assert MEM.domains == ("crypto",)
    assert MEM.veto is False
    assert isinstance(MEM, Bot)
    assert len(MEM.mandate) > 8


def test_memory_approves_on_winning_neighbourhood():
    v = MEM.evaluate({"features": [0.0, 0.0, 0.0], "memory": winsome_memory()})
    assert v.decision == "approve"
    assert v.evidence["n"] == 12
    assert v.evidence["k"] == 10
    assert v.evidence["base_rate"] == 1.0
    assert v.evidence["expected_r"] == pytest.approx(1.2)
    assert v.confidence == pytest.approx(1.0)
    assert len(v.evidence["neighbors"]) == 10


def test_memory_k_uses_all_rows_below_ten():
    rows = [{"features": [0.01 * i, 0.0, 0.0], "win": i < 5, "r": 0.5}
            for i in range(8)]
    v = MEM.evaluate({"features": [0.0, 0.0, 0.0], "memory": rows})
    assert v.evidence["n"] == 8
    assert v.evidence["k"] == 8
    assert v.evidence["base_rate"] == pytest.approx(0.625)
    assert v.decision == "approve"          # 0.625 >= 0.55 with positive mean R


def test_memory_rejects_on_cold_neighbourhood():
    v = MEM.evaluate({"features": [0.0, 0.0, 0.0], "memory": losing_memory()})
    assert v.decision == "reject"
    assert v.evidence["base_rate"] == pytest.approx(0.2)
    assert v.evidence["expected_r"] < 0


def test_memory_abstains_below_eight_rows():
    rows = winsome_memory()[:7]
    v = MEM.evaluate({"features": [0.0, 0.0, 0.0], "memory": rows})
    assert v.decision == "abstain"
    assert v.evidence["n"] == 7


def test_memory_abstains_without_features_or_memory():
    assert MEM.evaluate({"memory": winsome_memory()}).decision == "abstain"
    assert MEM.evaluate({"features": [0.0, 0.0, 0.0]}).decision == "abstain"
    assert MEM.evaluate({"features": [], "memory": winsome_memory()}).decision == "abstain"


def test_memory_skips_rows_of_a_different_width():
    rows = winsome_memory()
    wrong = [{"features": [0.0, 0.0], "win": True, "r": 5.0} for _ in range(20)]
    v = MEM.evaluate({"features": [0.0, 0.0, 0.0], "memory": wrong + rows})
    assert v.evidence["n"] == 12          # the 20 short rows are not comparable
    assert v.decision == "approve"


def test_memory_abstains_in_the_dead_zone():
    # base rate 0.45: neither good enough nor bad enough
    rows = [{"features": [0.01 * i, 0.0, 0.0], "win": i < 4 or i in (5,),
             "r": 0.5} for i in range(10)]
    v = MEM.evaluate({"features": [0.0, 0.0, 0.0], "memory": rows})
    assert v.evidence["base_rate"] == pytest.approx(0.5)
    assert v.decision == "abstain"


# --------------------------------------------------------------------------- #
# 2) kelly portfolio
# --------------------------------------------------------------------------- #
def test_kelly_metadata():
    assert KELLY.id == "kelly"
    assert KELLY.domains == ("crypto",)
    assert KELLY.veto is False
    assert isinstance(KELLY, Bot)
    assert len(KELLY.mandate) > 8


def test_kelly_formula_matches_spec():
    # Net win odds on a $1-payout contract bought at `price`: b = (1-price)/price
    #   f* = (p*(b+1) - 1) / b = (p - price) / (1 - price)
    p, price = 0.60, 0.50
    b = (1 - price) / price
    expected = (p * (b + 1) - 1) / b
    assert kelly_fraction(p, price) == pytest.approx(expected)
    assert kelly_fraction(p, price) == pytest.approx(0.20)
    assert kelly_fraction(0.30, 0.90) < 0


def test_kelly_approves_and_respects_caps():
    v = KELLY.evaluate(kelly_pass_ctx())
    assert v.decision == "approve"
    w = v.evidence["weights"]
    assert set(w) == {"BTC-moon", "ETH-flip"}
    for name, val in w.items():
        assert val <= PER_NAME_CAP + 1e-9, name
        assert val > 0
    assert v.evidence["total"] == pytest.approx(sum(w.values()))
    assert v.evidence["total"] <= TOTAL_CAP + 1e-9
    assert set(v.evidence["per_cluster"]) == {"btc", "eth"}


def test_kelly_is_half_kelly_below_the_cap():
    # tiny edge -> tiny weight, no cap involved (p must clear price to have edge)
    v = KELLY.evaluate({"bankroll": 1000.0,
                        "candidates": [{"name": "thin", "p": 0.51,
                                        "price": 0.50, "cluster": "t"}]})
    f = kelly_fraction(0.51, 0.50)           # (0.01)/(0.50) = 0.02 full Kelly
    assert v.decision == "approve"
    assert f / 2 < 0.02                      # below the per-name cap
    assert v.evidence["weights"]["thin"] == pytest.approx(f / 2, abs=1e-9)


def test_kelly_cluster_cap():
    cands = [{"name": f"n{i}", "p": 0.70, "price": 0.50, "cluster": "corr"}
             for i in range(5)]
    v = KELLY.evaluate({"bankroll": 1000.0, "candidates": cands})
    assert v.decision == "approve"
    assert v.evidence["per_cluster"]["corr"] <= CLUSTER_CAP + 1e-9
    assert v.evidence["total"] == pytest.approx(CLUSTER_CAP, abs=1e-8)


def test_kelly_total_cap_binds_across_clusters():
    cands = [{"name": f"n{i}", "p": 0.70, "price": 0.50, "cluster": f"c{i}"}
             for i in range(8)]
    v = KELLY.evaluate({"bankroll": 1000.0, "candidates": cands})
    assert v.decision == "approve"
    assert v.evidence["total"] == pytest.approx(TOTAL_CAP, abs=1e-8)
    for val in v.evidence["weights"].values():
        assert val <= PER_NAME_CAP + 1e-9
    assert all(t <= CLUSTER_CAP + 1e-9 for t in v.evidence["per_cluster"].values())


def test_kelly_rejects_when_no_candidate_has_edge():
    v = KELLY.evaluate({"bankroll": 1000.0, "candidates": [
        {"name": "trap", "p": 0.30, "price": 0.90, "cluster": "x"},
        {"name": "trap2", "p": 0.40, "price": 0.80, "cluster": "y"},
    ]})
    assert v.decision == "reject"
    assert "no_positive_edge" in v.reasons
    assert v.evidence["total"] == 0.0


def test_kelly_abstains_without_candidates():
    assert KELLY.evaluate({"bankroll": 1000.0}).decision == "abstain"
    assert KELLY.evaluate({"bankroll": 1000.0, "candidates": []}).decision == "abstain"


def test_kelly_skips_unpriceable_candidates():
    v = KELLY.evaluate({"bankroll": 1000.0, "candidates": [
        {"name": "ok", "p": 0.62, "price": 0.52, "cluster": "a"},
        {"name": "bad_p", "p": 1.4, "price": 0.5, "cluster": "a"},
        {"name": "bad_price", "p": 0.6, "price": 0.0, "cluster": "a"},
        {"name": "nan", "p": float("nan"), "price": 0.5, "cluster": "a"},
    ]})
    assert v.decision == "approve"
    assert set(v.evidence["weights"]) == {"ok"}


# --------------------------------------------------------------------------- #
# 3) calibration drift (veto)
# --------------------------------------------------------------------------- #
def test_calibration_metadata():
    assert CAL.id == "calibration_drift"
    assert CAL.domains == ("crypto",)
    assert CAL.veto is True
    assert isinstance(CAL, Bot)
    assert len(CAL.mandate) > 8


def test_calibration_approves_when_model_beats_market():
    v = CAL.evaluate(cal_pass_ctx())
    assert v.decision == "approve"
    assert v.evidence["brier_model"] == pytest.approx(0.18)
    assert v.evidence["brier_baseline"] == pytest.approx(0.25)
    assert v.evidence["drift"] == pytest.approx(0.005)


def test_calibration_rejects_small_sample():
    ctx = cal_pass_ctx()
    ctx["n"] = 29
    v = CAL.evaluate(ctx)
    assert v.decision == "reject"
    assert any("insufficient_sample" in r for r in v.reasons)


def test_calibration_rejects_worse_than_market():
    ctx = cal_pass_ctx()
    ctx["brier_model"] = 0.27
    v = CAL.evaluate(ctx)
    assert v.decision == "reject"
    assert any("model_worse_than_market" in r for r in v.reasons)


def test_calibration_rejects_degradation():
    ctx = cal_pass_ctx()
    ctx["prior_brier"] = 0.10
    ctx["recent_brier"] = 0.15          # > 0.12 and > 0.11
    v = CAL.evaluate(ctx)
    assert v.decision == "reject"
    assert any("degrading" in r for r in v.reasons)


def test_calibration_allows_noise_that_is_not_drift():
    ctx = cal_pass_ctx()
    ctx["prior_brier"] = 0.10
    ctx["recent_brier"] = 0.108         # +8%, above prior+0.01? no: 0.108 < 0.11
    assert CAL.evaluate(ctx).decision == "approve"

    ctx2 = cal_pass_ctx()
    ctx2["prior_brier"] = 0.10
    ctx2["recent_brier"] = 0.115        # > prior+0.01 but only +15% < 20%
    assert CAL.evaluate(ctx2).decision == "approve"


def test_calibration_rejects_missing_scores():
    for key in ("brier_model", "brier_baseline", "n"):
        ctx = cal_pass_ctx()
        del ctx[key]
        v = CAL.vote(ctx)
        assert v.decision == "reject", key


def test_calibration_exact_boundary_is_not_a_rejection():
    ctx = cal_pass_ctx()
    ctx["brier_model"] = 0.25            # equal to the market: not "worse"
    ctx["n"] = 30
    assert CAL.evaluate(ctx).decision == "approve"


# --------------------------------------------------------------------------- #
# Vote shape
# --------------------------------------------------------------------------- #
@pytest.mark.parametrize("bot", ALL, ids=lambda b: b.id)
def test_vote_shape(bot):
    ctx = full_pass_ctx()
    v = bot.vote(ctx)
    assert isinstance(v, Vote)
    assert v.bot == bot.id
    assert v.decision in DECISIONS
    assert isinstance(v.confidence, float)
    assert 0.0 <= v.confidence <= 1.0
    assert isinstance(v.reasons, list)
    assert all(isinstance(r, str) for r in v.reasons)
    assert isinstance(v.evidence, dict)
    d = v.as_dict()
    assert d["bot"] == bot.id and d["decision"] == v.decision
    assert set(d) == {"bot", "decision", "confidence", "reasons", "evidence"}


@pytest.mark.parametrize("bot", ALL, ids=lambda b: b.id)
def test_vote_shape_on_reject(bot):
    fail = {
        "memory": {"features": [0.0, 0.0, 0.0], "memory": losing_memory()},
        "kelly": {"candidates": [{"name": "t", "p": 0.30, "price": 0.90,
                                  "cluster": "x"}]},
        "calibration_drift": {"brier_model": 0.9, "brier_baseline": 0.2,
                              "recent_brier": 0.9, "prior_brier": 0.2, "n": 100},
    }[bot.id]
    v = bot.vote(fail)
    assert v.decision == "reject"
    assert v.reasons and v.evidence


# --------------------------------------------------------------------------- #
# coordinator
# --------------------------------------------------------------------------- #
def test_coordinator_allows_full_pass():
    out = decide(ALL, full_pass_ctx(), quorum=1)
    assert out["decision"] == "ALLOW"
    assert out["vetoes"] == 0
    assert out["n"] == 3


def test_coordinator_veto_on_calibration_failure():
    ctx = full_pass_ctx()
    ctx["n"] = 12                        # calibration_drift is the veto
    out = decide(ALL, ctx, quorum=1)
    assert out["decision"] == "VETO"
    assert out["vetoes"] == 1
    votes = {v["bot"]: v for v in out["votes"]}
    assert votes["calibration_drift"]["decision"] == "reject"
    assert votes["memory"]["decision"] == "approve"


def test_coordinator_reject_without_veto_but_no_allow():
    ctx = {"features": [0.0, 0.0, 0.0], "memory": losing_memory()}
    ctx.update(cal_pass_ctx())           # memory rejects, kelly abstains
    out = decide(ALL, ctx, quorum=1)
    assert out["decision"] == "REJECT"
    assert out["vetoes"] == 0
    assert out["decision"] != "VETO"


def test_coordinator_waits_when_only_kelly_speaks():
    ctxx = {"candidates": [{"name": "t", "p": 0.70, "price": 0.50,
                            "cluster": "c"}]}
    out = decide([KELLY], ctxx, quorum=1)
    assert out["decision"] == "ALLOW"
    out2 = decide([MEM, CAL], ctxx, quorum=2)   # both abstain except CAL's reject
    assert out2["decision"] == "VETO"


def test_coordinator_hash_is_stable():
    a = decide(ALL, full_pass_ctx(), quorum=1)
    b = decide(ALL, copy.deepcopy(full_pass_ctx()), quorum=1)
    assert a["votes_hash"] == b["votes_hash"]


# --------------------------------------------------------------------------- #
# resilience: never raise
# --------------------------------------------------------------------------- #
HOSTILE = [
    {},
    None,
    [],
    "not-a-dict",
    42,
    {"features": None, "memory": None},
    {"features": {"a": 1}, "memory": "nope"},
    {"features": [float("nan"), float("inf")], "memory": winsome_memory()},
    {"features": [0, 0, 0], "memory": [None, 1, "x", [], {}, {"features": [1], "win": "maybe"}]},
    {"features": [0, 0, 0], "memory": [{"features": ["a", "b", "c"], "win": True, "r": "x"}]},
    {"features": [0, 0, 0], "memory": [{"features": [1, 2, 3], "win": "true", "r": None}] * 12},
    {"candidates": "nope"},
    {"candidates": [None, 1, "x", {}, {"name": None, "p": None, "price": None}]},
    {"candidates": [{"name": 1, "p": "0.6", "price": "0.5", "cluster": None}]},
    {"candidates": [{"name": ["unhashable"], "p": 0.6, "price": 0.5}]},
    {"candidates": [{"name": "x", "p": float("inf"), "price": 0.5}]},
    {"bankroll": "lots", "candidates": [{"name": "x", "p": 0.7, "price": 0.5}]},
    {"brier_model": None, "brier_baseline": None, "n": None},
    {"brier_model": "abc", "brier_baseline": {}, "recent_brier": [], "prior_brier": object(), "n": "many"},
    {"brier_model": float("nan"), "brier_baseline": float("inf"), "n": -5},
    {"brier_model": 0.2, "brier_baseline": 0.3, "recent_brier": None, "prior_brier": 0.0, "n": 40},
    {"n": True, "brier_model": True},
    {"features": [0] * 50, "memory": [{"features": [0] * 50, "win": True, "r": 1.0}] * 9},
]


@pytest.mark.parametrize("ctx", HOSTILE, ids=range(len(HOSTILE)))
@pytest.mark.parametrize("bot", ALL, ids=lambda b: b.id)
def test_never_raises_on_hostile_contexts(bot, ctx):
    v = bot.vote(ctx)
    assert isinstance(v, Vote)
    assert v.decision in DECISIONS
    assert v.bot == bot.id
    assert 0.0 <= v.confidence <= 1.0
    assert isinstance(v.evidence, dict)


@pytest.mark.parametrize("bot", ALL, ids=lambda b: b.id)
def test_coordinator_survives_hostile_contexts(bot):
    for ctx in HOSTILE:
        out = decide([bot], ctx, quorum=1)
        assert out["decision"] in ("ALLOW", "REJECT", "VETO", "WAIT")
        assert out["n"] == 1
        assert isinstance(out["votes_hash"], str) and len(out["votes_hash"]) == 64


def test_veto_bot_fails_closed_on_exception(monkeypatch):
    def boom(self, context):
        raise RuntimeError("exploded")

    monkeypatch.setattr(CalibrationDriftBot, "evaluate", boom)
    v = CAL.vote(cal_pass_ctx())
    assert v.decision == "reject"
    assert v.evidence.get("error") is True


def test_non_veto_bot_abstains_on_exception(monkeypatch):
    def boom(self, context):
        raise ValueError("exploded")

    monkeypatch.setattr(MemoryBaseRateBot, "evaluate", boom)
    v = MEM.vote(full_pass_ctx())
    assert v.decision == "abstain"


def test_context_is_not_mutated():
    ctx = full_pass_ctx()
    snapshot = copy.deepcopy(ctx)
    for bot in ALL:
        bot.vote(ctx)
    decide(ALL, ctx, quorum=1)
    assert ctx == snapshot
