# Hermes Edge reliability review

Reviewed base: `6116d1b2cf75d6e28bd9c3d6f3cff0202f866ecc` on `main`.
Working branch: `codex/hermes-reliability`.

## Verified defects and changes

- Sports postmortem replay derived model probabilities from book odds plus fixed edge shifts, closing prices from win/loss outcomes, and ROI from fixed win/loss constants. These are no longer accepted as measured evidence. Legacy pick-only records return `EVIDENCE_UNAVAILABLE`, do not update learning state, and report missing/invalid evidence counts.
- Replay requires an observation ID, market family, model version, source, original probabilities, realized return, timezone-aware prediction/closing/start/settlement times, and the model's explicit maximum prediction age. Chronology and freshness are validated. This schema cannot authenticate the truth of a source; producers still need an auditable original prediction ledger.
- Replay receipts and learning changes commit together. Identical observation IDs are counted once; conflicting evidence aborts the whole transaction. Different events must have different stable observation IDs even if their pick labels match.
- Knowledge-store writes use a same-directory temporary file, flush/fsync, and atomic replacement. Read-modify-write operations serialize across threads and processes using bounded advisory OS locks. A process crash releases its lock. Corrupt state is preserved and raises an error instead of silently being replaced with defaults.
- Calibration no longer invents missing comparison prices, double-counts observations in the last ECE bin, or labels every sample below 20 observations calibrated. Brier scores use original probabilities; only logarithms are clipped. Zero market Brier has undefined skill score (`null`). The remaining `calibrated` flag is a descriptive threshold check, not a statistical certification.
- Nightly ablation no longer writes weights derived from hardcoded example cohorts. It reports `HELD_OUT_FEATURE_EVIDENCE_UNAVAILABLE`. The standalone feature-search helper remains available for explicitly supplied research data and is not a held-out certification.
- Invalid/nonfinite committee confidence raises inside evaluation, producing abstention for ordinary seats and rejection for veto seats.
- Telegram sends at most one normal request and one plain-text fallback, only after an explicit HTML parsing rejection. Timeouts and ambiguous failures return `delivery_unconfirmed` without retransmission. Both paths inspect Telegram's `ok` field. Configuration diagnostics expose presence booleans only; message content, credentials, response descriptions, and exception text are not logged or returned. Timeout is finite and bounded, and response reads are capped. Exactly-once Telegram delivery is not claimed.
- The pytest committee suite denies socket connections during collection and execution. CI adds Windows beside Ubuntu for platform-specific lock coverage. `tzdata` supports named timezones on Windows.

## Reproduce offline checks

Use an isolated environment with no production credentials. The suite uses synthetic fixtures and temporary state.

```text
python -m venv .venv
python -m pip install -r requirements.txt pytest
python -m pytest -q tests/
python -B tests/run_offline_repairs.py
python test_engine_safety_and_diagnostics.py -v
```

Run commands with the virtual environment's interpreter or activate it first. On this Windows host the interpreter is `.venv\Scripts\python`. The sandbox's default temporary directory was inaccessible; tests were rerun with `TEMP` and `TMP` set to `C:\Users\Simpl\AppData\Local\Temp` and a fresh `--basetemp` under the task workspace. That was an environment issue, not a product failure.

## State recovery and limits

All cooperating writers must use `knowledge_transaction`. Calling `load_knowledge_state` and later `save_knowledge_state` with a stale snapshot is not a transaction. Locks are local and advisory; independent hosts/network filesystems need a transactional shared store. Lock acquisition is bounded (five seconds per local/OS stage); lock files remain on disk and must not be deleted to steal an active lock. Process crash recovery is covered, not power-loss durability on every filesystem.

On invalid JSON/schema or changed receipt evidence, preserve the original file. Stop writers, inspect/reconcile the source evidence, and restore a verified backup or explicitly migrated state. This change does not fabricate a backup or silently reset learned values. It also does not migrate previously persisted synthetic weights. Existing bootstrap defaults and checked-in learning values still need a provenance audit before being treated as empirical history.

## Remaining engineering work

