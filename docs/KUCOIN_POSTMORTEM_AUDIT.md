# KuCoin Futures MFE/MAE Post-Mortem Audit (`$198.00` -> `$192.97`)

Using the exact MFE/MAE Post-Trade Decision Audit engine from `SovereignAI/src/learn/postmortem.py` (`elbrujo325/atr-tp-analysis`), we reconstructed and graded all **59 KuCoin Futures fills** (**27 closed round-trip slices**) across 5-minute candle paths (`get_futures_kline`).

## 1. Summary Metrics
- **Raw Fills Audited**: `59` (`39` Maker @ `2 bps`, `20` Taker @ `6 bps`)
- **Closed Round-Trip Slices Graded**: `27`
- **Gross Realized PnL**: `-$4.8513 USDT`
- **Total Exchange Fee Drag**: `-$1.3986 USDT` (`22.4%` of total net drawdown)
- **Net Realized PnL**: `-$6.2499 USDT`
- **Process Quality Breakdown**:
  - `smart_rate`: `33.33%` (`9/27` closes graded `SMART`: `4` `EARNED_WIN` + `5` `GOOD_LOSS`)
  - `poor_rate`: `25.93%` (`7/27` closes graded `POOR`: `6` `ZEC` shorts + `1` oversized `SOL` short)
  - `good_losses`: `5` (`AVAX` shorts — clean unchased entries within risk budget that moved `> 0.5R` in favor before reversing cleanly to stop; **never punished**)
  - `lucky_wins`: `0`
- **Error Tag Frequencies**:
  - `target_unreachable`: `12` (`mfe_r < 0.5 * rr_planned` and `mfe_r < 1.0`)
  - `oversized`: `7` (`position_risk_usd > 1.05 * $1.98` budget)
  - `premature_exit`: `3` (`mfe_r >= target` but `capture_ratio < 0.50` on `LINK` and `TAO`)
  - `stop_too_tight`: `3` (`SEI` stopped out on a `$0.0002` / `0.38%` wick before dropping `> 1R` in the planned short direction)

## 2. Coin-by-Coin Net Attribution & Honest Labels

| Coin | Closes (`n`) | Gross PnL (USDT) | Fee Drag (USDT) | Net PnL (USDT) | Honest Labels | Process Tags | Pattern Memory Status |
| :--- | :---: | :---: | :---: | :---: | :--- | :--- | :--- |
| **ZEC** | 6 | `-$4.9468` | `$0.3253` | **`-$5.2721`** | `6x BAD_LOSS` | `target_unreachable`, `oversized` | **`BLOCKED`** (`n=6, poor_rate=100%`) |
| **SOL** | 2 | `-$0.2318` | `$0.3221` | **`-$0.5539`** | `1x BAD_LOSS`, `1x EARNED_WIN` | `target_unreachable`, `oversized` | Active (`n=2, poor_rate=50%`) |
| **AVAX** | 5 | `-$0.3061` | `$0.1041` | **`-$0.4102`** | `5x GOOD_LOSS` | `[]` (Clean process) | Active (`n=5, smart=5, poor=0%`) |
| **SUI** | 1 | `-$0.3696` | `$0.0247` | **`-$0.3943`** | `1x MIXED_LOSS` | `target_unreachable` | Active (`n=1`) |
| **TAO** | 3 | `-$0.2064` | `$0.1379` | **`-$0.3443`** | `1x MIXED_WIN`, `2x MIXED_LOSS` | `target_unreachable`, `premature_exit` | Active (`n=3`) |
| **XRP** | 3 | `-$0.1102` | `$0.2132` | **`-$0.3234`** | `2x EARNED_WIN`, `1x MIXED_LOSS` | `target_unreachable` | Active (`n=3, smart=2`) |
| **SEI** | 3 | `-$0.2050` | `$0.0444` | **`-$0.2494`** | `3x MIXED_LOSS` | `stop_too_tight` | Active (`n=3`) |
| **DOGE** | 3 | `-$0.0240` | `$0.1452` | **`-$0.1692`** | `1x EARNED_WIN`, `2x MIXED_LOSS` | `target_unreachable` | Active (`n=3, smart=1`) |
| **LINK** | 1 | `+$1.5486` | `$0.0817` | **`+$1.4669`** | `1x MIXED_WIN` | `premature_exit` | Active (`n=1`) |

## 3. Seat Credit Scorecard & Policy Hints
- **Seat Credit (`data/learning/seat_credit.json`)**:
  - `risk_budget`: **`+16.0`** (correctly rejected oversized ZEC & SOL entries and approved in-budget SMART entries)
  - `fee_drag_gate`: **`+2.0`**
  - `momentum`: **`+2.0`**
  - `squeeze_gate`: **`-2.0`**
- **Concrete Policy Hints (`credit.policy_hints()`)**:
  1. `"targets rarely approached: shorten the first target or require a fresher breakout"`
  2. `"sizing exceeded budget: re-check the governor cap before entry"`
