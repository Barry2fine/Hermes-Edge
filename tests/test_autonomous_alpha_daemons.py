"""
Unit tests for `sovereign_bots/core/autonomous_alpha_daemons.py` and committee seat wiring.
"""
from __future__ import annotations

from sovereign_bots.bots.crypto.funding_carry import FundingCarryBot
from sovereign_bots.bots.sports.usage_redistribution import InjuryUsageRedistributionBot
from sovereign_bots.core.autonomous_alpha_daemons import (
    evaluate_cross_exchange_funding_and_basis,
    evaluate_pre_tipoff_scratch_spillover,
    run_nightly_clv_brier_ablation_audit,
    scan_live_espn_scratches_and_spillovers,
    scan_live_kucoin_hyperliquid_funding_arb,
)


def test_pre_tipoff_scratch_fresh_vs_stale_news():
    # Fresh scratch (5 minutes old, 35 minutes before tipoff) retains high unpriced alpha
    fresh = evaluate_pre_tipoff_scratch_spillover(
        archetype="nba_heliocentric_guard_out",
        absent_star_name="Trae Young",
        teammate_name="Jalen Johnson",
        stat_category="assists",
        baseline_per_minute_rate=0.21,
        baseline_minutes=34.5,
        milestone_target=7,
        offered_decimal_odds=2.05,
        news_age_minutes=5.0,
        minutes_until_tipoff=35.0,
        blowout_spread_abs=4.0,
    )
    assert fresh["action"] == "FIRE_PRE_TIPOFF_SPILLOVER_OVER"
    assert fresh["unpriced_alpha_retention"] > 0.80
    assert fresh["ev_pct"] > 5.0
    assert fresh["edge_bps"] > 200.0

    # Stale scratch (75 minutes old > 2.2 * 22 min half-life) is flagged as already priced
    stale = evaluate_pre_tipoff_scratch_spillover(
        archetype="nba_heliocentric_guard_out",
        absent_star_name="Trae Young",
        teammate_name="Jalen Johnson",
        stat_category="assists",
        baseline_per_minute_rate=0.21,
        baseline_minutes=34.5,
        milestone_target=7,
        offered_decimal_odds=2.05,
        news_age_minutes=75.0,
        minutes_until_tipoff=35.0,
        blowout_spread_abs=4.0,
    )
    assert stale["action"] == "PASS_STALE_SCRATCH_ALREADY_PRICED"
    assert stale["unpriced_alpha_retention"] < 0.15


def test_cross_exchange_funding_contrarian_and_arb_regimes():
    # 1. Crowded long on KuCoin (+22 bps / 8h) with positive mark-index basis (+12 bps)
    contrarian_short = evaluate_cross_exchange_funding_and_basis(
        symbol="SOLUSDTM",
        kucoin_funding_8h_bps=22.0,
        kucoin_mark_price=180.216,
        kucoin_index_price=180.000,
        hyperliquid_funding_8h_bps=18.0,
        kucoin_turnover_24h_usd=25_000_000.0,
    )
    assert contrarian_short["regime"] == "DIRECTIONAL_CONTRARIAN_CARRY_REVERSAL"
    assert contrarian_short["recommended_kucoin_side"] == "short"
    assert contrarian_short["expected_8h_edge_bps"] > 20.0

    # 2. Cross-venue delta-neutral funding arbitrage (KuCoin +11 bps vs Hyperliquid -6 bps -> 17 bps spread)
    arb = evaluate_cross_exchange_funding_and_basis(
        symbol="ETHUSDTM",
        kucoin_funding_8h_bps=11.0,
        kucoin_mark_price=2600.2,
        kucoin_index_price=2600.0,
        hyperliquid_funding_8h_bps=-6.0,
        kucoin_turnover_24h_usd=50_000_000.0,
    )
    assert arb["regime"] == "CROSS_VENUE_DELTA_NEUTRAL_FUNDING_ARB"
    assert arb["arb_legs"] == "SHORT_KUCOIN_PERP_LONG_HYPERLIQUID_PERP"
    assert arb["net_arb_apr_pct"] > 150.0


def test_mocked_live_scanners_and_committee_wiring(tmp_path):
    # Mock ESPN scoreboard scan
    mock_espn = {
        "events": [
            {
                "shortName": "BOS @ NYK",
                "status": {"type": {"state": "pre", "shortDetail": "7:30 PM EDT"}},
                "competitions": [{"odds": [{"spread": -5.5}]}],
            }
        ]
    }
    espn_res = scan_live_espn_scratches_and_spillovers(fetch_fn=lambda url: mock_espn)
    assert espn_res["games_inspected"] == 2
    assert espn_res["status"] == "ACTIVE_SLATE"

    # Mock KuCoin + Hyperliquid scan
    def _mock_crypto_fetch(url, method="GET", payload=None):
        if "hyperliquid" in url:
            return [{"universe": [{"name": "BTC"}]}, [{"funding": "0.00001"}]]
        return {
            "data": [
                {
                    "symbol": "XBTUSDTM",
                    "fundingFeeRate": 0.0020,  # +20 bps / 8h
                    "markPrice": 70100.0,
                    "indexPrice": 70000.0,
                    "turnoverOf24h": 80_000_000.0,
                }
            ]
        }

    arb_scan = scan_live_kucoin_hyperliquid_funding_arb(fetch_fn=_mock_crypto_fetch)
    assert arb_scan["kucoin_contracts_scanned"] == 1
    assert arb_scan["opportunities_found"] == 1
    assert arb_scan["top_opportunities"][0]["regime"] == "DIRECTIONAL_CONTRARIAN_CARRY_REVERSAL"

    # Committee Seat Wiring: FundingCarryBot & InjuryUsageRedistributionBot
    f_bot = FundingCarryBot()
    f_vote = f_bot.evaluate({
        "symbol": "XBTUSDTM",
        "funding_rate": 0.0020,
        "direction": "short",
        "mark_price": 70100.0,
        "index_price": 70000.0,
        "hyperliquid_funding_8h_bps": 1.0,
    })
    assert f_vote.decision == "approve"
    assert "cross_exchange_basis" in f_vote.evidence

    u_bot = InjuryUsageRedistributionBot()
    u_vote = u_bot.evaluate({
        "scratch_archetype": "nba_heliocentric_guard_out",
        "absent_star_name": "Star Guard",
        "teammate_name": "Secondary Creator",
        "stat_category": "assists",
        "baseline_per_minute_rate": 0.22,
        "baseline_minutes": 34.0,
        "milestone_target": 7,
        "decimal_odds": 2.00,
        "news_age_minutes": 4.0,
        "side": "over",
    })
    assert u_vote.decision == "approve"
    assert "pre_tipoff_spillover" in u_vote.evidence

    # Nightly CLV & Brier Grader
    k_path = str(tmp_path / "knowledge_state.json")
    audit = run_nightly_clv_brier_ablation_audit(knowledge_path=k_path, persist=True)
    assert audit["ok"] is True
    assert audit["calibration"]["brier_skill_score"] > 0.0
    assert "star_absence_usage_spillover" in audit["feature_ablation_weights"]
