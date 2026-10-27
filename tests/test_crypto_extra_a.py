"""Tests for the crypto wave-2 pack A: regime_cluster, impact,
data_integrity, reconciliation.

Each module is imported directly (the pack ``__init__`` is untouched).
Fixtures are deterministic — seeded ``random.Random`` where realistic noise
helps, hand-built series where the structure is the point.  No network, no
clock, no flakiness.

Run:  python3 -m pytest tests/test_crypto_extra_a.py -q
"""

import copy
import os
import random
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sovereign_bots.bots.crypto.data_integrity import DataIntegrityBot  # noqa: E402
from sovereign_bots.bots.crypto.impact import ImpactBot  # noqa: E402
from sovereign_bots.bots.crypto.reconciliation import ReconciliationBot  # noqa: E402
from sovereign_bots.bots.crypto.regime_cluster import RegimeClusterBot  # noqa: E402
from sovereign_bots.core.bot import DECISIONS, Bot, Vote  # noqa: E402
from sovereign_bots.core.coordinator import decide  # noqa: E402

FOUR = [RegimeClusterBot(), ImpactBot(), DataIntegrityBot(), ReconciliationBot()]
BY_ID = {b.id: b for b in FOUR}
EXPECTED = {
    "regime_cluster": ("regime_cluster", True),
    "impact": ("impact", True),
    "data_integrity": ("data_integrity", True),
    "reconciliation": ("reconciliation", True),
}


# --------------------------------------------------------------------------- #
# fixture builders
# --------------------------------------------------------------------------- #
def bars_from_returns(rets, start=100.0):
    """OHLCV bars from a list of simple returns."""
    out, p = [], float(start)
    for r in rets:
        c = p * (1.0 + r)
        out.append({
            "open": round(p, 6), "high": round(max(p, c) * 1.001, 6),
            "low": round(min(p, c) * 0.999, 6), "close": round(c, 6),
            "volume": 1000.0,
        })
        p = c
    return out


def noise(n, drift=0.0, sd=0.010, seed=5):
    """Deterministic pseudo-random simple returns."""
    rng = random.Random(seed)
    return [drift + rng.gauss(0.0, sd) for _ in range(n)]


def flat_tail(n, drift=0.0, amp=0.001):
    return [drift + (amp if i % 2 else -amp) for i in range(n)]


BULL_1D = noise(100, drift=0.0, sd=0.010, seed=5) + flat_tail(20, drift=0.006, amp=0.001)
BEAR_1D = noise(100, drift=0.0, sd=0.010, seed=7) + flat_tail(20, drift=-0.006, amp=0.001)
SIDE_1D = (noise(40, 0.012, 0.004, 1) + noise(40, -0.012, 0.004, 2)
           + noise(40, 0.0, 0.004, 3) + flat_tail(20, drift=0.0, amp=0.0004))
CHAOS_1D = noise(100, drift=0.0, sd=0.003, seed=3) + [
    0.05 if i % 2 else -0.05 for i in range(20)]


def hourly(n=60):
    rng = random.Random(31)
    return bars_from_returns([0.0012 + rng.gauss(0.0, 0.0022) for _ in range(n)])


def good_ctx():
    """Clean long: calm bull daily tape, fresh feed, matched books."""
    return {
        "coin": "SOL",
        "direction": "long",
        "size": 10.0,
        "price": 100.0,
        "mark": 100.0,
        "oracle_price": 100.05,
        "last_update_age_s": 2.0,
        "spread_bps": 4.0,
        "vol_pct": 2.4,
        "day_volume_usd": 25_000_000.0,
        "expected_edge_bps": 40.0,
        "horizon_hours": 12.0,
        "candles_1h": hourly(),
        "candles_1d": bars_from_returns(BULL_1D, start=70.0),
        "broker_positions": [{"asset": "SOL", "side": "long", "qty": 10.0}],
        "local_positions": [{"asset": "sol", "side": "LONG", "qty": 10.000000}],
        "broker_orders": [{"asset": "SOL", "side": "buy", "type": "limit",
                           "trigger": 98.0}],
        "local_orders": [{"asset": "SOL", "side": "long", "type": "LIMIT",
                          "trigger": 98.0}],
    }


