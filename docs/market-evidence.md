# Market-specific evidence pipeline

Contract version: `market-evidence.v1`, implemented in `sovereign_bots/core/market_evidence.py`.

These are minimum observation contracts for existing adapters and calculations. They do not claim every strategy variable is covered. Profile selection chooses relevant evidence; it does not count all registered domains as mandatory, confer committee approval, or grant execution permission.

## Profiles and applicability

| Profile | Required evidence groups | Optional groups | Grounding |
| --- | --- | --- | --- |
| `crypto.spot` | price | tape | crypto data-integrity price/tape inputs |
| `crypto.perpetual` | price, funding rate and interval | tape, funding schedule, reference price, turnover | data-integrity, funding-settlement gate, existing funding/basis scanner |
| `sports.moneyline` | two-way team odds | lineup, probability model | existing NBA/NHL moneyline adapter; starter/injury modules explicitly distinguish team markets |
| `sports.player_prop` | player distribution, lineup | none | count PMF, starter gate and injury gate |
| `polymarket.binary` | YES/NO prices, liquidity/volume, resolution | probability model | existing binary outcome mapping, liquidity, fee and resolution modules |

All other known groups are `not_applicable`. A supplied cross-market group is `unsupported` and blocks the assessment. Required groups cannot be exempted by a request. Unknown profiles, mismatched market kind/domain, unknown fields, and applicability override keys fail closed. Sports moneyline currently means two-way NBA/NHL outcomes; three-way or draw markets need a distinct adapter/profile. Count props support integer and half-integer lines only.

Each request has exactly `schema_version`, `profile`, `market`, and `evidence`. `market` includes domain, kind and nonempty market_id. Each evidence group contains market_id, source, observed_at, received_at, time_basis, values and units. Values have strict types and named units from the registry; booleans are not accepted as numbers. Source identity is carried through, not authenticated by this local validator. Both timestamps require timezone offsets and valid chronology.

`time_basis=source` means a supplied provider timestamp passed format/age checks. `time_basis=received` explicitly measures collection freshness only; it does not establish exchange quote freshness. Gamma `updatedAt` is market-record metadata, and optional ESPN `lastUpdated` is retained when supplied; their equivalence to a quote event timestamp is not asserted. Missing provider times are never fabricated. Raw source metadata remains visible in the result.

Freshness budgets: 60 seconds for crypto price/tape/funding/reference/turnover and binary price/liquidity; 300 seconds for sports/model observations; 3,600 seconds for resolution text/horizon. Crypto's 60 seconds follows the existing data-integrity limit. The other budgets are explicit conservative ingestion policy, not empirically calibrated market horizons. Change them through reviewed code, not per-message exemptions.

Results use the same top-level schema: profile, market, status (`READY`, `BLOCKED`, `UNSUPPORTED`), requirements, coverage, calculations and reasons. Requirement rows distinguish required/optional/not_applicable from data status available/missing/stale/malformed/unsupported. Optional evidence may be absent; supplied stale or malformed evidence is not silently ignored. All results retain `decision=ABSTAIN`, `committee_evaluated=false`, `execution_allowed=false`. `READY` means the supported observation contract is complete, not that a trade is safe or profitable.

## Actual caller integration

| Runtime caller | Acquisition -> validation -> calculation -> reporting |
| --- | --- |
| `polymarket_sports_github_cycle.main` | Dispatches explicit binary/moneyline profiles; reads existing Gamma/ESPN responses, maps labels/prices/odds with source metadata, validates, computes binary price sums or two-way implied probabilities/overround, and includes coverage and missing/stale reasons in the actual Telegram card path. Original raw observations may remain visible when blocked, but no gated calculations or approval are produced. |
| `autonomous_alpha_github_cycle.main` / funding scanner | Validates KuCoin and Hyperliquid perpetual observations before the existing research calculator. KuCoin rate normalization uses its supplied funding interval instead of assuming eight hours; missing intervals block. This research adapter only accepts USDT-quoted KuCoin contracts, preserves quote currency, and does not invent FX conversion. Hyperliquid's existing hourly-rate basis is retained. Reports identify research assumptions and ABSTAIN; failed Telegram delivery exits nonzero. |
| Autonomous injury scanner | Real injury acquisition now runs through the same injected transport seam as production. Only OUT is called confirmed; doubtful is not silently confirmed. Missing spreads remain missing instead of defaulting to 4.5. Bulletins expose incomplete player-prop contracts because they do not supply starter confirmation, offered prop line or projection; no distribution/edge is invented. Position-based archetype routing is labeled heuristic. |
| `kucoin_github_cycle.run_cycle` | Existing allMids collection produces explicit perpetual evidence assessments; absent funding blocks full profile completeness. Raw sample price changes are separately labeled descriptive telemetry and checked for finite results. Context-source HTTP success is labeled acquired/unvalidated, not LIVE proof. The existing account card shows evidence coverage and missing funding. The paper (`execute=False`) path no longer synchronizes a live worker, and the meaningless learning-store heartbeat is removed. |
| `python -m sovereign_bots.cli assess-market --context FILE` | Reads the versioned evidence file, validates profile/fields/metadata, executes the existing supported calculations and prints strict JSON. Exit 0 means observation-ready; exit 2 means blocked/unsupported, including malformed files. This supports spot and count-prop evaluation without pretending there is a live feed adapter. |

The KuCoin field names are grounded in the [official futures symbol response](https://www.kucoin.com/docs-new/rest/futures-trading/market-data/get-all-symbols), including currentFundingRateGranularity/fundingRateGranularity. The original scanner's turnover threshold parameter is historically named USD; this adapter supplies nominal USDT turnover and documents the currency rather than claiming an exact USD conversion.

## Intentionally not migrated

- `sovereign_bots.cli decide`, direct `Bot.evaluate` calls and legacy order runners (`autonomous_executor.py`, `shared_desk_cli.py`, `kucoin_multiservice_alpha.py`, sentinel/precision engines) retain their existing contracts. This patch adds no execution permission and does not silently disable or reroute existing live strategies. Migrating those paths requires explicit account/order-specific inputs and reconciliation tests; do not treat an observation READY as a substitute for their risk, fee, bracket, ownership or sizing gates.
- There is no new live player-prop projection/odds adapter or spot ticker adapter. `scan_market` rejects those unsupported acquisition profiles before network access; their file-ingestion calculation paths are real and tested. Injury data alone cannot fill their missing model inputs.
- Existing bootstrap priors/weights and heuristic funding/basis coefficients remain unverified, documented in `reliability-review.md`. No replacement datasets, profits, training results or committee votes were generated.
- Provider identity, the semantics of optional provider timestamps, live service health, exchange account state and profitability are not verified by mocks. Hosted Linux CI must run the POSIX fork regression skipped on this Windows host.

## Offline integration coverage

`tests/test_market_evidence.py` covers all profile schemas, spot/perpetual differences, team/player differences, missing funding/lineup, unknown profiles, cross-market evidence, stale/future/malformed timestamps, unit and numeric errors, unsupported quarter-lines, derived overflow, strict JSON, actual changing-input calculations, file ingestion, scheduled public/alpha entrypoints, and the KuCoin paper reporting cycle. Provider calls are mocked below the adapters; no live credentials, messages, orders or deployments are used.
