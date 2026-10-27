"""Breaker bot — circuit breakers on drawdown and losing streaks. VETO.

Rules (contract), thresholds in percent of equity / count of trades:
  * drawdown ``(equity_peak - equity) / equity_peak >= 6%`` or
    ``daily_loss_pct >= 4``                                   -> reject
  * drawdown ``>= 3%`` or ``daily_loss_pct >= 2`` or
    ``consecutive_losses >= 3``                               -> approve, confidence 0.4,
                                                                 reason "reduce"
  * otherwise                                                 -> approve

``daily_loss_pct`` is expressed in percent (2 == 2%).  ``recent_r`` is
reported as evidence only; the breaker never averages it into the verdict
because the explicit counters are the audited source of truth.
"""

from __future__ import annotations

from typing import Any, Dict, Optional

from ...core.bot import Bot, Vote

DD_HARD = 0.06
DD_SOFT = 0.03
DAILY_HARD = 4.0
DAILY_SOFT = 2.0
STREAK_SOFT = 3


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        v = float(str(x).strip().rstrip("%")) if isinstance(x, str) else float(x)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):
        return None
    return v


class BreakerBot(Bot):
    id = "breaker"
    mandate = ("Pull the plug on a damaged book: hard drawdown/daily-loss limits "
               "veto, soft limits downgrade the whole desk to reduce-only.")
    domains = ("crypto",)
    veto = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        equity = _num(ctx.get("equity"))
        peak = _num(ctx.get("equity_peak"))
        daily = _num(ctx.get("daily_loss_pct"))
        streak = _num(ctx.get("consecutive_losses"))
        recent_r = ctx.get("recent_r")
        if not isinstance(recent_r, (list, tuple)):
            recent_r = []

        if equity is None or peak is None:
            return Vote(self.id, "reject", 0.0, ["missing:equity/equity_peak"],
                        {"equity": equity, "equity_peak": peak})
        if equity <= 0 or peak <= 0:
            return Vote(self.id, "reject", 0.9, ["invalid:equity_non_positive"],
                        {"equity": equity, "equity_peak": peak})

        dd = max(0.0, (peak - equity) / peak)
        daily = 0.0 if daily is None else max(0.0, daily)
        streak = 0.0 if streak is None else max(0.0, streak)
        evidence: Dict[str, Any] = {
            "drawdown_pct": round(dd * 100.0, 4), "daily_loss_pct": daily,
            "consecutive_losses": streak, "recent_r": list(recent_r)[-10:],
        }

        if dd >= DD_HARD or daily >= DAILY_HARD:
            hits = []
            if dd >= DD_HARD:
                hits.append(f"drawdown={dd * 100:.2f}%>={DD_HARD * 100:.0f}%")
            if daily >= DAILY_HARD:
                hits.append(f"daily_loss={daily:.2f}%>={DAILY_HARD:.0f}%")
            return Vote(self.id, "reject", 0.95, ["hard_breaker:" + h for h in hits],
                        evidence)

        soft = []
        if dd >= DD_SOFT:
            soft.append(f"drawdown={dd * 100:.2f}%>={DD_SOFT * 100:.0f}%")
        if daily >= DAILY_SOFT:
            soft.append(f"daily_loss={daily:.2f}%>={DAILY_SOFT:.0f}%")
        if streak >= STREAK_SOFT:
            soft.append(f"streak={int(streak)}>={STREAK_SOFT}")
        if soft:
            return Vote(self.id, "approve", 0.4, ["reduce:" + s for s in soft], evidence)

        return Vote(self.id, "approve", 0.85,
                    [f"no_breaker:drawdown={dd * 100:.2f}%", f"daily_loss={daily:.2f}%"],
                    evidence)