def bad_ctx():
    """Hostile tape: chaos regime, stale and off-oracle feed, size that dwarfs
    the book, and a local position the broker has never seen."""
    ctx = good_ctx()
    ctx.update({
        "candles_1d": bars_from_returns(CHAOS_1D, start=70.0),
        "last_update_age_s": 900.0,
        "oracle_price": 90.0,
        "spread_bps": 180.0,
        "size": 400_000.0,
        "local_positions": [{"asset": "SOL", "side": "long", "qty": 10.0},
                            {"asset": "SOL", "side": "short", "qty": 4.0}],
    })
    return ctx


def vote(bot_id, ctx):
    return BY_ID[bot_id].vote(copy.deepcopy(ctx))


# --------------------------------------------------------------------------- #
# contract: shape
# --------------------------------------------------------------------------- #
def test_bots_subclass_core_bot():
    for b in FOUR:
        assert isinstance(b, Bot), b.id
        assert b.domains == ("crypto",), b.id
        assert b.veto is True, b.id
        assert isinstance(b.mandate, str) and len(b.mandate) > 20, b.id


def test_ids_are_the_contracted_ids():
    assert [b.id for b in FOUR] == ["regime_cluster", "impact",
                                    "data_integrity", "reconciliation"]
    assert {b.id: b.veto for b in FOUR} == {k: v[1] for k, v in EXPECTED.items()}


def test_vote_shape_for_every_bot():
    for b in FOUR:
        v = b.vote(copy.deepcopy(good_ctx()))
        assert isinstance(v, Vote), b.id
        assert v.bot == b.id
        assert v.decision in DECISIONS
        assert 0.0 <= v.confidence <= 1.0
        assert isinstance(v.reasons, list) and v.reasons
        assert all(isinstance(r, str) for r in v.reasons)
        assert isinstance(v.evidence, dict)
        d = v.as_dict()
        assert set(d) == {"bot", "decision", "confidence", "reasons", "evidence"}


def test_empty_context_rejects_every_veto_bot():
    for b in FOUR:
        v = b.vote({})
        assert v.decision == "reject", b.id
        assert any(r.startswith("missing") for r in v.reasons), b.id


def test_bots_do_not_mutate_context():
    ctx = good_ctx()
    snapshot = copy.deepcopy(ctx)
    decide(FOUR, ctx)
    assert ctx == snapshot


def test_votes_are_deterministic():
    ctx = good_ctx()
    a = decide(FOUR, ctx)["votes_hash"]
    b = decide(FOUR, copy.deepcopy(ctx))["votes_hash"]
    assert a == b


# --------------------------------------------------------------------------- #
# 1) regime_cluster
# --------------------------------------------------------------------------- #
def test_regime_cluster_pass_bull():
    v = vote("regime_cluster", good_ctx())
    assert v.decision == "approve"
    assert v.evidence["label"] == "bull"
    assert v.confidence > 0.5
    assert v.evidence["samples"] >= 10
    assert len(v.evidence["labels"]) == 4


def test_regime_cluster_bear_allows_short_only():
    ctx = good_ctx()
    ctx["candles_1d"] = bars_from_returns(BEAR_1D, start=70.0)
    ctx["direction"] = "short"
    assert vote("regime_cluster", ctx).decision == "approve"
    ctx["direction"] = "long"
    v = vote("regime_cluster", ctx)
    assert v.decision == "reject"
    assert v.reasons[0].startswith("bias_opposes")


def test_regime_cluster_fails_on_bull_short():
    ctx = good_ctx()
    ctx["direction"] = "short"
    v = vote("regime_cluster", ctx)
    assert v.decision == "reject"
    assert v.evidence["label"] == "bull"
    assert "bias_opposes" in v.reasons[0]


def test_regime_cluster_sideways_allows_both():
    ctx = good_ctx()
    ctx["candles_1d"] = bars_from_returns(SIDE_1D, start=70.0)
    assert vote("regime_cluster", ctx).evidence["label"] == "sideways"
    assert vote("regime_cluster", ctx).decision == "approve"
    ctx["direction"] = "short"
    assert vote("regime_cluster", ctx).decision == "approve"


