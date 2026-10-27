"""Impact bot — Almgren-style temporary impact for the intended size. VETO.

Rules (contract):
  participation = (size * price) / max(day_volume_usd, 1)
  sigma         = vol_pct / 100
  h_bps         = 1e4 * 0.5 * sigma * sqrt(max(participation, 1e-9))
  reject when h_bps > max(5.0, 0.25 * expected_edge_bps)
  reject on missing / short data
  otherwise approve with evidence {participation, h_bps, edge_bps}

Pure: no network, no file IO, no clock, no randomness.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from ...core.bot import Bot, Vote

FLOOR_BPS = 5.0
EDGE_FRACTION = 0.25
MIN_CANDLES = 10


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


def _n_bars(rows: Any) -> int:
    if not isinstance(rows, (list, tuple)):
        return 0
    n = 0
    for r in rows:
        if isinstance(r, dict):
            v = None
            for k in ("close", "c", "price", "last"):
                if k in r:
                    v = _num(r.get(k))
                    if v is not None:
                        break
            if v is not None:
                n += 1
        elif isinstance(r, (list, tuple)) and r:
            if _num(r[-1]) is not None:
                n += 1
        elif _num(r) is not None:
            n += 1
    return n


class ImpactBot(Bot):
    id = "impact"
    mandate = ("Price the temporary impact of our own size against daily "
               "volume and refuse any order that eats more than a quarter "
               "of its expected edge.")
    domains = ("crypto",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        size = _num(ctx.get("size"))
        price = _num(ctx.get("price"))
        if price is None:
            price = _num(ctx.get("mark"))
        volume = _num(ctx.get("day_volume_usd"))
        vol_pct = _num(ctx.get("vol_pct"))
        edge = _num(ctx.get("expected_edge_bps"))

        missing: List[str] = []
        if size is None or size <= 0:
            missing.append("size")
        if price is None or price <= 0:
            missing.append("price")
        if volume is None or volume <= 0:
            missing.append("day_volume_usd")
        if vol_pct is None or vol_pct < 0:
            missing.append("vol_pct")
        if edge is None:
            missing.append("expected_edge_bps")

        participation = None
        h_bps = None
        if not missing:
            participation = (size * price) / max(volume, 1.0)
            sigma = vol_pct / 100.0
            h_bps = 1e4 * 0.5 * sigma * math.sqrt(max(participation, 1e-9))

        evidence: Dict[str, Any] = {
            "participation": participation,
            "h_bps": h_bps,
            "edge_bps": edge,
        }

        if missing or participation is None or h_bps is None:
            return Vote(self.id, "reject", 0.0,
                        ["missing:" + ",".join(missing) if missing else "invalid:impact"],
                        evidence)

        bars = _n_bars(ctx.get("candles_1h"))
        if "candles_1h" in ctx and bars < MIN_CANDLES:
            return Vote(self.id, "reject", 0.0,
                        [f"short:candles_1h:{bars}<{MIN_CANDLES}"], evidence)

        limit = max(FLOOR_BPS, EDGE_FRACTION * edge)
        if h_bps > limit:
            return Vote(self.id, "reject", 0.9,
                        [f"impact:h_bps={h_bps:.2f}>limit={limit:.2f}",
                         f"participation={participation:.4f}"], evidence)

        margin = 1.0 - (h_bps / limit if limit > 0 else 1.0)
        conf = 0.55 + 0.40 * max(0.0, min(1.0, margin))
        return Vote(self.id, "approve", min(0.95, conf),
                    [f"impact_ok:h_bps={h_bps:.2f}<=limit={limit:.2f}",
                     f"participation={participation:.4f}"], evidence)
