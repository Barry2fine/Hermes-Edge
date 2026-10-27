"""Sports bot pack: 23 independent votes over one market context (7 Hard VETO + 16 Soft/Edge)."""

from __future__ import annotations

from .calibration_drift import CalibrationDriftBot
from .circadian import CircadianBot
from .clv import CLVBot
from .coach_intent import CoachIntentBot
from .conditions import ConditionsBot
from .copula_sgp import StudentTCopulaSGPBot
from .elo import EloBot
from .injury import InjuryBot
from .line_value import LineValueBot
from .liquidity import LiquidityBot
from .motivation import MotivationBot
from .pace_adjust import PaceAdjustBot
from .pinnacle_devig_steam import PinnacleDevigSharpSteamBot
from .prop_pmf import PropPMFBot
from .public_bias import PublicBiasBot
from .schedule_spot import ScheduleSpotBot
from .small_sample_lcb import SmallSampleOverfitBot
from .sports_postmortem import SportsPostmortemBot
from .starter_gate import StarterGateBot
from .timing import TimingBot
from .usage_redistribution import InjuryUsageRedistributionBot
from .vig_ceiling import JuiceAndVigCeilingBot
from .xg import ExpectedGoalsBot

BOTS = [
    EloBot(),
    StarterGateBot(),
    InjuryBot(),
    LineValueBot(),
    LiquidityBot(),
    CLVBot(),
    MotivationBot(),
    ScheduleSpotBot(),
    PublicBiasBot(),
    ConditionsBot(),
    CoachIntentBot(),
    PropPMFBot(),
    ExpectedGoalsBot(),
    PaceAdjustBot(),
    CircadianBot(),
    CalibrationDriftBot(),
    TimingBot(),
    JuiceAndVigCeilingBot(),
    SmallSampleOverfitBot(),
    SportsPostmortemBot(),
    PinnacleDevigSharpSteamBot(),
    InjuryUsageRedistributionBot(),
    StudentTCopulaSGPBot(),
]

BOT_IDS = [b.id for b in BOTS]
VETO_IDS = [b.id for b in BOTS if b.veto]
NON_VETO_IDS = [b.id for b in BOTS if not b.veto]


def describe():
    return {
        "domain": "sports",
        "count": len(BOTS),
        "bots": [{"id": b.id, "veto": b.veto, "mandate": b.mandate} for b in BOTS],
    }


__all__ = [
    "BOTS", "BOT_IDS", "VETO_IDS", "NON_VETO_IDS", "describe",
    "EloBot", "StarterGateBot", "InjuryBot", "LineValueBot", "LiquidityBot",
    "CLVBot", "MotivationBot", "CoachIntentBot", "PropPMFBot", "ExpectedGoalsBot",
    "PaceAdjustBot", "CircadianBot", "ConditionsBot", "PublicBiasBot",
    "ScheduleSpotBot", "CalibrationDriftBot", "TimingBot",
    "JuiceAndVigCeilingBot", "SmallSampleOverfitBot", "SportsPostmortemBot",
    "PinnacleDevigSharpSteamBot", "InjuryUsageRedistributionBot", "StudentTCopulaSGPBot",
]
