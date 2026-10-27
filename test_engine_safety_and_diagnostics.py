#!/usr/bin/env python3
"""
Production Regression & Read-Only Diagnostic Test Suite (28 Tests)
Combines:
  - Sonia's 15 Read-Only Diagnostic Tests (`test_review.py`)
  - Sonia's 13 Targeted Engine Safety Regression Tests (`test_engine_patch.py`) run directly
    against the LIVE production `shared_desk_cli.py` and `poet_sentinel.py` (`poet_max_engine.py`).
"""
import io
import json
import multiprocessing
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
if str(ROOT / "kucoin") not in sys.path:
    sys.path.insert(0, str(ROOT / "kucoin"))

from api_contract import OPENAPI, handle
from desk_review import ALLOWED_NAMES, MUTATING_ACTIONS, OwnedLock, ReviewError, dispatch, review
import read_only_diagnostic
from safety_checks import validate_protection, validate_risk
import shared_desk_cli as cli

try:
    import poet_sentinel as sentinel
    if not hasattr(sentinel, "PoetMaxHTTPHandler"):
        import poet_max_engine as sentinel
except ImportError:
    import poet_max_engine as sentinel


def try_lock(path, results):
    try:
        with OwnedLock(Path(path)):
            results.put("acquired")
    except ReviewError:
        results.put("blocked")


