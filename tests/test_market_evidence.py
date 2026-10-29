"""Market-specific contracts and actual acquisition -> calculation -> report paths."""
import io
import json
import math
import importlib.util
from pathlib import Path
import sys
import types
from datetime import datetime, timedelta, timezone
from unittest.mock import patch

import pytest

import polymarket_sports_github_cycle as cycle
from sovereign_bots import cli
from sovereign_bots.core.market_evidence import DOMAINS, PROFILES, assess_market, market_request, observation
from sovereign_bots.core.autonomous_alpha_daemons import scan_live_kucoin_hyperliquid_funding_arb
from sovereign_bots.core.autonomous_alpha_daemons import scan_live_espn_scratches_and_spillovers

NOW = datetime(2026, 1, 1, 12, tzinfo=timezone.utc)


def sample(name, identity="fixture", now=NOW, values=None):
    defaults = {
        "price": {"price": 100}, "tape": {"prices": [99, 100]},
        "funding": {"funding_rate_bps": 2, "interval_hours": 4},
        "team_odds": {"home_decimal": 2, "away_decimal": 2.5},
        "player_distribution": {"projection": 2, "line": 1.5, "market_probability": .5, "side": "over"},
        "lineup": {"starter_confirmed": True, "injury_status": "healthy"},
        "binary_prices": {"yes_price": .3, "no_price": .7},
        "liquidity": {"liquidity_usd": 15000, "volume_24h_usd": 10000},
        "resolution": {"rules": "Inert fixture settlement rules", "days_to_resolve": 2},
    }
    return observation(values or defaults[name], {key: field.unit for key, field in DOMAINS[name].fields.items()},
                       "inert-fixture", identity, now.isoformat(), now.isoformat())


def request(profile, now=NOW):
    return market_request(profile, "fixture", {name: sample(name, now=now) for name in PROFILES[profile][2]})


@pytest.mark.parametrize("profile", list(PROFILES))
def test_supported_profiles_have_consistent_nonexecuting_results(profile):
    result = assess_market(request(profile), now=NOW)
    assert result["status"] == "READY"
    assert result["decision"] == "ABSTAIN"
    assert not result["committee_evaluated"] and not result["execution_allowed"]
    assert result["coverage"]["required"] == result["coverage"]["available_required"]
    assert set(result["requirements"]) == set(DOMAINS)
    assert result["calculations"]
    json.dumps(result, allow_nan=False)


def test_cross_market_applicability_is_registry_owned():
    spot = assess_market(request("crypto.spot"), now=NOW)
    assert spot["requirements"]["funding"]["status"] == "not_applicable"
    perpetual = request("crypto.perpetual")
    del perpetual["evidence"]["funding"]
    result = assess_market(perpetual, now=NOW)
    assert result["status"] == "BLOCKED"
    assert result["requirements"]["funding"]["status"] == "missing"
    assert not result["calculations"]
    perpetual["evidence"]["funding"] = {"status": "not_applicable"}
    assert assess_market(perpetual, now=NOW)["requirements"]["funding"]["status"] == "malformed"
    moneyline = request("sports.moneyline")
    moneyline["evidence"]["funding"] = sample("funding")
    assert assess_market(moneyline, now=NOW)["status"] == "BLOCKED"
    assert assess_market(request("sports.moneyline"), now=NOW)["requirements"]["lineup"]["requirement"] == "optional"
    assert assess_market(request("sports.player_prop"), now=NOW)["requirements"]["lineup"]["requirement"] == "required"


@pytest.mark.parametrize("mutate", [
    lambda r: r.update(profile="unknown"),
    lambda r: r["market"].update(kind="player_prop"),
    lambda r: r.update(not_applicable=["team_odds"]),
    lambda r: r.update(schema_version="future"),
])
def test_unknown_or_mismatched_contract_cannot_bypass_requirements(mutate):
    envelope = request("sports.moneyline")
    mutate(envelope)
    result = assess_market(envelope, now=NOW)
    assert result["status"] == "UNSUPPORTED" and not result["calculations"]


