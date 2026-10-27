"""Append-only audit log for bot decisions."""

from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Dict


def append(path: str, domain: str, context: Dict[str, Any], result: Dict[str, Any]) -> Dict[str, Any]:
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "domain": domain,
        "decision": result.get("decision"),
        "votes_hash": result.get("votes_hash"),
        "reasons": (result.get("reasons") or [])[:10],
    }
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(rec, default=str) + "\n")
    return rec
