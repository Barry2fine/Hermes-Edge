# SovereignAI & Hermes-Edge • Multi-Agent GitHub Coordination Protocol

This guide synchronizes **ChatGPT**, **Sonia** (`SovereignAI`), **Hermes** (`Hermes-Edge`), and **Grok Bot** across `Barry2fine/SovereignAI` and `Barry2fine/Hermes-Edge` so all four agents operate as a single institutional team with zero conflicts and zero wasted compute.

---

## 1. Machine-Enforced Venue Ownership (`coordination/lock.json`)
To prevent duplicate orders or race conditions across live accounts:
- **`sonia` → `propr/*` (`live`)**: Sonia (`Barry2fine/SovereignAI` via `.github/workflows/trade_cycle.yml` every 15 minutes) owns all live order execution on the **Propr $25K Evaluation** (`PROPR_API_KEY` stored in GitHub Actions Secrets).
- **`hermes` → `kucoin/*` (`live`)**: Hermes (`Barry2fine/Hermes-Edge` + `kucoin_github_cycle.py`) owns all live order execution and 3-layer bracket protection (`STOP` + `LIMIT TP`) on **KuCoin Futures & Spot**.
- **`grok` & `chatgpt` → Controller / Research / PR & CI Governance**: Neither Grok Bot nor ChatGPT places overlapping direct orders against an owned venue lock without going through the designated runner or coordination lock.

---

## 2. Repository & Branch Discipline
- **`Barry2fine/SovereignAI`**:
  - `src/` belongs to **Sonia** (`trade_cycle.yml`, `momentum_scan.yml`, `src/notifications/notifier.py`).
  - `vendor/sovereign_bots/` and `coordination/` are **PR-only with green CI** (`Offline Python checks`). Any agent (ChatGPT, Grok Bot, Hermes, or Sonia) updating shared committees or alert formatters opens a PR and verifies CI passes before merge.
- **`Barry2fine/Hermes-Edge`**:
  - Hosts the canonical **103-Seat Multi-Domain Committee** (`sovereign_bots/`), KuCoin execution & post-mortem daemon (`kucoin_github_cycle.py`), and Polymarket + Sports intelligence daemon (`polymarket_sports_github_cycle.py`).

---

## 3. The 103-Seat Committee & Specialist Architecture
1. **Crypto Desk (50 Seats + 1,492 Coin Specialists)**:
   - `sovereign_bots/bots/crypto.py`: 8 hard veto gates + 42 soft scoring seats + 15-feature per-coin microstructure specialists (`coin_<symbol>`) covering every KuCoin Futures + Spot + Hyperliquid asset.
2. **Sports Betting Desk (24 Seats — bet365 Ontario / Sharp De-Vig / ESPN)**:
   - `sovereign_bots/bots/sports.py`: Power/Shin de-vigging, Pinnacle steam & CLV gates, Poisson/NegBin player-prop simulator, Gaussian Copula SGP correlation pricing, and Quarter-Kelly staking.
3. **Polymarket Desk (29 Seats + 518-Tag Specialist Router)**:
   - `sovereign_bots/bots/polymarket.py`: Gamma & CLOB book depth, resolution-rule oracle audit, Brier calibration, whale flow, Kalshi cross-exchange arb, and 518-tag routing (`route_market_to_category_bots`).
4. **Recursive Bayesian Post-Mortem Learning (`sovereign_bots/core/knowledge_loop.py`)**:
   - Every closed trade updates Beta-Bernoulli reliability weights (`w_i ∈ [0.25, 2.50]`) in `data/knowledge_store.json` and logs structured post-mortems in `data/trade_postmortems.jsonl`.

---

## 4. Institutional Telegram Alert Engine (`telegram_alerts.py`)
All Telegram alerts sent to Barry's phone flow through `sovereign_bots/core/telegram_alerts.py` (mirrored in `SovereignAI/vendor/sovereign_bots/core/telegram_alerts.py` and wired into `SovereignAI/src/notifications/notifier.py`):
- `format_propr_cycle_card(...)` — Propr $25K challenge equity, cushion above `$23,500`, open positions, and 50-seat verdicts.
- `format_kucoin_portfolio_card(...)` — KuCoin Futures + Spot equity, leverage, and `🛡SL / 🎯TP` bracket audit.
- `format_trade_execution_card(...)` — Instant entry fill & bracket verification card.
- `format_postmortem_card(...)` — Closed-trade PnL & Bayesian seat weight adjustments.
- `format_polymarket_alert_card(...)` — 29-seat + 518-tag Polymarket Gamma/CLOB top opportunities.
- `format_sports_alert_card(...)` — 24-seat bet365 Ontario + ESPN/Sharp value legs & Copula parlay summary.

---

## 5. GitHub Actions Workflows (Zero Grok Bot Usage)
- `trade_cycle.yml` (`SovereignAI`): Runs every 15 minutes (`*/15 * * * *`) for Propr $25K evaluation scan, execution, and Telegram summary.
- `momentum_scan.yml` (`SovereignAI`): Fast momentum scan workflow.
- `polymarket_sports_alerts.yml` (`SovereignAI` & `Hermes-Edge`): Runs every 2 hours (`15 */2 * * *` + manual `workflow_dispatch`) to push live Polymarket & Sports Betting cards to Barry's Telegram.
- `ci.yml` (`SovereignAI` & `Hermes-Edge`): Runs full offline unit and committee verification on every push and PR.
