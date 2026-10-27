"""Tests for the CRYPTO bot pack.

Every bot gets a pass context and a fail context.  Fixtures are deterministic
(seeded ``random.Random`` for realistic candles, hand-built bars where the
structure matters), so no network, no clock and no flakiness.

Run:  python3 -m pytest tests/test_crypto.py -q
"""

import copy
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sovereign_bots.bots.crypto import BOTS, BOT_CLASSES, BOT_IDS, VETO_IDS  # noqa: E402
from sovereign_bots.core.bot import DECISIONS, Bot, Vote  # noqa: E402
from sovereign_bots.core.coordinator import decide  # noqa: E402
from sovereign_bots.core.registry import domain_bots  # noqa: E402

EXPECTED_VETO = {
    "regime": True,
    "breaker": True,
    "execution": True,
    "entry_quality": True,
    "doubt": True,
    "momentum": False,
    "ict": False,
    "funding": False,
    "risk_budget": True,
}
BY_ID = {b.id: b for b in BOTS}


# --------------------------------------------------------------------------- #
# fixture builders
# --------------------------------------------------------------------------- #
def series(n=60, start=100.0, drift=0.18, sd=0.22, wick=0.04, seed=457):
    """Deterministic pseudo-random OHLCV uptrend (seed 457 is the 'good' tape)."""
    rng = random.Random(seed)
    out, p = [], start
    for i in range(n):
        o = p
        c = o + drift + rng.gauss(0.0, sd)
        out.append({
            "open": round(o, 4),
            "high": round(max(o, c) + abs(rng.gauss(0.0, wick)) + 0.01, 4),
            "low": round(min(o, c) - abs(rng.gauss(0.0, wick)) - 0.01, 4),
            "close": round(c, 4),
            "volume": 1000 + i * 7,
        })
        p = c
    return out


def downtrend(n=60, start=110.0, drift=-0.18, sd=0.22, seed=99):
    """Mirror tape: same generator, negative drift."""
    bars = series(n=n, start=start, drift=drift, sd=sd, seed=seed)
    return bars


def ramp(n=60, start=100.0, step=0.2):
    """Monotonic ramp: RSI pinned at 100 (nothing but gains)."""
    out, p = [], start
    for _ in range(n):
        o = p
        c = o + step
        out.append({"open": o, "high": c + 0.05, "low": o - 0.05, "close": c, "volume": 1000})
        p = c
    return out


def wide_wick(n=60, start=100.0):
    """Smooth closes but huge wicks -> efficient yet extremely choppy."""
    out, p = [], start
    for i in range(n):
        o = p
        c = o + (0.25 if i % 4 != 3 else -0.05)
        out.append({"open": o, "high": c + 2.0, "low": o - 2.0, "close": c, "volume": 1000})
        p = c
    return out


def bar(o, h, l, c):
    return {"open": o, "high": h, "low": l, "close": c, "volume": 1000}


def v_shape(last_close=97.6):
    """Decline into a discount, then an impulse that leaves a live bull FVG."""
    out, p = [], 105.0
    for _ in range(25):
        o = p
        c = p - 0.2
        out.append(bar(o, o + 0.05, c - 0.05, c))
        p = c
    for _ in range(6):
        o = p
        c = p - 0.9
        out.append(bar(o, o + 0.08, c - 0.08, c))
        p = c
    out.append(bar(96.6, 96.9, 95.9, 96.2))          # bearish candle -> bull OB origin
    out.append(bar(96.2, 97.8, 96.1, 97.7))          # impulse
    out.append(bar(97.7, 98.3, 97.2, 98.0))          # gap: low 97.2 > high 96.9
    out.append(bar(98.0, 98.1, 97.4, last_close))
    return out


def inverted(bars, axis=200.0):
    """Price mirror: swaps highs/lows and inverts direction."""
    out = []
    for b in bars:
        out.append(bar(axis - b["open"], axis - b["low"], axis - b["high"],
                       axis - b["close"]))
    return out


