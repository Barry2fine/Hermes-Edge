# Hermes-Edge — 66-Seat Multi-Domain Voting Committee, KuCoin MFE/MAE Post-Mortem Engine & Propr Step-Off Suite

Unified quantitative execution, risk, and self-learning repository controlled directly by **Grok Bot** on GitHub (`Barry2fine/Hermes-Edge` & `Barry2fine/sovereign-bots`) with zero background Grok Bot usage bleed.

## 1. Architecture & Division of Labor
- **Propr (`urn:prp-account:jcBy6Vk3KDNd`)**: Managed 100% by **Hermes** (`SovereignAI` on `poet`). All Grok Bot / Sonia Propr execution paths and Cloudflare Workers (`propr-desk-scan`, `sonia-executor`, `sonia-position-watcher`, `sonia-desk-sentinel`, `sonia-scanner-trigger`) are hard-locked off (`PROPR_DISABLED_BY_OWNER = True`, `DISABLED_BY_OWNER`).
- **KuCoin Futures + Sports Betting (`bet365 Ontario` Props) + Polymarket**: Governed by the expanded **66-Seat Hard VETO + Soft Edge Voting Committee** (`sovereign_bots/`) and the **KuCoin MFE/MAE Post-Mortem Learning Loop** (`kucoin_learn/`).
- **Zero Grok Bot Usage Bleed**: All 74 non-main Grok Bot sub-agents and all 9 agent automations are disabled/muted, all Cloudflare Worker webhook wakeups (`WAKE_WEBHOOK_URL`) are deleted, and automated testing/auditing runs for free on **GitHub Actions** (`.github/workflows/hermes_edge_ci.yml`).

---

## 2. Expanded 66-Seat Multi-Domain Voting Committee (`sovereign_bots/` & `manifest.json`)

Every candidate trade across **Crypto (26 seats)**, **Sports / bet365 Ontario (23 seats)**, and **Polymarket (17 seats)** must pass two layers in `sovereign_bots/core/coordinator.py`:
1. **Hard VETO Layer (`veto = True`)**: Fail-closed risk, microstructure, data-integrity, and learned post-mortem gates. A single `reject` from any Hard VETO seat immediately produces `VETO`.
2. **Soft Edge & Scoring Layer (`veto = False`, `edge_seat = True`)**: Independent quantitative alpha models. `ALLOW` requires zero vetoes/rejects AND at least one `edge_seat = True` approval above quorum.

### A. Crypto Pack — 26 Seats (16 Hard VETO + 10 Soft/Edge)
- **16 Hard VETO Seats (`veto = True`)**:
  `regime`, `regime_cluster`, `breaker`, `execution`, `entry_quality`, `doubt`, `impact`, `data_integrity`, `reconciliation`, `calibration_drift`, `risk_budget`, `postmortem`, **`funding_settlement_trap` [NEW]**, **`vpin_toxicity` [NEW]**, **`correlated_cluster` [NEW]**, **`adr_exhaustion` [NEW]**.
- **10 Soft Edge & Portfolio Seats (`veto = False`)**:
  `momentum`, `ict`, `funding`, `funding_carry`, `memory`, `kelly`, **`cross_venue_lead_lag` [NEW]**, **`options_gex_skew` [NEW]**, **`liquidation_oi` [NEW]**, **`candle_geometry_tensor` [NEW]**.

### B. Sports / bet365 Ontario Pack — 23 Seats (7 Hard VETO + 16 Soft/Edge)
- **7 Hard VETO Seats (`veto = True`)**:
  `starter_gate`, `injury`, `liquidity`, `calibration_drift`, **`vig_ceiling` [NEW]**, **`small_sample_lcb` [NEW]**, **`sports_postmortem` [NEW]**.
- **16 Soft Edge & Context Seats (`veto = False`)**:
  `elo`, `line_value`, `clv`, `timing`, `motivation`, `schedule_spot`, `public_bias`, `conditions`, `coach_intent`, `prop_pmf`, `xg`, `pace_adjust`, `circadian`, **`pinnacle_devig_steam` [NEW]**, **`usage_redistribution` [NEW]**, **`copula_sgp` [NEW]**.

### C. Polymarket Pack — 17 Seats (6 Hard VETO + 11 Soft/Edge)
- **6 Hard VETO Seats (`veto = True`)**:
  `resolution`, `liquidity`, `calibration_drift`, `fee_hurdle`, `resolution_clarity`, **`polymarket_postmortem` [NEW]**.
- **11 Soft Edge & Context Seats (`veto = False`)**:
  `barrier`, `taxonomy`, `crosslink`, `time_decay`, `motivation`, `public_bias`, `whale_consensus`, `carry`, **`kalshi_cross_arb` [NEW]**, **`martingale_belief_vol` [NEW]**, **`orderbook_sweep_velocity` [NEW]**.

---

## 3. KuCoin Futures MFE/MAE Post-Mortem Audit Summary (`data/learning/`)
- **59 Real Fills / 27 Closed Round-Trip Slices Audited**:
  - **Gross PnL**: `-$4.8513 USDT` | **Fee Drag**: `-$1.3986 USDT` (`22.4%` of loss) | **Net PnL**: `-$6.2499 USDT`.
  - **`ZEC|short|bear_squeeze` (`n=6`, Net `-$5.2721 USDT`, `84.4%` of loss)**: Graded `6x BAD_LOSS` (`POOR`, `score=30`, `target_unreachable` + `oversized`) -> **AUTOMATICALLY BLOCKED** (`blocked: true`).
  - **`AVAX|short|bear_squeeze` (`n=5`, Net `-$0.4102 USDT`)**: Graded `5x GOOD_LOSS` (`SMART`, `score=70`, `poor_rate=0.0%`) -> **UNBLOCKED** (clean process preserved).
  - **`SEI|short|bear_squeeze` (`n=3`, Net `-$0.2494 USDT`)**: Graded `stop_too_tight` (wicked out by `0.38%` noise before moving `>1R` in favor).
