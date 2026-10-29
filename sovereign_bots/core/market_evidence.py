"""Versioned, market-specific observation contracts. No execution authority.

Applicability comes from this registry, never an evidence producer's claim.
Freshness budgets are conservative ingestion policy, not fitted trading edges.
Receipt timestamps measure collection freshness, not exchange quote freshness.
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
from typing import Any, Literal, TypedDict


class MarketIdentity(TypedDict):
    domain: str
    kind: str
    market_id: str


class Observation(TypedDict):
    market_id: str
    source: str
    observed_at: str
    received_at: str
    time_basis: Literal["source", "received"]
    values: dict[str, Any]
    units: dict[str, str]


class MarketRequest(TypedDict):
    schema_version: Literal["market-evidence.v1"]
    profile: str
    market: MarketIdentity
    evidence: dict[str, Observation]


@dataclass(frozen=True)
class Field:
    kind: str
    unit: str
    minimum: float | None = None
    maximum: float | None = None


@dataclass(frozen=True)
class Domain:
    fields: dict[str, Field]
    max_age_seconds: int
    implementation: str


DOMAINS = {
    "price": Domain({"price": Field("positive", "quote/base")}, 60, "crypto/data_integrity.py"),
    "tape": Domain({"prices": Field("prices", "quote/base")}, 60, "crypto/data_integrity.py"),
    "funding": Domain({"funding_rate_bps": Field("number", "bps"),
                       "interval_hours": Field("positive", "hours")}, 60, "core/autonomous_alpha_daemons.py"),
    "funding_schedule": Domain({"minutes_to_funding": Field("number", "minutes", 0)}, 60, "crypto/funding_settlement_trap.py"),
    "reference_price": Domain({"index_price": Field("positive", "quote/base")}, 60, "core/autonomous_alpha_daemons.py"),
    "turnover": Domain({"volume_24h_quote": Field("number", "quote", 0)}, 60, "core/autonomous_alpha_daemons.py"),
    "team_odds": Domain({"home_decimal": Field("odds", "decimal_odds"),
                         "away_decimal": Field("odds", "decimal_odds")}, 300, "sports/line_value.py"),
    "player_distribution": Domain({"projection": Field("number", "count", 0, 200),
                                   "line": Field("count_line", "count", 0, 200),
                                   "market_probability": Field("probability", "probability"),
                                   "side": Field("side", "selection")}, 300, "sports/prop_pmf.py"),
    "lineup": Domain({"starter_confirmed": Field("bool", "boolean"),
                      "injury_status": Field("injury", "category")}, 300, "sports/starter_gate.py; sports/injury.py"),
    "binary_prices": Domain({"yes_price": Field("probability", "probability"),
                             "no_price": Field("probability", "probability")}, 60, "polymarket/fee_hurdle.py"),
    "liquidity": Domain({"liquidity_usd": Field("number", "USD", 0),
                         "volume_24h_usd": Field("number", "USD", 0)}, 60, "polymarket/liquidity.py"),
    "resolution": Domain({"rules": Field("text", "text"),
                          "days_to_resolve": Field("positive", "days")}, 3600, "polymarket/resolution.py"),
    "probability_model": Domain({"probability": Field("probability", "probability"),
                                 "model_version": Field("text", "identifier")}, 300, "polymarket/category_specialists.py"),
}

# Required data are observation inputs; READY is never trade permission.
PROFILES = {
    "crypto.spot": ("crypto", "spot", ("price",), ("tape",)),
    "crypto.perpetual": ("crypto", "perpetual", ("price", "funding"), ("tape", "funding_schedule", "reference_price", "turnover")),
    "sports.moneyline": ("sports", "moneyline", ("team_odds",), ("lineup", "probability_model")),
    "sports.player_prop": ("sports", "player_prop", ("player_distribution", "lineup"), ()),
    "polymarket.binary": ("polymarket", "binary", ("binary_prices", "liquidity", "resolution"), ("probability_model",)),
}


def _snapshot(value):
    """Preserve evidence while making invalid non-JSON numeric tokens explicit."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"invalid_numeric_token": str(value)}
    if isinstance(value, dict):
        return {str(key): _snapshot(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_snapshot(item) for item in value]
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return {"unsupported_type": type(value).__name__}


def timestamp(value):
    if not isinstance(value, str):
        raise ValueError("timestamp must be ISO text")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("timezone required")
    return parsed.astimezone(timezone.utc)


def observation(values, units, source, market_id, received_at, observed_at=None):
    """Keep source time when supplied; label collector time explicitly otherwise."""
    return {"values": values, "units": units, "source": source, "market_id": market_id,
            "received_at": received_at, "observed_at": observed_at if observed_at is not None else received_at,
            "time_basis": "source" if observed_at is not None else "received"}


def market_request(profile, market_id, evidence):
    config = PROFILES.get(profile)
    return {"schema_version": "market-evidence.v1", "profile": profile,
            "market": {"domain": config[0] if config else "unknown",
                       "kind": config[1] if config else "unknown", "market_id": market_id},
            "evidence": evidence}


def _valid(value, field):
    if field.kind == "bool":
        return isinstance(value, bool)
    if field.kind == "text":
        return isinstance(value, str) and bool(value.strip())
    if field.kind == "side":
        return value in ("over", "under")
    if field.kind == "injury":
        return value in ("healthy", "questionable", "confirmed_out", "unknown")
    if field.kind == "prices":
        return isinstance(value, list) and len(value) >= 2 and all(_valid(v, Field("positive", "quote/base")) for v in value)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or not math.isfinite(value):
        return False
    if field.minimum is not None and value < field.minimum or field.maximum is not None and value > field.maximum:
        return False
    return {"positive": value > 0, "odds": value > 1,
            "probability": 0 < value < 1, "number": True, "count_line": float(value * 2).is_integer()}.get(field.kind, False)


def assess_market(request: MarketRequest, *, now: datetime | None = None) -> dict:
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        raise ValueError("evaluation clock must include timezone")
    result = {"schema_version": "market-evidence.v1", "profile": None, "market": None,
              "status": "UNSUPPORTED", "decision": "ABSTAIN", "execution_allowed": False,
              "committee_evaluated": False, "requirements": {}, "calculations": {},
              "coverage": {"required": 0, "available_required": 0}, "reasons": []}
    if not isinstance(request, dict):
        result["reasons"] = ["malformed_request"]
        return result
    profile, market = request.get("profile"), request.get("market")
    result.update(profile=_snapshot(profile), market=_snapshot(market))
    config = PROFILES.get(profile) if isinstance(profile, str) else None
    if request.get("schema_version") != "market-evidence.v1" or config is None:
        result["reasons"] = ["unsupported_schema_or_profile"]
        return result
    if not isinstance(market, dict) or (market.get("domain"), market.get("kind")) != config[:2] or not isinstance(market.get("market_id"), str) or not market["market_id"].strip():
        result["reasons"] = ["market_profile_mismatch_or_missing_identity"]
        return result
    evidence = request.get("evidence")
    if not isinstance(evidence, dict) or any(not isinstance(key, str) for key in evidence) or set(request) - {"schema_version", "profile", "market", "evidence"}:
        result["reasons"] = ["malformed_envelope_or_applicability_override"]
        return result
    required, optional = config[2:]
    blocking = False
    for name, spec in DOMAINS.items():
        requirement = "required" if name in required else "optional" if name in optional else "not_applicable"
        item = evidence.get(name)
        row = {"requirement": requirement, "status": "missing", "reason": "not_supplied",
               "implementation": spec.implementation, "max_age_seconds": spec.max_age_seconds,
               "evidence": _snapshot(item)}
        if requirement == "not_applicable":
            row.update(status="not_applicable", reason="registry_excludes_for_market")
            if item is not None:
                row.update(status="unsupported", reason="cross_market_evidence")
                blocking = True
        elif item is not None:
            try:
                if not isinstance(item, dict) or set(item) != {"market_id", "source", "observed_at", "received_at", "time_basis", "values", "units"}:
                    raise ValueError("malformed_observation")
                if item["market_id"] != market["market_id"] or not isinstance(item["source"], str) or not item["source"].strip():
                    raise ValueError("source_or_market_mismatch")
                observed, received = timestamp(item["observed_at"]), timestamp(item["received_at"])
                if item["time_basis"] not in ("source", "received") or observed > received or received > now:
                    raise ValueError("invalid_time_metadata")
                if item["time_basis"] == "received" and observed != received:
                    raise ValueError("receipt_time_mismatch")
                values, units = item["values"], item["units"]
                if not isinstance(values, dict) or not isinstance(units, dict) or set(values) != set(spec.fields) or set(units) != set(spec.fields):
                    raise ValueError("missing_or_unknown_fields")
                for key, field in spec.fields.items():
                    if units[key] != field.unit or not _valid(values[key], field):
                        raise ValueError("invalid_value_or_unit:" + key)
                if (now - observed).total_seconds() > spec.max_age_seconds:
                    row.update(status="stale", reason="observation_age_exceeds_policy")
                else:
                    row.update(status="available", reason="source_timestamp_within_policy" if item["time_basis"] == "source" else "collection_freshness_only")
            except (ValueError, TypeError, KeyError, OverflowError) as exc:
                row.update(status="malformed", reason=str(exc))
        if requirement == "required":
            result["coverage"]["required"] += 1
            if row["status"] == "available":
                result["coverage"]["available_required"] += 1
            else:
                blocking = True
        elif requirement == "optional" and row["status"] not in ("missing", "available"):
            # Optional may be absent; supplied malformed/stale data cannot be silently ignored.
            blocking = True
        result["requirements"][name] = row
    for name in sorted(set(evidence) - set(DOMAINS)):
        result["requirements"][name] = {"requirement": "unsupported", "status": "unsupported",
                                        "reason": "unknown_evidence_domain", "implementation": None, "max_age_seconds": None, "evidence": _snapshot(evidence[name])}
        blocking = True
    result["status"] = "BLOCKED" if blocking else "READY"
    result["reasons"] = [f"{name}:{row['status']}" for name, row in result["requirements"].items()
                         if row["status"] not in ("available", "not_applicable") and not (row["requirement"] == "optional" and row["status"] == "missing")]
    try:
        if not blocking:
            values = {name: row["evidence"]["values"] for name, row in result["requirements"].items() if row["status"] == "available"}
            if "team_odds" in values:
                odds = values["team_odds"]
                home, away = 1 / odds["home_decimal"], 1 / odds["away_decimal"]
                result["calculations"] = {"home_implied_probability": home, "away_implied_probability": away,
                                          "two_way_overround": home + away - 1}
            elif "binary_prices" in values:
                prices = values["binary_prices"]
                result["calculations"] = {"yes_price": prices["yes_price"], "no_price": prices["no_price"],
                                          "price_sum": prices["yes_price"] + prices["no_price"]}
            elif "player_distribution" in values:
                lineup = values["lineup"]
                if not lineup["starter_confirmed"] or lineup["injury_status"] != "healthy":
                    result.update(status="BLOCKED", reasons=["player_not_confirmed_healthy"])
                else:
                    from ..bots.sports.prop_pmf import over_probability
                    player = values["player_distribution"]
                    pmf = over_probability(player["projection"], player["line"])
                    result["calculations"] = {"p_over": pmf["p_over"], "p_under": pmf["p_under"], "p_push": pmf["p_push"],
                                              "model": "repository_poisson_pmf", "validated_profitability": False}
            elif "price" in values:
                result["calculations"] = {"observed_price": values["price"]["price"]}
                if "funding" in values:
                    funding = values["funding"]
                    result["calculations"]["funding_8h_bps"] = funding["funding_rate_bps"] * 8 / funding["interval_hours"]
                if "reference_price" in values:
                    result["calculations"]["basis_bps"] = (values["price"]["price"] / values["reference_price"]["index_price"] - 1) * 10000
                if "tape" in values:
                    prices = values["tape"]["prices"]
                    result["calculations"]["sample_change_bps"] = (prices[-1] / prices[0] - 1) * 10000
        if any(isinstance(value, float) and not math.isfinite(value) for value in result["calculations"].values()):
            raise ValueError("nonfinite calculation")
    except (ValueError, OverflowError, ZeroDivisionError):
        result.update(status="BLOCKED", calculations={}, reasons=["nonfinite_or_unsupported_calculation"])
    return result