def good_ctx():
    """Everything a clean long needs. entry/stop/target derive from the tape."""
    h1 = series()
    entry = round(h1[-1]["close"], 2)
    return {
        # wave-2 evidence (fail-closed bots need real inputs)
        "brier_model": 0.18,
        "brier_baseline": 0.25,
        "recent_brier": 0.18,
        "prior_brier": 0.19,
        "n": 120,
        "features": [0.0, 0.0, 0.0],
        "memory": [{"features": [0.01 * i, 0.0, 0.0], "win": True, "r": 1.0} for i in range(12)],
        "candidates": [{"name": "SOL", "p": 0.62, "price": 0.55, "cluster": "sol"}],
        "vol_pct": 3.0,
        "expected_edge_bps": 40.0,
        "last_update_age_s": 2.0,
        "mark": entry,
        "oracle_price": round(entry * 1.0005, 4),
        "spread_bps": 4.0,
        "broker_positions": [],
        "local_positions": [],
        "broker_orders": [],
        "local_orders": [],
    
        "coin": "SOL",
        "direction": "long",
        "entry": entry,
        "stop": round(entry * 0.97, 2),
        "target": round(entry * 1.075, 2),
        "size": 10.0,
        "fees_per_unit": 0.05,
        "candles_1h": h1,
        "candles_4h": series(n=48, drift=0.30, sd=0.35, seed=7),
        "candles_1d": series(n=60, start=70.0, drift=0.90, sd=0.25, seed=11),
        "alignment": 3,
        "score": 0.72,
        "day_volume_usd": 25_000_000.0,
        "funding": 0.0002,
        "equity": 10000.0,
        "floor": 9000.0,
        "position_count": 1,
        "max_positions": 4,
        "recent_r": [1.2, -1.0, 0.8],
        "ranking_age_h": 1.5,
        "data_health": "ok",
        "btc_1d": "up",
        "btc_4h": "up",
        "breadth_ratio": 0.62,
        "vol_pct": 2.4,
        "equity_peak": 10200.0,
        "daily_loss_pct": 0.5,
        "consecutive_losses": 1,
        "bids": [[(entry - 0.05), 300], [(entry - 0.30), 500]],
        "asks": [[(entry + 0.05), 300], [(entry + 0.30), 500]],
    }


def bad_ctx():
    """RSI 100 tape + 6-unit-deep book + HTF against the long."""
    ctx = good_ctx()
    ctx.update({
        "candles_1h": ramp(),
        "candles_4h": ramp(n=48, step=-0.3),
        "btc_4h": "down",
        "bids": [[ctx["entry"] - 0.05, 6]],
        "asks": [[ctx["entry"] + 0.05, 6]],
    })
    return ctx


def vote(bot_id, ctx):
    return BY_ID[bot_id].vote(copy.deepcopy(ctx))


# --------------------------------------------------------------------------- #
# contract: pack shape
# --------------------------------------------------------------------------- #
def test_pack_exposes_at_least_nine_bot_instances():
    assert isinstance(BOTS, list) and len(BOTS) >= 9
    assert len(BOT_CLASSES) >= 9
    assert all(isinstance(b, Bot) for b in BOTS)
    assert not any(isinstance(b, type) for b in BOTS)


def test_ids_unique_and_stable():
    assert {"regime", "breaker", "execution", "entry_quality", "doubt", "momentum", "ict", "funding", "risk_budget"} <= set(BOT_IDS)
    assert len(set(BOT_IDS)) == len(BOT_IDS)


def test_veto_flags_match_contract():
    _veto = {b.id: b.veto for b in BOTS}
    assert all(_veto[k] is v for k, v in EXPECTED_VETO.items())
    assert {"regime", "breaker", "execution", "entry_quality", "doubt", "risk_budget"} <= set(VETO_IDS)


def test_domains_and_mandates():
    for b in BOTS:
        assert b.domains == ("crypto",), b.id
        assert isinstance(b.mandate, str) and len(b.mandate) > 20, b.id


def test_vote_shape_for_every_bot():
    ctx = good_ctx()
    for b in BOTS:
        v = b.vote(copy.deepcopy(ctx))
        assert isinstance(v, Vote), b.id
        assert v.bot == b.id
        assert v.decision in DECISIONS
        assert 0.0 <= v.confidence <= 1.0
        assert isinstance(v.reasons, list) and all(isinstance(r, str) for r in v.reasons)
        assert isinstance(v.evidence, dict)
        d = v.as_dict()
        assert set(d) == {"bot", "decision", "confidence", "reasons", "evidence"}


def test_registry_discovers_the_pack():
    assert [b.id for b in domain_bots("crypto")] == BOT_IDS