class ReviewTests(unittest.TestCase):
    """Sonia's 15 Read-Only Diagnostic Tool Tests (`test_review.py`)."""

    def setUp(self):
        self.root = Path(tempfile.mkdtemp(prefix="offline-fixture-", dir=ROOT)).resolve()
        if self.root.parent != ROOT:
            raise RuntimeError("fixture outside isolated workspace")
        self.addCleanup(self.cleanup_fixture)
        self.path = self.root / "LIVE_DESK_STATE.json"
        self.good = {name: {"ok": True} for name in ("propr_status", "kucoin_status", "auto_exec")}
        self.path.write_text(json.dumps(self.good), encoding="utf-8")

    def cleanup_fixture(self):
        for path in self.root.iterdir():
            if path.is_file():
                path.unlink()
        self.root.rmdir()

    def test_fresh_snapshot_is_explicitly_not_exchange_verified(self):
        result = review(self.path)
        self.assertTrue(result["ok"])
        self.assertFalse(result["exchange_verified"])
        self.assertFalse(result["execution_enabled"])

    def test_provider_errors_are_visible_but_secrets_are_not(self):
        self.good["kucoin_status"] = {"error": "secret-token http://private.example"}
        self.good["api_key"] = "secret-token"
        self.path.write_text(json.dumps(self.good), encoding="utf-8")
        result = review(self.path)
        self.assertFalse(result["ok"])
        self.assertEqual(result["sections"]["kucoin_status"], "source_error")
        self.assertNotIn("secret-token", json.dumps(result))

    def test_missing_section_is_not_success(self):
        self.path.write_text("{}", encoding="utf-8")
        self.assertFalse(review(self.path)["ok"])

    def test_stale_file_fails_without_fallback(self):
        os.utime(self.path, (1, 1))
        self.assertEqual(review(self.path)["error"], "SNAPSHOT_STALE")

    def test_future_file_fails(self):
        future = time.time() + 100
        os.utime(self.path, (future, future))
        self.assertEqual(review(self.path)["error"], "SNAPSHOT_FUTURE_TIMESTAMP")

    def test_invalid_json_and_nonfinite_values_fail(self):
        for text in ("{", "[]", '{"x":NaN}'):
            with self.subTest(text=text):
                self.path.write_text(text, encoding="utf-8")
                self.assertFalse(review(self.path)["ok"])

    def test_env_is_never_read(self):
        with patch.object(Path, "open", side_effect=AssertionError("unexpected read")):
            self.assertEqual(review(self.root / ".env")["error"], "SNAPSHOT_NAME_NOT_ALLOWED")

    def test_all_mutations_and_dry_runs_rejected_without_effects(self):
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with patch("desk_review.review", side_effect=AssertionError("unexpected read")), patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
            for action in MUTATING_ACTIONS:
                for dry in (True, False):
                    self.assertEqual(dispatch(action, snapshot=self.path, dry_run=dry)["error"], "EXECUTION_DISABLED")
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})

    def test_status_does_not_write_or_network(self):
        before = self.path.read_bytes()
        with patch.object(socket, "socket", side_effect=AssertionError("network forbidden")):
            self.assertTrue(dispatch("status", snapshot=self.path)["ok"])
        self.assertEqual(before, self.path.read_bytes())
        self.assertEqual(len(list(self.root.iterdir())), 1)

    def test_lock_excludes_other_process_and_checks_owner(self):
        path = self.root / "review.lock"
        ctx = multiprocessing.get_context("spawn")
        results = ctx.Queue()
        with OwnedLock(path) as lock:
            with self.assertRaises(ReviewError):
                lock.release("not-owner")
            process = ctx.Process(target=try_lock, args=(str(path), results))
            process.start()
            process.join(15)
            self.assertFalse(process.is_alive())
            self.assertEqual(process.exitcode, 0)
            self.assertEqual(results.get(timeout=2), "blocked")
        with OwnedLock(path):
            pass
        self.assertTrue(path.exists())
        results.close()

    def test_lock_io_error_fails_closed(self):
        with self.assertRaises(ReviewError):
            with OwnedLock(self.root / "missing" / "lock"):
                self.fail("acquired invalid lock")

    def test_api_requires_auth_and_never_exposes_execute(self):
        token = "synthetic-test-only-" + "x" * 32
        for method in ("POST", "PUT", "DELETE"):
            code, headers, body = handle(method, "/api/execute", {}, expected_token=token, snapshot=self.path)
            self.assertEqual(code, 405)
        self.assertEqual(handle("GET", "/api/chatgpt-brief", {}, expected_token=None, snapshot=self.path)[0], 503)
        self.assertEqual(handle("GET", "/api/chatgpt-brief", {}, expected_token=token, snapshot=self.path)[0], 401)
        code, headers, body = handle("GET", "/api/chatgpt-brief", {"Authorization": "Bearer " + token}, expected_token=token, snapshot=self.path)
        self.assertEqual(code, 200)
        self.assertEqual(headers["Cache-Control"], "no-store")
        self.assertNotIn("Access-Control-Allow-Origin", headers)
        self.assertFalse(body["execution_enabled"])
        self.assertNotIn(token, json.dumps(body))

    def test_openapi_contract_has_responses_and_no_trade_paths(self):
        self.assertEqual(set(OPENAPI["paths"]), {"/api/chatgpt-brief"})
        operation = OPENAPI["paths"]["/api/chatgpt-brief"]["get"]
        self.assertTrue({"200", "401", "503"} <= set(operation["responses"]))
        self.assertTrue(OPENAPI["security"])
        json.dumps(OPENAPI, allow_nan=False)

    def test_risk_buffer_accounts_for_existing_risk_and_reserve(self):
        common = dict(equity=200, floor=170, existing_risk=10, reserve=5)
        self.assertTrue(validate_risk(**common, proposed_risk=15)["ok"])
        self.assertFalse(validate_risk(**common, proposed_risk=15.01)["ok"])
        for bad in (-1, 0, "NaN", "Infinity", "not a number"):
            self.assertFalse(validate_risk(**common, proposed_risk=bad)["ok"])

    def test_entry_success_is_insufficient_without_verified_protection(self):
        self.assertFalse(validate_protection({"entry_confirmed": True})["ok"])
        checks = {
            key: True
            for key in (
                "entry_confirmed",
                "stop_confirmed",
                "stop_reduce_only",
                "stop_covers_filled_quantity",
                "take_profit_confirmed",
                "take_profit_reduce_only",
            )
        }
        self.assertTrue(validate_protection(checks)["ok"])
        for key in checks:
            broken = dict(checks, **{key: False})
            self.assertFalse(validate_protection(broken)["ok"])


