"""Synthetic evidence fixtures; no providers, credentials, or live execution."""
import io
import json
import multiprocessing
import os
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import patch
import urllib.error

import pytest

from sovereign_bots.core import knowledge_loop as knowledge
from sovereign_bots.core import telegram_alerts as telegram
from sovereign_bots.core.bot import Bot, Vote
from sovereign_bots.core.coordinator import decide
from sovereign_bots.core.state_io import state_lock
from sovereign_bots.core.walk_forward_calibrator import replay_sports_postmortems, compute_brier_and_log_loss


def evidence(identity="fixture-1", **changes):
    return dict({"observation_id": identity, "market_family": "fixture", "source": "inert-fixture",
                 "model_version": "fixture-v1", "prediction_at": "2026-01-01T12:00:00Z",
                 "closing_at": "2026-01-01T12:59:00Z", "event_start_at": "2026-01-01T13:00:00Z",
                 "settled_at": "2026-01-01T16:00:00Z", "max_prediction_age_seconds": 3600,
                 "p_model": .8, "p_market": .6, "p_closing": .7, "outcome": 1,
                 "realized_roi_mult": .5}, **changes)


def ledger(tmp_path, legs):
    path = tmp_path / "evidence.jsonl"
    path.write_text(json.dumps({"tickets": [{"legs": legs}]}), encoding="utf-8")
    return str(path)


def test_evidence_replay_is_atomic_idempotent_and_measured(tmp_path):
    source = ledger(tmp_path, [evidence(), evidence()])
    state = str(tmp_path / "state.json")
    result = replay_sports_postmortems(source, state)
    assert result["legs_evaluated"] == result["newly_recorded_legs"] == 1
    assert result["calibration"]["brier_model"] == .04
    assert result["calibration"]["mean_clv_bps"] == 1000
    before = knowledge.load_knowledge_state(state)
    assert before["sports_markets"]["fixture"]["settled_n"] == 1
    assert replay_sports_postmortems(source, state)["newly_recorded_legs"] == 0
    assert knowledge.load_knowledge_state(state)["sports_markets"] == before["sports_markets"]
    source = ledger(tmp_path, [evidence("fixture-2"), evidence(p_model=.4)])
    with pytest.raises(ValueError, match="reconciliation"):
        replay_sports_postmortems(source, state)
    assert knowledge.load_knowledge_state(state) == before


@pytest.mark.parametrize("changes", [
    {"p_model": float("nan")}, {"p_market": True}, {"p_closing": 1.2}, {"outcome": 2},
    {"outcome": "1"}, {"prediction_at": "2026-01-01T14:00:00Z"},
    {"prediction_at": "2026-01-01T12:00:00"}, {"max_prediction_age_seconds": 10},
    {"model_version": ""}, {"source": None}, {"realized_roi_mult": float("inf")},
    {"settled_at": "2099-01-01T00:00:00Z"},
])
def test_invalid_evidence_cannot_mutate_state(tmp_path, changes):
    state = tmp_path / "state.json"
    result = replay_sports_postmortems(ledger(tmp_path, [evidence(**changes)]), str(state))
    assert result["status"] == "EVIDENCE_UNAVAILABLE"
    assert result["invalid_or_missing_evidence"] == 1
    assert not state.exists()


def test_crash_before_replace_preserves_receipts_and_state(tmp_path):
    source = ledger(tmp_path, [evidence()])
    state = str(tmp_path / "state.json")
    knowledge.save_knowledge_state(knowledge.default_state(), state)
    before = (tmp_path / "state.json").read_bytes()
    with patch("sovereign_bots.core.state_io.os.replace", side_effect=OSError("injected failure")):
        with pytest.raises(OSError):
            replay_sports_postmortems(source, state)
    assert (tmp_path / "state.json").read_bytes() == before
    assert not list(tmp_path.glob("*.tmp"))
    assert replay_sports_postmortems(source, state)["newly_recorded_legs"] == 1


def test_concurrent_replays_apply_each_observation_once(tmp_path):
    source = ledger(tmp_path, [evidence()])
    state = str(tmp_path / "state.json")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: replay_sports_postmortems(source, state), range(20)))
    assert sum(r["newly_recorded_legs"] for r in results) == 1
    assert knowledge.load_knowledge_state(state)["sports_markets"]["fixture"]["settled_n"] == 1