# --------------------------------------------------------------------------- #
# resilience: never raise
# --------------------------------------------------------------------------- #
def test_never_raises_on_hostile_contexts():
    hostile = [
        {},
        {"candles_1h": "not-a-list", "bids": 7, "asks": None, "direction": 5},
        {"candles_1h": [{"close": "x"}, {}, None, 3], "equity": "abc", "floor": []},
        {"vol_pct": float("nan"), "breadth_ratio": float("inf"), "funding": "0.01%",
         "bids": [[0, 0]], "asks": [{"price": "1e400", "size": -1}]},
        {"size": None, "entry": -1, "stop": 0, "target": None},
    ]
    for ctx in hostile:
        for b in BOTS:
            v = b.vote(ctx)
            assert isinstance(v, Vote)
            assert v.decision in DECISIONS
            assert 0.0 <= v.confidence <= 1.0
            # a veto bot may never silently approve through an internal error
            assert not (b.veto and v.evidence.get("error") and v.decision == "approve")


# --------------------------------------------------------------------------- #
# 1) regime
# --------------------------------------------------------------------------- #
def test_regime_pass():
    v = vote("regime", good_ctx())
    assert v.decision == "approve" and v.confidence > 0.5


def test_regime_fail_high_vol():
    ctx = good_ctx()
    ctx["vol_pct"] = 9.5
    v = vote("regime", ctx)
    assert v.decision == "reject" and "risk_off" in v.reasons[0]


def test_regime_fail_narrow_breadth():
    ctx = good_ctx()
    ctx["breadth_ratio"] = 0.20
    v = vote("regime", ctx)
    assert v.decision == "reject" and "breadth" in v.reasons[0]


def test_regime_fail_btc_trend_opposes():
    ctx = good_ctx()
    ctx["btc_4h"] = "down"
    v = vote("regime", ctx)
    assert v.decision == "reject" and "btc_trend_opposes" in v.reasons[0]


def test_regime_short_symmetry():
    ctx = good_ctx()
    ctx["direction"] = "short"
    ctx["btc_1d"] = "up"          # 1d still bullish -> opposes a short
    ctx["btc_4h"] = "down"
    assert vote("regime", ctx).decision == "reject"
    ctx["btc_1d"] = "down"        # both higher timeframes now agree
    assert vote("regime", ctx).decision == "approve"


# --------------------------------------------------------------------------- #
# 2) breaker
# --------------------------------------------------------------------------- #
def test_breaker_pass():
    v = vote("breaker", good_ctx())
    assert v.decision == "approve" and v.confidence > 0.5


def test_breaker_soft_limit_reduces():
    ctx = good_ctx()
    ctx["equity"] = 9600.0     # 4% off a 10k peak
    v = vote("breaker", ctx)
    assert v.decision == "approve"
    assert v.confidence == 0.4
    assert v.reasons[0].startswith("reduce")


def test_breaker_fail_drawdown():
    ctx = good_ctx()
    ctx["equity_peak"] = 10000.0
    ctx["equity"] = 9300.0     # 7%
    v = vote("breaker", ctx)
    assert v.decision == "reject" and "hard_breaker" in v.reasons[0]


def test_breaker_fail_daily_loss():
    ctx = good_ctx()
    ctx["daily_loss_pct"] = 4.5
    v = vote("breaker", ctx)
    assert v.decision == "reject" and "daily_loss" in v.reasons[0]


def test_breaker_fail_streak_reduces():
    ctx = good_ctx()
    ctx["consecutive_losses"] = 4
    v = vote("breaker", ctx)
    assert v.decision == "approve" and v.confidence == 0.4


# --------------------------------------------------------------------------- #
# 3) execution
# --------------------------------------------------------------------------- #
def test_execution_pass():
    v = vote("execution", good_ctx())
    assert v.decision == "approve"
    assert v.evidence["spread_bps"] < 15
    assert v.evidence["participation"] < 0.25


def test_execution_fail_wide_spread():
    ctx = good_ctx()
    ctx["bids"] = [[ctx["entry"] - 0.30, 500]]
    ctx["asks"] = [[ctx["entry"] + 0.30, 500]]
    v = vote("execution", ctx)
    assert v.decision == "reject" and "spread=" in v.reasons[0]


def test_execution_fail_thin_book():
    ctx = good_ctx()
    ctx["size"] = 10.0
    ctx["bids"] = [[ctx["entry"] - 0.05, 12]]
    ctx["asks"] = [[ctx["entry"] + 0.05, 12]]
    v = vote("execution", ctx)
    assert v.decision == "reject" and "depth" in v.reasons[0]


def test_execution_fail_missing_book_is_fail_closed():
    ctx = good_ctx()
    ctx.pop("bids")
    ctx["asks"] = []
    v = vote("execution", ctx)
    assert v.decision == "reject" and v.reasons[0].startswith("missing:book")