- Capture actual pre-event model, market, closing and settlement evidence at producer boundaries; existing historical tickets cannot be retroactively certified. Integrate authenticated provenance and held-out feature cohorts before restoring automatic ablation updates.
- Audit every committee seat's evidence and all bootstrap/default statistics. Valid vote confidence alone does not prove an economically real edge or eliminate leakage.
- Audit acquisition freshness, retry budgets, adapter reconciliation and order idempotency throughout the other KuCoin, Sports and Polymarket paths. This patch does not certify the entire execution stack.
- Reconcile documentation conflicts: README/AGENTS describe different seat counts and venue owners; the current KuCoin workflow is advisory-only. Do not infer execution authority from old prose.
- Verify live services only through a separately scoped, non-mutating diagnostic session. No exchange orders, bets, Telegram messages, deployments, merges, credentials or security changes were performed here.
- Independent review was completed with reproduced findings fixed (see review record below). Hosted CI for this patch remains pending; local passing tests are not a full domain-coverage or live-reliability guarantee.

## Baseline CI evidence

GitHub's public API reported both base-SHA suites successful:

- [Committee CI](https://github.com/Barry2fine/Hermes-Edge/actions/runs/40825981770)
- [Safety and postmortem CI](https://github.com/Barry2fine/Hermes-Edge/actions/runs/40825981777)

These runs validate the base revision only. No branch was pushed and no PR was published.

## Bootstrap provenance inspection

The repository history establishes where the numbers entered the code, not that they were empirically measured:

- Commit `aa66eec` introduced `knowledge_loop.default_state` with 12 trades, 0.58 win rate, +0.42R EMA and +6.5bp CLV for every seeded coin. The current checked-in store has 16 coin entries, all with `trades_n = 12`, and contains no observation IDs, dataset references or per-entry provenance fields.
- Ten category defaults claim 15–35 resolved observations and category-specific Brier/CLV values. The checked-in category entries have no source-ledger references, model version, evaluation window or prediction timestamps. Their values are configuration/bootstrap assumptions of unverified provenance, not validated forward history.
- Six sports defaults contain 8–20 settled observations and non-neutral weights. Current persisted counts range from 8 to 22 after the old replay. The available sports ledger contains one record with eight legs whose fields are only `actual`, `hit` and `pick`; it lacks original model/market/closing probabilities and therefore cannot establish the stored Brier/CLV or weight adjustments.
- The repository also has 27 KuCoin postmortem rows and a raw fills file. Those may support specific trade-accounting analyses, but no mapping was found tying them to the uniform 16-coin bootstrap counts, category calibration, or original prediction probabilities. They do not validate those claims by association.
- Commit `4f6192a` added persisted feature-ablation output alongside the hardcoded cohorts in the nightly audit. Those cohorts are removed from the automatic update path in this patch. Existing stored weights remain preserved, not certified or silently replaced.

The safe implementation repairs here prevent unsupported replay/ablation updates and expose missing evidence. Recovering actual empirical parameters is a separate data task requiring original timestamped prediction/settlement records and a verified mapping. Do not substitute zeroed statistics, synthetic outcomes, or guessed weights and present that as historical reconstruction.

Consumer impact remains material: `crypto/coin_specialists.py` uses stored MWU weights in confidence and reports EMA R as learned; `polymarket/category_specialists.py` uses priors, edge hurdles and MWU weights and displays Brier/CLV values. A provenance-aware consumer/migration change is still needed before those legacy fields can be presented as verified learning. This patch is a reliability repair, not empirical certification of those specialists.

## Independent review record

A separate reviewer inspected actual changes and reproduced malformed-confidence mutation and nested-transaction lost-update bugs. Those are fixed with validation at Bot/coordinator consumption boundaries and explicit same-path transaction nesting rejection. The reviewer also identified fork inheritance risk: child lock registries/depths reset after fork and PID checks prevent an inherited context from unlocking its parent's lock or committing its transaction. The POSIX regression is included but skipped on Windows.

Subsequent integration reviews reproduced derived calculation overflow, unsupported quarter-line prop push math, and overflow in raw stream telemetry. Each was fixed with a regression. The final independent focused pass reported 94 passed / 1 POSIX skip and no remaining reproducible blockers in the reviewed paths. An earlier review pass encountered a transient Windows PermissionError during atomic replacement; a fresh-directory rerun passed. No locking overlap was established. That error remained observable and was not silently treated as a successful commit; transient host file-access behavior remains a qualification.

See `market-evidence.md` for the integrated market contracts, caller matrix, unsupported acquisition paths and execution migration boundaries. Nothing was pushed or published.
