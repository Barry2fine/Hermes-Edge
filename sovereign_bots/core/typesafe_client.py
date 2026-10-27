"""
TypeSafe System One (Jev `jev-latest`) Calibrated Judgment Client.
Follows the `typesafe-ai` skill craft:
- Jev makes atomic calibrated probability judgments (`noul`, `choice`, `score`);
- Code holds the deterministic truth table and EV math;
- Automatically sanitizes `TYPESAFE_API_KEY` if pasted as `TYPESAFE_API_KEY=ts-...` or `Bearer ts-...`.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any, Dict, Optional

TYPESAFE_ENDPOINT = "https://api.typesafe.ai/v1/systemone"
DEFAULT_MODEL = "jev-latest"


def get_clean_typesafe_key(explicit_key: Optional[str] = None) -> str:
    """Return the clean `ts-...` Bearer token even if stored as `KEY=ts-...` or `Bearer ts-...`."""
    raw = (explicit_key or os.environ.get("TYPESAFE_API_KEY") or "").strip()
    if not raw:
        return ""
    if "=" in raw and not raw.startswith("ts-"):
        raw = raw.split("=", 1)[-1].strip()
    if raw.lower().startswith("bearer "):
        raw = raw[7:].strip()
    return raw.strip("\"' ")


def query_system_one(
    *,
    state: Any,
    questions: Dict[str, Dict[str, Any]],
    model: str = DEFAULT_MODEL,
    api_key: Optional[str] = None,
    timeout: int = 12,
) -> Dict[str, Any]:
    """
    Send a bundled set of atomic typed questions (`noul`, `choice`, `score`) over shared `state`
    to TypeSafe System One (`POST https://api.typesafe.ai/v1/systemone`).
    """
    key = get_clean_typesafe_key(api_key)
    if not key:
        return {"ok": False, "error": "missing_typesafe_api_key", "answers": {}}

    payload = {
        "model": model,
        "state": state,
        "questions": questions,
    }
    req = urllib.request.Request(
        TYPESAFE_ENDPOINT,
        data=json.dumps(payload).encode("utf-8"),
        headers={
            "Authorization": f"Bearer {key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "SovereignAI-TypeSafe-Client/1.0",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return {
                "ok": True,
                "model": body.get("model", model),
                "answers": body.get("answers", {}),
                "usage": body.get("usage", {}),
            }
    except urllib.error.HTTPError as exc:
        err_text = exc.read().decode("utf-8", errors="ignore")
        return {"ok": False, "status": exc.code, "error": err_text, "answers": {}}
    except Exception as exc:
        return {"ok": False, "error": str(exc), "answers": {}}


def judge_polymarket_contract(
    *,
    question: str,
    description: str = "",
    tags: Optional[list] = None,
    api_key: Optional[str] = None,
) -> Dict[str, Any]:
    """
    Use Jev (`jev-latest`) to answer atomic calibrated questions on a Polymarket contract:
    - `has_objective_resolution`: noul (whether resolution criteria depend on an objective public source)
    - `ambiguity_trap`: noul (whether wording contains subjective or disputed terms)
    - `domain_category`: choice across canonical Polymarket specialist categories
    Code then maps the truth table into a resolution clarity score and canonical category.
    """
    state = {
        "contract_question": question,
        "resolution_summary": description or question,
        "market_tags": tags or [],
    }
    questions = {
        "has_objective_resolution": {
            "type": "noul",
            "instructions": "Does `contract_question` have a clear, verifiable outcome that can be checked from an official announcement or public data feed?",
        },
        "subjective_dispute_risk": {
            "type": "noul",
            "instructions": "Does settling `contract_question` depend on a subjective opinion or vague wording that reasonable people could dispute?",
        },
        "domain_category": {
            "type": "choice",
            "instructions": "Which topic is `contract_question` primarily about?",
            "criteria": {
                "politics": "Elections, political nominations, legislation, or domestic government officeholders.",
                "geopolitics": "International conflict, foreign leaders, treaties, sanctions, or military actions.",
                "crypto": "Cryptocurrency prices, blockchain protocols, tokens, or digital asset regulation.",
                "macro": "Central bank interest rates, inflation, GDP, tariffs, or macroeconomic indicators.",
                "sports": "Professional or collegiate sports games, championships, player awards, or rosters.",
                "ai_tech": "Artificial intelligence models, technology companies, product launches, or semiconductors.",
            },
        },
    }
    res = query_system_one(state=state, questions=questions, api_key=api_key)
    if not res.get("ok"):
        return {"ok": False, "error": res.get("error")}

    ans = res.get("answers", {})
    obj_noul = float((ans.get("has_objective_resolution") or {}).get("noul") or 0.75)
    dispute_noul = float((ans.get("subjective_dispute_risk") or {}).get("noul") or 0.20)
    dom_choice = (ans.get("domain_category") or {}).get("choice") or "universal"

    # Truth table in code (per `typesafe-ai` skill)
    clarity_score = round(max(0.0, min(1.0, obj_noul * (1.0 - 0.5 * dispute_noul))), 3)
    veto_ambiguity = dispute_noul >= 0.70 or obj_noul < 0.30

    return {
        "ok": True,
        "model": res.get("model"),
        "objective_noul": obj_noul,
        "dispute_noul": dispute_noul,
        "domain_category": dom_choice,
        "clarity_score": clarity_score,
        "veto_ambiguity": veto_ambiguity,
        "usage": res.get("usage", {}),
    }