def test_regime_cluster_fails_on_chaos():
    ctx = good_ctx()
    ctx["candles_1d"] = bars_from_returns(CHAOS_1D, start=70.0)
    v = vote("regime_cluster", ctx)
    assert v.decision == "reject"
    assert v.evidence["label"] == "chaos"
    assert v.reasons[0].startswith("chaos")


def test_regime_cluster_fails_closed_on_short_or_missing_candles():
    ctx = good_ctx()
    ctx["candles_1d"] = bars_from_returns(BULL_1D[:20], start=70.0)
    v = vote("regime_cluster", ctx)
    assert v.decision == "reject" and "candles_1d" in v.reasons[0]
    ctx.pop("candles_1d")
    v = vote("regime_cluster", ctx)
    assert v.decision == "reject" and v.reasons[0].startswith("missing:candles_1d")


def test_regime_cluster_needs_direction():
    ctx = good_ctx()
    ctx.pop("direction")
    assert vote("regime_cluster", ctx).decision == "reject"


def test_regime_cluster_is_deterministic_on_the_same_tape():
    ctx = good_ctx()
    a = BY_ID["regime_cluster"].vote(ctx)
    b = BY_ID["regime_cluster"].vote(ctx)
    assert (a.decision, a.evidence["label"], a.reasons) == (b.decision, b.evidence["label"], b.reasons)


# --------------------------------------------------------------------------- #
# 2) impact
# --------------------------------------------------------------------------- #
def test_impact_pass_and_formula():
    v = vote("impact", good_ctx())
    assert v.decision == "approve"
    participation = 10.0 * 100.0 / 25_000_000.0
    h_bps = 1e4 * 0.5 * (2.4 / 100.0) * (participation ** 0.5)
    assert abs(v.evidence["participation"] - participation) < 1e-12
    assert abs(v.evidence["h_bps"] - h_bps) < 1e-9
    assert v.evidence["edge_bps"] == 40.0
    assert v.evidence["h_bps"] <= max(5.0, 0.25 * 40.0)


def test_impact_fails_on_oversized_notional():
    ctx = good_ctx()
    ctx["size"] = 200_000.0        # 20m notional on a 25m day
    v = vote("impact", ctx)
    assert v.decision == "reject"
    assert v.reasons[0].startswith("impact:")
    assert v.evidence["h_bps"] > 5.0


def test_impact_fails_on_thin_volume():
    ctx = good_ctx()
    ctx["day_volume_usd"] = 50_000.0
    v = vote("impact", ctx)
    assert v.decision == "reject" and v.evidence["participation"] > 0.01


def test_impact_missing_and_short_data():
    ctx = good_ctx()
    ctx.pop("vol_pct")
    v = vote("impact", ctx)
    assert v.decision == "reject" and v.reasons[0].startswith("missing:vol_pct")

    ctx = good_ctx()
    ctx["candles_1h"] = ctx["candles_1h"][:3]
    v = vote("impact", ctx)
    assert v.decision == "reject" and v.reasons[0].startswith("short:candles_1h")

    ctx = good_ctx()
    ctx.pop("size")
    assert vote("impact", ctx).decision == "reject"


def test_impact_respects_the_edge_floor():
    ctx = good_ctx()
    ctx["expected_edge_bps"] = 200.0      # wide edge lifts the tolerance
    ctx["size"] = 20_000.0                # ~28 bps of impact
    v = vote("impact", ctx)
    assert v.decision == "approve"
    assert v.evidence["h_bps"] <= 0.25 * 200.0


# --------------------------------------------------------------------------- #
# 3) data_integrity
# --------------------------------------------------------------------------- #
def test_data_integrity_pass():
    v = vote("data_integrity", good_ctx())
    assert v.decision == "approve"
    assert v.evidence["age_s"] == 2.0
    assert v.evidence["dev_bps"] <= 50.0
    assert v.evidence["jump_pct"] <= 10.0


def test_data_integrity_fails_on_stale_feed():
    ctx = good_ctx()
    ctx["last_update_age_s"] = 61.0
    v = vote("data_integrity", ctx)
    assert v.decision == "reject" and v.reasons[0].startswith("stale")

    ctx = good_ctx()
    ctx["last_update_age_s"] = None
    v = vote("data_integrity", ctx)
    assert v.decision == "reject" and v.reasons[0].startswith("missing:last_update_age_s")

    ctx = good_ctx()
    ctx.pop("last_update_age_s")
    assert vote("data_integrity", ctx).decision == "reject"


