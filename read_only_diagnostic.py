#!/usr/bin/env python3
"""
Read-Only Diagnostic Validator Wrapper (compatible with Sonia's desk_review.py).
Strictly read-only: never reads .env, never opens network sockets, never mutates state,
and rejects all trading/mutation commands (`ratchet`, `auto-trade`, `trade-propr`, `trade-kucoin`, `close`).
"""
from desk_review import *  # noqa: F401,F403
from desk_review import main

if __name__ == "__main__":
    raise SystemExit(main())