def test_concurrent_updates_do_not_lose_observations(tmp_path):
    state = str(tmp_path / "state.json")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: knowledge.record_sports_outcome("fixture", .6, 1, path=state), range(30)))
    assert knowledge.load_knowledge_state(state)["sports_markets"]["fixture"]["settled_n"] == 30


def test_nested_transaction_rejected_without_partial_commit_and_lock_released(tmp_path):
    state = str(tmp_path / "state.json")
    knowledge.save_knowledge_state(knowledge.default_state(), state)
    before = (tmp_path / "state.json").read_bytes()
    with pytest.raises(RuntimeError, match="nested knowledge transaction"):
        with knowledge.knowledge_transaction(state) as outer:
            outer["uncommitted"] = True
            knowledge.record_sports_outcome("fixture", .6, 1, path=state)
    assert (tmp_path / "state.json").read_bytes() == before
    knowledge.record_sports_outcome("fixture", .6, 1, path=state)
    assert knowledge.load_knowledge_state(state)["sports_markets"]["fixture"]["settled_n"] == 1


def _fork_lock_probe(path, result):
    try:
        with state_lock(path, timeout=.2):
            result.send("acquired")
    except TimeoutError:
        result.send("blocked")
    finally:
        result.close()


@pytest.mark.skipif("fork" not in multiprocessing.get_all_start_methods(), reason="requires POSIX fork")
def test_fork_cannot_inherit_lock_ownership(tmp_path):
    state = str(tmp_path / "state.json")
    context = multiprocessing.get_context("fork")
    receiver, sender = context.Pipe(duplex=False)
    child = context.Process(target=_fork_lock_probe, args=(state, sender))
    try:
        with state_lock(state):
            child.start()
            sender.close()
            assert receiver.poll(5)
            assert receiver.recv() == "blocked"
            child.join(5)
            assert child.exitcode == 0
        with state_lock(state, timeout=.2):
            pass
    finally:
        receiver.close()
        sender.close()
        if child.is_alive():
            child.terminate()
            child.join(5)


def _process_writer(path):
    for _ in range(5):
        knowledge.record_sports_outcome("fixture", .6, 1, path=path)


def _crashed_lock_owner(path):
    with state_lock(path):
        os._exit(0)


def test_process_writers_and_crashed_owner_recovery(tmp_path):
    state = str(tmp_path / "state.json")
    context = multiprocessing.get_context("spawn")
    processes = [context.Process(target=_process_writer, args=(state,)) for _ in range(4)]
    try:
        for process in processes:
            process.start()
        for process in processes:
            process.join(15)
            assert process.exitcode == 0
        assert knowledge.load_knowledge_state(state)["sports_markets"]["fixture"]["settled_n"] == 20
        crashed = context.Process(target=_crashed_lock_owner, args=(state,))
        processes.append(crashed)
        crashed.start()
        crashed.join(15)
        assert crashed.exitcode == 0
        knowledge.record_sports_outcome("fixture", .6, 1, path=state)
        assert knowledge.load_knowledge_state(state)["sports_markets"]["fixture"]["settled_n"] == 21
    finally:
        for process in processes:
            if process.is_alive():
                process.terminate()
                process.join(5)


@pytest.mark.parametrize("payload", ['{', '[]', '{"coins": [], "polymarket_categories": {}}', '{"coins": {}, "polymarket_categories": {}, "x": NaN}'])
def test_corrupt_state_is_preserved(tmp_path, payload):
    state = tmp_path / "state.json"
    state.write_text(payload, encoding="utf-8")
    with pytest.raises(ValueError):
        knowledge.record_sports_outcome("fixture", .6, 1, path=str(state))
    assert state.read_text(encoding="utf-8") == payload


def test_ece_bins_do_not_double_count_and_small_sample_not_certified():
    rows = [{"p_model": .1, "p_market": .2, "p_closing": .2, "outcome": 0}]
    assert compute_brier_and_log_loss(rows)["ece"] == .1
    assert compute_brier_and_log_loss(rows)["calibrated"] is False
    assert compute_brier_and_log_loss(rows * 20)["calibrated"] is True