def test_data_integrity_fails_on_oracle_divergence():
    ctx = good_ctx()
    ctx["mark"] = 101.0                 # 1% off a 100.0 oracle
    v = vote("data_integrity", ctx)
    assert v.decision == "reject" and v.reasons[0].startswith("oracle_divergence")


def test_data_integrity_tolerates_small_oracle_gap():
    ctx = good_ctx()
    ctx["mark"] = 100.4                 # 0.4% < 0.5%
    assert vote("data_integrity", ctx).decision == "approve"


def test_data_integrity_fails_on_jump():
    ctx = good_ctx()
    h = [{"close": 100.0}, {"close": 118.0}]
    ctx["candles_1h"] = ctx["candles_1h"][:-2] + h
    v = vote("data_integrity", ctx)
    assert v.decision == "reject" and v.reasons[0].startswith("jump")


def test_data_integrity_fails_on_wide_spread_and_bad_book():
    ctx = good_ctx()
    ctx["spread_bps"] = 80.0
    assert vote("data_integrity", ctx).decision == "reject"

    ctx = good_ctx()
    ctx.pop("spread_bps")
    ctx["bids"] = [[99.9, 500]]
    ctx["asks"] = [[100.1, 500]]
    assert vote("data_integrity", ctx).decision == "approve"

    ctx = good_ctx()
    ctx["bids"] = [[100.5, 500]]        # crossed
    ctx["asks"] = [[99.5, 500]]
    v = vote("data_integrity", ctx)
    assert v.decision == "reject" and v.reasons[0].startswith("crossed_book")

    ctx = good_ctx()
    ctx["bids"] = [[99.9, 500]]         # one-sided
    ctx.pop("asks", None)
    v = vote("data_integrity", ctx)
    assert v.decision == "reject" and "book" in v.reasons[0]


def test_data_integrity_fails_without_spread_or_book():
    ctx = good_ctx()
    ctx.pop("spread_bps")
    v = vote("data_integrity", ctx)
    assert v.decision == "reject" and v.reasons[0].startswith("missing:book")


def test_data_integrity_fails_on_short_candles():
    ctx = good_ctx()
    ctx["candles_1h"] = [{"close": 100.0}]
    v = vote("data_integrity", ctx)
    assert v.decision == "reject" and "candles_1h" in v.reasons[0]


# --------------------------------------------------------------------------- #
# 4) reconciliation
# --------------------------------------------------------------------------- #
def test_reconciliation_pass_ignores_case_and_side_naming():
    v = vote("reconciliation", good_ctx())
    assert v.decision == "approve"
    assert v.evidence["ghosts"] == [] and v.evidence["untracked"] == []
    assert v.evidence["counts"]["broker_positions"] == 1


def test_reconciliation_pass_on_flat_book():
    ctx = good_ctx()
    for k in ("broker_positions", "local_positions", "broker_orders", "local_orders"):
        ctx[k] = []
    assert vote("reconciliation", ctx).decision == "approve"


def test_reconciliation_fails_on_ghost_position():
    ctx = good_ctx()
    ctx["local_positions"].append({"asset": "SOL", "side": "short", "qty": 4.0})
    v = vote("reconciliation", ctx)
    assert v.decision == "reject" and "ghost" in v.reasons[0]


def test_reconciliation_fails_on_untracked_order():
    ctx = good_ctx()
    ctx["broker_orders"].append({"asset": "BTC", "side": "sell", "type": "stop",
                                 "trigger": 61000.0})
    v = vote("reconciliation", ctx)
    assert v.decision == "reject" and "untracked" in v.reasons[0]


def test_reconciliation_fails_on_quantity_mismatch():
    ctx = good_ctx()
    ctx["local_positions"][0]["qty"] = 10.5
    v = vote("reconciliation", ctx)
    assert v.decision == "reject"
    assert v.evidence["ghosts"] and v.evidence["untracked"]


