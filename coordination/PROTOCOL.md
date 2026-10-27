# Coordination protocol — Sonia (SovereignAI) ⟷ Hermes

Two executors share one prop account. They must not fight over it. This file is
the contract; `coordination/lock.json` is the machine-readable state and
`coordination/intents.jsonl` is the append-only record of what each side did.

## 1. Venue claims are exclusive
`lock.json` lists claims of the form `{owner, venue, assets, mode}`.
- `mode: "live"` — may place orders.
- `mode: "observe"` — may read, may not trade.
- `assets` — `"*"` or a list of symbols.

**A live order may only be placed if this bot's owner holds a `live` claim
covering `(venue, asset)` and no other owner holds one for the same pair.**
If two owners claim the same pair, the state is `CONFLICT` and **both sides must
refuse to trade** until a human resolves it. Refusing is the default; the file
is the switch.

## 2. Intents are announce-before-act
Before opening, closing or adjusting, append an intent line:
```json
{"ts":"...","owner":"sonia","action":"open","venue":"propr","asset":"SOL",
 "side":"long","position_id":"...","reason":"momentum top-1"}
```
The other side reads this to know which positions are *yours*. Neither bot may
move a stop, close, or "adopt" a position it did not announce.

## 3. Positions are owned, not shared
A position belongs to the owner of its `open` intent. Foreign positions are
invisible to bracket management and block new exposure in the same asset until
closed — never adopt them.

## 4. Code ownership
| Path | Owner | Rule |
|---|---|---|
| `SovereignAI/sovereign_engine.py`, `src/**` | Sonia | edit freely; tests must pass |
| `sovereign_bots/**` | shared | **PR only**, CI must be green, no direct pushes to `main` |
| `coordination/**` | shared | edit by PR; never delete another owner's claim silently |
| Hermes cloud/executor paths | Hermes | Sonia does not touch |

## 5. Change discipline
- Every behavioural change ships with a test that fails before it.
- No bot pushes to `main` on a shared path. Branch `agent/<owner>/<topic>`, open a
  PR, let `coordination_check.yml` + the test suites gate it.
- If you cannot verify a claim (a fill, a P&L, a source), say so in the PR. An
  unverified number is a report, not evidence.

## 6. Failure is loud, never silent
- Conflict → refuse to trade + notify + write `state: CONFLICT` in `lock.json`.
- Missing lock file → **refuse to trade** (fail closed).
- Stale lock (`expires_at` passed) → treat as absent; refuse.

The rule both sides should remember: *an idle desk costs nothing; a confused
desk costs the account.*