def test_execution_fail_crossed_book():
    ctx = good_ctx()
    e = ctx["entry"]
    ctx["bids"] = [[e + 0.5, 500]]
    ctx["asks"] = [[e - 0.5, 500]]
    v = vote("execution", ctx)
    assert v.decision == "reject" and "crossed" in v.reasons[0]


# --------------------------------------------------------------------------- #
# 4) entry_quality
# --------------------------------------------------------------------------- #
def test_entry_quality_pass():
    v = vote("entry_quality", good_ctx())
    assert v.decision == "approve"
    assert v.evidence["rsi14"] < 68
    assert v.evidence["er"] >= 0.30
    assert v.evidence["chop"] <= 61.8


def test_entry_quality_fail_rsi_exhaustion():
    ctx = good_ctx()
    ctx["candles_1h"] = ramp()
    v = vote("entry_quality", ctx)
    assert v.decision == "reject" and "rsi=" in v.reasons[0]


def test_entry_quality_fail_chop():
    ctx = good_ctx()
    ctx["candles_1h"] = wide_wick()
    v = vote("entry_quality", ctx)
    assert v.decision == "reject"
    assert v.evidence["chop"] > 61.8


def test_entry_quality_fail_short_rsi():
    ctx = good_ctx()
    ctx["direction"] = "short"
    ctx["candles_1h"] = downtrend()
    v = vote("entry_quality", ctx)
    assert v.decision == "reject" and "short_exhaustion" in v.reasons[0]


def test_entry_quality_fail_short_candles():
    ctx = good_ctx()
    ctx["candles_1h"] = ctx["candles_1h"][:12]
    v = vote("entry_quality", ctx)
    assert v.decision == "reject" and "candles_1h" in v.reasons[0]


# --------------------------------------------------------------------------- #
# 5) doubt
# --------------------------------------------------------------------------- #
def test_doubt_pass():
    v = vote("doubt", good_ctx())
    assert v.decision == "approve"
    assert v.evidence["rr_post_fee"] >= 1.5


def test_doubt_fail_htf_conflict():
    ctx = good_ctx()
    ctx["candles_4h"] = downtrend(n=48, start=120.0)
    v = vote("doubt", ctx)
    assert v.decision == "reject" and "htf_conflict" in v.reasons[0]


def test_doubt_fail_low_volume():
    ctx = good_ctx()
    ctx["day_volume_usd"] = 1_000_000.0
    v = vote("doubt", ctx)
    assert v.decision == "reject" and "illiquid" in v.reasons[0]


def test_doubt_fail_crowded_funding():
    ctx = good_ctx()
    ctx["funding"] = 0.002
    v = vote("doubt", ctx)
    assert v.decision == "reject" and "crowded_funding" in v.reasons[0]


def test_doubt_fail_bad_rr():
    ctx = good_ctx()
    ctx["target"] = round(ctx["entry"] * 1.02, 2)   # 2% reward vs 3% risk
    v = vote("doubt", ctx)
    assert v.decision == "reject" and "rr_post_fee" in v.reasons[0]


def test_doubt_fail_tight_stop():
    ctx = good_ctx()
    ctx["stop"] = round(ctx["entry"] * 0.999, 4)
    v = vote("doubt", ctx)
    assert v.decision == "reject" and "stop_too_tight" in v.reasons[0]


def test_doubt_fail_slots_full():
    ctx = good_ctx()
    ctx["position_count"] = 4
    v = vote("doubt", ctx)
    assert v.decision == "reject" and "slots_full" in v.reasons[0]


def test_doubt_fail_stale_ranking_and_health():
    ctx = good_ctx()
    ctx["ranking_age_h"] = 7.5
    assert vote("doubt", ctx).decision == "reject"
    ctx = good_ctx()
    ctx["data_health"] = "degraded"
    assert vote("doubt", ctx).decision == "reject"


# --------------------------------------------------------------------------- #
# 6) momentum
# --------------------------------------------------------------------------- #
def test_momentum_pass_uses_score_as_confidence():
    v = vote("momentum", good_ctx())
    assert v.decision == "approve" and v.confidence == 0.72


def test_momentum_fail_low_score():
    ctx = good_ctx()
    ctx["score"] = 0.30
    assert vote("momentum", ctx).decision == "abstain"


def test_momentum_fail_thin_alignment():
    ctx = good_ctx()
    ctx["alignment"] = 1
    assert vote("momentum", ctx).decision == "abstain"


