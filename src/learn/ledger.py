"""Local position ledger — our own record of what we think is open.

Reconciliation can only be honest if it compares the broker against something
independent. Previously the engine passed the broker's own book as the "local"
book, so the seat approved itself; after that was fixed it had nothing to
compare and rejected every trade. This module is the missing side: an append-only
journal of our own opens and closes.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional


def _path(base: Optional[str] = None) -> str:
    root = os.path.abspath(base or os.path.join(os.path.dirname(__file__), "..", ".."))
    return os.path.join(root, "data", "learning", "local_ledger.jsonl")


def _append(row: Dict[str, Any], base: Optional[str] = None) -> None:
    p = _path(base)
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")


def record_open(coin: str, side: str, quantity: Any, position_id: Any = None,
                base: Optional[str] = None) -> Dict[str, Any]:
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": "open",
        "symbol": str(coin or "").upper(),
        "side": "long" if str(side).lower() in ("long", "buy") else "short",
        "quantity": abs(float(quantity or 0.0)),
        "position_id": None if position_id is None else str(position_id),
    }
    _append(row, base)
    return row


def record_close(coin: str, side: str, quantity: Any, position_id: Any = None,
                 base: Optional[str] = None) -> Dict[str, Any]:
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "event": "close",
        "symbol": str(coin or "").upper(),
        "side": "long" if str(side).lower() in ("long", "buy") else "short",
        "quantity": abs(float(quantity or 0.0)),
        "position_id": None if position_id is None else str(position_id),
    }
    _append(row, base)
    return row


def current_positions(base: Optional[str] = None) -> List[Dict[str, Any]]:
    """Everything we opened and have not yet closed (by position id, else symbol+side)."""
    p = _path(base)
    opens: Dict[str, Dict[str, Any]] = {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if not isinstance(row, dict):
                    continue
                key = (str(row.get("position_id")) if row.get("position_id")
                       else f"{row.get('symbol')}|{row.get('side')}")
                if row.get("event") == "open":
                    opens[key] = {"symbol": row.get("symbol"), "side": row.get("side"),
                                  "quantity": row.get("quantity"),
                                  "position_id": row.get("position_id")}
                elif row.get("event") == "close":
                    opens.pop(key, None)
    except FileNotFoundError:
        return []
    return list(opens.values())


def is_ours(coin: str, side: str = None, base: Optional[str] = None) -> bool:
    """True when this position was opened by us (so we may manage its stop).

    Another executor may hold positions on the same account. Moving a stop we
    did not place, or counting their exposure as ours, would be a mistake.
    """
    for p in current_positions(base):
        if str(p.get("symbol") or "").upper() != str(coin or "").upper():
            continue
        if side is None or str(p.get("side") or "").lower() == str(side).lower():
            return True
    return False


def foreign_positions(broker_positions, base: Optional[str] = None) -> List[Dict[str, Any]]:
    """Broker positions that are not in our ledger."""
    ours = current_positions(base)
    mine = {(str(p.get("symbol") or "").upper(), str(p.get("side") or "").lower())
            for p in ours}
    out = []
    for b in broker_positions or ():
        if not isinstance(b, dict):
            continue
        sym = str(b.get("symbol") or b.get("asset") or "").upper()
        sd = str(b.get("side") or b.get("positionSide") or "").lower()
        if (sym, sd) not in mine:
            out.append(b)
    return out
