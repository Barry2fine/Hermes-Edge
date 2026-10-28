#!/usr/bin/env python3
"""
GitHub Actions Entrypoint for the Three Institutional Autonomous Alpha Daemons:
  1. Pre-Tipoff Official Scratch & Teammate Spillover Monitor (NBA/NHL)
  2. Cross-Exchange Funding & Basis Harvester Scanner (KuCoin vs. Hyperliquid)
  3. Nightly Post-Settlement CLV, Brier & Feature-Ablation Grader
"""
from __future__ import annotations

import html
import json
import os
from typing import Any, Dict

from sovereign_bots.core.autonomous_alpha_daemons import (
    run_nightly_clv_brier_ablation_audit,
    scan_live_espn_scratches_and_spillovers,
    scan_live_kucoin_hyperliquid_funding_arb,
)
from sovereign_bots.core.telegram_alerts import send_institutional_telegram


def build_autonomous_alpha_report() -> Dict[str, Any]:
    scratch_scan = scan_live_espn_scratches_and_spillovers()
    funding_scan = scan_live_kucoin_hyperliquid_funding_arb(top_n=5)
    nightly_audit = run_nightly_clv_brier_ablation_audit(persist=True)
    return {
        "pre_tipoff_monitor": scratch_scan,
        "funding_harvester": funding_scan,
        "calibration_grader": nightly_audit,
    }


def format_telegram_summary(report: Dict[str, Any]) -> str:
    pre = report["pre_tipoff_monitor"]
    fund = report["funding_harvester"]
    cal = report["calibration_grader"].get("calibration") or {}
    weights = report["calibration_grader"].get("feature_ablation_weights") or {}

    lines = [
        "<b>Autonomous Alpha Daemons • Multi-Desk Cycle</b>",
        f"• <b>Pre-Tipoff Monitor:</b> {pre['games_inspected']} games inspected ({pre['status']})",
        f"• <b>Cross-Venue Funding Harvester:</b> {fund['kucoin_contracts_scanned']} KuCoin / "
        f"{fund['hyperliquid_coins_mapped']} HL perps -> {fund['opportunities_found']} actionable setups",
    ]
    for opp in fund.get("top_opportunities", [])[:3]:
        lines.append(
            f"  ◦ <code>{html.escape(opp['symbol'])}</code> [{opp['regime']}]: "
            f"KC 8h={opp['kucoin_funding_8h_bps']:+.1f}bp, Basis={opp['basis_bps']:+.1f}bp, "
            f"Edge={opp['expected_8h_edge_bps']:+.1f}bp ({opp['recommended_kucoin_side'].upper()})"
        )
    if cal.get("brier_model") is not None:
        bss_pct = float(cal.get("brier_skill_score") or 0.0) * 100.0
        lines.append(
            f"• <b>Walk-Forward Calibration:</b> Brier={cal['brier_model']:.4f} "
            f"(BSS={bss_pct:+.2f}%), LogLoss={cal['log_loss_model']:.4f}, "
            f"CLV={cal['mean_clv_bps']:+.1f}bp"
        )
    if weights:
        w_str = ", ".join(f"{k}={v:.2f}" for k, v in sorted(weights.items()))
        lines.append(f"• <b>Ablation Weights:</b> <code>{html.escape(w_str)}</code>")
    return "\n".join(lines)


def main() -> int:
    report = build_autonomous_alpha_report()
    print(json.dumps(report, indent=2))
    send = os.environ.get("SEND_TELEGRAM", "0").strip().lower() not in ("0", "false", "no", "off", "")
    if send and os.environ.get("TELEGRAM_BOT_TOKEN") and os.environ.get("TELEGRAM_CHAT_ID"):
        card = format_telegram_summary(report)
        send_institutional_telegram(card)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