# --------------------------------------------------------------------------- #
# 7) ict
# --------------------------------------------------------------------------- #
def test_ict_pass_structure_supports_long():
    v = vote("ict", {"candles_1h": v_shape(), "direction": "long"})
    assert v.decision == "approve"
    assert v.evidence["net_support"] >= 1


def test_ict_fail_structure_opposes_long():
    v = vote("ict", {"candles_1h": inverted(v_shape()), "direction": "long"})
    assert v.decision == "abstain"
    assert v.evidence["net_support"] <= 0


def test_ict_never_rejects():
    for ctx in ({}, {"candles_1h": []}, {"candles_1h": v_shape(), "direction": "short"},
                {"candles_1h": ramp(), "direction": "long"}):
        assert vote("ict", ctx).decision in ("approve", "abstain")


# --------------------------------------------------------------------------- #
# 8) funding
# --------------------------------------------------------------------------- #
def test_funding_pass_neutral():
    v = vote("funding", good_ctx())
    assert v.decision == "approve"


def test_funding_pass_positive_carry():
    ctx = good_ctx()
    ctx["funding"] = -0.0004          # longs get paid
    v = vote("funding", ctx)
    assert v.decision == "approve" and "positive_carry" in v.reasons[0]


def test_funding_fail_crowded_extreme():
    ctx = good_ctx()
    ctx["funding"] = 0.0015           # longs paying through the nose
    v = vote("funding", ctx)
    assert v.decision == "reject" and "crowded_extreme" in v.reasons[0]


def test_funding_unknown_abstains():
    ctx = good_ctx()
    ctx.pop("funding")
    assert vote("funding", ctx).decision == "abstain"


def test_funding_adverse_but_not_extreme_abstains():
    ctx = good_ctx()
    ctx["funding"] = 0.0006
    assert vote("funding", ctx).decision == "abstain"


# --------------------------------------------------------------------------- #
# 9) risk_budget
# --------------------------------------------------------------------------- #
def test_risk_budget_pass():
    v = vote("risk_budget", good_ctx())
    assert v.decision == "approve"
    assert v.evidence["risk"] <= v.evidence["budget"]


def test_risk_budget_fail_floor_breached():
    ctx = good_ctx()
    ctx["equity"] = 8900.0
    v = vote("risk_budget", ctx)
    assert v.decision == "reject" and "floor_breached" in v.reasons[0]


def test_risk_budget_fail_oversized():
    ctx = good_ctx()
    ctx["size"] = 60.0                 # 60 * 3.3 = ~199 risk vs 100 budget
    v = vote("risk_budget", ctx)
    assert v.decision == "reject" and "oversized" in v.reasons[0]


# --------------------------------------------------------------------------- #
# coordinator integration
# --------------------------------------------------------------------------- #
def test_coordinator_allows_good_context():
    res = decide(BOTS, good_ctx())
    assert res["decision"] == "ALLOW"
    assert res["vetoes"] == 0 and res["rejects"] == 0
    assert res["n"] >= 9 and res["approves"] >= 5


def test_coordinator_vetoes_bad_context():
    res = decide(BOTS, bad_ctx())
    assert res["decision"] == "VETO"
    assert res["vetoes"] >= 3
    joined = " ".join(res["reasons"])
    assert "rsi=" in joined and "depth" in joined and "btc_trend_opposes" in joined


def test_coordinator_quorum_still_allows_good_context():
    assert decide(BOTS, good_ctx(), quorum=6)["decision"] == "ALLOW"


def test_missing_data_veto_bots_reject():
    # `postmortem` is a LEARNED gate: "no history" is a legitimate neutral state,
    # not missing market data, so it abstains instead of inventing a veto.
    learned = {"postmortem"}
    for b in BOTS:
        if b.veto and b.id not in learned:
            v = b.vote({})
            assert v.decision == "reject", b.id
            assert any(r.startswith("missing") or "invalid" in r for r in v.reasons), b.id


def test_missing_data_non_veto_bots_abstain():
    for b in BOTS:
        if not b.veto:
            assert b.vote({}).decision == "abstain", b.id


def test_missing_data_context_is_veto():
    res = decide(BOTS, {})
    assert res["decision"] == "VETO"
    assert res["vetoes"] >= 6
    assert res["approves"] == 0


def test_decisions_are_deterministic():
    ctx = good_ctx()
    assert decide(BOTS, ctx)["votes_hash"] == decide(BOTS, copy.deepcopy(ctx))["votes_hash"]


def test_bots_do_not_mutate_context():
    ctx = good_ctx()
    snapshot = copy.deepcopy(ctx)
    decide(BOTS, ctx)
    assert ctx == snapshot
