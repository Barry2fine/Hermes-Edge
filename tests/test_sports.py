"""Tests for the sports bot pack: per-bot pass/fail, pack shape, coordinator."""

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sovereign_bots.bots import sports  # noqa: E402
from sovereign_bots.bots.sports import (  # noqa: E402
    BOTS,
    BOT_IDS,
    EloBot,
    InjuryBot,
    LineValueBot,
    LiquidityBot,
    StarterGateBot,
    TimingBot,
    VETO_IDS,
)
from sovereign_bots.bots.sports.liquidity import spread_proxy_bps  # noqa: E402
from sovereign_bots.core.bot import DECISIONS, Bot, Vote  # noqa: E402
from sovereign_bots.core.coordinator import decide  # noqa: E402


def good_ctx(**over):
    """A fully-tradeable player prop: every bot should be happy with this."""
    ctx = {
        "sport": "football",
        "event_id": "epl-2026-10-31-ars-che",
        "market": "player_shots_ou2.5",
        "side": "over",
        "market_p": 0.48,
        "model_p": 0.57,
        "lower_bound_p": 0.52,
        "fee": 0.010,
        "vig": 0.020,
        "starter_confirmed": True,
        "injury_status": "healthy",
        "line_move_bps": 20.0,
        "clv_estimate": 0.030,
        "liquidity": 150_000.0,
        "time_to_start_h": 12.0,
        # wave-2 evidence
        "clv_mean": 0.03,
        "clv_n": 40,
        "clv_hit_rate": 0.55,
        "brier_model": 0.18,
        "brier_baseline": 0.25,
        "recent_brier": 0.18,
        "prior_brier": 0.19,
        "n": 120,
    }
    ctx.update(over)
    return ctx


def vote_of(bot, ctx):
    v = bot.vote(ctx)
    assert isinstance(v, Vote), "vote() must always return a Vote"
    assert v.bot == bot.id
    assert v.decision in DECISIONS
    assert 0.0 <= v.confidence <= 1.0
    assert isinstance(v.reasons, list)
    return v


# --------------------------------------------------------------------------- elo

class TestElo:
    bot = EloBot()

    def test_approves_when_net_edge_clears_threshold(self):
        v = vote_of(self.bot, good_ctx())
        assert v.decision == "approve"
        # 0.57 - 0.48 - 0.01 - 0.02 = 0.06 net edge
        assert v.evidence["edge"] == pytest.approx(0.06, abs=1e-9)
        assert v.confidence > 0.5

    def test_confidence_scales_with_edge(self):
        small = vote_of(self.bot, good_ctx(model_p=0.53))
        big = vote_of(self.bot, good_ctx(model_p=0.62))
        assert small.decision == big.decision == "approve"
        assert big.confidence > small.confidence

    def test_abstains_when_edge_inside_threshold(self):
        v = vote_of(self.bot, good_ctx(model_p=0.52))
        assert v.decision == "abstain"
        assert v.confidence == 0.0

    def test_abstains_when_fees_eat_the_edge(self):
        v = vote_of(self.bot, good_ctx(fee=0.05, vig=0.05))
        assert v.decision == "abstain"
        assert v.evidence["edge"] < 0

    def test_absent_prices_abstain(self):
        assert vote_of(self.bot, good_ctx(model_p=None)).decision == "abstain"
        assert vote_of(self.bot, good_ctx(market_p=None)).decision == "abstain"
        assert vote_of(self.bot, {}).decision == "abstain"

    def test_absurd_price_abstains_not_approves(self):
        assert vote_of(self.bot, good_ctx(model_p=1.7)).decision == "abstain"

    def test_never_vetoes(self):
        assert self.bot.veto is False


# ------------------------------------------------------------------- starter_gate

class TestStarterGate:
    bot = StarterGateBot()

    def test_approves_confirmed_starter(self):
        v = vote_of(self.bot, good_ctx())
        assert v.decision == "approve"
        assert v.confidence >= 0.8

    def test_rejects_unconfirmed_starter(self):
        v = vote_of(self.bot, good_ctx(starter_confirmed=False))
        assert v.decision == "reject"
        assert self.bot.veto is True

    def test_rejects_missing_starter_flag(self):
        ctx = good_ctx()
        ctx.pop("starter_confirmed")
        assert vote_of(self.bot, ctx).decision == "reject"

    def test_unknown_market_needs_confirmation(self):
        # No market given: not team-level, so the starter flag decides.
        assert vote_of(self.bot, {"starter_confirmed": True}).decision == "approve"
        assert vote_of(self.bot, {}).decision == "reject"

    def test_team_level_markets_exempt(self):
        for market in ("h2h", "spread_home_-1.5", "total_ou2.5", "team_total"):
            v = vote_of(self.bot, good_ctx(market=market, starter_confirmed=False))
            assert v.decision == "approve", market
            assert v.evidence["team_level"] is True

    def test_player_totals_are_not_team_markets(self):
        v = vote_of(self.bot, good_ctx(market="player_total_shots_ou2.5",
                                       starter_confirmed=False))
        assert v.decision == "reject"

    def test_string_flags_parsed(self):
        assert vote_of(self.bot, good_ctx(starter_confirmed="true")).decision == "approve"
        assert vote_of(self.bot, good_ctx(starter_confirmed="no")).decision == "reject"


