"""Explicit repair suites; providers inert before imports, network/processes blocked."""
import contextlib
import importlib.util
import os
from pathlib import Path
import socket
import subprocess
import sys
import types
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]

def blocked(*args, **kwargs):
    raise AssertionError("external activity forbidden in offline tests")

def load(name, relative):
    spec = importlib.util.spec_from_file_location(name, ROOT / relative)
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    # Production imports add desk paths; restore checkout precedence immediately.
    sys.path.insert(0, str(ROOT))
    return module

def main():
    sys.dont_write_bytecode = True
    sys.path.insert(0, str(ROOT))
    providers = {}
    for name, cls in (("kucoin_client", "KuCoinClient"), ("propr_sdk", "ProprClient")):
        module = types.ModuleType(name)
        setattr(module, cls, blocked)
        module.FUTURES_BASE_URL = "https://inert.invalid"
        providers[name] = module
    transport = types.ModuleType("sovereign_bots.core.telegram_alerts")
    transport.send_institutional_telegram = blocked
    providers["sovereign_bots.core.telegram_alerts"] = transport
    providers["profit_gates"] = types.ModuleType("profit_gates")
    with contextlib.ExitStack() as stack:
        stack.enter_context(patch.dict(sys.modules, providers))
        for owner, names in [(socket, ["socket", "create_connection", "getaddrinfo"]),
                             (subprocess, ["Popen", "run", "call", "check_call", "check_output"]),
                             (os, ["system", "popen", "startfile", "fork", "forkpty"])]:
            for name in names:
                if hasattr(owner, name): stack.enter_context(patch.object(owner, name, blocked))
        for name in dir(os):
            if name.startswith(("spawn", "exec")) and callable(getattr(os, name)):
                stack.enter_context(patch.object(os, name, blocked))
        # Full modules, explicitly from this checkout, never from a running desk.
        for name in ("unified_ledger", "autonomous_executor", "shared_desk_cli", "kucoin_postmortem_engine"):
            load(name, name + ".py")
        suite = unittest.TestSuite()
        for name, relative in (("repair_cycle_tests", "tests/test_alert_cycle_evidence.py"),
                               ("repair_postmortem_tests", "test_kucoin_postmortem.py")):
            module = load(name, relative)
            suite.addTests(unittest.defaultTestLoader.loadTestsFromModule(module))
        return 0 if unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful() else 1

if __name__ == "__main__":
    raise SystemExit(main())
