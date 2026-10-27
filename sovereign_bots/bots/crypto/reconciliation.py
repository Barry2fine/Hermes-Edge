"""Reconciliation bot — broker truth versus local book. VETO.

Rules (contract):
  * positions normalise to (asset upper, side long/short, round(qty, 6))
  * orders    normalise to (asset upper, side, type, round(trigger, 6))
  * a local position/order with no broker match  -> reject (ghost)
  * a broker position/order with no local match  -> reject (untracked)
  * a missing / non-list book                    -> reject (fail-closed)
  * rows that cannot be parsed                   -> reject (fail-closed)

Pure: no network, no file IO, no clock, no randomness.
"""

from __future__ import annotations

from collections import Counter
from typing import Any, Dict, List, Optional, Sequence, Tuple

from ...core.bot import Bot, Vote

_ASSET = ("asset", "symbol", "coin", "market", "instrument", "ticker", "pair")
_SIDE = ("side", "direction", "pos_side", "position_side")
_QTY = ("qty", "quantity", "size", "amount", "contracts", "position")
_TYPE = ("type", "order_type", "ordertype", "kind", "orderType")
_TRIGGER = ("trigger", "trigger_price", "triggerPrice", "stop", "stop_price",
            "price", "triggerPx")

_SIDE_LONG = {"long", "buy", "b", "l", "bid", "longs"}
_SIDE_SHORT = {"short", "sell", "s", "a", "ask", "shorts"}


def _num(x: Any) -> Optional[float]:
    """Best-effort finite float, else None. Never raises."""
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(str(x).strip().rstrip("%")) if isinstance(x, str) else float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


def _pick(row: Dict[str, Any], keys: Sequence[str]) -> Any:
    for k in keys:
        if k in row and row.get(k) is not None:
            return row.get(k)
    return None


def _side(x: Any) -> Optional[str]:
    if not isinstance(x, str):
        return None
    s = x.strip().lower()
    if s in _SIDE_LONG:
        return "long"
    if s in _SIDE_SHORT:
        return "short"
    return None


def _asset(x: Any) -> Optional[str]:
    if not isinstance(x, str):
        return None
    s = x.strip().upper()
    return s or None


def _r(x: Optional[float]) -> Optional[float]:
    return None if x is None else round(float(x), 6)


def _pos_key(row: Any) -> Optional[Tuple[Any, ...]]:
    if not isinstance(row, dict):
        return None
    asset = _asset(_pick(row, _ASSET))
    side = _side(_pick(row, _SIDE))
    qty = _num(_pick(row, _QTY))
    if asset is None or side is None or qty is None:
        return None
    return (asset, side, _r(abs(qty)))


def _ord_key(row: Any) -> Optional[Tuple[Any, ...]]:
    if not isinstance(row, dict):
        return None
    asset = _asset(_pick(row, _ASSET))
    side = _side(_pick(row, _SIDE))
    raw_type = _pick(row, _TYPE)
    otype = raw_type.strip().lower() if isinstance(raw_type, str) and raw_type.strip() else None
    trigger = _num(_pick(row, _TRIGGER))
    if asset is None or side is None:
        return None
    return (asset, side, otype, _r(trigger))


def _keys(rows: Sequence[Any], fn) -> Tuple[Optional[Counter], Optional[int]]:
    """Counter of normalised rows, or (None, index) for the first bad row."""
    if not isinstance(rows, (list, tuple)):
        return None, -1
    out: Counter = Counter()
    for i, row in enumerate(rows):
        k = fn(row)
        if k is None:
            return None, i
        out[k] += 1
    return out, None


def _fmt(key: Tuple[Any, ...]) -> str:
    return "|".join("?" if k is None else str(k) for k in key)


class ReconciliationBot(Bot):
    id = "reconciliation"
    mandate = ("Trust nothing unverified: every local position and order must "
               "be matched one-for-one against broker truth before sizing up.")
    domains = ("crypto",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        names = ("broker_positions", "local_positions", "broker_orders", "local_orders")
        evidence: Dict[str, Any] = {
            "counts": {}, "ghosts": [], "untracked": [], "missing": [],
        }

        missing: List[str] = []
        for name in names:
            if name not in ctx or not isinstance(ctx.get(name), (list, tuple)):
                missing.append(name)
        evidence["missing"] = missing
        if missing:
            return Vote(self.id, "reject", 0.0,
                        ["missing:" + ",".join(missing)], evidence)

        bp, bad_bp = _keys(ctx.get("broker_positions"), _pos_key)
        lp, bad_lp = _keys(ctx.get("local_positions"), _pos_key)
        bo, bad_bo = _keys(ctx.get("broker_orders"), _ord_key)
        lo, bad_lo = _keys(ctx.get("local_orders"), _ord_key)
        for name, bad in (("broker_positions", bad_bp), ("local_positions", bad_lp),
                          ("broker_orders", bad_bo), ("local_orders", bad_lo)):
            if bad is not None:
                return Vote(self.id, "reject", 0.0,
                            [f"invalid:{name}:row_{bad}"], evidence)

        assert bp is not None and lp is not None and bo is not None and lo is not None
        evidence["counts"] = {
            "broker_positions": sum(bp.values()), "local_positions": sum(lp.values()),
            "broker_orders": sum(bo.values()), "local_orders": sum(lo.values()),
        }

        ghosts: List[str] = []
        untracked: List[str] = []
        for label, local, broker in (("position", lp, bp), ("order", lo, bo)):
            for key, n in (local - broker).items():
                ghosts.extend(f"{label}:{_fmt(key)}" for _ in range(n))
            for key, n in (broker - local).items():
                untracked.extend(f"{label}:{_fmt(key)}" for _ in range(n))

        evidence["ghosts"] = ghosts[:20]
        evidence["untracked"] = untracked[:20]

        if ghosts or untracked:
            reasons = []
            if ghosts:
                reasons.append("ghost:local_without_broker=" + str(len(ghosts)))
            if untracked:
                reasons.append("untracked:broker_without_local=" + str(len(untracked)))
            reasons.extend(ghosts[:2] + untracked[:2])
            return Vote(self.id, "reject", 0.9, reasons, evidence)

        n_pos = sum(bp.values()) + sum(lp.values())
        conf = 0.9 if n_pos > 0 else 0.7
        return Vote(self.id, "approve", conf,
                    [f"reconciled:positions={sum(bp.values())}",
                     f"orders={sum(bo.values())}", "ghosts=0", "untracked=0"], evidence)