# ------------------------------------------------------------------------ injury

class TestInjury:
    bot = InjuryBot()

    def test_approves_healthy_player(self):
        v = vote_of(self.bot, good_ctx())
        assert v.decision == "approve"
        assert v.confidence >= 0.8

    def test_rejects_confirmed_out(self):
        assert vote_of(self.bot, good_ctx(injury_status="confirmed_out")).decision == "reject"

    def test_rejects_unknown_status(self):
        ctx = good_ctx()
        ctx.pop("injury_status")
        assert vote_of(self.bot, ctx).decision == "reject"
        assert vote_of(self.bot, good_ctx(injury_status="unknown")).decision == "reject"
        assert vote_of(self.bot, good_ctx(injury_status="??")).decision == "reject"

    def test_questionable_approved_with_low_confidence(self):
        v = vote_of(self.bot, good_ctx(injury_status="questionable"))
        assert v.decision == "approve"
        assert 0.0 < v.confidence <= 0.5

    def test_team_level_survives_one_injury(self):
        v = vote_of(self.bot, good_ctx(market="spread_away_+1.5",
                                       injury_status="confirmed_out"))
        assert v.decision == "approve"
        assert self.bot.veto is True


# -------------------------------------------------------------------- line_value

class TestLineValue:
    bot = LineValueBot()

    def test_approves_positive_clv(self):
        v = vote_of(self.bot, good_ctx())
        assert v.decision == "approve"
        assert v.evidence["clv_estimate"] == pytest.approx(0.03)

    def test_approves_favourable_move_without_clv(self):
        v = vote_of(self.bot, good_ctx(clv_estimate=None, line_move_bps=40.0))
        assert v.decision == "approve"
        assert "bps" in v.reasons[0]

    def test_rejects_adverse_move(self):
        v = vote_of(self.bot, good_ctx(clv_estimate=None, line_move_bps=-60.0))
        assert v.decision == "reject"

    def test_rejects_strongly_negative_clv(self):
        v = vote_of(self.bot, good_ctx(clv_estimate=-0.04, line_move_bps=0.0))
        assert v.decision == "reject"

    def test_adverse_clv_outranks_favourable_move(self):
        v = vote_of(self.bot, good_ctx(clv_estimate=-0.04, line_move_bps=40.0))
        assert v.decision == "reject"

    def test_abstains_on_flat_market(self):
        v = vote_of(self.bot, good_ctx(clv_estimate=0.0, line_move_bps=0.0))
        assert v.decision == "abstain"

    def test_abstains_when_tape_missing(self):
        v = vote_of(self.bot, good_ctx(clv_estimate=None, line_move_bps=None))
        assert v.decision == "abstain"
        assert self.bot.veto is False


# --------------------------------------------------------------------- liquidity

class TestLiquidity:
    bot = LiquidityBot()

    def test_approves_deep_book(self):
        v = vote_of(self.bot, good_ctx())
        assert v.decision == "approve"
        assert v.evidence["spread_proxy_bps"] > 0

    def test_rejects_thin_book(self):
        v = vote_of(self.bot, good_ctx(liquidity=1_000.0))
        assert v.decision == "reject"
        assert self.bot.veto is True

    def test_rejects_just_under_floor(self):
        assert vote_of(self.bot, good_ctx(liquidity=4_999.0)).decision == "reject"
        assert vote_of(self.bot, good_ctx(liquidity=5_000.0)).decision == "approve"

    def test_rejects_missing_or_bad_liquidity(self):
        ctx = good_ctx()
        ctx.pop("liquidity")
        assert vote_of(self.bot, ctx).decision == "reject"
        assert vote_of(self.bot, good_ctx(liquidity=None)).decision == "reject"
        assert vote_of(self.bot, good_ctx(liquidity="deep")).decision == "reject"
        assert vote_of(self.bot, good_ctx(liquidity=0.0)).decision == "reject"
        assert vote_of(self.bot, good_ctx(liquidity=-10.0)).decision == "reject"

    def test_spread_proxy_decreases_with_depth(self):
        assert spread_proxy_bps(5_000) > spread_proxy_bps(50_000) > spread_proxy_bps(500_000)


# ------------------------------------------------------------------------ timing

