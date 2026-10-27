"""Seat credit + pattern memory — turn postmortems into policy.

Two feedback channels:

1. **Seat credit.** Every seat's vote is recorded at entry. When the trade is
   graded, each seat is credited by whether its stance matched a *good process*
   (not whether we made money): approving a SMART trade or rejecting a POOR one
   earns credit; approving a POOR trade costs it. A lucky win credits nobody.

2. **Pattern memory.** (coin, direction, regime) outcomes are accumulated. A
   pattern with repeated POOR verdicts is blocked for new entries — that is the
   loop closing: the same bad trade stops being available.

Only ever *tightens*. Nothing here can raise risk, widen a stop, or unblock
something the risk layers blocked.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, Iterable, List, Optional, Tuple

MIN_PATTERN_SAMPLE = 3          # never block a pattern on one bad trade
BLOCK_POOR_RATE = 0.6           # >=60% poor decisions on 3+ samples -> block
SEAT_PRIOR = 0.0                # neutral starting credit


def _paths(base: Optional[str] = None) -> Dict[str, str]:
    root = os.path.abspath(base or os.path.join(os.path.dirname(__file__), "..", ".."))
    d = os.path.join(root, "data", "learning")
    return {"dir": d,
            "postmortems": os.path.join(d, "postmortems.jsonl"),
            "credit": os.path.join(d, "seat_credit.json"),
            "patterns": os.path.join(d, "patterns.json")}


def _read_jsonl(path: str) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if isinstance(d, dict):
                    out.append(d)
    except FileNotFoundError:
        pass
    return out


def _load(path: str, default):
    try:
        with open(path, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else default
    except Exception:  # noqa: BLE001
        return default


def _save(path: str, obj: Any) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
    os.replace(tmp, path)


def pattern_key(trade: Dict[str, Any], regime: Optional[str] = None) -> str:
    coin = str(trade.get("coin") or "?").upper()
    direction = str(trade.get("direction") or "?")
    rg = str(regime or trade.get("regime") or "any")
    return f"{coin}|{direction}|{rg}"


def record_postmortem(trade: Dict[str, Any], graded: Dict[str, Any],
                      regime: Optional[str] = None,
                      base: Optional[str] = None) -> Dict[str, Any]:
    """Append the graded decision and update pattern memory."""
    p = _paths(base)
    os.makedirs(p["dir"], exist_ok=True)
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "trade_id": trade.get("trade_id") or trade.get("tradeId"),
        "coin": str(trade.get("coin") or "").upper(),
        "direction": trade.get("direction"),
        "pattern": pattern_key(trade, regime),
        "r": graded.get("r"),
        "label": graded.get("label"),
        "verdict": graded.get("verdict"),
        "score": graded.get("score"),
        "tags": graded.get("tags") or [],
        "capture_ratio": graded.get("capture_ratio"),
    }
    with open(p["postmortems"], "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")

    pats = _load(p["patterns"], {})
    key = row["pattern"]
    rec = pats.get(key) or {"n": 0, "poor": 0, "smart": 0, "lucky": 0,
                            "tags": {}, "last_ts": None}
    rec["n"] += 1
    if row["verdict"] == "POOR":
        rec["poor"] += 1
    if row["verdict"] == "SMART":
        rec["smart"] += 1
    if row["label"] == "LUCKY_WIN":
        rec["lucky"] += 1
    for t in row["tags"]:
        rec["tags"][t] = rec["tags"].get(t, 0) + 1
    rec["last_ts"] = row["ts"]
    pats[key] = rec
    _save(p["patterns"], pats)
    return row


def block_reason(pattern: str, base: Optional[str] = None) -> Optional[str]:
    """None when the pattern may trade, else the reason it is blocked."""
    pats = _load(_paths(base)["patterns"], {})
    rec = pats.get(pattern)
    if not rec or rec.get("n", 0) < MIN_PATTERN_SAMPLE:
        return None
    poor_rate = rec.get("poor", 0) / float(rec.get("n") or 1)
    if poor_rate >= BLOCK_POOR_RATE:
        tags = sorted((rec.get("tags") or {}).items(), key=lambda kv: -kv[1])
        top = tags[0][0] if tags else "poor_process"
        return f"pattern_blocked:{pattern}:poor_rate={poor_rate:.2f} n={rec['n']} top={top}"
    return None


def update_credit(votes: Iterable[Dict[str, Any]], graded: Dict[str, Any],
                  base: Optional[str] = None) -> Dict[str, float]:
    """Credit each seat by whether its stance matched a good PROCESS."""
    credits = _load(_paths(base)["credit"], {})
    verdict = graded.get("verdict")
    if verdict not in ("SMART", "POOR"):
        return credits                      # MIXED / unknown moves nobody
    good_process = verdict == "SMART"
    for v in votes or ():
        if not isinstance(v, dict):
            continue
        bot = v.get("bot")
        if not bot:
            continue
        decision = str(v.get("decision") or "").lower()
        if decision not in ("approve", "reject"):
            continue                    # abstain carries no information
        agreed = (decision == "approve") == good_process
        cur = float(credits.get(bot, SEAT_PRIOR))
        credits[bot] = round(cur + (1.0 if agreed else -1.0), 4)
    _save(_paths(base)["credit"], credits)
    return credits


def seat_scorecard(credits: Optional[Dict[str, float]] = None,
                   base: Optional[str] = None) -> List[Dict[str, Any]]:
    """Seats ranked by earned credit (evidence, not opinion)."""
    credits = credits if credits is not None else _load(_paths(base)["credit"], {})
    rows = [{"bot": k, "credit": v} for k, v in credits.items()]
    rows.sort(key=lambda r: (-r["credit"], r["bot"]))
    return rows


def pattern_report(base: Optional[str] = None) -> Dict[str, Any]:
    """Every pattern with its record and whether it is currently blocked."""
    pats = _load(_paths(base)["patterns"], {})
    out = {}
    for key, rec in sorted(pats.items()):
        n = rec.get("n", 0) or 0
        out[key] = {
            "n": n,
            "poor_rate": round((rec.get("poor", 0) / n) if n else 0.0, 3),
            "smart": rec.get("smart", 0),
            "lucky": rec.get("lucky", 0),
            "blocked": block_reason(key, base) is not None,
        }
    return out


def policy_hints(base: Optional[str] = None, window: int = 30) -> List[str]:
    """Actionable suggestions derived from the failure mix (advisory only)."""
    rows = _read_jsonl(_paths(base)["postmortems"])[-window:]
    hints: List[str] = []
    if len(rows) < MIN_PATTERN_SAMPLE:
        return hints
    tags: Dict[str, int] = {}
    for r in rows:
        for t in r.get("tags") or []:
            tags[t] = tags.get(t, 0) + 1
    n = len(rows)
    if tags.get("premature_exit", 0) >= max(2, n * 0.4):
        hints.append("exit too early in most trades: widen the trail or raise the partial-TP trigger")
    if tags.get("target_unreachable", 0) >= max(2, n * 0.4):
        hints.append("targets rarely approached: shorten the first target or require a fresher breakout")
    if tags.get("chased_entry", 0) >= max(2, n * 0.4):
        hints.append("entries were extended: tighten the RSI/EMA-distance veto")
    if tags.get("stop_too_tight", 0) >= max(2, n * 0.4):
        hints.append("stopped out before the move: widen the stop to >=1.5 ATR and cut size instead")
    if tags.get("oversized", 0) >= 2:
        hints.append("sizing exceeded budget: re-check the governor cap before entry")
    lucky = sum(1 for r in rows if r.get("label") == "LUCKY_WIN")
    if lucky >= max(2, n * 0.3):
        hints.append("many profitable-but-poor entries: stop counting those as evidence of skill")
    return hints