def test_missing_comparison_evidence_is_not_fabricated():
    result = compute_brier_and_log_loss([{"p_model": .8, "outcome": 1}])
    assert result["brier_model"] == .04
    assert result["brier_market"] is None
    assert result["mean_clv_bps"] is None
    assert not result["calibrated"]


def test_endpoint_probabilities_use_exact_brier_scores():
    result = compute_brier_and_log_loss([
        {"p_model": 1, "p_market": 0, "p_closing": 0, "outcome": 0}])
    assert result["brier_model"] == 1
    assert result["brier_market"] == 0
    assert result["brier_skill_score"] is None


def test_nonpersistent_replay_does_not_create_state(tmp_path):
    state = tmp_path / "state.json"
    result = replay_sports_postmortems(ledger(tmp_path, [evidence()]), str(state), persist=False)
    assert result["legs_evaluated"] == 1
    assert result["newly_recorded_legs"] == 0
    assert not state.exists()


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -.1, 1.1, True])
def test_invalid_confidence_cannot_approve(value):
    class Broken(Bot):
        id = "broken"
        edge_seat = True
        def evaluate(self, context):
            return Vote(self.id, "approve", value)
    assert decide([Broken()], {})["decision"] == "WAIT"
    Broken.veto = True
    assert decide([Broken()], {})["decision"] == "VETO"


@pytest.mark.parametrize("value", [float("inf"), float("nan"), True, "0.9", -1, 2])
@pytest.mark.parametrize("override_vote", [False, True])
def test_mutated_confidence_revalidated_at_consumption(value, override_vote):
    class Mutating(Bot):
        id = "mutating"
        edge_seat = True
        def evaluate(self, context):
            result = Vote(self.id, "approve", .5)
            result.confidence = value
            return result
    if override_vote:
        Mutating.vote = Mutating.evaluate
    assert decide([Mutating()], {})["decision"] == "WAIT"
    Mutating.veto = True
    assert decide([Mutating()], {})["decision"] == "VETO"


class Response(io.BytesIO):
    status = 200


def test_telegram_uncertain_delivery_never_retries_or_logs_sensitive_content(capsys):
    with patch.object(telegram.urllib.request, "urlopen", side_effect=TimeoutError("secret-token")) as send:
        result = telegram.send_institutional_telegram("private body", token="secret-token", chat_id="private-chat")
    assert send.call_count == 1
    assert result == {"ok": False, "reason": "delivery_unconfirmed"}
    assert capsys.readouterr().out == ""


def test_telegram_plain_fallback_checks_api_body():
    rejection = urllib.error.HTTPError("https://inert.invalid", 400, "bad request", {},
                                      io.BytesIO(b'{"ok":false,"description":"Bad Request: can\'t parse entities"}'))
    with patch.object(telegram.urllib.request, "urlopen", side_effect=[rejection, Response(b'{"ok":false}')]) as send:
        result = telegram.send_institutional_telegram("fixture", token="fixture", chat_id="fixture")
    assert send.call_count == 2
    assert result["fallback_plain"] and not result["ok"]


@pytest.mark.parametrize("code", [400, 401, 403, 429, 500])
def test_telegram_non_parse_errors_not_retried(code):
    error = urllib.error.HTTPError("https://inert.invalid", code, "fixture", {}, io.BytesIO(b'{"ok":false}'))
    with patch.object(telegram.urllib.request, "urlopen", side_effect=error) as send:
        assert not telegram.send_institutional_telegram("fixture", token="fixture", chat_id="fixture")["ok"]
        assert send.call_count == 1


def test_telegram_config_diagnostic_contains_presence_only(capsys):
    with patch.object(telegram.urllib.request, "urlopen") as send:
        result = telegram.send_institutional_telegram("private message", token="private-token", chat_id="")
    assert result == {"ok": False, "reason": "missing_telegram_credentials", "token_present": True, "chat_id_present": False}
    send.assert_not_called()
    assert capsys.readouterr().out == ""


@pytest.mark.parametrize("timeout", [0, -1, float("inf"), float("nan"), True, 61])
def test_telegram_invalid_timeout_never_sends(timeout):
    with patch.object(telegram.urllib.request, "urlopen") as send:
        result = telegram.send_institutional_telegram("fixture", token="fixture", chat_id="fixture", timeout=timeout)
    assert result["reason"] == "invalid_timeout"
    send.assert_not_called()
