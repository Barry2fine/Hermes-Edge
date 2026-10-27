"""Post-trade review loop: grade closed trades, persist, feed policy back."""

from __future__ import annotations

import json
import os
from typing import Any, Callable, Dict, List, Optional, Sequence

from . import credit, postmortem


def _graded_ids(base: Optional[str] = None) -> set:
    path = credit._paths(base)["postmortems"]
    ids = set()
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
                if isinstance(d, dict) and d.get("trade_id"):
                    ids.add(str(d["trade_id"]))
    except FileNotFoundError:
        pass
    return ids


def _ts(x: Any) -> Optional[float]:
    """>>> epochs from ms/s strings or ISO timestamps."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        try:
            from datetime import datetime, timezone
            s = str(x).replace("Z", "+00:00")
            dt = datetime.fromisoformat(s)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return dt.timestamp()
        except Exception:  # noqa: BLE001
            return None
    return v / 1000.0 if v > 1e11 else v


def _slice(path: Sequence[Dict[str, Any]], start: Optional[float],
           end: Optional[float]) -> List[Dict[str, Any]]:
    if start is None or end is None:
        return []
    out = []
    for c in path or ():
        if not isinstance(c, dict):
            continue
        t = _ts(c.get("t") or c.get("time") or c.get("timestamp"))
        if t is None or t < start or t > end:
            continue
        out.append(c)
    return out


def review_trades(
    trades: Sequence[Dict[str, Any]],
    candles_for: Optional[Callable[[str], Sequence[Dict[str, Any]]]] = None,
    contexts: Optional[Dict[str, Dict[str, Any]]] = None,
    base: Optional[str] = None,
    regime: Optional[str] = None,
) -> Dict[str, Any]:
    """Grade every not-yet-graded trade and feed the loop.

    ``candles_for(coin)`` supplies a candle path (injected so this stays offline
    in tests). Without it, grading falls back to the trade's own fields.
    """
    done = _graded_ids(base)
    graded: List[Dict[str, Any]] = []
    for t in trades or ():
        if not isinstance(t, dict):
            continue
        tid = str(t.get("trade_id") or t.get("tradeId") or "")
        if not tid or tid in done:
            continue
        coin = str(t.get("asset") or t.get("coin") or "").upper()
        path: Sequence[Dict[str, Any]] = []
        if candles_for is not None and coin:
            try:
                path = _slice(candles_for(coin),
                              _ts(t.get("opened_ts") or t.get("openTime")),
                              _ts(t.get("closed_ts") or t.get("timestamp")))
            except Exception:  # noqa: BLE001
                path = []
        # Prefer a per-trade context; otherwise fall back to the entry context
        # recorded for this coin+direction (what the engine actually stores).
        ctx = ((contexts or {}).get(tid)
               or (contexts or {}).get(f"{coin}|{t.get('direction') or t.get('positionSide')}")
               or (contexts or {}).get(f"{coin}|{str(t.get('positionSide') or '').lower()}")
               or {})
        # A broker trade carries positionSide, not direction.
        norm = {**t, "coin": coin,
                "direction": t.get("direction") or
                ({"long": "long", "short": "short"}.get(
                    str(t.get("positionSide") or "").lower()))}
        g = postmortem.grade(norm, path, ctx)
        row = credit.record_postmortem({**norm, "trade_id": tid}, g,
                                       regime=regime, base=base)
        credit.update_credit((ctx.get("votes") or []), g, base=base)
        graded.append(row)
    return {
        "graded": graded,
        "count": len(graded),
        "summary": postmortem.summarize([{"verdict": g["verdict"], "label": g["label"],
                                          "tags": g["tags"]} for g in graded]),
        "patterns": credit.pattern_report(base=base),
        "hints": credit.policy_hints(base=base),
    }


def gate_context(base: Optional[str] = None) -> Dict[str, Any]:
    """The postmortem table the gate bot expects."""
    return {"postmortem": credit.pattern_report(base=base)}