def test_reconciliation_matches_rounded_quantities():
    ctx = good_ctx()
    ctx["local_positions"][0]["qty"] = 10.0000004
    assert vote("reconciliation", ctx).decision == "approve"


def test_reconciliation_fails_closed_on_missing_lists():
    for key in ("broker_positions", "local_positions", "broker_orders", "local_orders"):
        ctx = good_ctx()
        ctx.pop(key)
        v = vote("reconciliation", ctx)
        assert v.decision == "reject" and key in v.reasons[0]
    ctx = good_ctx()
    ctx["broker_positions"] = {"SOL": 10}
    assert vote("reconciliation", ctx).decision == "reject"


def test_reconciliation_fails_closed_on_unparseable_rows():
    ctx = good_ctx()
    ctx["local_orders"] = [{"asset": "SOL", "side": "?", "type": "limit"}]
    v = vote("reconciliation", ctx)
    assert v.decision == "reject" and v.reasons[0].startswith("invalid:local_orders")
    ctx["broker_orders"] = [None]
    ctx["local_orders"] = [None]
    assert vote("reconciliation", ctx).decision == "reject"


# --------------------------------------------------------------------------- #
# coordinator integration
# --------------------------------------------------------------------------- #
def test_coordinator_allows_clean_context():
    # FOUR is a pure gate pack (data integrity / execution / impact /
    # reconciliation): no seat establishes edge, so the caller opts out of the
    # edge gate. The edge gate's own behaviour lives in test_model_maths_bugs.py.
    res = decide(FOUR, good_ctx(), require_edge=False)
    assert res["decision"] == "ALLOW"
    assert res["vetoes"] == 0 and res["rejects"] == 0
    assert res["n"] == 4 and res["approves"] == 4


def test_coordinator_vetoes_bad_context():
    res = decide(FOUR, bad_ctx())
    assert res["decision"] == "VETO"
    assert res["vetoes"] >= 3
    joined = " ".join(res["reasons"])
    assert "chaos" in joined and "stale" in joined
    assert "impact:" in joined and "ghost" in joined


def test_coordinator_vetoes_empty_context():
    res = decide(FOUR, {})
    assert res["decision"] == "VETO"
    assert res["vetoes"] == 4 and res["approves"] == 0


# --------------------------------------------------------------------------- #
# resilience: never raise, never approve through an internal error
# --------------------------------------------------------------------------- #
HOSTILE = [
    {},
    {"candles_1h": "not-a-list", "candles_1d": 7, "bids": 7, "asks": None},
    {"candles_1d": [{"close": "x"}, {}, None, 3], "candles_1h": [{"close": None}]},
    {"size": float("nan"), "price": float("inf"), "day_volume_usd": -1,
     "vol_pct": "2.4%", "expected_edge_bps": None},
    {"last_update_age_s": "soon", "mark": -1, "oracle_price": 0, "spread_bps": "wide"},
    {"bids": [[0, 0]], "asks": [{"price": "1e400", "size": -1}]},
    {"broker_positions": [{"asset": None, "side": 5, "qty": "x"}],
     "local_positions": [1, 2, 3], "broker_orders": "nope", "local_orders": None},
    {"candles_1d": [{"close": 100.0}] * 5, "direction": 42, "size": "10"},
    {"candles_1h": [[1, 2, 3, 4, 5, 6]], "candles_1d": [[0.0] * 6] * 40},
    {"size": 1e300, "price": 1e300, "day_volume_usd": 1e-300, "vol_pct": 1e300,
     "expected_edge_bps": 1e300, "direction": "long"},
]


def test_never_raises_on_hostile_contexts():
    for ctx in HOSTILE:
        for b in FOUR:
            v = b.vote(ctx)
            assert isinstance(v, Vote), b.id
            assert v.bot == b.id
            assert v.decision in DECISIONS
            assert 0.0 <= v.confidence <= 1.0
            assert isinstance(v.reasons, list) and isinstance(v.evidence, dict)
            assert not (v.evidence.get("error") and v.decision == "approve"), b.id


def test_hostile_contexts_never_allow():
    for ctx in HOSTILE:
        assert decide(FOUR, ctx)["decision"] in ("VETO", "REJECT", "WAIT")
