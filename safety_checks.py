"""Offline validation of synthetic planned risk/outcomes. Never submits orders."""
from decimal import Decimal, InvalidOperation


def validate_risk(*, equity, floor, existing_risk, proposed_risk, reserve):
    try:
        values = [Decimal(str(v)) for v in (equity, floor, existing_risk, proposed_risk, reserve)]
    except (InvalidOperation, ValueError):
        return {"ok": False, "error": "INVALID_RISK_INPUT"}
    if any(not v.is_finite() or v < 0 for v in values) or values[3] <= 0:
        return {"ok": False, "error": "INVALID_RISK_INPUT"}
    equity, floor, existing, proposed, reserve = values
    if existing + proposed + reserve > equity - floor:
        return {"ok": False, "error": "INSUFFICIENT_RISK_BUFFER"}
    return {"ok": True, "execution_enabled": False}


def validate_protection(outcome):
    """Require independent verification flags, not merely an entry ID.

    This cannot establish protection on an exchange. On failure it emits an alert;
    it never cancels, closes, replaces, or sends any order as a fallback.
    """
    if not isinstance(outcome, dict):
        return {"ok": False, "error": "INVALID_OUTCOME"}
    required = ("entry_confirmed", "stop_confirmed", "stop_reduce_only", "stop_covers_filled_quantity", "take_profit_confirmed", "take_profit_reduce_only")
    missing = [key for key in required if outcome.get(key) is not True]
    return {"ok": not missing, "error": "PROTECTION_UNVERIFIED" if missing else None,
            "missing_checks": missing, "execution_enabled": False}