@pytest.mark.parametrize("change,status", [
    ({"source": ""}, "malformed"), ({"market_id": "other"}, "malformed"),
    ({"observed_at": "2026-01-01T11:00:00Z"}, "stale"),
    ({"observed_at": "2026-01-01T12:01:00Z"}, "malformed"),
    ({"received_at": "2026-01-01T12:00:00"}, "malformed"),
    ({"values": {"home_decimal": True, "away_decimal": 2}}, "malformed"),
    ({"values": {"home_decimal": float("nan"), "away_decimal": 2}}, "malformed"),
    ({"units": {"home_decimal": "probability", "away_decimal": "decimal_odds"}}, "malformed"),
])
def test_missing_stale_malformed_evidence_blocks_calculation(change, status):
    envelope = request("sports.moneyline")
    envelope["evidence"]["team_odds"].update(change)
    result = assess_market(envelope, now=NOW)
    assert result["status"] == "BLOCKED" and not result["calculations"]
    assert result["requirements"]["team_odds"]["status"] == status
    json.dumps(result, allow_nan=False)


def test_source_metadata_preserved_and_values_drive_real_calculations():
    envelope = request("sports.moneyline")
    result = assess_market(envelope, now=NOW)
    assert result["requirements"]["team_odds"]["evidence"] == envelope["evidence"]["team_odds"]
    assert result["calculations"]["home_implied_probability"] == .5
    envelope["evidence"]["team_odds"]["values"]["home_decimal"] = 4
    assert assess_market(envelope, now=NOW)["calculations"]["home_implied_probability"] == .25
    prop = assess_market(request("sports.player_prop"), now=NOW)
    assert prop["calculations"]["p_over"] == pytest.approx(1 - 3 * math.exp(-2))
    assert assess_market(request("crypto.perpetual"), now=NOW)["calculations"]["funding_8h_bps"] == 4


@pytest.mark.parametrize("line", [1.25, .01, 1.75])
def test_unsupported_fractional_count_lines_fail_closed(line):
    envelope = request("sports.player_prop")
    envelope["evidence"]["player_distribution"]["values"]["line"] = line
    result = assess_market(envelope, now=NOW)
    assert result["status"] == "BLOCKED" and not result["calculations"]


def test_derived_overflow_is_blocked_and_json_serializable():
    envelope = request("crypto.perpetual")
    envelope["evidence"]["funding"]["values"]["interval_hours"] = 5e-324
    result = assess_market(envelope, now=NOW)
    assert result["status"] == "BLOCKED" and not result["calculations"]
    json.dumps(result, allow_nan=False)
    spot = request("crypto.spot")
    spot["evidence"]["tape"] = sample("tape", values={"prices": [5e-324, 1e308]})
    result = assess_market(spot, now=NOW)
    assert result["status"] == "BLOCKED" and not result["calculations"]
    json.dumps(result, allow_nan=False)


