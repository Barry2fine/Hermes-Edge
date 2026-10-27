"""Polymarket bot pack: 29 independent votes over one prediction-market context (6 Hard VETO + 23 Soft/Edge)."""

from __future__ import annotations

from .barrier import BarrierBot
from .calibration_drift import CalibrationDriftBot
from .carry import CarryBot
from .category_specialists import (
    POLY_CATEGORY_BOTS,
    POLY_CATEGORY_CLASSES,
    TAG_TO_CANONICAL_CATEGORY,
    PolyAiTechCategoryBot,
    PolyCryptoCategoryBot,
    PolyEarningsIpoCategoryBot,
    PolyEntertainmentCategoryBot,
    PolyGeopoliticsCategoryBot,
    PolyMacroCategoryBot,
    PolyPoliticsCategoryBot,
    PolyRegulatoryCategoryBot,
    PolySportsCategoryBot,
    PolyUniversalTagRouterBot,
    PolyWeatherCategoryBot,
    resolve_canonical_category,
)
from .clodds_synthetic_implied_arb import CloddsSyntheticImpliedArbBot
from .crosslink import CrosslinkBot
from .fee_hurdle import FeeHurdleBot
from .kalshi_cross_arb import KalshiPolymarketCrossArbBot
from .liquidity import LiquidityBot
from .martingale_belief_vol import MartingaleBeliefVolatilityBot
from .orderbook_sweep_velocity import SmartMoneyOrderbookSweepBot
from .polymarket_postmortem import PolymarketPostmortemBot
from .resolution import ResolutionBot
from .resolution_clarity import ResolutionClarityBot
from .taxonomy import TaxonomyBot
from .time_decay import TimeDecayBot
from .whale_consensus import WhaleConsensusBot
from ..sports.motivation import MotivationBot
from ..sports.public_bias import PublicBiasBot

BOTS = [
    BarrierBot(),
    TaxonomyBot(),
    ResolutionBot(),
    LiquidityBot(),
    CalibrationDriftBot(),
    CrosslinkBot(),
    MotivationBot(),
    PublicBiasBot(),
    WhaleConsensusBot(),
    FeeHurdleBot(),
    CarryBot(),
    ResolutionClarityBot(),
    TimeDecayBot(),
    PolymarketPostmortemBot(),
    KalshiPolymarketCrossArbBot(),
    MartingaleBeliefVolatilityBot(),
    SmartMoneyOrderbookSweepBot(),
    CloddsSyntheticImpliedArbBot(),
    *POLY_CATEGORY_BOTS,
]

__all__ = [
    "BOTS",
    "POLY_CATEGORY_BOTS", "POLY_CATEGORY_CLASSES", "TAG_TO_CANONICAL_CATEGORY", "resolve_canonical_category",
    "BarrierBot", "TaxonomyBot", "ResolutionBot", "LiquidityBot",
    "CalibrationDriftBot", "CrosslinkBot", "MotivationBot", "PublicBiasBot",
    "WhaleConsensusBot", "FeeHurdleBot", "CarryBot", "ResolutionClarityBot",
    "TimeDecayBot", "PolymarketPostmortemBot", "KalshiPolymarketCrossArbBot",
    "MartingaleBeliefVolatilityBot", "SmartMoneyOrderbookSweepBot",
    "CloddsSyntheticImpliedArbBot",
    "PolyCryptoCategoryBot", "PolyPoliticsCategoryBot", "PolyMacroCategoryBot",
    "PolyGeopoliticsCategoryBot", "PolySportsCategoryBot", "PolyAiTechCategoryBot",
    "PolyEarningsIpoCategoryBot", "PolyRegulatoryCategoryBot", "PolyWeatherCategoryBot",
    "PolyEntertainmentCategoryBot", "PolyUniversalTagRouterBot",
]
