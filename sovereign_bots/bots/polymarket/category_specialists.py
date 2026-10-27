"""Per-Category Polymarket Specialist Bots (All 518 Gamma API Tags + 10 Canonical Category Seats)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple
from ...core.bot import Bot, Vote
from ...core.knowledge_loop import POLY_CATEGORY_DEFAULTS, load_knowledge_state

TAG_TO_CANONICAL_CATEGORY: Dict[str, str] = {
    "crypto": "crypto", "crypto-prices": "crypto", "bitcoin": "crypto", "ethereum": "crypto",
    "solana": "crypto", "xrp": "crypto", "ripple": "crypto", "up-or-down": "crypto",
    "5m": "crypto", "hit-price": "crypto", "price-milestone": "crypto", "fdv": "crypto",
    "token-launch": "crypto", "airdrops": "crypto", "defi": "crypto", "memecoins": "crypto",
    "hyperliquid": "crypto", "dogecoin": "crypto", "bnb": "crypto", "sui": "crypto",
    "politics": "politics", "elections": "politics", "us-presidential-election": "politics",
    "midterms": "politics", "governor-midterms": "politics", "senate-midterms": "politics",
    "senate-midterm-winners": "politics", "house-elections": "politics", "nov-4-elections": "politics",
    "midterm-tossups": "politics", "house-tossups": "politics", "governor-tossups": "politics",
    "senate-tossups": "politics", "tossup-house-races": "politics", "trump": "politics",
    "trump-presidency": "politics", "global-elections": "politics", "world-elections": "politics",
    "main-election": "politics", "texas-midterm": "politics", "canada": "politics",
    "macro": "macro", "economy": "macro", "finance": "macro", "fed": "macro",
    "interest-rates": "macro", "inflation": "macro", "cpi": "macro", "gdp": "macro",
    "recession": "macro", "treasuries": "macro", "tariffs": "macro", "commodities": "macro",
    "gold": "macro", "oil": "macro", "forex": "macro",
    "geopolitics": "geopolitics", "world": "geopolitics", "ukraine": "geopolitics",
    "ukraine-map": "geopolitics", "russia": "geopolitics", "putin": "geopolitics",
    "israel": "geopolitics", "middle-east": "geopolitics", "gaza": "geopolitics",
    "foreign-policy": "geopolitics", "nato": "geopolitics", "trump-zelenskyy": "geopolitics",
    "world-affairs": "geopolitics", "china": "geopolitics", "taiwan": "geopolitics",
    "iran": "geopolitics", "ceasefire": "geopolitics",
    "sports": "sports", "nba": "sports", "nhl": "sports", "nfl": "sports",
    "mlb": "sports", "soccer": "sports", "ufc": "sports", "mma": "sports",
    "tennis": "sports", "f1": "sports", "golf": "sports", "champions-league": "sports",
    "premier-league": "sports", "ncaa": "sports", "march-madness": "sports",
    "ai": "ai_tech", "tech": "ai_tech", "big-tech": "ai_tech", "openai": "ai_tech",
    "chatgpt": "ai_tech", "anthropic": "ai_tech", "claude": "ai_tech", "xai": "ai_tech",
    "grok": "ai_tech", "google": "ai_tech", "gemini": "ai_tech", "meta": "ai_tech",
    "apple": "ai_tech", "nvidia": "ai_tech", "agi": "ai_tech", "llm": "ai_tech",
    "business": "earnings_ipo", "ipos": "earnings_ipo", "pre-market": "earnings_ipo",
    "earnings": "earnings_ipo", "stocks": "earnings_ipo", "equities": "earnings_ipo",
    "m-and-a": "earnings_ipo", "tesla": "earnings_ipo", "microstrategy": "earnings_ipo",
    "spac": "earnings_ipo", "bankruptcy": "earnings_ipo",
    "regulatory": "regulatory", "congress": "regulatory", "sec": "regulatory",
    "etf": "regulatory", "supreme-court": "regulatory", "scotus": "regulatory",
    "doj": "regulatory", "antitrust": "regulatory", "law": "regulatory",
    "legal": "regulatory", "indictment": "regulatory", "pardon": "regulatory",
    "weather": "weather", "climate-science": "weather", "climate": "weather",
    "science": "weather", "hurricane": "weather", "temperature": "weather",
    "noaa": "weather", "space": "weather", "spacex": "weather", "nasa": "weather",
    "pandemic": "weather", "health": "weather", "fda": "weather",
    "entertainment": "entertainment", "pop-culture": "entertainment", "culture": "entertainment",
    "movies": "entertainment", "box-office": "entertainment", "oscars": "entertainment",
    "grammys": "entertainment", "emmys": "entertainment", "music": "entertainment",
    "streaming": "entertainment", "youtube": "entertainment", "tiktok": "entertainment",
    "gaming": "entertainment", "gta-vi": "entertainment", "celebrities": "entertainment",
}


def _f(val: Any) -> Optional[float]:
    if val is None or isinstance(val, bool):
        return None
    try:
        return float(val)
    except (ValueError, TypeError):
        return None


def resolve_canonical_category(ctx: Dict[str, Any]) -> str:
    raw_cat = str(ctx.get("category") or ctx.get("tag") or "").lower().strip()
    if raw_cat in POLY_CATEGORY_DEFAULTS:
        return raw_cat
    if raw_cat in TAG_TO_CANONICAL_CATEGORY:
        return TAG_TO_CANONICAL_CATEGORY[raw_cat]

    tags = ctx.get("tags")
    if isinstance(tags, (list, tuple, set)):
        for t in tags:
            slug = str(t.get("slug") if isinstance(t, dict) else t).lower().strip()
            if slug in POLY_CATEGORY_DEFAULTS:
                return slug
            if slug in TAG_TO_CANONICAL_CATEGORY:
                return TAG_TO_CANONICAL_CATEGORY[slug]

    q = str(ctx.get("question") or ctx.get("title") or "").lower()
    if any(k in q for k in ("bitcoin", "btc", "ethereum", "eth", "solana", "xrp", "token", "fdv", "airdrop")):
        return "crypto"
    if any(k in q for k in ("election", "president", "senate", "governor", "house", "trump", "democrat", "republican")):
        return "politics"
    if any(k in q for k in ("fed", "fomc", "rate cut", "cpi", "inflation", "gdp", "recession", "payroll")):
        return "macro"
    if any(k in q for k in ("ukraine", "russia", "israel", "gaza", "iran", "china", "ceasefire", "nato", "war")):
        return "geopolitics"
    if any(k in q for k in ("nba", "nhl", "nfl", "mlb", "ufc", "champions league", "win the", "playoffs")):
        return "sports"
    if any(k in q for k in ("openai", "gpt", "claude", "anthropic", "grok", "xai", "gemini", "ai model")):
        return "ai_tech"
    if any(k in q for k in ("ipo", "earnings", "revenue", "market cap", "acquire", "merger")):
        return "earnings_ipo"
    if any(k in q for k in ("sec ", "supreme court", "congress pass", "bill", "etf approval", "doj")):
        return "regulatory"
    if any(k in q for k in ("temperature", "hurricane", "weather", "climate", "noaa", "launch", "fda")):
        return "weather"
    if any(k in q for k in ("oscar", "grammy", "box office", "movie", "album", "taylor swift", "gta")):
        return "entertainment"
    return raw_cat or "unknown"


class _BasePolyCategoryBot(Bot):
    id: str = "poly_cat_crypto"
    mandate: str = "Category-specialized Polymarket calibration, base-rate shrinkage, and Brier/CLV learning."
    domains: Tuple[str, ...] = ("polymarket",)
    veto: bool = False
    edge_seat: bool = True
    target_category: str = "crypto"

    def evaluate_for_category(self, cat: str, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        model_p = _f(ctx.get("model_p"))
        market_p = _f(ctx.get("market_p"))
        lower_p = _f(ctx.get("lower_bound_p"))
        spread_bps = _f(ctx.get("spread_bps"))
        days = _f(ctx.get("days_to_resolve"))

        if model_p is None or market_p is None:
            return Vote(self.id, "abstain", 0.0, [f"{self.id}_missing_probabilities"], {})

        state = load_knowledge_state()
        cfg = state.get("polymarket_categories", {}).get(cat) or POLY_CATEGORY_DEFAULTS.get(cat, {})
        min_edge = float(cfg.get("min_edge", 0.035))
        prior = float(cfg.get("base_rate_prior", 0.45))
        brier_ema = float(cfg.get("brier_ema", 0.168))
        clv_cents = float(cfg.get("clv_ema_cents", 1.8))
        mwu = float(cfg.get("mwu_weight", 1.0))

        shrunk_p = 0.90 * model_p + 0.10 * prior
        eff_p = lower_p if (lower_p is not None and lower_p < shrunk_p) else shrunk_p
        edge = eff_p - market_p

        reasons = [
            f"poly_category:{cat}",
            f"brier_ema:{brier_ema:.3f}",
            f"clv_ema:+{clv_cents:.1f}c",
        ]
        evidence = {"category": cat, "shrunk_edge": round(edge, 4), "brier_ema": brier_ema}

        if spread_bps is not None and spread_bps > 300.0:
            return Vote(self.id, "reject", 0.74, reasons + [f"{cat}_clob_spread_too_wide:{spread_bps:.0f}bps"], evidence)

        if days is not None and days > 180 and edge < min_edge * 1.5:
            return Vote(self.id, "reject", 0.71, reasons + [f"{cat}_capital_lockup_too_long:{days:.0f}d"], evidence)

        if edge < -0.01:
            return Vote(self.id, "reject", 0.80, reasons + [f"{cat}_negative_shrunk_edge:{edge:+.3f}"], evidence)
        if edge < min_edge * 0.65:
            return Vote(self.id, "reject", 0.68, reasons + [f"{cat}_below_learned_hurdle:{edge:+.3f}<{min_edge:.3f}"], evidence)

        conf = min(0.89, max(0.60, (0.72 + min(0.12, edge)) * min(1.12, max(0.85, mwu))))
        reasons.append(f"shrunk_edge:{edge:+.3f}")
        return Vote(self.id, "approve", round(conf, 4), reasons, evidence)

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        cat = resolve_canonical_category(ctx)
        if cat != self.target_category:
            return Vote(self.id, "abstain", 0.0, [f"not_category:{cat}!={self.target_category}"], {"category": cat})
        return self.evaluate_for_category(cat, ctx)


class PolyCryptoCategoryBot(_BasePolyCategoryBot):
    id = "poly_cat_crypto"
    target_category = "crypto"

class PolyPoliticsCategoryBot(_BasePolyCategoryBot):
    id = "poly_cat_politics"
    target_category = "politics"

class PolyMacroCategoryBot(_BasePolyCategoryBot):
    id = "poly_cat_macro"
    target_category = "macro"

class PolyGeopoliticsCategoryBot(_BasePolyCategoryBot):
    id = "poly_cat_geopolitics"
    target_category = "geopolitics"

class PolySportsCategoryBot(_BasePolyCategoryBot):
    id = "poly_cat_sports"
    target_category = "sports"

class PolyAiTechCategoryBot(_BasePolyCategoryBot):
    id = "poly_cat_ai_tech"
    target_category = "ai_tech"

class PolyEarningsIpoCategoryBot(_BasePolyCategoryBot):
    id = "poly_cat_earnings_ipo"
    target_category = "earnings_ipo"

class PolyRegulatoryCategoryBot(_BasePolyCategoryBot):
    id = "poly_cat_regulatory"
    target_category = "regulatory"

class PolyWeatherCategoryBot(_BasePolyCategoryBot):
    id = "poly_cat_weather"
    target_category = "weather"

class PolyEntertainmentCategoryBot(_BasePolyCategoryBot):
    id = "poly_cat_entertainment"
    target_category = "entertainment"


class PolyUniversalTagRouterBot(Bot):
    id = "poly_universal_tag_router"
    mandate = "Route any of the 518 active Polymarket Gamma API tags into its canonical category specialist."
    domains = ("polymarket",)
    veto = False
    edge_seat = True

    def evaluate(self, context: Dict[str, Any]) -> Vote:
        ctx = context or {}
        cat = resolve_canonical_category(ctx)
        if not cat or cat == "unknown":
            cat = "politics"
        helper = _BasePolyCategoryBot()
        helper.id = f"poly_tag_{cat}"
        helper.target_category = cat
        v = helper.evaluate_for_category(cat, ctx)
        return Vote(self.id, v.decision, v.confidence, [f"routed_category:{cat}"] + list(v.reasons), v.evidence)


POLY_CATEGORY_CLASSES: Tuple[type, ...] = (
    PolyCryptoCategoryBot,
    PolyPoliticsCategoryBot,
    PolyMacroCategoryBot,
    PolyGeopoliticsCategoryBot,
    PolySportsCategoryBot,
    PolyAiTechCategoryBot,
    PolyEarningsIpoCategoryBot,
    PolyRegulatoryCategoryBot,
    PolyWeatherCategoryBot,
    PolyEntertainmentCategoryBot,
    PolyUniversalTagRouterBot,
)

POLY_CATEGORY_BOTS: Tuple[Bot, ...] = tuple(cls() for cls in POLY_CATEGORY_CLASSES)