def test_cli_reads_contract_calculates_and_reports(tmp_path, capsys):
    envelope = request("sports.player_prop", now=datetime.now(timezone.utc))
    path = tmp_path / "evidence.json"
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert cli.main(["assess-market", "--context", str(path)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["status"] == "READY" and result["calculations"]["p_over"] > .5
    del envelope["evidence"]["lineup"]
    path.write_text(json.dumps(envelope), encoding="utf-8")
    assert cli.main(["assess-market", "--context", str(path)]) == 2
    assert json.loads(capsys.readouterr().out)["status"] == "BLOCKED"
    path.write_text("{", encoding="utf-8")
    assert cli.main(["assess-market", "--context", str(path)]) == 2
    failed = json.loads(capsys.readouterr().out)
    assert failed["status"] == "UNSUPPORTED" and not failed["calculations"]


def sports_raw():
    return {"events": [{"id": "fixture", "shortName": "A vs B", "competitions": [{
        "competitors": [{"homeAway": "home", "team": {"displayName": "A"}},
                        {"homeAway": "away", "team": {"displayName": "B"}}],
        "odds": [{"provider": {"name": "Inert Book"}, "homeTeamOdds": {"moneyLine": 100},
                  "awayTeamOdds": {"moneyLine": 150}}]}]}]}


def poly_raw():
    return [{"id": "fixture", "question": "Fixture threshold by tomorrow?", "outcomes": ["No", "Yes"],
             "outcomePrices": [.7, .3], "volume24hr": 10000, "liquidityNum": 15000,
             "description": "Inert fixture rules", "endDate": (datetime.now(timezone.utc) + timedelta(days=2)).isoformat()}]


def responses(*payloads):
    return [io.BytesIO(json.dumps(payload).encode()) for payload in payloads]


def test_actual_public_acquisition_calculation_and_report_pipeline():
    with patch.object(cycle.urllib.request, "urlopen", side_effect=responses(poly_raw(), sports_raw(), {"events": []})):
        poly = cycle.scan_market("polymarket.binary")
        sports = cycle.scan_market("sports.moneyline")
    assert poly["markets"][0]["market_evidence"]["calculations"]["yes_price"] == .3
    assessment = sports["edges"][0]["market_evidence"]
    assert assessment["calculations"]["home_implied_probability"] == .5
    assert assessment["calculations"]["two_way_overround"] == pytest.approx(-.1)
    card = cycle._observation_card("Sports", sports)
    assert "sports.moneyline" in card and "overround: -0.1000" in card
    assert "source quote time unverified" in card and "ABSTAIN" in card


def test_actual_stale_provider_data_remains_visible_but_cannot_drive_calculation():
    payload = sports_raw()
    payload["events"][0]["competitions"][0]["odds"][0]["lastUpdated"] = "2020-01-01T00:00:00Z"
    with patch.object(cycle.urllib.request, "urlopen", side_effect=responses(payload, {"events": []})):
        result = cycle.scan_market("sports.moneyline")
    assessment = result["edges"][0]["market_evidence"]
    assert assessment["status"] == "BLOCKED" and not assessment["calculations"]
    assert "team_odds:stale" in cycle._observation_card("Sports", result)


def test_unknown_acquisition_profile_does_no_network():
    with patch.object(cycle.urllib.request, "urlopen") as send:
        with pytest.raises(ValueError):
            cycle.scan_market("sports.player_prop")  # no live player feed adapter
    send.assert_not_called()


def crypto_fetch(**changes):
    contract = {"symbol": "XBTUSDTM", "fundingFeeRate": .002,
                "currentFundingRateGranularity": 14400000, "markPrice": 70100,
                "indexPrice": 70000, "turnoverOf24h": 80000000, "quoteCurrency": "USDT"}
    contract.update(changes)
    def fetch(url, method="GET", payload=None):
        if "hyperliquid" in url:
            return [{"universe": [{"name": "BTC"}]}, [{"funding": ".00001", "markPx": "70000"}]]
        return {"data": [contract]}
    return fetch


def test_crypto_acquisition_normalizes_actual_funding_interval_before_calculation():
    result = scan_live_kucoin_hyperliquid_funding_arb(fetch_fn=crypto_fetch())
    assert result["market_evidence"][0]["calculations"]["funding_8h_bps"] == pytest.approx(40)
    signal = result["top_opportunities"][0]
    assert signal["kucoin_funding_8h_bps"] == 40 and signal["decision"] == "ABSTAIN"
    assert result["peer_market_evidence"][0]["calculations"]["funding_8h_bps"] == pytest.approx(.8)


@pytest.mark.parametrize("changes", [{"currentFundingRateGranularity": None}, {"markPrice": float("nan")},
                                     {"updatedAt": "2020-01-01T00:00:00Z"}, {"quoteCurrency": "BTC"}])
def test_crypto_missing_stale_malformed_or_unsupported_data_never_reaches_signal(changes):
    result = scan_live_kucoin_hyperliquid_funding_arb(fetch_fn=crypto_fetch(**changes))
    assert result["kucoin_contracts_scanned"] == 0
    assert result["opportunities_found"] == 0
    assert result["market_evidence"][0]["status"] in ("BLOCKED", "UNSUPPORTED")


@pytest.mark.parametrize("prices", [("100", "110"), ("5e-324", "1e308")])
def test_kucoin_reporting_cycle_uses_stream_contract_without_live_side_effects(monkeypatch, prices):
    # The real client autoloads credential files at import; replace it before
    # loading this module so this test cannot read credentials or call providers.
    sync_calls = []
    class Client:
        api_key = "inert-fixture"
        def get_futures_overview(self, currency):
            return {"accountEquity": 100, "availableBalance": 100, "unrealisedPNL": 0}
        def get_futures_positions(self): return []
        def get_futures_active_orders(self): return []
        def get_futures_stop_orders(self): return []
        def get_spot_accounts(self): return []
        def sync_worker_state(self): sync_calls.append(True)
    provider = types.ModuleType("kucoin_client")
    provider.KuCoinClient = Client
    module_path = Path(__file__).resolve().parents[1] / "kucoin_github_cycle.py"
    spec = importlib.util.spec_from_file_location("isolated_kucoin_market_cycle", module_path)
    module = importlib.util.module_from_spec(spec)
    with patch.dict(sys.modules, {"kucoin_client": provider}):
        spec.loader.exec_module(module)
    monkeypatch.setenv("STREAM_1S_SECONDS", "2")
    monkeypatch.setattr(module, "can_trade", lambda *a, **k: {"allowed": True})
    with patch.object(module.urllib.request, "urlopen", side_effect=responses({"BTC": prices[0]}, {"BTC": prices[1]}, {}, {}, {}, {})), \
         patch.object(module.time, "sleep"), patch.object(module, "send_institutional_telegram") as send:
        result = module.run_cycle(execute=False, notify_telegram=False)
    send.assert_not_called()
    assert not sync_calls
    stream = result["multi_domain_1s_stream"]
    if prices[0] == "100":
        assert stream["raw_sample_change_bps"]["BTC"] == 1000
    else:
        assert stream["raw_sample_change_bps"] == {}
        assert stream["invalid_sample_changes"] == {"BTC": "nonfinite_sample_change"}
    json.dumps(result, allow_nan=False)
    assessment = stream["market_evidence"]["BTC"]
    assert assessment["profile"] == "crypto.perpetual" and assessment["status"] == "BLOCKED"
    assert assessment["requirements"]["funding"]["status"] == "missing"
    card = module.format_kucoin_cycle_card(result)
    assert "funding:missing" in card and "committee not evaluated" in card


def test_scheduled_public_cycle_reports_contracts_through_real_main(monkeypatch):
    monkeypatch.setenv("SEND_TELEGRAM", "1")
    with patch.object(cycle.urllib.request, "urlopen", side_effect=responses(poly_raw(), sports_raw(), {"events": []})), \
         patch.object(cycle, "send_institutional_telegram", return_value={"ok": True}) as send:
        assert cycle.main() == 0
    assert send.call_count == 2
    cards = [call.args[0] for call in send.call_args_list]
    assert "polymarket.binary" in cards[0] and "sports.moneyline" in cards[1]
    assert all("ABSTAIN" in card for card in cards)


def test_injury_acquisition_does_not_invent_prop_inputs_or_confirm_doubtful():
    scoreboard = sports_raw()
    scoreboard["events"][0]["status"] = {"type": {"state": "pre"}}
    injury_payload = {"injuries": [{"displayName": "A", "injuries": [
        {"status": "Out", "athlete": {"id": "p1", "displayName": "Fixture Player", "position": {"abbreviation": "PG"}}},
        {"status": "Doubtful", "athlete": {"id": "p2", "displayName": "Uncertain Player"}},
    ]}]}
    result = scan_live_espn_scratches_and_spillovers(fetch_fn=lambda url: injury_payload if "injuries" in url else scoreboard)
    assert all(row["spread_abs"] is None for row in result["pre_tipoff_games"])
    assert all(row["player"] == "Fixture Player" for row in result["confirmed_scratches_sample"])
    for row in result["confirmed_scratches_sample"]:
        evidence = row["market_evidence"]
        assert evidence["status"] == "BLOCKED"
        assert evidence["requirements"]["player_distribution"]["status"] == "missing"
        assert not evidence["calculations"]


def test_autonomous_scheduled_pipeline_reports_validated_research_and_delivery_failure(monkeypatch, capsys):
    import autonomous_alpha_github_cycle as alpha_cycle
    def provider(req, **kwargs):
        url = req.full_url
        if "scoreboard" in url or "injuries" in url:
            return io.BytesIO(json.dumps({"events": [], "injuries": []}).encode())
        payload = crypto_fetch()(url)
        return io.BytesIO(json.dumps(payload).encode())
    monkeypatch.setenv("SEND_TELEGRAM", "1")
    with patch("urllib.request.urlopen", side_effect=provider), \
         patch.object(alpha_cycle, "send_institutional_telegram", return_value={"ok": False}) as send:
        assert alpha_cycle.main() == 1
    card = send.call_args.args[0]
    assert "research signals (ABSTAIN)" in card
    assert "complete observation contracts" in card
    assert "TELEGRAM_DELIVERY_UNCONFIRMED" in capsys.readouterr().out
