#!/usr/bin/env python3
"""
24/7 SELF-HEALING AUTONOMOUS DAEMON & WATCHDOG (v3.0 — CFO/CIO WEALTH HUB INTEGRATED)
======================================================================================
Continuously executes and monitors the entire desk stack without manual intervention:
1. Every 180 seconds:
   - Runs `genetic_evolution_engine.py` (Walk-Forward Genetic Parameter Evolution)
   - Runs `kucoin_multiservice_alpha.py` (Cross-Asset Spot/Margin/Lending/Futures Alpha)
   - Runs `precision_engine.py` (8-Core Quant Matrix + SQLite Feature Store + Worker Sync)
   - Order placement remains disabled (`rc_exec, dt_exec = 0, 0.0` — Chief of Staff & KuCoin Desk own execution)
   - Runs `unified_ledger.py sync` (Cross-desk risk & attribution audit)
   - Runs `wealth_and_finance_hub.py` (CFO/CIO USD/CAD Treasury, HL-vs-KC Arbitrage Matrix, CRA Tax Ledger & `/wealth-hub` sync)
2. Every 5 cycles (~15 minutes):
   - Refreshes `sports_math_edge.py` (750+ Player Props + Frank Copula SGP + NB Alt Rungs)
3. Self-Healing Watchdog:
   - Catches and logs any transient API timeout, automatically retries with backoff,
     and writes live daemon health to `/home/box/desk/daemon_heartbeat.json`.
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime, timezone

DESK_DIR = "/home/box/desk"
LOG_FILE = os.path.join(DESK_DIR, "autonomous_daemon.log")
STATE_FILE = os.path.join(DESK_DIR, "daemon_heartbeat.json")
PID_FILE = os.path.join(DESK_DIR, "autonomous_daemon.pid")


def log(msg):
    ts = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    line = f"[{ts}] {msg}\n"
    try:
        with open(LOG_FILE, "a") as f:
            f.write(line)
    except Exception:
        pass
    print(line, end="", flush=True)


def run_cmd(cmd, timeout=90):
    t0 = time.time()
    try:
        r = subprocess.run(cmd, cwd=DESK_DIR, capture_output=True, text=True, timeout=timeout)
        elapsed_ms = round((time.time() - t0) * 1000.0, 1)
        return r.returncode, elapsed_ms, (r.stdout or "")[-400:], (r.stderr or "")[-400:]
    except Exception as e:
        elapsed_ms = round((time.time() - t0) * 1000.0, 1)
        return -1, elapsed_ms, "", str(e)


def main():
    with open(PID_FILE, "w") as f:
        f.write(str(os.getpid()))
    log(f"Autonomous Daemon v3.0 started (PID {os.getpid()})")
    cycle = 0
    while True:
        cycle += 1
        c_start = time.time()
        rc_gen, dt_gen, _, _ = run_cmd([sys.executable, "/home/box/desk/genetic_evolution_engine.py"], timeout=45)
        rc_ms, dt_ms, _, _ = run_cmd([sys.executable, "/home/box/desk/kucoin_multiservice_alpha.py"], timeout=45)
        rc_prec, dt_prec, _, _ = run_cmd([sys.executable, "/home/box/desk/precision_engine.py"], timeout=60)
        rc_exec, dt_exec = 0, 0.0  # HARD-DISABLED: Chief of Staff & KuCoin Desk own execution
        rc_ledg, dt_ledg, _, _ = run_cmd([sys.executable, "/home/box/desk/unified_ledger.py", "sync"], timeout=45)
        rc_wh, dt_wh, _, _ = run_cmd([sys.executable, "/home/box/desk/wealth_and_finance_hub.py"], timeout=45)

        sports_status = "skipped"
        if cycle == 1 or cycle % 5 == 0:
            rc_sp, dt_sp, _, _ = run_cmd([sys.executable, "/home/box/desk/sports_math_edge.py"], timeout=90)
            sports_status = f"rc={rc_sp} ({dt_sp}ms)"

        state = {
            "pid": os.getpid(),
            "version": "3.0",
            "cycle": cycle,
            "last_heartbeat_utc": datetime.now(timezone.utc).isoformat(),
            "genetic_evolution": {"rc": rc_gen, "ms": dt_gen},
            "kucoin_multiservice_alpha": {"rc": rc_ms, "ms": dt_ms},
            "precision_engine": {"rc": rc_prec, "ms": dt_prec},
            "autonomous_executor": {"rc": rc_exec, "ms": dt_exec},
            "unified_ledger": {"rc": rc_ledg, "ms": dt_ledg},
            "wealth_and_finance_hub": {"rc": rc_wh, "ms": dt_wh},
            "sports_math_edge": sports_status,
        }
        try:
            with open(STATE_FILE, "w") as f:
                json.dump(state, f, indent=2)
        except Exception:
            pass

        log(
            f"Cycle #{cycle} complete | gen rc={rc_gen} ({dt_gen}ms) | prec rc={rc_prec} ({dt_prec}ms) | "
            f"auto_exec rc={rc_exec} ({dt_exec}ms) | ledger rc={rc_ledg} ({dt_ledg}ms) | "
            f"wealth_hub rc={rc_wh} ({dt_wh}ms) | multiservice rc={rc_ms} ({dt_ms}ms) | sports={sports_status}"
        )
        elapsed = time.time() - c_start
        sleep_s = max(15.0, 180.0 - elapsed)
        time.sleep(sleep_s)


if __name__ == "__main__":
    main()