class EngineTests(unittest.TestCase):
    """Sonia's 13 Targeted Engine Safety Regression Tests (`test_engine_patch.py`) run against live production engine."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.old_lock = cli.LOCK_FILE
        self.old_audit = cli.AUDIT_LOG_FILE
        cli.LOCK_FILE = str(Path(self.tmp.name) / "shared_desk_lock.json")
        cli.AUDIT_LOG_FILE = str(Path(self.tmp.name) / "shared_desk_actions.jsonl")
        self.addCleanup(self.reset_lock)

    def reset_lock(self):
        try:
            while cli._LOCK_DEPTH:
                cli.release_lock()
        except Exception:
            pass
        cli.LOCK_FILE = self.old_lock
        cli.AUDIT_LOG_FILE = self.old_audit

    def test_original_lock_fails_closed_and_owner_cannot_be_stolen_by_ttl(self):
        with patch("builtins.open", side_effect=OSError("disk error")):
            self.assertFalse(cli.acquire_lock("a"))
        self.assertTrue(cli.acquire_lock("owner", ttl_sec=0.0))
        self.assertTrue(cli.acquire_lock("owner", ttl_sec=0.0))
        cli.release_lock()
        proc = subprocess.run(
            [
                sys.executable, "-I", "-c",
                f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
                f"sys.path.insert(0, {str(ROOT / 'kucoin')!r}); "
                f"import shared_desk_cli as c; c.LOCK_FILE={cli.LOCK_FILE!r}; "
                f"sys.exit(0 if not c.acquire_lock('thief', ttl_sec=0.0) else 1)",
            ],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)
        cli.release_lock()
        with self.assertRaises(RuntimeError):
            cli.release_lock()

    def test_all_original_mutation_functions_have_guard(self):
        self.assertTrue(cli.acquire_lock("owner"))
        proc = subprocess.run(
            [
                sys.executable, "-I", "-c",
                f"import sys; sys.path.insert(0, {str(ROOT)!r}); "
                f"sys.path.insert(0, {str(ROOT / 'kucoin')!r}); "
                f"import shared_desk_cli as c; c.LOCK_FILE={cli.LOCK_FILE!r}; "
                "res = [c.run_ratchet(execute=True), c.run_auto_trade(execute=True), "
                "c.place_propr_trade_safe('TAO','SHORT',execute=True), "
                "c.place_kucoin_trade_safe('SOLUSDTM','SHORT',1,100,110,execute=True), "
                "c.close_position_safe('propr','TAO',execute=True)]; "
                "sys.exit(0 if all(r.get('reason')=='LOCK_HELD_BY_PEER_AGENT' for r in res) else 1)",
            ],
            capture_output=True, text=True, timeout=10,
        )
        self.assertEqual(proc.returncode, 0, proc.stderr)

    def test_mutation_exception_is_observable_and_lock_released(self):
        with patch.object(cli, "ProprClient", side_effect=RuntimeError("boom")), self.assertLogs("desk_engine", level="ERROR") as logs:
            result = cli.run_ratchet(execute=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "ACTION_FAILED")
        self.assertTrue(any("MUTATION_FAILED:run_ratchet" in msg for msg in logs.output))
        self.assertIsNone(cli._LOCK_HANDLE)

    def test_actual_dry_run_close_and_other_paths_have_no_adapter_or_log_effect(self):
        with patch.object(cli, "ProprClient") as pr, patch.object(cli, "KuCoinClient") as kc, patch.object(cli, "log_action") as audit:
            calls = [
                (cli.close_position_safe, ("propr", "TAO")),
                (cli.run_ratchet, ()),
                (cli.run_auto_trade, ()),
                (cli.place_propr_trade_safe, ("TAO", "SHORT")),
                (cli.place_kucoin_trade_safe, ("SOLUSDTM", "SHORT", 1, 100, 110)),
            ]
            for fn, args in calls:
                result = fn(*args, execute=False)
                self.assertTrue(result["dry_run"])
                self.assertFalse(result["validated"])
            pr.assert_not_called()
            kc.assert_not_called()
            audit.assert_not_called()

    def propr(self, stop_return, tp_return, leverage_exc=None):
        provider = MagicMock()
        if leverage_exc:
            provider.set_leverage.side_effect = leverage_exc
        provider.create_order.side_effect = [
            [{"positionId": "pos1", "orderId": "entry1", "averageFillPrice": 250.0}],
            stop_return,
            tp_return,
        ]
        snap = {"used_slots": 0, "equity_usd": 23672.48, "positions": [], "open_risk_usd": 5.0, "floor_buffer_usd": 172.48}
        with patch.object(cli, "ProprClient", return_value=provider), patch.object(cli, "get_propr_live_snapshot", return_value=snap), patch.object(cli, "fetch_json_safe", return_value={"TAO": 250.0}):
            return cli.place_propr_trade_safe("TAO", "SHORT", 310.0, 258.0, 240.0, execute=True), provider

    def test_original_propr_entry_no_longer_success_when_stop_or_tp_missing(self):
        for stop, tp in [([], [{"orderId": "tp"}]), ([{"orderId": "sl"}], [])]:
            result, provider = self.propr(stop, tp)
            self.assertFalse(result["ok"])
            self.assertTrue(result["requires_operator_review"])
            self.assertEqual(provider.create_order.call_count, 3)

    def test_original_leverage_failure_aborts_before_entry(self):
        result, provider = self.propr([{"orderId": "sl"}], [{"orderId": "tp"}], leverage_exc=RuntimeError("bad leverage"))
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "LEVERAGE_CONFIGURATION_FAILED")
        provider.create_order.assert_not_called()

    def test_original_kucoin_buffer_gate_runs_before_order_submission(self):
        kc = MagicMock()
        snap = {
            "used_slots": 1, "equity_usdt": 194.79, "open_risk_usd": 20.0,
            "_contracts_map": {"SOLUSDTM": {"tickSize": 0.01, "multiplier": 0.1, "markPrice": 100.0}},
        }
        with patch.object(cli, "KuCoinClient", return_value=kc), patch.object(cli, "get_kucoin_live_snapshot", return_value=snap):
            result = cli.place_kucoin_trade_safe("SOLUSDTM", "SHORT", 32, 100.0, 102.0, execute=True)
        self.assertFalse(result["ok"])
        self.assertEqual(result["error"], "INSUFFICIENT_RISK_BUFFER")
        kc.place_futures_order.assert_not_called()

    def test_original_kucoin_missing_stop_is_failure(self):
        kc = MagicMock()
        kc.place_futures_order.return_value = {"data": {"orderId": "entry1"}}
        kc.place_futures_stop_order.return_value = {"data": {}}
        snap = {
            "used_slots": 1, "equity_usdt": 250.0, "open_risk_usd": 2.0,
            "_contracts_map": {"SOLUSDTM": {"tickSize": 0.01, "multiplier": 0.1, "markPrice": 100.0}},
        }
        with patch.object(cli, "KuCoinClient", return_value=kc), patch.object(cli, "get_kucoin_live_snapshot", return_value=snap):
            result = cli.place_kucoin_trade_safe("SOLUSDTM", "SHORT", 10, 100.0, 102.0, execute=True)
        self.assertFalse(result["ok"])
        self.assertTrue(result["requires_operator_review"])

    def test_original_stale_fallback_removed(self):
        stale = Path(self.tmp.name) / "stale.json"
        stale.write_text('{"active_genome":{"tp_r_mult":9.9}}', encoding="utf-8")
        old = time.time() - 1000
        os.utime(stale, (old, old))
        with patch.object(cli, "fetch_json_safe", return_value={"error": "timeout"}):
            with self.assertRaises(RuntimeError):
                cli.load_local_or_remote_json([str(stale)], "https://invalid.local", max_age_sec=10)

    def post(self, payload, content_type="application/json", length=None):
        raw = payload if isinstance(payload, bytes) else json.dumps(payload).encode("utf-8")
        handler = sentinel.PoetMaxHTTPHandler.__new__(sentinel.PoetMaxHTTPHandler)
        handler.path = "/api/execute"
        handler.headers = {"Content-Length": str(len(raw) if length is None else length), "Content-Type": content_type}
        handler.rfile = io.BytesIO(raw)
        handler.wfile = io.BytesIO()
        status_holder = {}
        handler.send_response = lambda code: status_holder.setdefault("code", code)
        handler.send_header = lambda k, v: None
        handler.end_headers = lambda: None
        handler.do_POST()
        return status_holder["code"], json.loads(handler.wfile.getvalue().decode("utf-8"))

    def test_original_http_schema_rejects_nonboolean_dryrun_and_oversize(self):
        status, body = self.post({"action": "ratchet", "dry_run": "false"})
        self.assertEqual(status, 400)
        self.assertEqual(body["error"], "INVALID_DRY_RUN")
        status, body = self.post({"action": "ratchet", "dry_run": False})
        self.assertEqual(status, 403)
        self.assertEqual(body["error"], "EXECUTION_DISABLED_IN_REVIEW_CANDIDATE")
        status, _ = self.post({"action": "status", "dry_run": True}, length=5000)
        self.assertEqual(status, 400)

    def test_original_http_close_dry_run_never_dispatches(self):
        with patch.object(cli, "ProprClient") as pr:
            status, body = self.post({"action": "close", "venue": "propr", "symbol": "TAO", "dry_run": True})
        self.assertEqual(status, 200)
        self.assertTrue(body["dry_run"])
        self.assertFalse(body["validated"])
        pr.assert_not_called()

    def test_original_handler_openapi_has_request_and_response_schema(self):
        handler = sentinel.PoetMaxHTTPHandler.__new__(sentinel.PoetMaxHTTPHandler)
        handler.path = "/openapi.json"
        handler.wfile = io.BytesIO()
        handler.send_response = lambda code: None
        handler.send_header = lambda k, v: None
        handler.end_headers = lambda: None
        handler.do_GET()
        spec = json.loads(handler.wfile.getvalue().decode("utf-8"))
        for methods in spec["paths"].values():
            for op in methods.values():
                self.assertIn("responses", op)
        self.assertIn("requestBody", spec["paths"]["/api/execute"]["post"])

    def test_read_only_diagnostic_rejects_all_trading_commands(self):
        for action in MUTATING_ACTIONS:
            res = read_only_diagnostic.dispatch(action, dry_run=True)
            self.assertFalse(res["ok"])
            self.assertEqual(res["error"], "EXECUTION_DISABLED")


if __name__ == "__main__":
    unittest.main(verbosity=2)
