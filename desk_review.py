"""Credential-free diagnostics; deliberately has no exchange or HTTP client."""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import sys
import time
import uuid

MAX_BYTES = 2_000_000
ALLOWED_NAMES = {"LIVE_DESK_STATE.json", "latest_poet_max_telemetry.json"}
MUTATING_ACTIONS = {"ratchet", "auto-trade", "autotrade", "trade-propr", "trade-kucoin", "close"}


class ReviewError(Exception):
    """A safe fixed error code; never include raw provider responses or credentials."""


class OwnedLock:
    """Exclusive OS lock for offline artifacts, not a distributed trading lock.

    The OS releases locks on crashes. Never unlink the lock file: doing so creates
    an inode race. No TTL takeover is permitted, and acquisition fails closed.
    Windows byte-range locking and POSIX flock are kept on the open file handle.
    """
    def __init__(self, path: Path):
        self.path = Path(path)
        self.handle = None
        self.owner = None

    def __enter__(self):
        if self.handle is not None:
            raise ReviewError("LOCK_ALREADY_OWNED")
        handle = None
        try:
            handle = open(self.path, "a+b")
            if os.name == "nt":
                import msvcrt
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
            self.handle = handle
            self.owner = uuid.uuid4().hex
            return self
        except (OSError, ValueError):
            if handle is not None:
                handle.close()
            raise ReviewError("LOCK_UNAVAILABLE") from None

    def release(self, owner):
        if self.handle is None or owner != self.owner:
            raise ReviewError("LOCK_OWNER_MISMATCH")
        handle, self.handle = self.handle, None
        self.owner = None
        handle.close()

    def __exit__(self, *_):
        if self.handle is not None:
            self.release(self.owner)


def load_snapshot(path, *, now=None, max_age=120):
    """Bounded local JSON read. Reject stale/future files, symlinks, and torn reads."""
    path = Path(path)
    if path.name not in ALLOWED_NAMES:
        raise ReviewError("SNAPSHOT_NAME_NOT_ALLOWED")
    if not math.isfinite(max_age) or max_age <= 0:
        raise ReviewError("INVALID_MAX_AGE")
    now = time.time() if now is None else now
    try:
        if path.is_symlink():
            raise ReviewError("SYMLINK_NOT_ALLOWED")
        with path.open("rb") as stream:
            before = os.fstat(stream.fileno())
            if before.st_size > MAX_BYTES:
                raise ReviewError("SNAPSHOT_TOO_LARGE")
            raw = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
        current = path.stat()
        if (before.st_mtime_ns, before.st_size, before.st_ino) != (after.st_mtime_ns, after.st_size, after.st_ino) or (after.st_mtime_ns, after.st_size, after.st_ino) != (current.st_mtime_ns, current.st_size, current.st_ino):
            raise ReviewError("SNAPSHOT_CHANGED_DURING_READ")
        age = now - after.st_mtime
        if age < -5:
            raise ReviewError("SNAPSHOT_FUTURE_TIMESTAMP")
        if age > max_age:
            raise ReviewError("SNAPSHOT_STALE")
        if len(raw) > MAX_BYTES:
            raise ReviewError("SNAPSHOT_TOO_LARGE")
        doc = json.loads(raw, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        if not isinstance(doc, dict):
            raise ReviewError("INVALID_SNAPSHOT_SHAPE")
        return doc, max(0, age), hashlib.sha256(raw).hexdigest()
    except ReviewError:
        raise
    except (OSError, ValueError, UnicodeError):
        raise ReviewError("SNAPSHOT_UNREADABLE") from None


def summarize(doc, age, digest):
    """Allowlist output. Never forward arbitrary strings, account IDs or positions.

    File freshness is not exchange-data freshness. This reports source health only.
    """
    sections = {}
    for name in ("propr_status", "kucoin_status", "auto_exec"):
        value = doc.get(name)
        if not isinstance(value, dict) or not value:
            state = "missing"
        elif value.get("error") or value.get("ok") is False:
            state = "source_error"
        else:
            state = "present_unverified"
        sections[name] = state
    return {
        "ok": all(v == "present_unverified" for v in sections.values()),
        "mode": "read_only_snapshot_review",
        "execution_enabled": False,
        "exchange_verified": False,
        "source_age_seconds": round(age, 3),
        "source_sha256": digest,
        "sections": sections,
    }


def review(path, **kwargs):
    try:
        return summarize(*load_snapshot(path, **kwargs))
    except ReviewError as exc:
        return {"ok": False, "execution_enabled": False, "exchange_verified": False, "error": str(exc)}


def dispatch(action, *, snapshot=None, dry_run=False):
    # Reject before snapshot reads, imports, lock creation, or any other effects.
    if action in MUTATING_ACTIONS:
        return {"ok": False, "error": "EXECUTION_DISABLED", "execution_enabled": False}
    if action != "status" or snapshot is None:
        return {"ok": False, "error": "INVALID_REQUEST", "execution_enabled": False}
    return review(snapshot)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only local snapshot diagnostics; no live trading access")
    parser.add_argument("action", choices=["status"])
    parser.add_argument("--snapshot", required=True)
    args = parser.parse_args(argv)
    result = dispatch(args.action, snapshot=args.snapshot)
    print(json.dumps(result, indent=2))
    return 0 if result["ok"] else 2


if __name__ == "__main__":
    sys.exit(main())
