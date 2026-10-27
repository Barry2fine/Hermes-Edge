"""Expected-goals (xG) bot — score the chances, not the goals.

Result-based numbers lie in small samples: a team that scored 3 on 0.8 xG got
lucky; the model should price the *chances created*. This bot turns shot volume
and shot quality into expected goals and compares that to the market's implied
total, so we fade results-driven pricing.

Adopted from open xG work (shot-quality models, logistic/RF calibration).
"""

from __future__ import annotations

import math
from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

DEFAULT_FEE = 0.02
DEFAULT_XG_PER_SHOT = 0.10   # league-average quality per shot


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def expected_goals(ctx: Dict[str, Any], prefix: str = "") -> Dict[str, Any]:
    """xG from either a supplied xg_* number or shots x quality."""
    xg = _num(ctx.get(f"{prefix}xg") if prefix else ctx.get("xg"))
    if xg is not None:
        return {"xg": xg, "method": "supplied"}

    shots = _num(ctx.get(f"{prefix}shots") if prefix else ctx.get("shots"))
    quality = _num(ctx.get(f"{prefix}shot_quality") if prefix else ctx.get("shot_quality"))
    if shots is None:
        return {"xg": None, "method": "missing"}
    q = quality if quality is not None else DEFAULT_XG_PER_SHOT
    return {"xg": shots * q, "method": "shots_x_quality", "shots": shots, "quality": q}


class ExpectedGoalsBot(Bot):
    id = "xg"
    edge_seat = True   # an approve means: expected goals vs the market line
    mandate = ("Price chances, not results: expected goals versus the market line "
               "catches teams whose results have outrun their play.")
    domains = ("sports",)
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        side = str(ctx.get("side") or "over").strip().lower()
        line = _num(ctx.get("line"))
        market_p = _num(ctx.get("market_p"))
        fee = _num(ctx.get("fee")) or DEFAULT_FEE

        home = expected_goals(ctx, "home_")
        away = expected_goals(ctx, "away_")
        total = _num(ctx.get("xg_total"))
        if total is None and home["xg"] is not None and away["xg"] is not None:
            total = home["xg"] + away["xg"]

        evidence: Dict[str, Any] = {"home": home, "away": away, "xg_total": total,
                                    "line": line, "market_p": market_p, "side": side}
        if total is None or line is None or market_p is None:
            return Vote(self.id, "abstain", 0.0, ["missing:xg/line/market_p"], evidence)
        if not (0.0 < market_p < 1.0):
            return Vote(self.id, "abstain", 0.0, ["invalid_market_p"], evidence)

        # Poisson total with the xG as the mean.
        from .prop_pmf import over_probability
        d = over_probability(max(total, 1e-6), line, 1.0)
        p_over = d["p_over"]
        if p_over is None:
            return Vote(self.id, "abstain", 0.0, ["pmf_failed"], evidence)

        p = p_over if side == "over" else 1.0 - p_over
        edge = p - market_p - fee
        evidence.update({"p_model": round(p, 4), "edge": round(edge, 4),
                         "xg_vs_line": round(total - line, 3)})

        if edge > 0.02:
            return Vote(self.id, "approve", min(0.85, 0.50 + min(0.35, edge * 4)),
                        [f"xg_edge:{edge:+.3f}", f"xg={total:.2f} vs line={line:g}"], evidence)
        if edge < 0:
            return Vote(self.id, "reject", min(0.80, 0.50 + abs(edge) * 2),
                        [f"xg_against:{edge:+.3f}"], evidence)
        return Vote(self.id, "abstain", 0.25, [f"thin_xg_edge:{edge:+.3f}"], evidence)
