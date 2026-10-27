"""CRYPTO bot pack: 50 independent committee seats + 1,492 instantiated Per-Coin Specialist Bots."""

from __future__ import annotations

from ...core.bot import Bot, Vote
from .adr_exhaustion import AdrExhaustionChaseBot
from .breaker import BreakerBot
from .calibration_drift import CalibrationDriftBot
from .candle_geometry_tensor import CandlestickGeometryTensorBot
from .chronos_conformal_interval import ChronosConformalIntervalBot
from .coin_specialists import (
    ALL_1492_COIN_BOTS,
    COIN_SPECIALIST_BOTS,
    COIN_SPECIALIST_CLASSES,
    EXCHANGE_COIN_REGISTRY,
    CoinAaveSpecialistBot,
    CoinAvaxSpecialistBot,
    CoinBnbSpecialistBot,
    CoinBtcSpecialistBot,
    CoinDogeSpecialistBot,
    CoinEnaSpecialistBot,
    CoinEthSpecialistBot,
    CoinHypeSpecialistBot,
    CoinLinkSpecialistBot,
    CoinNearSpecialistBot,
    CoinSolSpecialistBot,
    CoinSuiSpecialistBot,
    CoinXmrSpecialistBot,
    CoinXrpSpecialistBot,
    CoinZecSpecialistBot,
    HyperliquidFundingBasisArbBot,
    HyperliquidProprMicrostructureBot,
    KuCoinMultiServiceAllocatorBot,
    KuCoinSpotOcoGridBot,
    UniversalCatalogCoinRouterBot,
    get_coin_bot,
    get_coin_bots_for_venue,
)
from .correlated_cluster import CorrelatedClusterBetaBot
from .cross_venue_lead_lag import CrossVenueLeadLagBot
from .cryptobert_narrative_divergence import CryptoBertNarrativeDivergenceBot
from .data_integrity import DataIntegrityBot
from .doubt import DoubtBot
from .entry_quality import EntryQualityBot
from .execution import ExecutionBot
from .funding import FundingBot
from .funding_carry import FundingCarryBot
from .funding_settlement_trap import FundingSettlementTrapBot
from .ict import ICTBot
from .impact import ImpactBot
from .kelly_portfolio import KellyPortfolioBot
from .kronos_kline_quantizer import KronosKlineQuantizerBot
from .liquidation_oi import LiquidationCascadeOIBot
from .memory_base_rate import MemoryBaseRateBot
from .momentum import MomentumBot
from .nof0_arena_sortino_decay import Nof0ArenaSortinoDecayBot
from .options_gex_skew import DeribitOptionsGexSkewBot
from .postmortem_gate import PostmortemBot
from .reconciliation import ReconciliationBot
from .regime import RegimeBot
from .regime_cluster import RegimeClusterBot
from .risk_budget import RiskBudgetBot
from .vpin_toxicity import VpinToxicFlowBot

BOT_CLASSES = (
    RegimeBot, RegimeClusterBot, BreakerBot, ExecutionBot, EntryQualityBot,
    DoubtBot, ImpactBot, DataIntegrityBot, ReconciliationBot, CalibrationDriftBot,
    RiskBudgetBot, PostmortemBot,
    FundingSettlementTrapBot, VpinToxicFlowBot, CorrelatedClusterBetaBot, AdrExhaustionChaseBot,
    ChronosConformalIntervalBot, CryptoBertNarrativeDivergenceBot,
    MomentumBot, ICTBot, FundingBot, FundingCarryBot, MemoryBaseRateBot,
    KellyPortfolioBot,
    CrossVenueLeadLagBot, DeribitOptionsGexSkewBot, LiquidationCascadeOIBot, CandlestickGeometryTensorBot,
    KronosKlineQuantizerBot, Nof0ArenaSortinoDecayBot,
) + COIN_SPECIALIST_CLASSES

BOTS = [cls() for cls in BOT_CLASSES]
BOT_IDS = [b.id for b in BOTS]
VETO_IDS = [b.id for b in BOTS if b.veto]

__all__ = [
    "Bot", "Vote", "BOTS", "BOT_CLASSES", "BOT_IDS", "VETO_IDS",
    "ALL_1492_COIN_BOTS", "COIN_SPECIALIST_BOTS", "COIN_SPECIALIST_CLASSES",
    "EXCHANGE_COIN_REGISTRY", "get_coin_bot", "get_coin_bots_for_venue",
    "RegimeBot", "RegimeClusterBot", "BreakerBot", "ExecutionBot",
    "EntryQualityBot", "DoubtBot", "ImpactBot", "DataIntegrityBot",
    "ReconciliationBot", "PostmortemBot", "CalibrationDriftBot", "RiskBudgetBot",
    "FundingSettlementTrapBot", "VpinToxicFlowBot", "CorrelatedClusterBetaBot", "AdrExhaustionChaseBot",
    "ChronosConformalIntervalBot", "CryptoBertNarrativeDivergenceBot",
    "MomentumBot", "ICTBot", "FundingBot", "FundingCarryBot", "MemoryBaseRateBot", "KellyPortfolioBot",
    "CrossVenueLeadLagBot", "DeribitOptionsGexSkewBot", "LiquidationCascadeOIBot", "CandlestickGeometryTensorBot",
    "KronosKlineQuantizerBot", "Nof0ArenaSortinoDecayBot",
    "CoinBtcSpecialistBot", "CoinEthSpecialistBot", "CoinSolSpecialistBot", "CoinHypeSpecialistBot",
    "CoinXmrSpecialistBot", "CoinZecSpecialistBot", "CoinXrpSpecialistBot", "CoinDogeSpecialistBot",
    "CoinSuiSpecialistBot", "CoinNearSpecialistBot", "CoinAvaxSpecialistBot", "CoinLinkSpecialistBot",
    "CoinBnbSpecialistBot", "CoinAaveSpecialistBot", "CoinEnaSpecialistBot",
    "UniversalCatalogCoinRouterBot", "KuCoinMultiServiceAllocatorBot",
    "KuCoinSpotOcoGridBot", "HyperliquidProprMicrostructureBot", "HyperliquidFundingBasisArbBot",
]
