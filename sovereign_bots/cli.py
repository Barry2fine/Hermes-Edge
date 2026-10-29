"""CLI: decide / list / coverage."""

from __future__ import annotations

import argparse
import json
from typing import Optional

from .core import audit as _audit
from .core.coordinator import decide
from .core.registry import DOMAINS, coverage, domain_bots, load_manifest


def main(argv: Optional[list] = None) -> int:
    ap = argparse.ArgumentParser(prog="sovereign-bots")
    sub = ap.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("decide", help="run a domain's bots on a context")
    d.add_argument("--domain", required=True, choices=DOMAINS)
    d.add_argument("--context", required=True, help="context JSON path")
    d.add_argument("--audit", default=None, help="append verdict to this JSONL")
    d.add_argument("--quorum", type=int, default=1)
    d.add_argument("--no-require-edge", dest="require_edge", action="store_false",
                   help="allow a pack with no edge-bearing seat (context/sanity "
                        "packs only; breaks the fail-closed ALLOW rule)")

    evidence = sub.add_parser("assess-market", help="validate a market evidence file and calculate supported observations; never authorize execution")
    evidence.add_argument("--context", required=True, help="market-evidence.v1 JSON path")
    sub.add_parser("list", help="list bots per domain")
    c = sub.add_parser("coverage", help="check manifest coverage")
    c.add_argument("--manifest", default="manifest.json")

    args = ap.parse_args(argv)

    if args.cmd == "list":
        for dom in DOMAINS:
            try:
                bots = domain_bots(dom)
            except Exception:  # noqa: BLE001
                bots = []
            print(f"{dom}: {[b.id for b in bots]}")
        return 0

    if args.cmd == "coverage":
        cov = coverage(load_manifest(args.manifest))
        print(json.dumps(cov, indent=2))
        return 0 if all(not v["missing"] for v in cov.values()) else 1

    try:
        with open(args.context, "r", encoding="utf-8") as f:
            ctx = json.load(f)
    except (OSError, ValueError) as exc:
        if args.cmd != "assess-market":
            raise
        from .core.market_evidence import assess_market
        result = assess_market(None)
        result["reasons"] = ["input_acquisition_failed:" + type(exc).__name__]
        print(json.dumps(result, indent=2, allow_nan=False))
        return 2
    if args.cmd == "assess-market":
        from .core.market_evidence import assess_market
        result = assess_market(ctx)
        print(json.dumps(result, indent=2, allow_nan=False))
        return 0 if result["status"] == "READY" else 2
    bots = domain_bots(args.domain)
    res = decide(bots, ctx, quorum=args.quorum, require_edge=args.require_edge)
    print(json.dumps(res, indent=2, default=str))
    if args.audit:
        _audit.append(args.audit, args.domain, ctx, res)
    return 0 if res["decision"] == "ALLOW" else 2


if __name__ == "__main__":
    raise SystemExit(main())