class TestTiming:
    bot = TimingBot()

    def test_approves_inside_window(self):
        v = vote_of(self.bot, good_ctx())
        assert v.decision == "approve"
        assert v.confidence >= 0.8

    def test_abstains_too_late(self):
        v = vote_of(self.bot, good_ctx(time_to_start_h=0.2))
        assert v.decision == "abstain"
        assert "too late" in v.reasons[0]

    def test_abstains_too_early(self):
        v = vote_of(self.bot, good_ctx(time_to_start_h=120.0))
        assert v.decision == "abstain"
        assert "stale" in v.reasons[0]

    def test_edge_of_window_lower_confidence(self):
        v = vote_of(self.bot, good_ctx(time_to_start_h=1.0))
        assert v.decision == "approve"
        assert v.confidence < 0.8

    def test_missing_time_abstains(self):
        ctx = good_ctx()
        ctx.pop("time_to_start_h")
        assert vote_of(self.bot, ctx).decision == "abstain"
        assert vote_of(self.bot, good_ctx(time_to_start_h="soon")).decision == "abstain"


# -------------------------------------------------------------------- pack shape

EXPECTED_IDS = ["elo", "starter_gate", "injury", "line_value", "liquidity", "timing"]


class TestPackShape:
    def test_pack_exposes_six_instances(self):
        assert len(BOTS) >= 6
        assert all(isinstance(b, Bot) for b in BOTS)
        assert set(EXPECTED_IDS) <= set(BOT_IDS)

    def test_ids_unique(self):
        assert len(set(BOT_IDS)) == len(BOT_IDS)

    def test_every_bot_declares_domain_and_mandate(self):
        for b in BOTS:
            assert "sports" in b.domains, b.id
            assert isinstance(b.mandate, str) and len(b.mandate) > 20, b.id
            assert isinstance(b.veto, bool), b.id

    def test_veto_flags_exact(self):
        assert {"starter_gate", "injury", "liquidity"} <= set(VETO_IDS)

    def test_evaluate_returns_vote_on_good_ctx(self):
        for b in BOTS:
            v = b.evaluate(good_ctx())
            assert isinstance(v, Vote)
            assert v.bot == b.id

    def test_no_bot_raises_on_garbage(self):
        junk = [None, {}, {"market": None}, {"liquidity": "x", "model_p": []},
                {"time_to_start_h": object()}, {"injury_status": 0},
                {"starter_confirmed": []}]
        for b in BOTS:
            for ctx in junk:
                v = b.vote(ctx)
                assert isinstance(v, Vote)
                assert v.decision in DECISIONS

    def test_evaluate_survives_non_mapping_context(self):
        for b in BOTS:
            assert isinstance(b.vote("not a context"), Vote)

    def test_describe_helper(self):
        info = sports.describe()
        assert info["domain"] == "sports"
        assert set(EXPECTED_IDS) <= {b["id"] for b in info["bots"]}


# ------------------------------------------------------------------ coordinator

class TestCoordinator:
    def test_allow_on_good_context(self):
        res = decide(BOTS, good_ctx())
        assert res["decision"] == "ALLOW", res["reasons"]
        assert res["n"] >= 6
        assert res["vetoes"] == 0
        assert res["rejects"] == 0
        assert res["approves"] >= 6
        assert len(res["votes_hash"]) == 64

    def test_veto_when_starter_not_confirmed(self):
        res = decide(BOTS, good_ctx(starter_confirmed=False))
        assert res["decision"] == "VETO"
        assert any("starter_gate" in r for r in res["reasons"])

    def test_veto_when_injury_unknown(self):
        res = decide(BOTS, good_ctx(injury_status="unknown"))
        assert res["decision"] == "VETO"
        assert any("injury" in r for r in res["reasons"])

    def test_veto_when_injury_flag_missing(self):
        ctx = good_ctx()
        ctx.pop("injury_status")
        assert decide(BOTS, ctx)["decision"] == "VETO"

    def test_reject_when_only_non_veto_disagrees(self):
        res = decide(BOTS, good_ctx(clv_estimate=-0.05, line_move_bps=-80.0))
        assert res["decision"] == "REJECT"
        assert res["rejects"] == 1

    def test_reject_when_clv_runs_against_a_favourable_move(self):
        res = decide(BOTS, good_ctx(clv_estimate=-0.05, line_move_bps=40.0))
        assert res["decision"] == "REJECT"
        assert res["rejects"] == 1 and res["vetoes"] == 0

    def test_empty_context_vetoes_fail_closed(self):
        assert decide(BOTS, {})["decision"] == "VETO"

    def test_min_confidence_and_quorum(self):
        assert decide(BOTS, good_ctx(), min_confidence=0.99, quorum=1)["decision"] == "WAIT"
        assert decide(BOTS, good_ctx(), quorum=99)["decision"] == "WAIT"

    def test_verdict_is_deterministic(self):
        r1 = decide(BOTS, good_ctx())
        r2 = decide(BOTS, good_ctx())
        assert r1["votes_hash"] == r2["votes_hash"]
        assert r1["decision"] == r2["decision"]
