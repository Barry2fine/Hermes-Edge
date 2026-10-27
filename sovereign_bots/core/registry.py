"""Bot registry: discover each domain's bots and validate coverage."""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import Any, Dict, List

DOMAINS = ("crypto", "polymarket", "sports")
BOTS_PKG = "sovereign_bots.bots"


def domain_bots(domain: str, module_prefix: str = BOTS_PKG) -> List[Any]:
    mod = importlib.import_module(f"{module_prefix}.{domain}")
    return list(getattr(mod, "BOTS", []))


def all_bots(domains=DOMAINS, module_prefix: str = BOTS_PKG) -> List[Any]:
    out = []
    for d in domains:
        try:
            out.extend(domain_bots(d, module_prefix))
        except Exception:  # noqa: BLE001
            continue
    return out


def load_manifest(path: str = "manifest.json") -> Dict[str, List[str]]:
    p = Path(path)
    if not p.exists():
        return {}
    try:
        return json.loads(p.read_text())
    except Exception:  # noqa: BLE001
        return {}


def coverage(manifest: Dict[str, List[str]]) -> Dict[str, Dict[str, List[str]]]:
    """Which required bots are present/missing per domain."""
    out: Dict[str, Dict[str, List[str]]] = {}
    for domain, required in (manifest or {}).items():
        try:
            present = {b.id for b in domain_bots(domain)}
        except Exception:  # noqa: BLE001
            present = set()
        out[domain] = {
            "present": sorted(present),
            "missing": sorted(set(required) - present),
            "extra": sorted(present - set(required)),
        }
    return out
