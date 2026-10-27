"""Per-Coin Specialist Bots for ALL 1,492 KuCoin + Hyperliquid (Propr) Coins & Multi-Service Allocators.

Covers the complete union of:
  - 702 KuCoin Futures contracts (USDT-M, USDC-M, and Coin-M Inverse `SOLUSDM`/`XBTUSDM`/`ETHUSDM`/`XRPUSDM`)
  - 816 KuCoin USDT/USDC Spot pairs (`postOnly` Maker + `/api/v3/oco/order` brackets + Cross/Isolated Margin)
  - 179 Active Hyperliquid (`Propr`) Perpetuals (`BTC`, `ETH`, `HYPE`, `ZEC`, `NEAR`, `SOL`, `XRP`, `ENA`, `PUMP`, `XMR`, ...)
  - 347 Hyperliquid HIP-1 / HIP-2 Spot pairs

Architecture:
  - `EXCHANGE_COIN_REGISTRY` (`sovereign_bots/data/exchange_coin_registry.json`) holds calibrated
    microstructure bounds (`liquidity_tier`, `max_spread_bps`, `min_vol_pct`, `max_vol_pct`,
    `funding_crowd_bps`, `venues`, `max_lev`, `vol24h_usd`) for all 1,492 unique coins.
  - `ALL_1492_COIN_BOTS`: Dynamically instantiated `_BaseCoinSpecialistBot` instances (`coin_<symbol>`)
    for every single one of the 1,492 coins (`get_coin_bot("SOL")`, `get_coin_bots_for_venue("hyperliquid_propr_perp")`,
    `get_coin_bots_for_venue("kucoin_usdtm_perp")`, `get_coin_bots_for_venue("kucoin_spot")`).
  - `COIN_SPECIALIST_CLASSES` / `COIN_SPECIALIST_BOTS`: 20 committee seats (15 flagship coin specialist seats +
    1 Universal 1,492-Coin Specialist Router + 4 Multi-Service KuCoin & Hyperliquid Venue Allocators).
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional, Tuple
from ...core.bot import Bot, Vote
from ...core.knowledge_loop import COIN_DEFAULTS, load_knowledge_state

_REGISTRY_PATH = os.path.join(
    os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))),
    "data",
    "exchange_coin_registry.json",
)


def _load_registry() -> Dict[str, Dict[str, Any]]:
    if os.path.exists(_REGISTRY_PATH):
        try:
            with open(_REGISTRY_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            if isinstance(data, dict):
                return data
        except Exception:
            pass
    return dict(COIN_DEFAULTS)


EXCHANGE_COIN_REGISTRY: Dict[str, Dict[str, Any]] = _load_registry()


def _normalize_coin(raw: Any) -> str:
    if not raw:
        return ""
    s = str(raw).upper().strip()
    for suffix in ("-USDT", "-USDC", "USDTM", "USDM", "USDCM", "USDT", "USDC", "-PERP", "/USDT"):
        if s.endswith(suffix) and len(s) > len(suffix):
            s = s[: -len(suffix)]
            break
    if s in ("XBT", "WBTC"):
        return "BTC"
    return s


def _f(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


class _BaseCoinSpecialistBot(Bot):
    """Dedicated single-coin microstructure, funding, basis & venue-routing specialist bot."""

    id: str = "coin_btc"
    mandate: str = "Per-coin microstructure, volatility regime, funding equilibrium, and venue routing specialist."
    domains: Tuple[str, ...] = ("crypto",)
    veto: bool = False
    edge_seat: bool = True
    target_coin: str = "BTC"

    def __init__(self, target_coin: Optional[str] = None, bot_id: Optional[str] = None) -> None:
        if target_coin is not None:
            self.target_coin = target_coin.upper()
        if bot_id is not None:
            self.id = bot_id
        elif not getattr(self, "id", None) or self.id == "bot":
            self.id = f"coin_{self.target_coin.lower()}"

    def evaluate_for_coin(self, coin: str, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        vol = _f(ctx.get("daily_vol_pct") if "daily_vol_pct" in ctx else ctx.get("atr_pct"))
        spread = _f(ctx.get("spread_bps"))
        rr = _f(ctx.get("rr"))
        funding = _f(ctx.get("funding_rate") if "funding_rate" in ctx else ctx.get("funding_8h"))
        side = str(ctx.get("side") or ctx.get("direction") or "long").lower()
        basis_bps = _f(ctx.get("basis_bps"))

        if vol is None and spread is None and rr is None:
            return Vote(self.id, "abstain", 0.0, [f"{self.id}_insufficient_metrics"], {"coin": coin})

        reg_cfg = EXCHANGE_COIN_REGISTRY.get(coin) or COIN_DEFAULTS.get(coin) or {}
        state = load_knowledge_state()
        learned_cfg = state.get("coins", {}).get(coin) or {}

        min_vol = float(learned_cfg.get("min_vol_pct") or reg_cfg.get("min_vol_pct", 0.7))
        max_vol = float(learned_cfg.get("max_vol_pct") or reg_cfg.get("max_vol_pct", 8.5))
        max_spread = float(learned_cfg.get("max_spread_bps") or reg_cfg.get("max_spread_bps", 8.0))
        crowd_bps = float(learned_cfg.get("funding_crowd_bps") or reg_cfg.get("funding_crowd_bps", 16.0))
        mwu_weight = float(learned_cfg.get("mwu_weight", 1.0))
        ema_r = float(learned_cfg.get("ema_realized_r", 0.35))
        venues = learned_cfg.get("preferred_venues") or reg_cfg.get("venues") or ["kucoin_usdtm_perp", "hyperliquid_propr_perp"]
        tier = reg_cfg.get("liquidity_tier", "tier2_liquid")

        reasons: List[str] = [f"coin_specialist:{coin}", f"tier:{tier}", f"venues:{'/'.join(venues[:2])}"]
        evidence: Dict[str, Any] = {"coin": coin, "tier": tier, "venues": venues, "mwu_weight": mwu_weight}
        score = 0.72 * min(1.15, max(0.80, mwu_weight))

        if vol is not None:
            if vol < min_vol * 0.55:
                return Vote(self.id, "reject", 0.70, reasons + [f"{coin}_dead_vol:{vol:.2f}%<{min_vol:.2f}%"], evidence)
            if vol > max_vol * 1.45:
                return Vote(self.id, "reject", 0.76, reasons + [f"{coin}_disorderly_vol:{vol:.2f}%>{max_vol:.2f}%"], evidence)
            if min_vol <= vol <= max_vol:
                score += 0.06
                reasons.append(f"{coin}_vol_in_sweet_spot:{vol:.2f}%")

        if spread is not None:
            if spread > max_spread * 1.6:
                return Vote(self.id, "reject", 0.74, reasons + [f"{coin}_wide_spread:{spread:.1f}bps>{max_spread:.1f}bps"], evidence)
            if spread <= max_spread:
                score += 0.04
                reasons.append(f"{coin}_tight_l2:{spread:.1f}bps")

        if funding is not None:
            f_bps = funding * 10000.0
            if side == "long" and f_bps > crowd_bps:
                return Vote(self.id, "reject", 0.72, reasons + [f"{coin}_crowded_long_funding:{f_bps:.1f}bps"], evidence)
            if side == "short" and f_bps < -crowd_bps:
                return Vote(self.id, "reject", 0.72, reasons + [f"{coin}_crowded_short_funding:{f_bps:.1f}bps"], evidence)
            if (side == "short" and f_bps > 1.5) or (side == "long" and f_bps < -1.5):
                score += 0.05
                reasons.append(f"{coin}_positive_funding_carry:{f_bps:.1f}bps")

        if basis_bps is not None:
            if side == "short" and basis_bps >= 4.0:
                score += 0.04
                reasons.append(f"{coin}_mark_premium_fade:+{basis_bps:.1f}bps")
            elif side == "long" and basis_bps <= -4.0:
                score += 0.04
                reasons.append(f"{coin}_mark_discount_reversion:{basis_bps:.1f}bps")

        if rr is not None and rr < 1.35:
            return Vote(self.id, "reject", 0.75, reasons + [f"{coin}_rr_below_hurdle:{rr:.2f}"], evidence)

        reasons.append(f"learned_ema_r:{ema_r:+.2f}R")
        return Vote(self.id, "approve", min(0.89, round(score, 4)), reasons, evidence)

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        raw_coin = ctx.get("coin") or ctx.get("symbol") or ctx.get("asset")
        coin = _normalize_coin(raw_coin)
        if not coin:
            return Vote(self.id, "abstain", 0.0, [f"{self.id}_missing_coin"], {})
        if coin != self.target_coin:
            return Vote(self.id, "abstain", 0.0, [f"not_target_coin:{coin}!={self.target_coin}"], {"coin": coin})
        return self.evaluate_for_coin(coin, ctx)


class CoinBtcSpecialistBot(_BaseCoinSpecialistBot):
    id = "coin_btc"
    target_coin = "BTC"

class CoinEthSpecialistBot(_BaseCoinSpecialistBot):
    id = "coin_eth"
    target_coin = "ETH"

class CoinSolSpecialistBot(_BaseCoinSpecialistBot):
    id = "coin_sol"
    target_coin = "SOL"

class CoinHypeSpecialistBot(_BaseCoinSpecialistBot):
    id = "coin_hype"
    target_coin = "HYPE"

class CoinXmrSpecialistBot(_BaseCoinSpecialistBot):
    id = "coin_xmr"
    target_coin = "XMR"

class CoinZecSpecialistBot(_BaseCoinSpecialistBot):
    id = "coin_zec"
    target_coin = "ZEC"

class CoinXrpSpecialistBot(_BaseCoinSpecialistBot):
    id = "coin_xrp"
    target_coin = "XRP"

class CoinDogeSpecialistBot(_BaseCoinSpecialistBot):
    id = "coin_doge"
    target_coin = "DOGE"

class CoinSuiSpecialistBot(_BaseCoinSpecialistBot):
    id = "coin_sui"
    target_coin = "SUI"

class CoinNearSpecialistBot(_BaseCoinSpecialistBot):
    id = "coin_near"
    target_coin = "NEAR"

class CoinAvaxSpecialistBot(_BaseCoinSpecialistBot):
    id = "coin_avax"
    target_coin = "AVAX"

class CoinLinkSpecialistBot(_BaseCoinSpecialistBot):
    id = "coin_link"
    target_coin = "LINK"

class CoinBnbSpecialistBot(_BaseCoinSpecialistBot):
    id = "coin_bnb"
    target_coin = "BNB"

class CoinAaveSpecialistBot(_BaseCoinSpecialistBot):
    id = "coin_aave"
    target_coin = "AAVE"

class CoinEnaSpecialistBot(_BaseCoinSpecialistBot):
    id = "coin_ena"
    target_coin = "ENA"


ALL_1492_COIN_BOTS: Dict[str, _BaseCoinSpecialistBot] = {
    sym: _BaseCoinSpecialistBot(target_coin=sym, bot_id=f"coin_{sym.lower()}")
    for sym in sorted(EXCHANGE_COIN_REGISTRY.keys())
}


def get_coin_bot(symbol: str) -> _BaseCoinSpecialistBot:
    norm = _normalize_coin(symbol)
    if norm in ALL_1492_COIN_BOTS:
        return ALL_1492_COIN_BOTS[norm]
    return _BaseCoinSpecialistBot(target_coin=norm or "BTC", bot_id=f"coin_{(norm or 'btc').lower()}")


def get_coin_bots_for_venue(venue: str) -> List[_BaseCoinSpecialistBot]:
    return [
        ALL_1492_COIN_BOTS[sym]
        for sym, cfg in EXCHANGE_COIN_REGISTRY.items()
        if venue in (cfg.get("venues") or [])
    ]


class UniversalCatalogCoinRouterBot(Bot):
    id = "universal_1492_coin_specialist"
    mandate = "Route any of the 1,492 KuCoin & Hyperliquid/Propr coins to its dedicated per-coin specialist bot."
    domains = ("crypto",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        raw_coin = ctx.get("coin") or ctx.get("symbol") or ctx.get("asset")
        coin = _normalize_coin(raw_coin)
        if not coin:
            return Vote(self.id, "abstain", 0.0, ["universal_coin_router_no_symbol"], {})
        bot = get_coin_bot(coin)
        v = bot.evaluate_for_coin(coin, ctx)
        return Vote(self.id, v.decision, v.confidence, [f"routed_to:{bot.id}"] + list(v.reasons), v.evidence)


class KuCoinMultiServiceAllocatorBot(Bot):
    id = "kucoin_multiservice_allocator"
    mandate = "Allocate capital across KuCoin Spot OCO, Spot Maker, Margin, Coin-M Inverse, and USDT-M Perps."
    domains = ("crypto",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        coin = _normalize_coin(ctx.get("coin") or ctx.get("symbol"))
        rr = _f(ctx.get("rr"))
        spread = _f(ctx.get("spread_bps"))
        funding = _f(ctx.get("funding_rate") if "funding_rate" in ctx else ctx.get("funding_8h"))
        side = str(ctx.get("side") or ctx.get("direction") or "").lower()
        if not coin and rr is None and spread is None:
            return Vote(self.id, "abstain", 0.0, ["missing_kucoin_allocation_context"], {})

        sleeves: List[str] = []
        f_val = funding or 0.0
        if abs(f_val) >= 0.0008:
            sleeves.append("kucoin_extreme_funding_harvester")
        if f_val >= 0.0003:
            sleeves.append("kucoin_spot_perp_cash_and_carry")
        if coin in ("BTC", "ETH", "SOL", "XRP"):
            sleeves.append(f"kucoin_coinm_{coin.lower()}usdm_basis")
        sleeves.append("kucoin_spot_oco_bracket")
        sleeves.append("kucoin_usdtm_postonly_maker")

        if spread is not None and spread > 16.0:
            return Vote(self.id, "reject", 0.72, [f"kucoin_spread_too_wide:{spread:.1f}bps"], {"sleeves": sleeves})
        if rr is not None and rr < 1.30:
            return Vote(self.id, "reject", 0.74, [f"kucoin_rr_below_min:{rr:.2f}"], {"sleeves": sleeves})

        primary = sleeves[0]
        if side in ("short", "long") and (rr or 2.0) >= 1.8:
            primary = "kucoin_usdtm_postonly_maker+spot_oco"
        return Vote(self.id, "approve", 0.79, [f"kucoin_sleeve:{primary}", f"active_sleeves:{len(sleeves)}"], {"sleeves": sleeves})


class KuCoinSpotOcoGridBot(Bot):
    id = "kucoin_spot_oco_grid"
    mandate = "Evaluate unlevered KuCoin Spot Post-Only Maker + OCO (One-Cancels-the-Other) range brackets."
    domains = ("crypto",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        vol = _f(ctx.get("daily_vol_pct") if "daily_vol_pct" in ctx else ctx.get("atr_pct"))
        spread = _f(ctx.get("spread_bps"))
        rr = _f(ctx.get("rr"))
        if vol is None and spread is None and rr is None:
            return Vote(self.id, "abstain", 0.0, ["missing_spot_oco_context"], {})
        if spread is not None and spread > 15.0:
            return Vote(self.id, "reject", 0.70, [f"spot_oco_spread_too_wide:{spread:.1f}bps"], {})
        if vol is not None and vol < 0.5:
            return Vote(self.id, "reject", 0.68, [f"spot_oco_range_too_tight:{vol:.2f}%"], {})
        if rr is not None and rr < 1.35:
            return Vote(self.id, "reject", 0.71, [f"spot_oco_rr_low:{rr:.2f}"], {})
        return Vote(self.id, "approve", 0.76, ["spot_oco_bracket_viable", "zero_liquidation_risk"], {})


class HyperliquidProprMicrostructureBot(Bot):
    id = "hyperliquid_propr_microstructure"
    mandate = "Evaluate Hyperliquid 179 Perps & 347 HIP-2 Spot pairs for Propr $25K evaluation compliance."
    domains = ("crypto",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        spread = _f(ctx.get("spread_bps"))
        slippage = _f(ctx.get("est_slippage_bps") if "est_slippage_bps" in ctx else ctx.get("slippage_bps"))
        rr = _f(ctx.get("rr"))
        daily_loss = _f(ctx.get("daily_loss_used_pct"))
        if spread is None and rr is None and slippage is None:
            return Vote(self.id, "abstain", 0.0, ["missing_hl_propr_context"], {})
        if daily_loss is not None and daily_loss > 2.2:
            return Vote(self.id, "reject", 0.84, [f"propr_stay_green_throttle:{daily_loss:.2f}%"], {})
        if spread is not None and spread > 12.0:
            return Vote(self.id, "reject", 0.75, [f"hl_l2_spread_wide:{spread:.1f}bps"], {})
        if slippage is not None and slippage > 10.0:
            return Vote(self.id, "reject", 0.75, [f"hl_ioc_slippage_high:{slippage:.1f}bps"], {})
        if rr is not None and rr < 1.50:
            return Vote(self.id, "reject", 0.76, [f"hl_propr_rr_low:{rr:.2f}<1.50"], {})
        return Vote(self.id, "approve", 0.81, ["hl_propr_l2_clean", "hip2_spot_perp_aligned"], {})


class HyperliquidFundingBasisArbBot(Bot):
    id = "hyperliquid_funding_basis_arb"
    mandate = "Evaluate Hyperliquid 1-hour continuous funding rate carry + Mark-Oracle basis convergence."
    domains = ("crypto",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        funding = _f(ctx.get("funding_rate") if "funding_rate" in ctx else ctx.get("funding_8h"))
        side = str(ctx.get("side") or ctx.get("direction") or "long").lower()
        basis_bps = _f(ctx.get("basis_bps"))
        if funding is None and basis_bps is None:
            return Vote(self.id, "abstain", 0.0, ["missing_hl_funding_basis"], {})
        f_bps = (funding or 0.0) * 10000.0
        if side == "long" and f_bps > 15.0:
            return Vote(self.id, "reject", 0.77, [f"hl_paying_extreme_long_funding:{f_bps:.1f}bps"], {})
        if side == "short" and f_bps < -15.0:
            return Vote(self.id, "reject", 0.77, [f"hl_paying_extreme_short_funding:{f_bps:.1f}bps"], {})
        reasons = [f"hl_funding_bps:{f_bps:.2f}"]
        if basis_bps is not None:
            reasons.append(f"hl_mark_oracle_basis_bps:{basis_bps:+.2f}")
        return Vote(self.id, "approve", 0.77, reasons, {"funding_bps": f_bps})


COIN_SPECIALIST_CLASSES: Tuple[type, ...] = (
    CoinBtcSpecialistBot,
    CoinEthSpecialistBot,
    CoinSolSpecialistBot,
    CoinHypeSpecialistBot,
    CoinXmrSpecialistBot,
    CoinZecSpecialistBot,
    CoinXrpSpecialistBot,
    CoinDogeSpecialistBot,
    CoinSuiSpecialistBot,
    CoinNearSpecialistBot,
    CoinAvaxSpecialistBot,
    CoinLinkSpecialistBot,
    CoinBnbSpecialistBot,
    CoinAaveSpecialistBot,
    CoinEnaSpecialistBot,
    UniversalCatalogCoinRouterBot,
    KuCoinMultiServiceAllocatorBot,
    KuCoinSpotOcoGridBot,
    HyperliquidProprMicrostructureBot,
    HyperliquidFundingBasisArbBot,
)

COIN_SPECIALIST_BOTS: Tuple[Bot, ...] = tuple(cls() for cls in COIN_SPECIALIST_CLASSES)
