"""Cross-agent coordination: venue claims + intent log (see coordination/PROTOCOL.md).

Fail-closed by design: a missing lock file, an unreadable lock, a stale lock or
a conflicting claim all mean "do not trade". An idle desk costs nothing; a
confused desk costs the account.
"""

from __future__ import annotations

import json
import os
import time
from typing import Any, Dict, List, Optional, Sequence

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))
COORD_DIR = os.path.join(ROOT, "coordination")
LOCK = os.path.join(COORD_DIR, "lock.json")
INTENTS = os.path.join(COORD_DIR, "intents.jsonl")
OWNER = "sonia"


def _load(path: str = None) -> Optional[Dict[str, Any]]:
    try:
        with open(path or LOCK, "r", encoding="utf-8") as f:
            d = json.load(f)
        return d if isinstance(d, dict) else None
    except Exception:  # noqa: BLE001
        return None


def _asset_match(claim_assets: Any, asset: str) -> bool:
    if claim_assets in ("*", None):
        return True
    if isinstance(claim_assets, str):
        claim_assets = [claim_assets]
    try:
        return str(asset).upper() in {str(a).upper() for a in claim_assets}
    except TypeError:
        return False


def _expired(claim: Dict[str, Any]) -> bool:
    exp = claim.get("expires_at")
    if not exp:
        return False
    try:
        from datetime import datetime, timezone
        dt = datetime.fromisoformat(str(exp).replace("Z", "+00:00"))
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt < datetime.now(timezone.utc)
    except Exception:  # noqa: BLE001
        return True                     # unreadable expiry => treat as stale


def can_trade(venue: str, asset: str, owner: str = OWNER,
              path: Optional[str] = None) -> Dict[str, Any]:
    """May ``owner`` place a live order on (venue, asset)?"""
    lock = _load(path)
    if lock is None:
        return {"allowed": False, "state": "NO_LOCK",
                "reason": "coordination lock missing/unreadable — fail closed"}

    claims = [c for c in (lock.get("claims") or []) if isinstance(c, dict)]
    mine = []
    theirs = []
    for c in claims:
        if not _asset_match(c.get("assets"), asset):
            continue
        if str(c.get("venue") or "").lower() != str(venue).lower():
            continue
        if _expired(c):
            continue
        if str(c.get("owner") or "") == owner:
            mine.append(c)
        else:
            theirs.append(c)

    live_mine = [c for c in mine if str(c.get("mode") or "").lower() == "live"]
    live_theirs = [c for c in theirs if str(c.get("mode") or "").lower() == "live"]

    if live_mine and live_theirs:
        return {"allowed": False, "state": "CONFLICT",
                "reason": f"both {owner} and "
                          f"{sorted({c.get('owner') for c in live_theirs})} claim live "
                          f"{venue}/{asset}",
                "others": [c.get("owner") for c in live_theirs]}
    if live_theirs:
        return {"allowed": False, "state": "NOT_OWNED",
                "reason": f"{venue}/{asset} is claimed live by "
                          f"{sorted({c.get('owner') for c in live_theirs})}",
                "others": [c.get("owner") for c in live_theirs]}
    if not live_mine:
        return {"allowed": False, "state": "NO_CLAIM",
                "reason": f"no live claim for {owner} on {venue}/{asset}"}
    return {"allowed": True, "state": "OK", "reason": "claim held",
            "claim": live_mine[0]}


def append_intent(action: str, venue: str, asset: str, side: Optional[str] = None,
                  position_id: Any = None, reason: str = "",
                  owner: str = OWNER, path: Optional[str] = None) -> Dict[str, Any]:
    row = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "owner": owner, "action": action, "venue": venue,
        "asset": str(asset or "").upper(), "side": side,
        "position_id": None if position_id is None else str(position_id),
        "reason": reason,
    }
    p = path or INTENTS
    os.makedirs(os.path.dirname(p), exist_ok=True)
    with open(p, "a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True) + "\n")
    return row


def read_intents(owner: Optional[str] = None, path: Optional[str] = None) -> List[Dict[str, Any]]:
    out: List[Dict[str, Any]] = []
    try:
        with open(path or INTENTS, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    d = json.loads(line)
                except Exception:  # noqa: BLE001
                    continue
                if not isinstance(d, dict):
                    continue
                if owner and str(d.get("owner")) != owner:
                    continue
                out.append(d)
    except FileNotFoundError:
        pass
    return out


def foreign_open_intents(positions: Sequence[Dict[str, Any]],
                         owner: str = OWNER, path: Optional[str] = None) -> List[Dict[str, Any]]:
    """Broker positions that another owner announced (we must not adopt them)."""
    mine = {(str(i.get("asset") or "").upper(), str(i.get("side") or "").lower())
            for i in read_intents(owner=owner, path=path) if i.get("action") == "open"}
    out = []
    for p in positions or ():
        if not isinstance(p, dict):
            continue
        sym = str(p.get("symbol") or p.get("asset") or "").upper()
        sd = str(p.get("side") or p.get("positionSide") or "").lower()
        if (sym, sd) not in mine:
            out.append(p)
    return out


_UNSET = object()


def validate(lock: Any = _UNSET) -> Dict[str, Any]:
    """Structural check used by CI. Pass None explicitly to test a missing lock."""
    if lock is _UNSET:
        lock = _load()
    problems: List[str] = []
    if not isinstance(lock, dict):
        return {"ok": False, "problems": ["lock missing or unreadable"]}
    claims = lock.get("claims")
    if not isinstance(claims, list):
        problems.append("claims must be a list")
        claims = []
    seen: Dict[Any, List[str]] = {}
    for i, c in enumerate(claims):
        if not isinstance(c, dict):
            problems.append(f"claim[{i}] is not an object")
            continue
        for k in ("owner", "venue", "mode"):
            if not c.get(k):
                problems.append(f"claim[{i}] missing {k}")
        if str(c.get("mode")) not in ("live", "observe"):
            problems.append(f"claim[{i}] mode must be live|observe")
        assets = c.get("assets")
        if not (assets == "*" or isinstance(assets, list)):
            problems.append(f"claim[{i}] assets must be '*' or a list")
        if str(c.get("mode")) == "live":
            key = (str(c.get("venue")), json.dumps(assets, sort_keys=True)
                   if isinstance(assets, list) else assets)
            seen.setdefault(key, []).append(str(c.get("owner")))
    for key, owners in seen.items():
        if len(set(owners)) > 1:
            problems.append(f"CONFLICT on {key[0]}/{key[1]}: {sorted(set(owners))}")
    return {"ok": not problems, "problems": problems}
