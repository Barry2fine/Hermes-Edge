"""bet365 Ontario Bonus Bet (Stake-Not-Returned / SNR) Conversion Optimizer Seat (Soft Edge Seat).

When `is_bonus_bet=True`, switches evaluation from standard cash EV `p*d - 1` to
Stake-Not-Returned (SNR) Bonus Bet Conversion Rate `CR = p_true * (d - 1)`:
rejects short-odds bonus bet placements where the unreturned stake destroys conversion
value (`CR < 0.65` or single-ticket decimal odds `< 2.10`), and approves high-conversion
plus-money props and multi-leg parlays (`CR >= 0.85`).
"""

from __future__ import annotations
from typing import Any, Dict, Optional
from ...core.bot import Bot, Vote


def _num(x: Any) -> Optional[float]:
    if x is None or isinstance(x, bool):
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


class BonusBetSNROptimizerBot(Bot):
    id = "bonus_bet_snr_optimizer"
    mandate = ("Optimize bet365 Ontario Stake-Not-Returned (SNR) Bonus Bet conversion "
               "rate CR = p_true * (d - 1) and block low-conversion short-odds usage.")
    domains = ("sports",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        if not bool(ctx.get("is_bonus_bet", False)):
            return Vote(self.id, "abstain", 0.0, ["cash_wager_not_bonus_bet"], {})

        model_p = _num(ctx.get("model_p"))
        dec = _num(ctx.get("bet365_decimal_odds") or ctx.get("decimal_odds"))
        if model_p is None or dec is None or dec <= 1.0:
            return Vote(self.id, "abstain", 0.0, ["missing_bonus_bet_inputs"], {})

        cr = model_p * (dec - 1.0)
        evidence = {"model_p": model_p, "decimal_odds": dec, "snr_conversion_rate": round(cr, 4)}

        if dec < 2.10 and cr < 0.65:
            return Vote(self.id, "reject", 0.84, [f"snr_short_odds_value_burn:d={dec:.2f},cr={cr*100:.1f}%"], evidence)
        if cr >= 0.85:
            return Vote(self.id, "approve", min(0.93, 0.55 + (cr - 0.70) * 0.6), [f"high_snr_bonus_conversion:{cr*100:.1f}%"], evidence)
        if cr < 0.60:
            return Vote(self.id, "reject", 0.78, [f"low_snr_bonus_conversion:{cr*100:.1f}%"], evidence)
        return Vote(self.id, "abstain", 0.0, [f"moderate_snr_conversion:{cr*100:.1f}%"], evidence)
