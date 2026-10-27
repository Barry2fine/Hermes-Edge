"""Learning state + trade ledger persistence for the adaptive tuner."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any, Dict, List, Optional

from . import tuner


def _dir(base: Optional[str] = None) -> Path:
    d = Path(base) if base else (Path(__file__).resolve().parents[2] / "data" / "learning")
    d.mkdir(parents=True, exist_ok=True)
    return d


def paths(base: Optional[str] = None) -> Dict[str, Path]:
    d = _dir(base)
    return {"dir": d, "state": d / "state.json", "opens": d / "opens.jsonl",
            "trades": d / "trades.jsonl", "consumed": d / "consumed.jsonl"}


def _append(path: Path, rec: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")


def _read_jsonl(path: Path) -> List[Dict[str, Any]]:
    out = []
    if not path.exists():
        return out
    try:
        for line in path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                out.append(json.loads(line))
            except Exception:  # noqa: BLE001
                continue
    except OSError:
        return []
    return out


def load_params(base: Optional[str] = None) -> Dict[str, Any]:
    p = paths(base)["state"]
    if p.exists():
        try:
            data = json.loads(p.read_text())
            return {**tuner.DEFAULTS, **(data.get("params") or {})}
        except Exception:  # noqa: BLE001
            # A corrupt/unreadable state must NOT silently unblock everything:
            # block all new risk until the tuner is rebuilt.
            blocked = {"min_score": 1.01, "min_alignment": 99,
                       "blocked_directions": [], "blocked_coins": [],
                       "state_corrupt": True}
            return {**tuner.DEFAULTS, **blocked}
    return dict(tuner.DEFAULTS)


def save_state(params: Dict[str, Any], stats: Dict[str, Any], base: Optional[str] = None) -> Path:
    p = paths(base)["state"]
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps({"params": params, "stats": stats}, indent=2, sort_keys=True, default=str))
    tmp.replace(p)
    return p


def record_open(trade: Dict[str, Any], base: Optional[str] = None) -> None:
    ts = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    rec = {"ts": ts, **trade}
    rec.setdefault("open_id", f"{ts}|{trade.get('coin')}|{trade.get('entry')}")
    _append(paths(base)["opens"], rec)


def _consumed_ids(base: Optional[str] = None) -> set:
    return {str(r.get("open_id")) for r in _read_jsonl(paths(base)["consumed"]) if r.get("open_id")}


def load_trades(base: Optional[str] = None) -> List[Dict[str, Any]]:
    return _read_jsonl(paths(base)["trades"])


def load_opens(base: Optional[str] = None) -> List[Dict[str, Any]]:
    return _read_jsonl(paths(base)["opens"])


def sync_closes(broker_trades: List[Dict[str, Any]], base: Optional[str] = None) -> int:
    """Append newly realized closes from broker trades. Dedupes by trade id."""
    p = paths(base)
    existing = {str(t.get("trade_id")) for t in load_trades(base)}
    opens = load_opens(base)
    added = 0
    # Honour the caller's sandbox: a hard-coded repo path let tests/backfills
    # write fake R multiples straight into the LIVE sizing statistics.
    closes_path = paths(base)["dir"].parent / "logs" / "closes.jsonl"

    for t in broker_trades or []:
        if not isinstance(t, dict):
            continue
        tid = t.get("tradeId") or t.get("trade_id") or t.get("id")
        if not tid or str(tid) in existing:
            continue
        pnl = t.get("realizedPnl", t.get("realized_pnl"))
        try:
            pnl = float(pnl)
        except (TypeError, ValueError):
            continue
        if pnl == 0.0:
            continue
        coin = str(t.get("asset") or t.get("base") or t.get("symbol") or "").upper()
        side = str(t.get("positionSide") or t.get("side") or "").lower()
        direction = "long" if side in ("long", "buy") else "short" if side in ("short", "sell") else side

        consumed = _consumed_ids(base)
        trade_pid = str(t.get("positionId") or t.get("position_id") or "")
        trade_ts = str(t.get("timestamp") or t.get("time") or "")
        match = None
        # 1) exact position id wins when both sides carry one
        if trade_pid:
            for o in opens:
                if str(o.get("position_id") or o.get("positionId") or "") == trade_pid:
                    oid = str(o.get("open_id") or f"{o.get('ts')}|{o.get('coin')}|{o.get('entry')}")
                    if oid not in consumed:
                        match = o
                        match["open_id"] = oid
                    break
        # 2) otherwise the OLDEST unmatched open that predates the close
        if match is None:
            for o in opens:                       # opens are chronological
                oid = str(o.get("open_id") or f"{o.get('ts')}|{o.get('coin')}|{o.get('entry')}")
                if oid in consumed:
                    continue
                if str(o.get("coin", "")).upper() != coin:
                    continue
                if o.get("direction") != direction:
                    continue
                if trade_ts and str(o.get("ts") or "") > trade_ts:
                    continue                      # an open after the close cannot own it
                match = o
                match["open_id"] = oid
                break
        risk = None
        if match:
            try:
                risk = float(match.get("risk_usd"))
            except (TypeError, ValueError):
                risk = None
            # Persist the consumption so the next run cannot reuse this open.
            _append(paths(base)["consumed"], {"open_id": match.get("open_id"),
                                              "trade_id": str(tid),
                                              "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        r = (pnl / risk) if risk else None

        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "trade_id": str(tid),
            "coin": coin,
            "direction": direction,
            "realized_pnl": pnl,
            "r": (round(r, 4) if r is not None else None),
            "win": pnl > 0,
        }
        _append(p["trades"], rec)
        # Feed the sizing multiplier's journal too.
        if rec["r"] is not None:
            _append(closes_path, {"ts": rec["ts"], "coin": coin, "direction": direction, "r": rec["r"]})
        existing.add(str(tid))
        added += 1
    return added


def recompute(base: Optional[str] = None) -> Dict[str, Any]:
    """Re-derive tuned parameters from the ledger and persist them."""
    trades = load_trades(base)
    stats = tuner.bucket_stats(trades)
    params = tuner.derive_params(trades, load_params(base))
    save_state(params, stats, base)
    return {"params": params, "stats": stats}


def apply(ranking: List[Dict[str, Any]], base: Optional[str] = None) -> List[Dict[str, Any]]:
    """Load tuned params and filter a ranking through them."""
    return tuner.apply_to_ranking(ranking, load_params(base))
