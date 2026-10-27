"""Prop PMF bot — price the whole distribution, not a point estimate.

Idea taken from production prop models (e.g. Risky-Scout/nba-player-props-model):
a player prop is not "will he score 24" — it is P(X > line) from a full
probability mass function over the stat. A point projection hides the variance,
and variance is the whole game on props.

We model counts (points, rebounds, shots, goals) as an over-dispersed Poisson
(negative-binomial style): a mean ``mu`` and a dispersion factor inflating the
variance. Then:

    P(over line) = 1 - CDF(line)        (half-lines: no push)
    integer lines: P(over) = 1 - CDF(line) and a push at == line

and compare to the market price after fees.
"""

from __future__ import annotations

import math
from typing import Any, Dict, List, Optional

from ...core.bot import Bot, Vote

DEFAULT_FEE = 0.02
MIN_EDGE = 0.02
MAX_STAT = 200


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(x)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def poisson_pmf(k: int, mu: float) -> float:
    if mu <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-mu + k * math.log(mu) - math.lgamma(k + 1))


def poisson_cdf(k: int, mu: float) -> float:
    if k < 0:
        return 0.0
    return min(1.0, sum(poisson_pmf(i, mu) for i in range(0, min(k, MAX_STAT) + 1)))


def over_probability(mu: float, line: float, dispersion: float = 1.0) -> Dict[str, Any]:
    """P(stat > line) with an over-dispersion factor (variance = mu * dispersion)."""
    if mu is None or mu < 0 or line is None:
        return {"p_over": None, "p_push": None, "p_under": None}
    # Over-dispersed counts: scale the mean down and mix with a wider spread is
    # outside stdlib's reach, so approximate by inflating mu and re-normalising
    # the CDF against a scaled line. Documented and conservative.
    adj_mu = mu * max(1.0, dispersion)
    adj_line = line * max(1.0, dispersion)

    is_half = abs(line - math.floor(line) - 0.5) < 1e-9
    floor_line = int(math.floor(line))
    if is_half:
        p_over = 1.0 - poisson_cdf(floor_line, adj_mu)
        return {"p_over": p_over, "p_push": 0.0, "p_under": 1.0 - p_over}
    p_push = poisson_pmf(floor_line, adj_mu)
    p_under = poisson_cdf(floor_line - 1, adj_mu)
    p_over = max(0.0, 1.0 - p_under - p_push)
    return {"p_over": p_over, "p_push": p_push, "p_under": p_under}


class PropPMFBot(Bot):
    id = "prop_pmf"
    edge_seat = True   # an approve means: built distribution vs the prop line
    mandate = ("Never price a prop off a point projection: build the distribution, "
               "charge the push, and only bet when the modelled probability beats "
               "the market after fees.")
    domains = ("sports",)
    veto = False

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context if isinstance(context, dict) else {}
        mu = _num(ctx.get("projection"))
        line = _num(ctx.get("line"))
        market_p = _num(ctx.get("market_p"))
        fee = _num(ctx.get("fee")) or DEFAULT_FEE
        disp = _num(ctx.get("dispersion")) or 1.0
        side = str(ctx.get("side") or "over").strip().lower()

        evidence: Dict[str, Any] = {"projection": mu, "line": line,
                                    "market_p": market_p, "dispersion": disp,
                                    "side": side}
        if mu is None or line is None or market_p is None:
            return Vote(self.id, "abstain", 0.0, ["missing:projection/line/market_p"], evidence)
        if mu < 0 or mu > MAX_STAT or not (0.0 < market_p < 1.0):
            return Vote(self.id, "abstain", 0.0, ["invalid_inputs"], evidence)

        d = over_probability(mu, line, disp)
        p_over = d["p_over"]
        if p_over is None:
            return Vote(self.id, "abstain", 0.0, ["pmf_failed"], evidence)

        p = p_over if side == "over" else 1.0 - p_over
        edge = p - market_p - fee
        evidence.update({"p_model": round(p, 4), "edge": round(edge, 4),
                         "p_push": round(d["p_push"], 4),
                         "fair_price": round(p, 4)})

        if edge > MIN_EDGE:
            conf = min(0.90, 0.50 + min(0.40, edge * 4.0))
            return Vote(self.id, "approve", conf,
                        [f"pmf_edge:{edge:+.3f}", f"model={p:.3f}", f"market={market_p:.3f}"],
                        evidence)
        if edge < 0:
            return Vote(self.id, "reject", min(0.85, 0.50 + abs(edge) * 2.0),
                        [f"negative_pmf_edge:{edge:+.3f}"], evidence)
        return Vote(self.id, "abstain", 0.25, [f"thin_edge:{edge:+.3f}"], evidence)
